# river_foodweb: a Welsh river food web, built from published diets

**Use it for:** long food chains (5 to 6 links), pollution reaching top predators, parasite life cycles, invasive species, joining to the welsh_rivers surveys.

**This graph is synthetic.** The species are real UK river species. The feeding links are generated from published diet rules, not observed in one river. Read every answer as "what the published diets imply". Use it to show how far multi-hop questions can go, or as a template for building your own graph from open sources.

## Why it exists

Norwood is a real survey, but its chains stop after 2 links: plant, then aphid or seed eater, then parasitoid. A river food web has deeper chains:
- leaf litter → freshwater shrimp → bullhead → brown trout → otter
- algae → mayfly larva → stonefly larva → bullhead → trout → kingfisher

Parasites add chains of their own. The eye fluke passes from a snail to a fish to the bird that eats the fish.

This graph uses the same invertebrate family names as the `welsh_rivers` graph. So you can take a real survey site that lost families and ask the food web which predators depended on them. See `queries/river_foodweb_join.py`.

## How it was built

1. **The spec.** `build/river_foodweb/spec.json` lists 245 taxa found in Welsh and UK rivers:

   | Group | Taxa |
   |---|---|
   | Invertebrates | 158 |
   | Fish | 22 |
   | Parasites | 19 |
   | Birds | 17 |
   | Basal resources | 10 |
   | Riparian spiders and beetles | 8 |
   | Mammals | 7 |
   | Amphibians | 4 |

   Each taxon has a family, order, feeding group, habitats, body size and BMWP pollution score.
2. **Diet rules.** There are 248 diet rules. A rule names documented prey, for example "dipper eats Baetis, Rhyacophila, Gammarus", or a kind of prey, for example "trout eats invertebrates up to 30 mm in riffles and the drift".
3. **Life cycles.** There are 20 parasite life cycles, each listing its hosts in order.
4. **Sources.** Every rule and life cycle cites a source. The 133 URLs are listed in `build/river_foodweb/SOURCES.md`.
5. **Building the links.** `build/build_river_foodweb.py` turns the rules into edges:
   - Every documented link is kept.
   - A rule that matches many taxa keeps only the consumer's 7 most likely prey. These are the prey whose families turn up most often in the 8,328 real Welsh kick samples in `welsh_rivers` (see `build/river_foodweb/family_frequency.json`).
   - `trophic_level` is computed from the links, with basal resources at level 1.

Each edge records whether it came from a documented link or a rule (`evidence`).

## Size

433 nodes and 2,294 edges, about the same size as Norwood. It loads in 0.2 s.

| Node | Count | What it is |
|---|---|---|
| `Taxon` | 245 | Every species or resource. Each one also has a group label: `Invertebrate`, `Fish`, `Bird`, `Mammal`, `Amphibian`, `Terrestrial`, `Parasite` or `Basal`. |
| `Family`, `Order` | 118, 60 | Taxonomy, with family names matching `welsh_rivers` |
| `Habitat` | 10 | riffle, pool, margin, macrophyte, hyporheic, water column, surface, riparian, air, drift |

## Schema

```
(:Taxon)-[:EATS {share, evidence, stage, source, hops}]->(:Taxon)    consumer -> food
(:Parasite)-[:INFECTS {stage, step, source}]->(:Taxon)                parasite -> host; step 1, 2, 3 = order in its life cycle
(:Taxon)-[:IN_FAMILY]->(:Family)-[:IN_ORDER]->(:Order)
(:Taxon)-[:LIVES_IN]->(:Habitat)
```

`EATS` points from the consumer to its food. To go down the chain from a predator, follow it forwards: `(:Taxon {name: 'Lutra lutra'})-[:EATS*1..5]->(r:Basal)`. To find who depends on a species, follow it backwards.

### Properties

