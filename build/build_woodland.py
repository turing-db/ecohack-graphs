"""woodland_soil: WrEN woodland restoration sites (UKCEH/EIDC).

Joins four EIDC releases on Site_code:
  - insects, plants and trees  (8c997943-1f90-4897-87b3-491eaef534ec)
  - soil microbial diversity   (a21b8ed1-124b-4b2a-adb4-c3fdcc9f95a2)
  - acoustic indices           (33b6dea2-94c5-4e33-a1b3-9a6bd1c8776a)
"""
from __future__ import annotations

from collections import Counter, defaultdict

from common import SOURCES, bng_to_latlon, clean, mean, new_builder, num, read_csv

FLORA = SOURCES / "eidc_flora" / "data"
SOIL = SOURCES / "eidc_soil" / "data"
ACOUSTIC = SOURCES / "eidc_acoustic" / "data" / "acoustic_indices.csv"

# tray-beating records name the tree in English; trees.csv uses genus
TREE_GENUS = {
    "oak": "Quercus", "hawthorn": "Crataegus", "birch": "Betula", "willow": "Salix", "alder": "Alnus",
    "grey alder": "Alnus", "sycamore": "Acer", "field maple": "Acer", "norway maple": "Acer",
    "hazel": "Corylus", "ash": "Fraxinus", "elder": "Sambucus", "rowan": "Sorbus", "whitebeam": "Sorbus",
    "cherry": "Prunus", "blackthorn": "Prunus", "beech": "Fagus", "lime": "Tilia", "elm": "Ulmus",
    "hornbeam": "Carpinus", "holly": "Ilex", "apple": "Malus", "spindle": "Euonymus", "poplar": "Populus",
    "aspen": "Populus", "dogwood": "Cornus", "sweet chestnut": "Castanea",
}
LAND_USE = {"Agriculture": "Agricultural", "Agricultural": "Agricultural", "Industrial": "Industrial",
            "Ancient": "Ancient woodland", "Rewilding": "Rewilding"}
ACOUSTIC_INDICES = ["ACIsw", "ADI", "AEve", "Bio", "H", "NDSI"]


def invert_taxon(r: dict) -> tuple[str, str] | None:
    """Finest name available -> (name, rank)."""
    genus, species = clean(r.get("Genus")), clean(r.get("Species"))
    if genus and species:
        return f"{genus} {species}", "species"
    for col, rank in (("Genus", "genus"), ("Subfamily", "subfamily"), ("Family", "family"), ("Order", "order")):
        if clean(r.get(col)):
            return clean(r[col]), rank
    return None


