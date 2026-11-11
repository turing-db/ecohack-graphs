"""Load a built JSONL into a TuringDB 3.1 server and verify node/edge counts.

    python build/load.py out/woodland_soil.jsonl --graph woodland_soil --turing-dir turing --port 6780 [--reload]

LOAD JSONL only creates graphs (there is no DROP GRAPH), so --reload stops the server,
removes turing/graphs/<graph>, restarts it and loads again. Only use it on this
project's own turing dir.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from turingdb import TuringDB, TuringDBException

BIN = Path(sys.executable).parent / "turingdb"


def server(cmd: str, turing_dir: Path, port: int) -> None:
    args = [str(BIN), cmd, "-turing-dir", str(turing_dir)]
    if cmd == "start":
        args += ["-p", str(port), "-demon"]
    subprocess.run(args, check=cmd == "start", capture_output=True)


def connect(port: int, wait: float = 30) -> TuringDB:
    t0 = time.time()
    while True:
        try:
            c = TuringDB(host="localhost", port=port)
            c.list_loaded_graphs()
            return c
        except Exception:
            if time.time() - t0 > wait:
                raise
            time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--graph", required=True)
    ap.add_argument("--turing-dir", type=Path, default=Path("turing"))
    ap.add_argument("--port", type=int, default=6666)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    td = a.turing_dir.resolve()
    manifest = json.loads(Path(str(a.jsonl) + ".manifest.json").read_text())

    c = connect(a.port)
    if a.graph in c.list_available_graphs():
        if not a.reload:
            sys.exit(f"graph {a.graph} exists; pass --reload to rebuild it")
        server("stop", td, a.port)
        time.sleep(1)
        shutil.rmtree(td / "graphs" / a.graph)
        server("start", td, a.port)
        c = connect(a.port)

    (td / "data").mkdir(exist_ok=True)
    staged = td / "data" / a.jsonl.name
    shutil.copyfile(a.jsonl, staged)
    t0 = time.time()
    c.query(f"LOAD JSONL '{a.jsonl.name}' AS {a.graph}")
    print(f"LOAD JSONL {a.graph}: {time.time() - t0:.1f}s")
    staged.unlink()
    try:
        c.load_graph(a.graph)
    except TuringDBException:
        pass
    c.set_graph(a.graph)

    bad = []
    for label, want in manifest["firstLabels"].items():
        got = int(c.query(f"MATCH (n:{label}) RETURN count(n)").iloc[0, 0])
        if got != want:
            bad.append(f"{label}: {got} != {want}")
    for et, want in manifest["edgeTypes"].items():
        got = int(c.query(f"MATCH ()-[e:{et}]->() RETURN count(e)").iloc[0, 0])
        if got != want:
            bad.append(f"{et}: {got} != {want}")
    n = int(c.query("MATCH (n) RETURN count(n)").iloc[0, 0])
    e = int(c.query("MATCH ()-[e]->() RETURN count(e)").iloc[0, 0])
    print(f"{a.graph}: {n} nodes, {e} edges")
    if bad:
        sys.exit("count mismatch:\n  " + "\n  ".join(bad))
    print("counts match the manifest")


if __name__ == "__main__":
    main()