| On | Property | Meaning |
|---|---|---|
| `Taxon` | `name`, `common_name` | Scientific and common name, for example `Cinclus cinclus`, `dipper` |
| | `group` | basal, invertebrate, fish, bird, mammal, amphibian, terrestrial, parasite |
| | `family`, `order` | Taxonomy |
| | `feeding_group` | For invertebrates: grazer, shredder, collector-gatherer, filterer, predator, piercer. For others: piscivore, invertivore, omnivore, herbivore. |
| | `trophic_level` | Computed from the links (basal = 1, the otter is about 4) |
| | `bmwp_score`, `pollution_sensitive` | BMWP family score. Families scoring 8 or 10 count as pollution sensitive. |
| | `emerges` | Insect with a flying adult stage that leaves the river |
| | `native`, `size_mm`, `habitats` | |
| `EATS` | `evidence` | `documented` (a named link in a source) or `rule` (generated from a diet rule) |
| | `stage` | `any`, `emerging adult` (bats and swallows take flying insects), or a life stage such as `eggs/juveniles` |
| | `share` | The consumer's diet split evenly over its links, with documented links counted double |
| | `hops` | Always 1.0, as a weight for `shortestPath` |
| `INFECTS` | `stage`, `step` | For example `first intermediate`, step 1 |

## Questions it answers

All of these are in [`queries/river_foodweb.cypher`](../queries/river_foodweb.cypher). Each runs in 1 to 2 ms.

1. **Five links deep.** Every food chain from the otter down to a basal resource in exactly 5 steps. There are hundreds of chains, mostly starting from algae and biofilm.
2. **One chain, written out.** Leaf litter → freshwater shrimp → bullhead → trout → otter. Every link is documented.
3. **Reach.** How many basal resources each bird and mammal depends on within 6 links.
4. **Pollution reaches the top.** Which pollution-sensitive invertebrates each predator depends on within 4 links.
5. **The dipper.** Its prey and their pollution scores. The dipper is the classic Welsh indicator of acid and polluted streams.
6. **Invasive signal crayfish.** What it eats, including trout and salmon eggs, and the crayfish plague it carries to the native white-clawed crayfish.
7. **A parasite's life cycle as a chain.** The eye fluke goes from snail to fish to the bird that eats that fish.
8. **Bottleneck hosts.** The species that the most parasite life cycles pass through. Brown trout and the freshwater shrimp come top.
9. **Pearl mussel.** Its larvae can only grow on trout or salmon gills, and those fish depend on pollution-sensitive prey.
10. **River energy on land.** Bats, spiders, sand martins and swallows that eat emerging river insects, traced back to the river's algae and leaf litter.

**Joining two graphs:** `uv run python queries/river_foodweb_join.py` takes the real survey site in `welsh_rivers` that lost the most families between before 2008 and after 2015. It then shows which predators in this food web ate those families, and how many of their 4-link food chains run through them.

Ideas to go further:
- Run an extinction cascade, as in `queries/norwood_cascade.py`, starting from a pollution event.
- Weight chains by `share` to estimate how much of the otter's food traces back to leaf litter rather than algae.
- Join to `welsh_rivers` chemistry to go from a sewage outfall, to the river reaches downstream, to the families lost there, to the predators that ate them.
- Replace the generated links with observed diets from an open source such as [GloBI](https://www.globalbioticinteractions.org/).

## Caveats

- **Synthetic links.** The links describe what published diets imply, not what was observed in one river. A rule-generated link means "this predator eats this kind of prey, and this prey is common in Welsh samples".
- **BMWP scores for split families.** Glossosomatidae, Hydraenidae, Bithyniidae, Tateidae, Dugesiidae, Crangonyctidae, Pediciidae, Limoniidae and Veliidae take their former parent family's score.
- **Feeding groups.** Many invertebrate feeding groups come from family or genus traits, not species studies.
- **Uncertain parasite details.** These are uncertain: the snail host of *Pseudamphistomum*, the bird hosts of *Schistocephalus*, *Cystidicola* in UK rivers, and the split of *Pomphorhynchus* into *P. tereticollis*.
- **Weaker sources.** A few bird diet sources are general references rather than papers.
- **Left out on purpose.** *Gyrodactylus salaris* (Great Britain is free of it) and grey seals. The full list is in the `exclusions` block of `spec.json`.
