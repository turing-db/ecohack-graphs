"""Make every graph in graphs/*.jsonl.gz available on the running TuringDB server.

First run: each graph is imported from its JSONL file (seconds).
Later runs: the graphs are already on disk in .turing/ and are just loaded into memory.

    uv run python scripts/load_graphs.py [--port 6666] [graph ...]
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import sys
import time
from pathlib import Path

from turingdb import TuringDB, TuringDBException

ROOT = Path(__file__).resolve().parent.parent
GRAPHS = ROOT / "graphs"


def connect(port: int, wait: float = 60) -> TuringDB:
    t0 = time.time()
    while True:
        try:
            c = TuringDB(host="localhost", port=port)
            c.list_loaded_graphs()
            return c
        except Exception:
            if time.time() - t0 > wait:
                sys.exit(f"No TuringDB server on port {port}. Start it with ./start.sh")
            time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("graphs", nargs="*", help="default: every graphs/*.jsonl.gz")
    ap.add_argument("--port", type=int, default=6666)
    ap.add_argument("--turing-dir", type=Path, default=ROOT / ".turing")
    a = ap.parse_args()

    files = {p.name.removesuffix(".jsonl.gz"): p for p in sorted(GRAPHS.glob("*.jsonl.gz"))}
    names = a.graphs or list(files)
    c = connect(a.port)
    available = set(c.list_available_graphs())
    data = a.turing_dir / "data"
    data.mkdir(parents=True, exist_ok=True)

    for name in names:
        if name not in files:
            sys.exit(f"unknown graph {name!r}; choose from {', '.join(files)}")
        t0 = time.time()
        if name not in available:
            staged = data / f"{name}.jsonl"
            with gzip.open(files[name], "rb") as src, open(staged, "wb") as dst:
                shutil.copyfileobj(src, dst)
            c.query(f"LOAD JSONL '{staged.name}' AS {name}")
            staged.unlink()
            how = "imported"
        else:
            how = "loaded"
        try:
            c.load_graph(name)
        except TuringDBException:
            pass  # already in memory
        c.set_graph(name)
        n = int(c.query("MATCH (n) RETURN count(n)").iloc[0, 0])
        e = int(c.query("MATCH ()-[e]->() RETURN count(e)").iloc[0, 0])
        print(f"  {name:<14} {how} in {time.time() - t0:5.1f}s  {n:>9,} nodes {e:>10,} edges")


if __name__ == "__main__":
    main()