def build():
    g = new_builder(doubles={
        "age_years", "survey_area_ha", "contiguous_area_ha", "proximity_index", "canopy_cover_pct",
        "deadwood_volume_m3", "mean_dbh_cm", "soil_pH", "soil_conductivity_us",
        "bact_shannon", "bact_faith_pd", "bact_pathway_shannon", "bact_pathway_chao1",
        "fungal_shannon", "fungal_guild_chao1", "easting", "northing",
        *[f"broadleaf_{d}m" for d in (100, 250, 500, 1000, 1500, 2000, 2500, 3000)],
        *[f"sound_{i}" for i in ACOUSTIC_INDICES],
    })

    # ---- sites ------------------------------------------------------------
    sites = {r["Site_code"]: r for r in read_csv(FLORA / "woodland_site_details.csv")}
    soil_chem = {r["site"]: r for r in read_csv(SOIL / "woodland_soil_data_biochemical.csv")}

    # soil samples: bacterial and fungal files list 5 samples per site in the same order
    def by_site(rows):
        d = defaultdict(list)
        for r in rows:
            d[r["Site_code"]].append(r)
        return d
    b_alpha = by_site(read_csv(SOIL / "woodland_bacterial_data_alpha.csv"))
    b_func = by_site(read_csv(SOIL / "woodland_bacterial_data_functional.csv"))
    f_alpha = by_site(read_csv(SOIL / "woodlands_fungi_data_alpha.csv"))
    f_func = by_site(read_csv(SOIL / "woodlands_fungi_data_functional.csv"))

    # acoustic: one row per 10-minute recording; summarise per site
    ac = defaultdict(lambda: defaultdict(list))
    ac_meta = {}
    for r in read_csv(ACOUSTIC):
        s = r["Site_Code"]
        ac_meta[s] = r
        for i in ACOUSTIC_INDICES:
            ac[s][i].append(num(r.get(i)))
        ac[s]["_n"].append(1)

    regions, uses = set(), set()
    for code in sorted(set(sites) | set(ac_meta)):
        s, chem, m = sites.get(code), soil_chem.get(code, {}), ac_meta.get(code)
        props = {"name": code, "code": code}
        if s:
            habitat = {"Ancient": "Ancient woodland", "Rewilding": "Rewilding"}.get(s["Former_land_use"], "Woodland restoration")
            e, n = num(s["X"]), num(s["Y"])
            lat, lon = bng_to_latlon(e, n)
            props.update(
                habitat=habitat, region=s["Region"], former_land_use=LAND_USE[s["Former_land_use"]],
                age_years=num(s["Site_age"]), survey_area_ha=num(s["Survey_area"]),
                contiguous_area_ha=num(s["Contiguous_area"]), proximity_index=num(s["Proximity_index_1km"]),
                easting=e, northing=n, lat=lat, lon=lon, survey_year=2021 if code.startswith("SW") else 2022,
                **{f"broadleaf_{d}m": num(s[f"BL_{d}"]) for d in (100, 250, 500, 1000, 1500, 2000, 2500, 3000)},
            )
        else:  # acoustic-only grassland sites
            habitat = {"Grassland": "Grassland"}.get(m["Habitat"], m["Habitat"])
            props.update(habitat=habitat, region=m["Country"],
                         former_land_use=LAND_USE.get(m["FLU"], m["FLU"]))
        if chem:
            props.update(soil_pH=num(chem.get("pH")), soil_conductivity_us=num(chem.get("conductivity_uS")))
        if code in b_alpha:
            props.update(
                bact_shannon=mean(num(r["shannon_entropy"]) for r in b_alpha[code]),
                bact_faith_pd=mean(num(r["faith_pd"]) for r in b_alpha[code]),
                bact_pathway_shannon=mean(num(r["picrust_shannon"]) for r in b_func[code]),
                bact_pathway_chao1=mean(num(r["picrust _chao1"]) for r in b_func[code]),
                fungal_shannon=mean(num(r["shannon_entropy"]) for r in f_alpha[code]),
                fungal_guild_chao1=mean(num(r["funguild_chao1"]) for r in f_func[code]),
            )
        if code in ac:
            props["sound_recordings"] = len(ac[code]["_n"])
            props.update({f"sound_{i}": mean(ac[code][i]) for i in ACOUSTIC_INDICES})
        g.node(f"site:{code}", ["Site"], **props)
        g.upsert(f"region:{props['region']}", ["Region"], name=props["region"])
        g.link(f"site:{code}", f"region:{props['region']}", "IN_REGION")
        g.upsert(f"use:{props['former_land_use']}", ["LandUse"], name=props["former_land_use"])
        g.link(f"site:{code}", f"use:{props['former_land_use']}", "FORMER_LAND_USE")
        g.upsert(f"habitat:{props['habitat']}", ["Habitat"], name=props["habitat"])
        g.link(f"site:{code}", f"habitat:{props['habitat']}", "HABITAT")

        # soil samples (5 per site)
        for i, (ba, bf, fa) in enumerate(zip(b_alpha.get(code, []), b_func.get(code, []), f_alpha.get(code, [])), 1):
            ff = f_func[code][i - 1] if i - 1 < len(f_func.get(code, [])) else {}
            sk = f"soil:{code}:{i}"
            g.node(sk, ["SoilSample"], name=f"{code} soil {i}", site=code, bacterial_id=ba["id"], fungal_id=fa["id"],
                   date=ba.get("date"), bact_shannon=num(ba["shannon_entropy"]), bact_faith_pd=num(ba["faith_pd"]),
                   bact_pathway_shannon=num(bf["picrust_shannon"]), bact_pathway_chao1=num(bf["picrust _chao1"]),
                   fungal_shannon=num(fa["shannon_entropy"]), fungal_guild_chao1=num(ff.get("funguild_chao1")))
            g.link(f"site:{code}", sk, "HAS_SOIL_SAMPLE")

    # ---- plots: canopy, deadwood ---------------------------------------
    def plot(site, p):
        k = f"plot:{site}:{p}"
        if k not in plots:
            plots[k] = {"name": f"{site} {p}", "site": site, "plot": p}
        return k
    plots: dict[str, dict] = {}
    cc = defaultdict(list)
    for r in read_csv(FLORA / "canopy_cover.csv"):
        k = plot(r["Site_code"], r["Plot_no"])
        cc[k] += [num(r["CC_centre"]), num(r["CC_Q1"]), num(r["CC_Q2"])]
    dw = defaultdict(lambda: [0, 0.0])
    for r in read_csv(FLORA / "deadwood.csv"):
        k = plot(r["Site_code"], r["Plot_no"])
        dw[k][0] += 1
        dw[k][1] += (num(r["Volume_cm3"]) or 0) / 1e6

    flora = defaultdict(set)
    for r in read_csv(FLORA / "ground_flora.csv"):
        sp = clean(r["Species_name"])
        if sp:
            flora[(plot(r["Site_code"], r["Plot_no"]), sp)].add(r["Quadrat_no"])

    trees = defaultdict(list)
    for r in read_csv(FLORA / "trees.csv"):
        gen = clean(r["Genus"])
        if gen:
            trees[(plot(r["Site_code"], r["Plot_no"]), gen.capitalize())].append((r["Tree_code"], num(r["DBH"])))
    regen = defaultdict(lambda: {"seedlings": 0, "saplings": 0})
    for r in read_csv(FLORA / "tree_seedlings_saplings.csv"):
        gen = clean(r["Genus"])
        if gen and r["Size_class"].lower() in ("seedlings", "saplings"):
            regen[(plot(r["Site_code"], r["Plot_no"]), gen.capitalize())][r["Size_class"].lower()] += int(num(r["N"]) or 0)

    for (pk, sp), quads in flora.items():
        g.upsert(f"plant:{sp}", ["Plant"], name=sp)
        g.link(pk, f"plant:{sp}", "HAS_PLANT", quadrats=len(quads))

    common_names = defaultdict(set)
    for en, gen in TREE_GENUS.items():
        common_names[gen].add(en)
    def tree(gen):
        g.upsert(f"tree:{gen}", ["TreeGenus"], name=gen, common_names=", ".join(sorted(common_names.get(gen, []))))
        return f"tree:{gen}"
    for (pk, gen), stems in trees.items():
        dbhs = [d for _, d in stems]
        g.link(pk, tree(gen), "HAS_TREE", trees=len({c for c, _ in stems}), stems=len(stems), mean_dbh_cm=mean(dbhs))
    for (pk, gen), n in regen.items():
        g.link(pk, tree(gen), "REGENERATING", **n)

    # ---- invertebrates ------------------------------------------------
    def taxon(r):
        t = invert_taxon(r)
        if not t:
            return None
        name, rank = t
        order, family = clean(r.get("Order")), clean(r.get("Family"))
        k = f"inv:{name}"
        if g.has(k):
            return k
        g.node(k, ["Invertebrate"], name=name, rank=rank, order=order, family=family)
        if family and rank != "family":
            fk = f"family:{family}"
            if not g.has(fk):
                g.node(fk, ["Family"], name=family, order=order)
                if order:
                    g.upsert(f"order:{order}", ["Order"], name=order)
                    g.link(fk, f"order:{order}", "IN_ORDER")
            g.link(k, fk, "IN_FAMILY")
        elif order and rank != "order":
            g.upsert(f"order:{order}", ["Order"], name=order)
            g.link(k, f"order:{order}", "IN_ORDER")
        return k

    malaise = Counter()
    for r in read_csv(FLORA / "invertebrates_malaise_trap.csv"):
        k = taxon(r)
        if k:
            malaise[(f"site:{r['Site_code']}", k)] += 1
    for (sk, k), n in malaise.items():
        g.link(sk, k, "TRAPPED", individuals=n, method="malaise")

    beat, on_tree, on_tree_sites = Counter(), Counter(), defaultdict(set)
    for r in read_csv(FLORA / "invertebrates_tray_beating.csv"):
        k = taxon(r)
        if not k:
            continue
        pk = plot(r["Site_code"], r["Plot_no"])
        beat[(pk, k)] += 1
        gen = TREE_GENUS.get(r["Tree_species"].strip().lower())
        if gen:
            on_tree[(k, gen)] += 1
            on_tree_sites[(k, gen)].add(r["Site_code"])
    for (pk, k), n in beat.items():
        g.link(pk, k, "BEATEN_FROM_TREES", individuals=n)
    for (k, gen), n in on_tree.items():
        g.link(k, tree(gen), "FOUND_ON", individuals=n, sites=len(on_tree_sites[(k, gen)]))

    # plot nodes last: every table above may add a plot
    for k, p in plots.items():
        g.node(k, ["Plot"], **p, canopy_cover_pct=mean(cc.get(k, [])),
               deadwood_pieces=dw[k][0] if k in dw else 0,
               deadwood_volume_m3=round(dw[k][1], 4) if k in dw else 0.0)
        g.link(f"site:{p['site']}", k, "HAS_PLOT")
    return g


if __name__ == "__main__":
    import sys
    m = build().write(sys.argv[1] if len(sys.argv) > 1 else "woodland_soil.jsonl")
    print(m)
