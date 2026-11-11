"""jsonl_bulk_builder — build a TuringDB graph in one shot with LOAD JSONL.

Accumulate nodes keyed by a stable business key and links by key, write the
APOC-shaped JSONL TuringDB's `LOAD JSONL` reads, stage it into the SERVER's
`<turing-dir>/data/`, load it as a NEW graph (one commit) and verify the counts.

    from tc.tdb_client import Tdb
    from tc.jsonl_bulk_builder import GraphBuilder, local_stager

    g = GraphBuilder(doubles={"lat", "lon", "w"})
    g.node("gw", ["Node", "Gateway"], name="Gateway", lat=43.67, lon=4.62)
    g.node("s1", ["Node", "Sensor"], name="Sensor 1", lat=43.52, lon=4.49)
    g.link("gw", "s1", "RELAYS_TO", both=True, w=17.2)       # both ways: shortestPath is directed
    rep = g.load(Tdb(url="http://127.0.0.1:6666"), "my_mesh", stage=local_stager("~/.turing"))
    assert rep.verify["ok"], rep.verify["mismatches"]

Rules this encodes (all measured on TuringDB 3.0, see README):
  * ~200x faster than Cypher CREATE: edges reference nodes by a file-local id, so the
    server never searches for an endpoint (codegraph: 18k nodes/65k edges 79 s -> 0.4 s).
  * ids are written dense from 0 (nodes and relationships separately), in insertion
    order or sorted by key (`sort=True`) for byte-reproducible files.
  * property types are per NAME, graph-wide, nodes AND edges. An int written to a name
    that is Double elsewhere lands in a shadow `<name> (Int64)` property and the real
    property reads null on that node. The builder pins one type per name: names in
    `doubles` are always written as doubles, a name that is int on some elements and
    float on others is promoted to Double, anything else mixed raises.
  * a JSON null is stored as the STRING 'null' (and registers the name as String), so
    None / NaN are omitted, never written.
  * LOAD JSONL only CREATES: it fails on an existing graph name (even an empty one made
    by CREATE GRAPH) and there is no DROP GRAPH. The engine's error is the same opaque
    "failed to import graph" for a missing file, so the loader checks both first.
  * the path is resolved relative to the server's <turing-dir>/data/.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

__all__ = ["GraphBuilder", "JsonlWriter", "LoadReport", "TypePlan", "local_stager", "scp_stager",
           "load_jsonl", "verify_counts", "rebuild_hint", "plain"]

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FILE_RE = re.compile(r"^[A-Za-z0-9_.\-/]+$")

# TuringDB 3.0 db.propertyTypes() valueType per Python kind
ENGINE_TYPE = {"bool": "Bool", "int": "Int64", "float": "Double", "str": "String", "list": "List"}


# ----------------------------------------------------------------------------- values
def plain(v: Any) -> Any:
    """numpy / pandas scalars -> Python natives; NaN / NA / NaT -> None."""
    if v is None:
        return None
    if hasattr(v, "item") and not isinstance(v, (list, tuple, dict, str, bytes)):
        try:
            v = v.item()
        except (ValueError, TypeError):
            pass
    if isinstance(v, float) and math.isnan(v):
        return None
    try:  # pandas.NA / NaT without importing pandas
        if type(v).__name__ in ("NAType", "NaTType"):
            return None
    except Exception:  # noqa: BLE001
        pass
    if isinstance(v, tuple):
        v = list(v)
    if isinstance(v, list):
        return [plain(x) for x in v]
    return v


def _kind(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    raise TypeError(f"unsupported property value {v!r} ({type(v).__name__}); "
                    "TuringDB has no map properties — json.dumps it into a string")


class TypePlan:
    """One engine type per property name, decided from every value before writing.

    doubles: names always written as Double (pin names whose values may all happen to
             be whole numbers in this build but are fractional in a later commit).
    promote_mixed_numbers: int on some elements + float on others -> Double (default),
             else raise.
    """

    def __init__(self, doubles: Iterable[str] = (), promote_mixed_numbers: bool = True):
        self.doubles = set(doubles)
        self.promote = promote_mixed_numbers
        self.seen: dict[str, set[str]] = {}

    def observe(self, props: dict[str, Any]) -> None:
        for k, v in props.items():
            self.seen.setdefault(k, set()).add(_kind(v))

    def resolve(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, kinds in self.seen.items():
            if k in self.doubles:
                bad = kinds - {"int", "float"}
                if bad:
                    raise TypeError(f"property {k!r} is declared a double but has {sorted(bad)} values")
                out[k] = "float"
            elif len(kinds) == 1:
                out[k] = next(iter(kinds))
            elif kinds == {"int", "float"} and self.promote:
                out[k] = "float"
            else:
                raise TypeError(f"property {k!r} mixes {sorted(kinds)} across elements; TuringDB keeps ONE "
                                "type per property name graph-wide (nodes and edges) — rename one or coerce")
        for k in self.doubles - set(out):
            out[k] = "float"
        return out

    @staticmethod
    def engine_types(kinds: dict[str, str]) -> dict[str, str]:
        return {k: ENGINE_TYPE[v] for k, v in kinds.items()}


def _clean(props: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in props.items():
        v = plain(v)
        if v is None:
            continue  # a JSON null is stored as the string 'null'
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(f"property {k!r} is not finite: {v}")
        out[k] = v
    return out


def _cast(v: Any, kind: str) -> Any:
    if kind == "float" and not isinstance(v, bool) and isinstance(v, (int, float)):
        return float(v)  # json.dumps(2.0) == "2.0": never serialised as an integer
    return v


# ----------------------------------------------------------------------------- writer
class JsonlWriter:
    """Streaming low-level writer: node() returns the dense file id, rel() takes ids.

    Use directly for graphs too big to hold in a GraphBuilder (millions of elements);
    you are then responsible for one type per property name (pass `kinds`, e.g. from a
    TypePlan, to force casts). Counts per label / sublabel / edge type are kept for the
    manifest."""

    def __init__(self, path: str | Path, kinds: dict[str, str] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.kinds = kinds or {}
        self._fh = open(self.path, "w", encoding="utf-8")
        self.nodes = 0
        self.rels = 0
        self.label_counts: Counter = Counter()      # every label a node carries
        self.first_label_counts: Counter = Counter()
        self.edge_counts: Counter = Counter()
        self._rel_started = False

    def _props(self, props: dict[str, Any]) -> dict[str, Any]:
        p = _clean(props)
        return {k: _cast(v, self.kinds.get(k, "")) for k, v in p.items()}

    def node(self, labels: list[str], props: dict[str, Any]) -> int:
        if self._rel_started:
            raise RuntimeError("write every node before the first relationship")
        if not labels:
            raise ValueError("a node needs at least one label")
        for lb in labels:
            if not _NAME_RE.match(lb):
                raise ValueError(f"bad label {lb!r}")
        self._fh.write(json.dumps({"type": "node", "id": str(self.nodes), "labels": list(labels),
                                   "properties": self._props(props)},
                                  allow_nan=False, separators=(",", ":")) + "\n")
        self.label_counts.update(set(labels))
        self.first_label_counts[labels[0]] += 1
        self.nodes += 1
        return self.nodes - 1

    def rel(self, type_: str, start: int, end: int, props: dict[str, Any] | None = None) -> int:
        if not _NAME_RE.match(type_):
            raise ValueError(f"bad edge type {type_!r}")
        if not (0 <= start < self.nodes and 0 <= end < self.nodes):
            raise KeyError(f"relationship {type_} {start}->{end} references a node not written")
        self._rel_started = True
        self._fh.write(json.dumps({"type": "relationship", "id": str(self.rels), "label": type_,
                                   "start": {"id": str(start)}, "end": {"id": str(end)},
                                   "properties": self._props(props or {})},
                                  allow_nan=False, separators=(",", ":")) + "\n")
        self.edge_counts[type_] += 1
        self.rels += 1
        return self.rels - 1

    def close(self) -> dict:
        self._fh.close()
        data = self.path.read_bytes()
        return {
            "jsonl": self.path.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "nodes": self.nodes,
            "relationships": self.rels,
            "labels": dict(sorted(self.label_counts.items())),
            "firstLabels": dict(sorted(self.first_label_counts.items())),
            "edgeTypes": dict(sorted(self.edge_counts.items())),
            "propertyTypes": TypePlan.engine_types(self.kinds),
        }

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if not self._fh.closed:
            self._fh.close()


# ----------------------------------------------------------------------------- builder
@dataclass
class _Link:
    src: str
    tgt: str
    type: str
    props: dict


class GraphBuilder:
    """In-memory accumulation by business key -> one JSONL file -> LOAD JSONL.

    doubles      names always written as Double (lat, lon, weights, scores...)
    key_prop     property that stores the business key on every node (None = do not store)
    mirror       edge-type globs written in BOTH directions at write time
                 (e.g. {"HAS_*", "IN_PATHWAY"}): shortestPath only walks outgoing edges.
                 Mirroring doubles the stored count — the manifest counts what is stored.
    edge_defaults props added to every edge that lacks them (e.g. {"weight": 1.0} so a
                 weighted shortestPath works as hop count)
    caption      callable(labels, props) -> str|None, or {label: "{name} {surname}"} templates;
                 stored as `caption_prop` when the node does not set it (visualizer caption)
    on_missing   "raise" (default) or "skip" a link whose endpoint is not a node; skipped
                 links are counted in the manifest, never silently dropped
    """

    def __init__(self, doubles: Iterable[str] = (), key_prop: str | None = "sid", *,
                 mirror: Iterable[str] = (), edge_defaults: dict[str, Any] | None = None,
                 caption: Callable[[list[str], dict], str | None] | dict[str, str] | None = None,
                 caption_prop: str = "name", on_missing: str = "raise",
                 promote_mixed_numbers: bool = True):
        if on_missing not in ("raise", "skip"):
            raise ValueError("on_missing must be 'raise' or 'skip'")
        self.doubles = set(doubles)
        self.key_prop = key_prop
        self.mirror = list(mirror)
        self.edge_defaults = dict(edge_defaults or {})
        self.caption = caption
        self.caption_prop = caption_prop
        self.on_missing = on_missing
        self.promote = promote_mixed_numbers
        self.nodes: dict[str, tuple[list[str], dict]] = {}
        self.links: list[_Link] = []
        self.skipped: Counter = Counter()

    # -- accumulate --------------------------------------------------------------------
    def node(self, key: str, labels: list[str] | str, **props: Any) -> str:
        """Add a node; a duplicate key raises (use upsert() for get-or-create/merge)."""
        key = str(key)
        if key in self.nodes:
            raise ValueError(f"duplicate node key {key!r}")
        labels = [labels] if isinstance(labels, str) else list(labels)
        if not labels:
            raise ValueError("a node needs at least one label")
        self.nodes[key] = (labels, dict(props))
        return key

    def upsert(self, key: str, labels: list[str] | str, **props: Any) -> str:
        """Get-or-create: a new key is added; an existing one gains any new labels and
        has `props` merged over its own (a stub created by a link source gets filled in)."""
        key = str(key)
        labels = [labels] if isinstance(labels, str) else list(labels)
        if key not in self.nodes:
            return self.node(key, labels, **props)
        have, p = self.nodes[key]
        for lb in labels:
            if lb not in have:
                have.append(lb)
        p.update({k: v for k, v in props.items() if plain(v) is not None})
        return key

    def has(self, key: str) -> bool:
        return str(key) in self.nodes

    def link(self, src: str, tgt: str, type_: str, both: bool = False, **props: Any) -> None:
        """Add an edge by node keys. Endpoints are resolved at write time, so links may
        be added before their nodes. both=True also stores tgt->src with the same props."""
        self.links.append(_Link(str(src), str(tgt), type_, dict(props)))
        if both:
            self.links.append(_Link(str(tgt), str(src), type_, dict(props)))

    # -- shape ---------------------------------------------------------------------------
    def _caption(self, labels: list[str], props: dict) -> str | None:
        c = self.caption
        if c is None:
            return None
        if callable(c):
            return c(labels, props)
        for lb in labels[::-1]:  # most specific label first
            if lb in c:
                try:
                    s = c[lb].format_map({k: ("" if v is None else v) for k, v in props.items()}).strip()
                except KeyError:
                    continue
                return s or None
        return None

    def _mirrored(self, type_: str) -> bool:
        return any(fnmatch.fnmatchcase(type_, pat) for pat in self.mirror)

    def _materialise(self, sort: bool) -> tuple[list[tuple[str, list[str], dict]], list[tuple[str, str, str, dict]]]:
        keys = sorted(self.nodes) if sort else list(self.nodes)
        nodes = []
        for k in keys:
            labels, props = self.nodes[k]
            p = dict(props)
            if self.key_prop:
                p.setdefault(self.key_prop, k)
            if self.caption is not None and plain(p.get(self.caption_prop)) is None:
                cap = self._caption(labels, p)
                if cap is not None:
                    p[self.caption_prop] = cap
            nodes.append((k, labels, _clean(p)))
        self.skipped = Counter()
        links = []
        for ln in self.links:
            if ln.src not in self.nodes or ln.tgt not in self.nodes:
                if self.on_missing == "raise":
                    missing = ln.src if ln.src not in self.nodes else ln.tgt
                    raise KeyError(f"{ln.type} link endpoint {missing!r} is not a node")
                self.skipped[ln.type] += 1
                continue
            p = _clean({**self.edge_defaults, **ln.props})
            links.append((ln.src, ln.tgt, ln.type, p))
            if self._mirrored(ln.type):
                links.append((ln.tgt, ln.src, ln.type, p))
        if sort:
            links.sort(key=lambda e: (e[2], e[0], e[1], json.dumps(e[3], sort_keys=True)))
        return nodes, links

    def plan(self, nodes, links) -> dict[str, str]:
        tp = TypePlan(self.doubles, self.promote)
        for _, _, p in nodes:
            tp.observe(p)
        for *_, p in links:
            tp.observe(p)
        return tp.resolve()

    # -- output ----------------------------------------------------------------------------
    def write(self, path: str | Path, sort: bool = False) -> dict:
        """Write the JSONL; returns the manifest (counts, sha256, property types) and
        writes it next to the file as <name>.manifest.json."""
        nodes, links = self._materialise(sort)
        kinds = self.plan(nodes, links)
        t0 = time.perf_counter()
        idx: dict[str, int] = {}
        with JsonlWriter(path, kinds) as w:
            for k, labels, p in nodes:
                idx[k] = w.node(labels, p)
            for s, t, typ, p in links:
                w.rel(typ, idx[s], idx[t], p)
            man = w.close()
        man.update({"keyProp": self.key_prop, "skippedLinks": dict(self.skipped),
                    "mirrored": sorted({t for _, _, t, _ in links if self._mirrored(t)}),
                    "writeSeconds": round(time.perf_counter() - t0, 3)})
        Path(str(path) + ".manifest.json").write_text(json.dumps(man, indent=1) + "\n")
        return man

    def expected_counts(self) -> dict:
        """{labels: {label: n}, edgeTypes: {type: n}} as they will be stored (mirroring included)."""
        nodes, links = self._materialise(False)
        lc: Counter = Counter()
        for _, labels, _ in nodes:
            lc.update(set(labels))
        return {"nodes": len(nodes), "relationships": len(links), "labels": dict(lc),
                "edgeTypes": dict(Counter(t for _, _, t, _ in links))}

    def load(self, tdb, graph: str, stage: Callable[[Path, str], None], *, workdir: str | Path | None = None,
             embeddings: list[tuple[str, int]] | None = None, if_exists: str = "error", sort: bool = False,
             verify: bool = True) -> "LoadReport":
        """write() + stage + LOAD JSONL + verify. See load_jsonl() for the arguments."""
        workdir = Path(workdir or os.environ.get("TC_BUILD_DIR", ".build"))
        local = workdir / f"{graph}.jsonl"
        man = self.write(local, sort=sort)
        return load_jsonl(tdb, graph, local, stage, manifest=man, embeddings=embeddings,
                          if_exists=if_exists, verify=verify)


# ----------------------------------------------------------------------------- staging
def local_stager(turing_dir: str | Path) -> Callable[[Path, str], None]:
    """Copy into <turing-dir>/data/<name> on THIS machine (server runs locally). The
    turing dir must be the one the daemon was started with (-turing-dir)."""
    root = Path(os.path.expanduser(str(turing_dir)))

    def stage(src: Path, name: str) -> None:
        if not root.is_dir():
            raise FileNotFoundError(f"{root} does not exist — pass the server's -turing-dir "
                                    "(and start the server once before writing into it)")
        dst = root / "data" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if Path(src).is_dir():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
        else:
            shutil.copyfile(src, dst)
    return stage


def scp_stager(host: str, turing_dir: str = "~/.turing") -> Callable[[Path, str], None]:
    """Copy to <turing-dir>/data/<name> on a remote server with scp (the server reads
    files from ITS data dir, not the client's)."""
    def stage(src: Path, name: str) -> None:
        remote = f"{turing_dir.rstrip('/')}/data/{name}"
        parent = os.path.dirname(remote)
        subprocess.run(["ssh", host, f"mkdir -p {parent}" + (f" && rm -rf {remote}" if Path(src).is_dir() else "")],
                       check=True)
        remote_scp = remote[2:] if remote.startswith("~/") else remote  # scp paths are home-relative
        subprocess.run(["scp", "-q"] + (["-r"] if Path(src).is_dir() else []) + [str(src), f"{host}:{remote_scp}"],
                       check=True)
    return stage


# ----------------------------------------------------------------------------- load
@dataclass
class LoadReport:
    graph: str
    seconds: float
    elements: int
    commit: str | None
    manifest: dict
    verify: dict = field(default_factory=dict)
    renamed_from: str | None = None
    skipped: bool = False

    @property
    def elements_per_second(self) -> int:
        return int(self.elements / self.seconds) if self.seconds else 0

    def as_dict(self) -> dict:
        return {"graph": self.graph, "seconds": self.seconds, "elements": self.elements,
                "elementsPerSecond": self.elements_per_second, "commit": self.commit,
                "renamedFrom": self.renamed_from, "skipped": self.skipped, "verify": self.verify}


def rebuild_hint(graph: str, turing_dir: str = "<turing-dir>") -> str:
    return (f"graph {graph!r} already exists and LOAD JSONL/PARQUET only create (there is no DROP GRAPH). "
            f"Rebuild: stop THIS server (turingdb stop -turing-dir {turing_dir}), rm -rf {turing_dir}/graphs/{graph} "
            f"(and {turing_dir}/vector/{graph}), start it, load again — see ops/tdb-server. "
            "Or load under a new name (if_exists='suffix').")


def _graph_names(tdb) -> list[str]:
    return list(tdb.list_graphs())


def _next_free(base: str, names: list[str]) -> str:
    i = 2
    while f"{base}_{i}" in names:
        i += 1
    return f"{base}_{i}"


def load_jsonl(tdb, graph: str, local_file: str | Path, stage: Callable[[Path, str], None] | None, *,
               manifest: dict | None = None, embeddings: list[tuple[str, int]] | None = None,
               if_exists: str = "error", verify: bool = True, staged_name: str | None = None) -> LoadReport:
    """Stage `local_file` as <data>/<graph>.jsonl and `LOAD JSONL ... AS graph`.

    tdb        a tc.tdb_client.Tdb (any graph; LOAD JSONL is sent graphless)
    stage      local_stager(...) / scp_stager(...) / None when the file is already in place
    embeddings [("emb", 32)] -> WITH EMBEDDINGS [{"emb", 32}] (dimension must be > 1)
    if_exists  "error" (default: raise with the rebuild recipe), "skip" (verify the
               existing graph, load nothing) or "suffix" (load as graph_2, graph_3 ...)
    Returns a LoadReport; .graph is the name actually loaded. Raises on any engine error
    and (verify=True) on a count mismatch — a short load is a failure, not a success."""
    if not _NAME_RE.match(graph):
        raise ValueError(f"bad graph name {graph!r}")
    local_file = Path(local_file)
    if manifest is None:
        mf = Path(str(local_file) + ".manifest.json")
        manifest = json.loads(mf.read_text()) if mf.exists() else {}
    names = _graph_names(tdb)
    renamed = None
    if graph in names:
        if if_exists == "skip":
            g = tdb.on(graph)
            g.ensure_loaded(graph)
            rep = LoadReport(graph, 0.0, 0, g.head(), manifest, skipped=True)
            if verify and manifest:
                rep.verify = verify_counts(g, manifest)
            return rep
        if if_exists == "suffix":
            renamed, graph = graph, _next_free(graph, names)
        else:
            raise RuntimeError(rebuild_hint(graph))
    name = staged_name or f"{graph}.jsonl"
    if not _FILE_RE.match(name) or ".." in name:
        raise ValueError(f"bad staged file name {name!r}")
    if stage is not None:
        stage(local_file, name)
    q = f"LOAD JSONL '{name}' AS {graph}"
    if embeddings:
        for prop, dim in embeddings:
            if not _NAME_RE.match(prop) or int(dim) < 2:
                raise ValueError(f"bad embedding spec {(prop, dim)!r} (dimension must be > 1)")
        q += " WITH EMBEDDINGS [" + ", ".join(f'{{"{p}", {int(d)}}}' for p, d in embeddings) + "]"
    t0 = time.perf_counter()
    try:
        tdb.statement(q, timeout=max(getattr(tdb, "timeout", 30) or 30, 3600))
    except Exception as e:
        raise RuntimeError(f"{q} failed: {e}\n(the engine says the same thing for a file missing from "
                           f"<turing-dir>/data/{name}, a malformed line and an existing graph; check the "
                           "server log <turing-dir>/logs/)") from e
    secs = time.perf_counter() - t0
    g = tdb.on(graph)
    rep = LoadReport(graph, round(secs, 3), int(manifest.get("nodes", 0)) + int(manifest.get("relationships", 0)),
                     g.head(), manifest, renamed_from=renamed)
    if verify and manifest:
        rep.verify = verify_counts(g.at(commit=rep.commit), manifest)
        if not rep.verify["ok"]:
            raise RuntimeError(f"LOAD JSONL into {graph} does not match the export: {rep.verify['mismatches']}")
    return rep


# ----------------------------------------------------------------------------- verify
def verify_counts(tdb, manifest: dict, *, check_types: bool = True) -> dict:
    """Compare a loaded graph (the Tdb view's graph/commit) with a manifest:
    total nodes/edges, every label (membership), every edge type, and — if the manifest
    has propertyTypes — the engine type of each name plus shadow '<name> (Type)' entries.

    Count at the LOAD commit (tdb.at(commit=...)), not HEAD, once later commits add to
    the same labels. Edges are counted directed (()-[e]->()); the undirected form
    double-counts."""
    def count(q: str) -> int:
        return int(tdb.scalar(q, default=0) or 0)

    got = {"nodes": count("MATCH (n) RETURN count(n)"),
           "relationships": count("MATCH ()-[e]->() RETURN count(e)"),
           "labels": {lb: count(f"MATCH (n:{lb}) RETURN count(n)") for lb in manifest.get("labels", {})},
           "edgeTypes": {et: count(f"MATCH ()-[e:{et}]->() RETURN count(e)") for et in manifest.get("edgeTypes", {})}}
    mism = []
    for k in ("nodes", "relationships"):
        if k in manifest and got[k] != manifest[k]:
            mism.append(f"{k}: expected {manifest[k]}, got {got[k]}")
    for sect in ("labels", "edgeTypes"):
        for name, want in manifest.get(sect, {}).items():
            if got[sect][name] != want:
                mism.append(f"{sect[:-1]} {name}: expected {want}, got {got[sect][name]}")
    shadow, wrong = [], {}
    if check_types and manifest.get("propertyTypes"):
        live = tdb.property_types()
        shadow = sorted(k for k in live if " (" in k)
        for k, want in manifest["propertyTypes"].items():
            have = live.get(k)
            if have != want and not (want == "List" and have in ("List", "Embedding")):
                wrong[k] = {"expected": want, "got": have}
        if shadow:
            mism.append(f"shadow properties (one name written with two types): {shadow}")
        if wrong:
            mism.append(f"property types differ: {wrong}")
    return {"ok": not mism, "mismatches": mism, "counts": got, "shadowProperties": shadow, "wrongTypes": wrong}


def iter_jsonl(path: str | Path) -> Iterator[dict]:
    """Read a JSONL graph file back (tests, converters)."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)
