"""Run the example queries for one graph and print each answer with its time.

    uv run python queries/run.py norwood            # all queries
    uv run python queries/run.py welsh_rivers 5 7   # only queries 5 and 7
    PORT=6700 uv run python queries/run.py woodland_soil
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
from turingdb import TuringDB, TuringDBException

HERE = Path(__file__).resolve().parent


def parse(path: Path) -> list[tuple[int, str, str]]:
    """-> [(number, title, cypher)] from blocks of '// N. title' comments + a query ending with ';'."""
    out = []
    for block in re.split(r";\s*\n", path.read_text()):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        notes = [l[2:].strip() for l in lines if l.lstrip().startswith("//")]
        cypher = "\n".join(l for l in lines if not l.lstrip().startswith("//")).strip().rstrip(";")
        if not cypher:
            continue
        m = re.match(r"(\d+)\.\s*(.*)", notes[0] if notes else "")
        num, title = (int(m.group(1)), m.group(2)) if m else (len(out) + 1, notes[0] if notes else "")
        out.append((num, " ".join([title] + notes[1:]), cypher))
    return out


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(f"usage: run.py <graph> [query numbers]\ngraphs: {', '.join(p.stem for p in HERE.glob('*.cypher'))}")
    graph, only = sys.argv[1], {int(x) for x in sys.argv[2:]}
    c = TuringDB(host="localhost", port=int(os.environ.get("PORT", 6666)))
    try:
        c.load_graph(graph)
    except TuringDBException:
        pass
    c.set_graph(graph)
    pd.set_option("display.width", 160)
    pd.set_option("display.max_colwidth", 60)
    for num, title, q in parse(HERE / f"{graph}.cypher"):
        if only and num not in only:
            continue
        t0 = time.perf_counter()
        df = c.query(q)
        ms = (time.perf_counter() - t0) * 1000
        print(f"\n{num}. {title}\n   {len(df)} rows in {ms:.0f} ms")
        print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
