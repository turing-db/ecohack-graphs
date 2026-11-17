"""Join two graphs: real river surveys (welsh_rivers) -> the river food web (river_foodweb).

The two graphs share invertebrate family names. This script takes a real survey site, finds the
families it lost between before 2008 and after 2015, and asks the food web which predators depend on
those families and through how many food chains (up to 4 links).

    uv run python queries/river_foodweb_join.py              # the site that lost the most families
    uv run python queries/river_foodweb_join.py "<site name>"

The food web links are synthetic (generated from published diets), so the impact is "what the
published diets imply", not a measured decline.
"""
from __future__ import annotations

import os
import sys
import time

from turingdb import TuringDB

LOST = """
MATCH (s:InvertSite)-[:SURVEYED]->(v:InvertSurvey)-[:FOUND]->(t:Taxon)
WHERE t.family <> ''
RETURN DISTINCT s.name AS site, v.year AS year, t.family AS family
"""

IMPACT = """
MATCH (top:Taxon)-[:EATS*1..4]->(x:Invertebrate)
WHERE top:Bird OR top:Mammal OR top:Fish
WITH top, count(*) AS chains, sum(CASE WHEN x.family IN $lost THEN 1 ELSE 0 END) AS through_lost
WHERE through_lost > 0
RETURN top.common_name AS predator, top.group AS grp, chains, through_lost
ORDER BY through_lost DESC
LIMIT 12
"""

DIRECT = """
MATCH (c:Taxon)-[:EATS]->(x:Invertebrate)
WHERE x.family IN $lost AND (c:Bird OR c:Mammal OR c:Fish)
RETURN c.common_name AS consumer, collect(DISTINCT x.family) AS lost_families_it_eats
ORDER BY size(lost_families_it_eats) DESC
LIMIT 10
"""


def use(c: TuringDB, graph: str) -> None:
    """Switch to a graph, loading it first if the server has it on disk but not in memory."""
    if graph not in c.list_loaded_graphs():
        c.load_graph(graph)
    c.set_graph(graph)


def main() -> None:
    c = TuringDB(host="localhost", port=int(os.environ.get("PORT", 6666)))
    use(c, "welsh_rivers")
    t = time.time()
    rows = c.query(LOST)
    # Families recorded before 2008 that were not recorded in 2015 or later. Only sites with a real
    # survey in both periods count (at least 5 families after 2015), so an empty survey is not a loss.
    by_site = {}
    for site, g in rows.groupby("site"):
        before, after = set(g[g.year < 2008].family), set(g[g.year >= 2015].family)
        if len(before) >= 5 and len(after) >= 5:
            by_site[site] = (sorted(before - after), len(before))
    if len(sys.argv) > 1:
        if sys.argv[1] not in by_site:
            sys.exit(f"no site called {sys.argv[1]!r} with surveys before 2008 and after 2015")
        site = sys.argv[1]
    else:
        site = max(by_site, key=lambda k: len(by_site[k][0]))
    lost, n_before = by_site[site]
    print(f"welsh_rivers ({(time.time() - t) * 1000:.0f} ms): {site} lost {len(lost)} of {n_before} families after 2015:")
    print("   " + ", ".join(lost) + "\n")

    use(c, "river_foodweb")
    t = time.time()
    direct = c.query(DIRECT, {"lost": lost})
    impact = c.query(IMPACT, {"lost": lost})
    print(f"river_foodweb ({(time.time() - t) * 1000:.0f} ms): who eats those families directly")
    print(direct.to_string(index=False) if len(direct) else "   nobody")
    print("\nfood chains up to 4 links that run through a lost family")
    print(impact.to_string(index=False) if len(impact) else "   none")


if __name__ == "__main__":
    main()
