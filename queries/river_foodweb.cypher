// 1. Five links deep: every food chain from the otter down to a basal resource in exactly 5 steps
MATCH (o:Taxon {name: 'Lutra lutra'})-[:EATS]->(a:Taxon)-[:EATS]->(b:Taxon)-[:EATS]->(c:Taxon)-[:EATS]->(d:Taxon)-[:EATS]->(r:Basal)
RETURN r.name AS basal_resource, count(*) AS chains, count(DISTINCT a) AS otter_prey_used
ORDER BY chains DESC;

// 2. One chain written out: leaf litter to the otter through the freshwater shrimp, bullhead and trout
MATCH (o:Taxon {name: 'Lutra lutra'})-[e1:EATS]->(t:Taxon {name: 'Salmo trutta'})-[e2:EATS]->(b:Taxon {name: 'Cottus gobio'})-[e3:EATS]->(g:Taxon {name: 'Gammarus pulex'})-[e4:EATS]->(r:Basal)
RETURN o.common_name AS top, t.common_name AS eats, b.common_name AS which_eats, g.common_name AS which_eats_2, r.name AS which_eats_3, e1.evidence AS link_1, e4.evidence AS link_4;

// 3. How deep does each top predator reach? Basal resources reached within 1 to 6 links
MATCH (top:Taxon)-[:EATS*1..6]->(r:Basal)
WHERE top:Bird OR top:Mammal
RETURN top.common_name AS predator, top.trophic_level AS trophic_level, count(DISTINCT r) AS basal_resources_reached
ORDER BY trophic_level DESC LIMIT 12;

// 4. Pollution reaches the top: pollution-sensitive invertebrates each bird and mammal depends on, within 4 links
MATCH (top:Taxon)-[:EATS*1..4]->(s:Invertebrate {pollution_sensitive: true})
WHERE top:Bird OR top:Mammal
RETURN top.common_name AS predator, count(DISTINCT s) AS sensitive_prey_within_4_links, count(DISTINCT s.family) AS sensitive_families
ORDER BY sensitive_prey_within_4_links DESC LIMIT 12;

// 5. The dipper and acid or polluted water: its prey, and which of those are sensitive families
MATCH (d:Bird {name: 'Cinclus cinclus'})-[e:EATS]->(p:Invertebrate)
RETURN p.name AS prey, p.family AS family, p.bmwp_score AS bmwp, p.pollution_sensitive AS sensitive, e.evidence AS evidence
ORDER BY bmwp DESC;

// 6. Invasive signal crayfish: what it eats, and who it spreads crayfish plague to
MATCH (s:Taxon {name: 'Pacifastacus leniusculus'})-[:EATS]->(food:Taxon)
OPTIONAL MATCH (plague:Parasite {name: 'Aphanomyces astaci'})-[i:INFECTS]->(host:Taxon)
RETURN collect(DISTINCT food.common_name) AS signal_crayfish_eats, collect(DISTINCT host.common_name + ' (' + i.stage + ')') AS plague_hosts;

// 7. A parasite's life cycle as a chain: eye fluke from snail to fish to bird, where the bird eats that fish
MATCH (p:Parasite {name: 'Diplostomum spathaceum'})-[:INFECTS {step: 1}]->(h1:Taxon),
      (p)-[:INFECTS {step: 2}]->(h2:Taxon),
      (p)-[:INFECTS {step: 3}]->(h3:Taxon)-[:EATS]->(h2)
RETURN h1.common_name AS first_host, h2.common_name AS second_host, h3.common_name AS final_host_eats_it
ORDER BY final_host_eats_it;

// 8. Bottleneck hosts: which species do the most parasite life cycles pass through?
MATCH (p:Parasite)-[:INFECTS]->(h:Taxon)
RETURN h.common_name AS host, h.group AS grp, count(DISTINCT p) AS parasite_life_cycles
ORDER BY parasite_life_cycles DESC LIMIT 10;

// 9. Freshwater pearl mussel: its larvae need trout or salmon gills, and those fish need sensitive invertebrates
MATCH (m:Taxon {name: 'Margaritifera margaritifera'})-[:INFECTS]->(fish:Fish)-[:EATS]->(s:Invertebrate {pollution_sensitive: true})-[:EATS]->(r:Basal)
RETURN fish.common_name AS host_fish, count(DISTINCT s) AS sensitive_prey, collect(DISTINCT r.name) AS fed_by;

// 10. River energy on land: land animals eating emerging insects, traced back to the river's basal resources
MATCH (land:Taxon)-[e:EATS {stage: 'emerging adult'}]->(i:Invertebrate)-[:EATS*1..2]->(r:Basal)
WHERE land:Bird OR land:Mammal OR land:Terrestrial
RETURN land.common_name AS land_animal, land.group AS grp, count(DISTINCT i) AS river_insects, count(DISTINCT r) AS river_resources
ORDER BY river_insects DESC LIMIT 12;
