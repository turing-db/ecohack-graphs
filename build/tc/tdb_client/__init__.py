"""TuringDB 3.0 REST client: raw Cypher over POST /query, change/commit aware, measured.

    from tc.tdb_client import Tdb, TdbError, QueryLog
    tdb = Tdb(url="http://127.0.0.1:6666", graph="g")
    res = tdb.query("MATCH (n:X) RETURN n.id AS id")      # raises TdbError on engine error
    res.rows, res.columns, res.engine_ms, res.wall_ms, res.raw
    old = tdb.at(commit=tdb.history()[-1]["commit"])        # a view sharing the pool and log

The wire contract (measured on 3.0, see README):
  * body = raw Cypher; graph / change / commit are query-string params
  * failures come back as HTTP 200 with a non-null top-level `error` (+ `error_details`),
    so a caller that checks the status or the row count turns a PARSE_ERROR into
    "no results". This client raises on `error`, always.
  * data is column-oriented: data=[chunk, ...], each chunk a list of COLUMNS.
  * an unparseable ?change= or ?commit= value is silently IGNORED by the server (you read
    main/HEAD instead), so both are validated here before sending.
"""
from __future__ import annotations

import os
import re
import threading
import time
import warnings
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import httpx

__all__ = ["Tdb", "TdbError", "QueryLog", "Result", "from_env",
           "valid_graph_name", "valid_commit", "change_hex"]

_UNSET: Any = object()
_GRAPH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{1,16}$")   # commit hashes and change ids are 64-bit: at most 16 hex digits
# 1.x-era "unknown name" messages. On 3.0 an unknown label / edge type / property just
# yields 0 rows (or null), so this only matters against older servers.
_UNKNOWN_RE = re.compile(r"unknown (edge type|label|property)|does not exist|not found in graph", re.I)


# ----------------------------------------------------------------------------- errors
class TdbError(RuntimeError):
    """An engine (or transport) error. `.code` is the engine code (PARSE_ERROR,
    ANALYZE_ERROR, PLAN_ERROR, EXEC_ERROR, GRAPH_NOT_FOUND, CHANGE_NOT_FOUND,
    COMMIT_NOT_FOUND, COMMIT_NOT_LOADED, ...) or HTTP_<status> / TRANSPORT."""

    def __init__(self, code: str, message: str = "", query: str = "", http_status: int | None = None):
        self.code = str(code)
        self.message = str(message or "")
        self.query = query or ""
        self.http_status = http_status
        q = self.query.strip().replace("\n", " ")
        super().__init__(f"{self.code}: {self.message}" + (f" -- in: {q[:300]}" if q else ""))

    # aliases used by older demo code (cx/perimeter: .error/.details/.cypher; codegraph: .details)
    @property
    def error(self) -> str: return self.code
    @property
    def details(self) -> str: return self.message
    @property
    def cypher(self) -> str: return self.query

    @property
    def is_unknown_name(self) -> bool:
        """1.x 'Unknown edge type/label/property'. Never true on 3.0 (it returns 0 rows)."""
        return bool(_UNKNOWN_RE.search(self.message))

    @property
    def is_commit_not_loaded(self) -> bool:
        return self.code == "COMMIT_NOT_LOADED" or "COMMIT_NOT_LOADED" in self.message

    @property
    def is_graph_not_found(self) -> bool:
        return self.code == "GRAPH_NOT_FOUND"

    @property
    def is_change_not_found(self) -> bool:
        return self.code == "CHANGE_NOT_FOUND"

    @property
    def is_already_loaded(self) -> bool:
        return "already loaded" in self.message.lower()

    @property
    def is_already_exists(self) -> bool:
        return "already exists" in self.message.lower() or self.code == "GRAPH_ALREADY_EXISTS"

    @property
    def is_conflict(self) -> bool:
        """CHANGE SUBMIT rejected because main moved under an entity this change edited."""
        return "has been modified on main" in self.message


