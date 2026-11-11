// 1. What is in the graph?
MATCH (s:Site)-[:HABITAT]->(h:Habitat)
RETURN h.name AS habitat, count(s) AS sites
ORDER BY sites DESC;

// 2. Soil legacy: does former land use still show in the soil microbes?
MATCH (s:Site)-[:FORMER_LAND_USE]->(u:LandUse)
WHERE s.fungal_shannon IS NOT NULL
RETURN u.name AS former_land_use, count(s) AS sites,
       avg(s.fungal_shannon) AS fungal_diversity, avg(s.bact_shannon) AS bacterial_diversity,
       avg(s.soil_pH) AS soil_pH
ORDER BY fungal_diversity DESC;

// 3. Land use -> site -> plot -> plant: plant richness by former land use (4 hops)
MATCH (u:LandUse)<-[:FORMER_LAND_USE]-(s:Site)-[:HAS_PLOT]->(:Plot)-[:HAS_PLANT]->(p:Plant)
WITH u, s, count(DISTINCT p) AS plants
RETURN u.name AS former_land_use, count(s) AS sites, avg(plants) AS plant_species_per_site
ORDER BY plant_species_per_site DESC;

// 4. Which young woods already look most like ancient woodland? (shared ground-flora species, 6 hops)
MATCH (a:Site {habitat: "Ancient woodland"})-[:HAS_PLOT]->(:Plot)-[:HAS_PLANT]->(p:Plant)
      <-[:HAS_PLANT]-(:Plot)<-[:HAS_PLOT]-(s:Site {habitat: "Woodland restoration"})
RETURN s.code AS site, s.region AS region, s.former_land_use AS former_land_use, s.age_years AS age,
       count(DISTINCT p) AS plants_shared_with_ancient
ORDER BY plants_shared_with_ancient DESC
LIMIT 10;

// 5. Which trees host insects found on no other tree? (protect or plant these)
MATCH (i:Invertebrate)-[:FOUND_ON]->(t:TreeGenus)
WITH i, collect(t) AS trees
WHERE size(trees) = 1
UNWIND trees AS t
RETURN t.name AS tree, t.common_names AS common_names, count(i) AS insects_found_only_here
ORDER BY insects_found_only_here DESC
LIMIT 10;

// 6. Planting advice: soil microbes at sites where a tree is common (20+ stems)
MATCH (t:TreeGenus)<-[h:HAS_TREE]-(:Plot)<-[:HAS_PLOT]-(s:Site)
WITH t, s, sum(h.stems) AS stems
WHERE stems >= 20
RETURN t.name AS tree, count(s) AS sites, avg(s.fungal_shannon) AS fungal_diversity,
       avg(s.bact_shannon) AS bacterial_diversity
ORDER BY sites DESC
LIMIT 10;

// 7. One row per site joining four datasets: insects, plants, soil and sound.
// Pull this into pandas to test "is soundscape a cheap proxy for biodiversity?"
MATCH (s:Site {habitat: "Woodland restoration"})-[:HAS_PLOT]->(pl:Plot)
OPTIONAL MATCH (pl)-[:BEATEN_FROM_TREES]->(i:Invertebrate)
OPTIONAL MATCH (pl)-[:HAS_PLANT]->(p:Plant)
RETURN s.code AS site, s.former_land_use AS former_land_use, s.age_years AS age,
       count(DISTINCT i) AS tree_insects, count(DISTINCT p) AS plants,
       s.fungal_shannon AS fungi, s.bact_shannon AS bacteria, s.sound_ADI AS sound_diversity
ORDER BY tree_insects DESC;

// 8. Invertebrates caught in ancient woods but never in a restoration site
MATCH (a:Site {habitat: "Ancient woodland"})-[:TRAPPED]->(i:Invertebrate)
WHERE NOT EXISTS { MATCH (:Site {habitat: "Woodland restoration"})-[:TRAPPED]->(i) }
RETURN i.name AS invertebrate, i.order AS order, i.family AS family, count(DISTINCT a) AS ancient_sites
ORDER BY ancient_sites DESC, invertebrate
LIMIT 20;

// 9. Taxonomy roll-up: insect orders beaten from trees, by former land use
// (site -> plot -> insect -> family -> order: 4 hops)
MATCH (s:Site)-[:HAS_PLOT]->(:Plot)-[:BEATEN_FROM_TREES]->(i:Invertebrate)-[:IN_FAMILY]->(:Family)-[:IN_ORDER]->(o:Order)
RETURN s.former_land_use AS former_land_use, o.name AS order, count(DISTINCT i) AS taxa
ORDER BY former_land_use, taxa DESC;

// 10. Soundscape by habitat (acoustic indices averaged over every 10-minute recording)
MATCH (s:Site)
WHERE s.sound_ADI IS NOT NULL
RETURN s.habitat AS habitat, count(s) AS sites, avg(s.sound_ADI) AS acoustic_diversity,
       avg(s.sound_NDSI) AS biophony_vs_traffic
ORDER BY acoustic_diversity DESC;
