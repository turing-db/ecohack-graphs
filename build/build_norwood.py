"""norwood: the Norwood Farm network of ecological networks (Pocock, Evans & Memmott 2012).

Source: Dryad doi:10.5061/dryad.3s36r118, norwood.csv (CC0). One row per interaction:
  lower guild, upper guild, lower taxon, upper taxon,
  estimated interaction strength (farm scale, one year), direct interaction (yes/no)

Direct rows become DEPENDS_ON edges pointing from the consumer (upper) to its resource
(lower). Indirect rows ("parasitoid A was reared from an aphid on plant B") become
VIA_PLANT edges so the plant a parasitoid chain starts from is never lost.
"""
from __future__ import annotations

import glob
from collections import Counter, defaultdict

from common import SOURCES, new_builder, num, read_csv

SRC = SOURCES / "norwood"


def interaction(upper_guild: str, lower_guild: str) -> str:
    u, l = upper_guild.lower(), lower_guild.lower()
    if "parasit" in u:
        return "parasitises leaf miners on" if l == "plant" else "parasitises"
    if "flower" in u or "pollinat" in u or "butterfl" in u:
        return "visits flowers of"
    if "seed" in u or "granivor" in u:
        return "eats seeds of"
    if "leaf" in u and "min" in u:
        return "mines leaves of"
    if "aphid" in u:
        return "feeds on"
    return "feeds on"


def col(row: dict, *names: str) -> str:
    for n in names:
        for k in row:
            if k.strip().lower() == n:
                return (row[k] or "").strip()
    raise KeyError(names)


def build():
    path = (glob.glob(str(SRC / "norwood.csv")) + glob.glob(str(SRC / "*.csv")))[0]
    rows = read_csv(path, encoding="cp1252")
    g = new_builder(doubles={"strength", "hops"})

    guilds_as_upper = {col(r, "upper guild") for r in rows}
    guild_of = defaultdict(Counter)
    for r in rows:
        guild_of[col(r, "lower taxon")][col(r, "lower guild")] += 1
        guild_of[col(r, "upper taxon")][col(r, "upper guild")] += 1

    for taxon, gs in guild_of.items():
        guild = gs.most_common(1)[0][0]
        is_plant = all(gn not in guilds_as_upper for gn in gs)
        g.node(f"t:{taxon}", ["Taxon", "Plant" if is_plant else "Animal"], name=taxon, guild=guild,
               kind="plant" if is_plant else "animal", identified_to=_rank(taxon))
        for gn in gs:
            g.upsert(f"g:{gn}", ["Guild"], name=gn)
            g.link(f"t:{taxon}", f"g:{gn}", "IN_GUILD")

    edges = {}
    for r in rows:
        lo, up = col(r, "lower taxon"), col(r, "upper taxon")
        lg, ug = col(r, "lower guild"), col(r, "upper guild")
        s = num(col(r, "estimated interaction strength"))
        direct = col(r, "direct interaction").lower() in ("yes", "y", "true", "1", "direct")
        et = "DEPENDS_ON" if direct else "VIA_PLANT"
        e = edges.setdefault((up, lo, et), {"hops": 1.0, "network": f"{lg} - {ug}", "strength": 0.0, "rows": 0})
        e["strength"] += s or 0.0
        e["rows"] += 1
        if direct:
            e["interaction"] = interaction(ug, lg)
    for (up, lo, et), props in edges.items():
        props["strength"] = round(props["strength"], 6)
        g.link(f"t:{up}", f"t:{lo}", et, **props)
    return g


def _rank(name: str) -> str:
    n = name.strip()
    if " sp." in n or n.endswith(" sp") or " spp" in n or "agg" in n:
        return "genus or aggregate"
    parts = n.split()
    if len(parts) >= 2 and parts[1][:1].islower():
        return "species"
    return "higher taxon or morphospecies"


if __name__ == "__main__":
    import sys
    m = build().write(sys.argv[1] if len(sys.argv) > 1 else "norwood.jsonl")
    print({k: m[k] for k in ("nodes", "relationships", "labels", "edgeTypes", "bytes")})