# ----------------------------------------------------------------------------- log
class QueryLog:
    """Thread-safe ring buffer of every query a Tdb (and all its views) ran.

    Entry: {ts, graph, cypher, engine_ms, wall_ms, rows, error, change, commit}.
    `subscribe(cb)` calls cb(entry) after every query (for SSE activity streams) and
    returns an unsubscribe function. A failing callback is warned about, never raised
    into the query path."""

    def __init__(self, maxlen: int = 500):
        self._buf: deque[dict] = deque(maxlen=maxlen)
        self._subs: list[Callable[[dict], Any]] = []
        self._lock = threading.Lock()
        self._seq = 0

    def append(self, entry: dict) -> dict:
        with self._lock:
            self._seq += 1
            entry = {"seq": self._seq, **entry}
            self._buf.append(entry)
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(entry)
            except Exception as e:  # a broken UI stream must not fail the query
                warnings.warn(f"QueryLog subscriber failed: {e!r}")
        return entry

    def subscribe(self, cb: Callable[[dict], Any]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)

        def unsubscribe():
            with self._lock:
                if cb in self._subs:
                    self._subs.remove(cb)
        return unsubscribe

    def entries(self, since: int = 0) -> list[dict]:
        """Entries with seq > since (poll-style streams keep the last seq they saw)."""
        with self._lock:
            return [e for e in self._buf if e["seq"] > since]

    def take(self) -> list[dict]:
        """Drain: return everything and clear (per-request traces)."""
        with self._lock:
            out = list(self._buf)
            self._buf.clear()
            return out

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    @property
    def last_seq(self) -> int:
        return self._seq

    def __len__(self) -> int:
        return len(self._buf)

    def __iter__(self):
        return iter(self.entries())


# ----------------------------------------------------------------------------- result
@dataclass
class Result:
    columns: list[str]
    rows: list[dict]
    engine_ms: float | None       # the server's own `time` field
    wall_ms: float                # round trip incl. HTTP + decode
    raw: dict = field(repr=False, default_factory=dict)
    column_types: list[str] = field(default_factory=list)

    @property
    def total_ms(self) -> float:  # alias (cx/perimeter Result)
        return self.wall_ms

    def tuples(self) -> list[tuple]:
        return [tuple(r[c] for c in self.columns) for r in self.rows]

    def column(self, name: str | int = 0) -> list:
        key = self.columns[name] if isinstance(name, int) else name
        return [r[key] for r in self.rows]

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)


def decode(raw: dict, query: str = "") -> tuple[list[str], list[dict], list[str]]:
    """Column-chunked envelope -> (columns, row dicts, column types). Raises on `error`.

    Duplicate column names (e.g. `RETURN n.x, m.x` without aliases) would collide in a
    dict; later duplicates get a `#2`, `#3` suffix rather than silently overwriting."""
    if raw.get("error") is not None:
        raise TdbError(str(raw["error"]), str(raw.get("error_details") or ""), query)
    header = raw.get("header") or {}
    names = list(header.get("column_names") or [])
    seen: dict[str, int] = {}
    keys = []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        keys.append(n if seen[n] == 1 else f"{n}#{seen[n]}")
    cols: list[list] = [[] for _ in keys]
    for chunk in raw.get("data") or []:
        for i, col in enumerate(chunk[: len(keys)]):
            if isinstance(col, list):
                cols[i].extend(col)
    n = max((len(c) for c in cols), default=0)
    rows = [{keys[i]: (cols[i][k] if k < len(cols[i]) else None) for i in range(len(keys))}
            for k in range(n)]
    return keys, rows, list(header.get("column_types") or [])


# ----------------------------------------------------------------------------- validation
def valid_graph_name(name: str) -> str:
    """Graph names are bare identifiers (LOAD GRAPH rejects quoted names). Raises ValueError."""
    if not isinstance(name, str) or not _GRAPH_RE.match(name):
        raise ValueError(f"invalid graph name {name!r} (want [A-Za-z_][A-Za-z0-9_]*)")
    return name


def valid_commit(h: str) -> str:
    """A commit hash: 1-16 hex chars (hashes may print with the leading zero dropped).
    Strips a trailing '(HEAD)' as printed by db.history(). Raises ValueError — the server
    silently treats a malformed ?commit= as HEAD, which is a wrong answer, not an error."""
    s = str(h).strip()
    if s.endswith("(HEAD)"):
        s = s[: -len("(HEAD)")]
    if not _HEX_RE.match(s):
        raise ValueError(f"invalid commit hash {h!r}")
    return s.lower()


