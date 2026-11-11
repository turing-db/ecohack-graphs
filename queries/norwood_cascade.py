"""Follow an extinction cascade through the Norwood food web.

Remove one or more species, then repeat: every animal whose resources are now all gone
is lost too, which can starve its parasitoids in the next round, and so on.

    uv run python queries/norwood_cascade.py "Cirsium arvense"
    uv run python queries/norwood_cascade.py "Ranunculus repens" "Cirsium arvense"
    uv run python queries/norwood_cascade.py --rank      # rank every plant by the size of its cascade
"""
from __future__ import annotations

import os
import sys
import time

from turingdb import TuringDB, TuringDBException

# One round of the cascade: animals that still have resources listed but have lost all of them.
ROUND = """
MATCH (a:Animal)-[:DEPENDS_ON]->(r)
WHERE NOT a.name IN $removed
WITH a, count(r) AS total, sum(CASE WHEN r.name IN $removed THEN 1 ELSE 0 END) AS lost
WHERE lost = total
RETURN a.name AS name, a.guild AS guild
"""


def connect() -> TuringDB:
    c = TuringDB(host="localhost", port=int(os.environ.get("PORT", 6666)))
    try:
        c.load_graph("norwood")
    except TuringDBException:
        pass
    c.set_graph("norwood")
    return c


def cascade(c: TuringDB, start: list[str], verbose: bool = True) -> list[list[tuple[str, str]]]:
    removed, rounds = list(start), []
    while True:
        df = c.query(ROUND, {"removed": removed})
        if df.empty:
            return rounds
        lost = list(zip(df["name"], df["guild"]))
        rounds.append(lost)
        removed += [n for n, _ in lost]
        if verbose:
            print(f"\nround {len(rounds)}: {len(lost)} species lose all their resources")
            for n, gname in sorted(lost, key=lambda x: (x[1], x[0])):
                print(f"   {gname:<28} {n}")


def main() -> None:
    c = connect()
    if sys.argv[1:] == ["--rank"]:
        plants = c.query("MATCH (p:Plant) RETURN p.name AS name")["name"].tolist()
        t0 = time.perf_counter()
        sizes = sorted(((sum(len(r) for r in cascade(c, [p], verbose=False)), p) for p in plants), reverse=True)
        print(f"cascade size for each of {len(plants)} plants in {time.perf_counter() - t0:.1f}s\n")
        for n, p in sizes[:15]:
            print(f"   {n:>4}  {p}")
        return
    start = sys.argv[1:] or ["Cirsium arvense"]
    missing = [s for s in start if c.query("MATCH (t:Taxon {name: $n}) RETURN count(t) AS n", {"n": s})["n"][0] == 0]
    if missing:
        sys.exit(f"not in the graph: {missing}. Try: MATCH (p:Plant) RETURN p.name")
    print(f"removing: {', '.join(start)}")
    rounds = cascade(c, start)
    print(f"\ntotal secondary losses: {sum(len(r) for r in rounds)} species in {len(rounds)} rounds")


if __name__ == "__main__":
    main()
