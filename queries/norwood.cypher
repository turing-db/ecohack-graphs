// 1. What is in the food web?
// Number of taxa in each guild (plants, flower visitors, aphids, parasitoids, birds...).
MATCH (t:Taxon)-[:IN_GUILD]->(g:Guild)
RETURN g.name AS guild, count(t) AS taxa
ORDER BY taxa DESC;

// 2. Which plants feed the most animals directly? (1 hop)
MATCH (p:Plant)<-[:DEPENDS_ON]-(a:Animal)
RETURN p.name AS plant, count(a) AS direct_dependents
ORDER BY direct_dependents DESC
LIMIT 10;

// 3. Keystone ranking: which plants hold up the most species within 3 hops?
// Counts plant users plus everything that depends on them (parasitoids of aphids, seed feeders...).
// Compare with query 2: some plants climb once indirect dependents are counted.
MATCH (p:Plant)<-[:DEPENDS_ON*1..3]-(a:Animal)
RETURN p.name AS plant, count(DISTINCT a) AS species_supported
ORDER BY species_supported DESC
LIMIT 10;

// 4. Single points of failure: which resources are the ONLY food or host of some species?
MATCH (a:Animal)-[:DEPENDS_ON]->(r)
WITH a, collect(r) AS resources
WHERE size(resources) = 1
UNWIND resources AS r
RETURN r.name AS sole_resource, r.kind AS kind, count(a) AS specialists
ORDER BY specialists DESC
LIMIT 10;

// 5. Extinction cascade, first step: if creeping thistle disappears, who loses all their food?
// (Run queries/norwood_cascade.py to follow the cascade to the end.)
MATCH (a:Animal)-[:DEPENDS_ON]->(:Plant {name: "Cirsium arvense"})
WHERE NOT EXISTS { MATCH (a)-[:DEPENDS_ON]->(other) WHERE other.name <> "Cirsium arvense" }
RETURN a.name AS loses_everything, a.guild AS guild
ORDER BY guild;

// 6. Competition: which pairs of flower visitors share the most plants?
MATCH (a:Animal)-[:DEPENDS_ON]->(p:Plant)<-[:DEPENDS_ON]-(b:Animal)
WHERE a.guild = "flower visitor" AND b.guild = "flower visitor" AND a.name < b.name
RETURN a.name AS visitor_1, b.name AS visitor_2, count(p) AS shared_plants
ORDER BY shared_plants DESC
LIMIT 10;

// 7. Which plants connect seed-eating birds to aphid parasitoids?
// Birds eat the seeds; parasitoids were reared from aphids living on the same plant.
MATCH (b:Animal {guild: "seed-feeding bird"})-[:DEPENDS_ON]->(p:Plant)<-[:VIA_PLANT]-(x:Animal)
WHERE x.guild = "primary aphid parasitoid" OR x.guild = "secondary aphid parasitoid"
RETURN p.name AS plant, count(DISTINCT b) AS birds, count(DISTINCT x) AS parasitoids
ORDER BY birds + parasitoids DESC
LIMIT 10;

// 8. How far apart are two species in the web? (any direction, up to 4 hops)
MATCH path = (a:Animal {name: "Emberiza citrinella"})-[*1..4]-(b:Animal {name: "Asaphes suspensus"})
RETURN length(path) AS hops, [n IN nodes(path) | n.name] AS via
ORDER BY hops
LIMIT 3;

// 9. Which guilds are most specialised? (average number of resources per animal)
MATCH (a:Animal)-[:DEPENDS_ON]->(r)
WITH a, count(r) AS resources
RETURN a.guild AS guild, count(a) AS animals, avg(resources) AS mean_resources
ORDER BY mean_resources;

// 10. Restoration: plants that support the most specialists (planting them helps species with no alternative).
MATCH (a:Animal)-[:DEPENDS_ON]->(r)
WITH a, collect(r) AS rs
WHERE size(rs) <= 2
UNWIND rs AS p
WITH a, p
WHERE p:Plant
RETURN p.name AS plant, count(DISTINCT a) AS specialists_supported
ORDER BY specialists_supported DESC
LIMIT 10;
