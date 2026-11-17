"""river_foodweb: a literature-informed food web for a Welsh river (synthetic links).

Source: build/river_foodweb/spec.json, written for this repo from published diets, life cycles
and the BMWP family scores (every rule carries its source; see build/river_foodweb/SOURCES.md).

The species are real UK river species. The links are GENERATED from diet rules: a rule such as
"brown trout eats grazing, shredding and predatory invertebrates up to 30 mm in riffles, pools
and the drift" matches many taxa. Each consumer keeps every documented link plus its 7 most likely
rule matches, ranked by how often the prey's family turns up in real Welsh kick samples
(river_foodweb/family_frequency.json, from the welsh_rivers graph). So an edge means "this consumer
is known to eat this kind of prey, and this one is common", a potential feeding link, not an
observation. Edges carry evidence = 'documented' or 'rule'.

Model:
  (:Taxon:<Group>)-[:EATS {share, evidence, stage, source, hops}]->(:Taxon)   consumer -> food
  (:Parasite)-[:INFECTS {stage, step, source}]->(:Taxon)                       parasite -> host
  (:Taxon)-[:IN_FAMILY]->(:Family)-[:IN_ORDER]->(:Order)                        same names as welsh_rivers
  (:Taxon)-[:LIVES_IN]->(:Habitat)
Taxon.trophic_level is computed from the EATS edges (basal resources = 1).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from common import new_builder

SPEC = Path(__file__).resolve().parent / "river_foodweb" / "spec.json"
GROUP_LABEL = {"basal": "Basal", "invertebrate": "Invertebrate", "fish": "Fish", "bird": "Bird", "mammal": "Mammal",
               "amphibian": "Amphibian", "terrestrial": "Terrestrial", "parasite": "Parasite"}


def load_spec(path: Path = SPEC) -> dict:
    spec = json.loads(path.read_text())
    ids = {t["id"] for t in spec["taxa"]}
    bad = []
    for r in spec.get("diet_rules", []):
        if r["consumer"] not in ids:
            bad.append(f"diet consumer {r['consumer']}")
        bad += [f"{r['consumer']} eats {x}" for x in r.get("eats_ids", []) if x not in ids]
    for lc in spec.get("life_cycles", []):
        if lc["parasite"] not in ids:
            bad.append(f"parasite {lc['parasite']}")
        for h in lc["hosts"]:
            bad += [f"{lc['parasite']} host {x}" for x in h.get("host_ids", []) if x not in ids]
    if bad:
        raise SystemExit("spec refers to unknown taxa:\n  " + "\n  ".join(bad))
    return spec


def matches(t: dict, g: dict) -> bool:
    if g.get("group") and t["group"] != g["group"]:
        return False
    if g.get("feeding_groups") and t.get("feeding_group") not in g["feeding_groups"]:
        return False
    if g.get("habitats") and not set(t.get("habitats") or []) & set(g["habitats"]):
        return False
    if g.get("families") and t.get("family") not in g["families"]:
        return False
    if g.get("max_size_mm") is not None and (t.get("size_mm") or 0) > g["max_size_mm"]:
        return False
    if g.get("min_size_mm") is not None and (t.get("size_mm") or 0) < g["min_size_mm"]:
        return False
    if g.get("emerging_only") and not t.get("emerges"):
        return False
    return True


FREQ = Path(__file__).resolve().parent / "river_foodweb" / "family_frequency.json"
RULE_LINKS_PER_CONSUMER = 7


def prevalence(t: dict, freq: dict) -> float:
    """How common a prey taxon is: the share of real Welsh kick samples (KiEco) holding its family.
    Prey without a kick-sample family (fish, amphibians, basal resources) count as common."""
    if t["group"] != "invertebrate":
        return 0.5
    return freq["family_surveys"].get(t.get("family") or "", 0) / freq["surveys"]


def feeding_links(spec: dict, per_consumer: int = RULE_LINKS_PER_CONSUMER) -> dict[tuple[str, str], dict]:
    """Documented links are all kept. Rule links are capped per consumer, keeping the prey whose
    families are most frequent in the real Welsh kick samples: a generalist mostly eats what is common."""
    taxa = spec["taxa"]
    by_id = {t["id"]: t for t in taxa}
    freq = json.loads(FREQ.read_text()) if FREQ.exists() else {"surveys": 1, "family_surveys": {}}
    links: dict[tuple[str, str], dict] = {}
    candidates: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in spec.get("diet_rules", []):
        c = r["consumer"]
        for prey in r.get("eats_ids", []):
            # A consumer that eats emerging adults takes the flying stage of an insect that emerges.
            stage = "emerging adult" if r.get("eats_emerging_adults") and by_id[prey].get("emerges") and by_id[c]["group"] in ("bird", "mammal", "terrestrial") else "any"
            if r.get("prey_stage") and stage == "any" and by_id[prey]["group"] != "basal":
                stage = r["prey_stage"]  # e.g. crayfish take trout eggs and juveniles, pike take ducklings
            links[(c, prey)] = {"evidence": "documented", "stage": stage, "source": r.get("source", "")}
        for g in r.get("eats_groups", []):
            for t in taxa:
                if t["id"] != c and matches(t, g):
                    stage = "emerging adult" if g.get("emerging_only") else "any"
                    candidates[c].setdefault(t["id"], {"evidence": "rule", "stage": stage, "source": r.get("source", "")})
        if r.get("eats_emerging_adults"):
            for t in taxa:
                if t.get("emerges") and t["id"] != c:
                    candidates[c].setdefault(t["id"], {"evidence": "rule", "stage": "emerging adult", "source": r.get("source", "")})
    for c, cands in candidates.items():
        ranked = sorted((p for p in cands if (c, p) not in links), key=lambda p: (-prevalence(by_id[p], freq), by_id[p]["name"]))
        for p in ranked[:per_consumer]:
            links[(c, p)] = cands[p]
    # Share: each consumer's diet split evenly over its links, documented links counted double.
    by_consumer = defaultdict(list)
    for (c, p), e in links.items():
        by_consumer[c].append((p, e))
    for c, items in by_consumer.items():
        w = {p: (2.0 if e["evidence"] == "documented" else 1.0) for p, e in items}
        tot = sum(w.values())
        for p, e in items:
            e["share"] = round(w[p] / tot, 4)
    return links


def trophic_levels(spec: dict, links) -> dict[str, float]:
    prey = defaultdict(list)
    for (c, p) in links:
        prey[c].append(p)
    tl = {t["id"]: 1.0 for t in spec["taxa"]}
    for _ in range(50):
        new = {k: (1.0 + sum(tl[p] for p in prey[k]) / len(prey[k])) if prey[k] else 1.0 for k in tl}
        if max(abs(new[k] - tl[k]) for k in tl) < 1e-6:
            break
        tl = new
    return {k: round(v, 3) for k, v in tl.items()}


def build(spec_path: Path = SPEC):
    spec = load_spec(spec_path)
    g = new_builder(doubles={"share", "hops", "trophic_level", "size_mm"})
    links = feeding_links(spec)
    tl = trophic_levels(spec, links)

    family_done: set[str] = set()
    for t in spec["taxa"]:
        props = {"name": t["name"], "common_name": t.get("common_name") or t["name"], "group": t["group"],
                 "feeding_group": t.get("feeding_group") or "", "trophic_level": tl[t["id"]],
                 "native": bool(t.get("native", True)), "pollution_sensitive": bool(t.get("pollution_sensitive", False)),
                 "emerges": bool(t.get("emerges", False)), "habitats": ", ".join(t.get("habitats") or [])}
        if t.get("size_mm") is not None:
            props["size_mm"] = float(t["size_mm"])
        if t.get("bmwp_score") is not None:
            props["bmwp_score"] = int(t["bmwp_score"])
        if t.get("family"):
            props["family"] = t["family"]
        if t.get("order"):
            props["order"] = t["order"]
        g.node(f"t:{t['id']}", ["Taxon", GROUP_LABEL[t["group"]]], **props)
        if t.get("family"):
            g.upsert(f"f:{t['family']}", ["Family"], name=t["family"])
            g.link(f"t:{t['id']}", f"f:{t['family']}", "IN_FAMILY")
            if t.get("order") and t["family"] not in family_done:
                family_done.add(t["family"])
                g.upsert(f"o:{t['order']}", ["Order"], name=t["order"])
                g.link(f"f:{t['family']}", f"o:{t['order']}", "IN_ORDER")
        for h in t.get("habitats") or []:
            g.upsert(f"h:{h}", ["Habitat"], name=h)
            g.link(f"t:{t['id']}", f"h:{h}", "LIVES_IN")

    for (c, p), e in links.items():
        g.link(f"t:{c}", f"t:{p}", "EATS", share=e["share"], evidence=e["evidence"], stage=e["stage"], source=e["source"], hops=1.0)

    for lc in spec.get("life_cycles", []):
        for step, h in enumerate(lc["hosts"], start=1):
            for host in h.get("host_ids", []):
                g.link(f"t:{lc['parasite']}", f"t:{host}", "INFECTS", stage=h["stage"], step=step, source=lc.get("source", ""))
    return g


if __name__ == "__main__":
    import sys
    m = build().write(sys.argv[1] if len(sys.argv) > 1 else "river_foodweb.jsonl")
    print({k: m[k] for k in ("nodes", "relationships", "labels", "edgeTypes", "bytes")})