def change_hex(ch: int | str) -> str:
    """CHANGE NEW / CHANGE LIST return integers; ?change= wants hex. Accepts an int, a
    hex string or a '0x' string. Raises ValueError on anything else (the server would
    silently read main for a malformed id)."""
    if isinstance(ch, bool):
        raise ValueError(f"invalid change id {ch!r}")
    if isinstance(ch, int):
        if ch < 0:
            raise ValueError(f"invalid change id {ch!r}")
        return format(ch, "x")
    s = str(ch).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if not _HEX_RE.match(s):
        raise ValueError(f"invalid change id {ch!r}")
    return s


# ----------------------------------------------------------------------------- client
class Tdb:
    """A TuringDB REST client bound to one graph (and optionally a change or commit).

    Views made with `.at()` / `.on()` share the HTTP pool and the QueryLog. The client
    holds no server-side state, so it is safe to share between threads; a view's
    change/commit is a per-request parameter, not a mode."""

    def __init__(self, url: str | None = None, graph: str | None = None, timeout: float = 30.0,
                 token: str | None = None, log: QueryLog | None = None, *,
                 change: int | str | None = None, commit: str | None = None,
                 auto_load_commit: bool = True, client: httpx.Client | None = None):
        self.url = (url or os.environ.get("TDB_URL") or os.environ.get("TURINGDB_URL")
                    or "http://127.0.0.1:6666").rstrip("/")
        self.graph = graph if graph is not None else (os.environ.get("TDB_GRAPH") or os.environ.get("TURINGDB_GRAPH"))
        self.timeout = timeout
        self.token = token if token is not None else os.environ.get("TURINGDB_AUTH_TOKEN")
        self.log = log if log is not None else QueryLog(maxlen=500)
        self.change = change_hex(change) if change is not None else None
        self.commit = valid_commit(commit) if commit is not None else None
        self.auto_load_commit = auto_load_commit
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._headers = headers
        self._client = client or httpx.Client(timeout=timeout)
        self._loaded_commits: set[tuple[str, str]] = set()

    # -- views -------------------------------------------------------------------------
    def at(self, change: int | str | None = None, commit: str | None = None) -> "Tdb":
        """A view on the same graph at a change (hex/int id) or a commit hash. at() = main/HEAD."""
        return self.on(self.graph, change=change, commit=commit)

    def on(self, graph: str | None, change: int | str | None = None, commit: str | None = None) -> "Tdb":
        """A view on another graph sharing the pool and log."""
        t = Tdb.__new__(Tdb)
        t.__dict__.update(self.__dict__)
        t.graph = graph
        t.change = change_hex(change) if change is not None else None
        t.commit = valid_commit(commit) if commit is not None else None
        return t

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __repr__(self) -> str:
        at = f" change={self.change}" if self.change else f" commit={self.commit}" if self.commit else ""
        return f"Tdb({self.url} graph={self.graph}{at})"

    # -- core --------------------------------------------------------------------------
    def query(self, cypher: str, change: Any = _UNSET, commit: Any = _UNSET, *,
              graph: Any = _UNSET, timeout: float | None = None, log: bool = True) -> Result:
        """Run one Cypher statement. change/commit/graph default to the view's; pass None
        explicitly to force main / HEAD / graphless. Raises TdbError on any engine error.

        If the server answers COMMIT_NOT_LOADED (past commit after a restart) and
        auto_load_commit is on, issues LOAD COMMIT and retries once."""
        g = self.graph if graph is _UNSET else graph
        ch = self.change if change is _UNSET else (change_hex(change) if change is not None else None)
        cm = self.commit if commit is _UNSET else (valid_commit(commit) if commit is not None else None)
        try:
            return self._send(cypher, g, ch, cm, timeout, log)
        except TdbError as e:
            if cm and g and self.auto_load_commit and e.is_commit_not_loaded:
                self.load_commit(cm, graph=g)
                return self._send(cypher, g, ch, cm, timeout, log)
            raise

    def _send(self, cypher: str, g: str | None, ch: str | None, cm: str | None,
              timeout: float | None, log: bool) -> Result:
        params = {}
        if g:
            params["graph"] = g
        if ch:
            params["change"] = ch
        if cm:
            params["commit"] = cm
        entry = {"ts": time.time(), "graph": g, "cypher": cypher.strip(), "change": ch, "commit": cm,
                 "engine_ms": None, "wall_ms": None, "rows": None, "error": None}
        t0 = time.perf_counter()
        try:
            try:
                r = self._client.post(f"{self.url}/query", params=params, content=cypher.encode("utf-8"),
                                      headers=self._headers,
                                      timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
            except httpx.HTTPError as e:
                raise TdbError("TRANSPORT", f"{type(e).__name__}: {e}", cypher) from e
            wall = (time.perf_counter() - t0) * 1000
            entry["wall_ms"] = round(wall, 3)
            if r.status_code == 401:
                raise TdbError("HTTP_401", "unauthorized: server runs -auth-on; set token= or TURINGDB_AUTH_TOKEN",
                               cypher, 401)
            try:
                raw = r.json()
            except ValueError as e:
                # 413 = body over the ~1 MB ceiling; other non-JSON bodies are proxy/server faults
                raise TdbError(f"HTTP_{r.status_code}", r.text[:300], cypher, r.status_code) from e
            if r.status_code >= 400 and raw.get("error") is None:
                raise TdbError(f"HTTP_{r.status_code}", str(raw)[:300], cypher, r.status_code)
            t = raw.get("time")
            entry["engine_ms"] = float(t) if isinstance(t, (int, float)) else None
            cols, rows, types = decode(raw, cypher)
            entry["rows"] = len(rows)
            return Result(cols, rows, entry["engine_ms"], wall, raw, types)
        except TdbError as e:
            entry["error"] = f"{e.code}: {e.message}"[:500]
            if entry["wall_ms"] is None:
                entry["wall_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            raise
        finally:
            if log and self.log is not None:
                self.log.append(entry)

    def rows(self, cypher: str, **kw) -> list[dict]:
        return self.query(cypher, **kw).rows

    def scalar(self, cypher: str, default: Any = None, **kw) -> Any:
        """First column of the first row, or `default` when there are no rows."""
        res = self.query(cypher, **kw)
        if not res.rows or not res.columns:
            return default
        return res.rows[0][res.columns[0]]

    def statement(self, cypher: str, **kw) -> Result:
        """A graphless statement (LIST AVAILABLE GRAPHS, CREATE GRAPH, LOAD GRAPH ...):
        no graph, change or commit param. LOAD GRAPH answers GRAPH_NOT_FOUND otherwise."""
        return self.query(cypher, graph=None, change=None, commit=None, **kw)

    def ping(self) -> bool:
        """Server reachable (and token accepted). Does not prove the graph is loaded."""
        try:
            self.statement("LIST AVAILABLE GRAPHS", log=False)
            return True
        except TdbError:
            return False

    def wait_ready(self, timeout: float = 30.0, interval: float = 0.25) -> None:
        """Poll until the server answers; raises TdbError('TIMEOUT') after `timeout` s."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.ping():
                return
            time.sleep(interval)
        raise TdbError("TIMEOUT", f"server {self.url} not ready after {timeout}s")

    # -- graphs ------------------------------------------------------------------------
    def list_graphs(self, detail: bool = False) -> list:
        """Graph names on disk (LIST AVAILABLE GRAPHS). detail=True returns the rows
        {graphName, isLoaded, isLoading}."""
        rows = self.statement("LIST AVAILABLE GRAPHS").rows
        return rows if detail else [r["graphName"] for r in rows]

    def loaded_graphs(self) -> list[str]:
        return [r["graphName"] for r in self.list_graphs(detail=True) if r.get("isLoaded")]

    def create_graph(self, name: str | None = None, exist_ok: bool = False) -> bool:
        """CREATE GRAPH (it is loaded as a side effect). Returns True if created,
        False if it existed and exist_ok (then it is made sure to be loaded)."""
        name = valid_graph_name(name or self.graph)
        try:
            self.statement(f"CREATE GRAPH {name}")
            return True
        except TdbError as e:
            if exist_ok and e.is_already_exists:
                self.ensure_loaded(name)
                return False
            raise

    def load_graph(self, name: str | None = None) -> bool:
        """LOAD GRAPH; True if it loaded now, False if it was already loaded."""
        name = valid_graph_name(name or self.graph)
        try:
            self.statement(f"LOAD GRAPH {name}")
            return True
        except TdbError as e:
            if e.is_already_loaded:
                return False
            raise

    def ensure_loaded(self, *names: str, create: bool = False) -> list[str]:
        """Make each graph (default: this view's) resident. After a daemon restart graphs
        on disk are NOT loaded and every query answers GRAPH_NOT_FOUND. Returns the names
        that were loaded (or created) now. Missing graphs raise unless create=True."""
        wanted = [valid_graph_name(n) for n in (names or [self.graph])]
        avail = {r["graphName"]: r for r in self.list_graphs(detail=True)}
        done = []
        for n in wanted:
            row = avail.get(n)
            if row is None:
                if create:
                    self.create_graph(n)
                    done.append(n)
                    continue
                raise TdbError("GRAPH_NOT_FOUND",
                               f"graph {n!r} is not on this server ({self.url}); available: {sorted(avail)}. "
                               f"Build it first (or pass create=True).")
            if not row.get("isLoaded"):
                if row.get("isLoading"):
                    self._wait_loaded(n)
                elif self.load_graph(n):
                    done.append(n)
        return done

    def _wait_loaded(self, name: str, timeout: float = 120.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            row = next((r for r in self.list_graphs(detail=True) if r["graphName"] == name), None)
            if row and row.get("isLoaded"):
                return
            time.sleep(0.25)
        raise TdbError("TIMEOUT", f"graph {name!r} still loading after {timeout}s")

    # -- versions ----------------------------------------------------------------------
    def load_commit(self, commit: str, graph: str | None = None) -> None:
        """LOAD COMMIT '<hash>' (needed for past commits after a restart; harmless if
        resident). Sent on the graph with no change/commit param."""
        h = valid_commit(commit)
        g = graph or self.graph
        if (g, h) in self._loaded_commits:
            return
        try:
            self.query(f"LOAD COMMIT '{h}'", graph=g, change=None, commit=None)
        except TdbError as e:
            if "already" not in e.message.lower():
                raise
        self._loaded_commits.add((g, h))

    def history(self) -> list[dict]:
        """CALL db.history() as dicts, newest first:
        {commit, head: bool, nodeCount, edgeCount, partCount}. The '(HEAD)' suffix the
        engine prints on row 0 is stripped. node/edge/partCount are per-commit deltas
        (what that commit's datapart added), not totals."""
        res = self.query("CALL db.history()", commit=None)
        out = []
        for r in res.rows:
            raw = str(r.get("commit", ""))
            out.append({**r, "commit": raw.replace("(HEAD)", "").strip(), "head": raw.endswith("(HEAD)")})
        return out

    def head(self) -> str:
        """The current HEAD commit hash of this view's graph (or change)."""
        h = self.history()
        if not h:
            raise TdbError("NO_HISTORY", f"graph {self.graph!r} has no history")
        return next((r["commit"] for r in h if r["head"]), h[0]["commit"])

    # -- introspection (3.0 procedure catalog) -----------------------------------------
    def labels(self) -> list[str]:
        return [r["label"] for r in self.rows("CALL db.labels()")]

    def edge_types(self) -> list[str]:
        return [r["edgeType"] for r in self.rows("CALL db.edgeTypes()")]

    def property_types(self) -> dict[str, str]:
        """{propertyName: valueType} — ONE type per name, graph-wide, nodes and edges."""
        return {r["propertyType"]: r["valueType"] for r in self.rows("CALL db.propertyTypes()")}

    def changes(self) -> list[str]:
        """Open change ids (hex) on this graph."""
        return [change_hex(int(r["changeID"])) for r in self.query("CHANGE LIST", change=None, commit=None).rows]

    def optional(self, cypher: str, **kw) -> list[dict]:
        """Rows, or [] when the only problem is an unknown label/edge type/property
        (1.x servers raised for types not yet created at a commit; 3.0 returns 0 rows).
        Every other error still raises."""
        try:
            return self.rows(cypher, **kw)
        except TdbError as e:
            if e.is_unknown_name:
                return []
            raise


def from_env(graph: str | None = None, **kw) -> Tdb:
    """Tdb from TDB_URL | TURINGDB_URL | TURINGDB_HOST+TURINGDB_PORT, TDB_GRAPH |
    TURINGDB_GRAPH and TURINGDB_AUTH_TOKEN."""
    url = os.environ.get("TDB_URL") or os.environ.get("TURINGDB_URL")
    if not url:
        host = os.environ.get("TURINGDB_HOST", "127.0.0.1")
        port = os.environ.get("TURINGDB_PORT", "6666")
        url = host if host.startswith("http") else f"http://{host}:{port}"
    return Tdb(url=url, graph=graph, **kw)
