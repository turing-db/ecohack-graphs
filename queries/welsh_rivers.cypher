// 1. What is in the graph? Stations by type in each catchment
MATCH (s:Station)-[:IN_CATCHMENT]->(c:Catchment)
RETURN c.name AS catchment, s.kind AS station_type, count(s) AS stations
ORDER BY catchment, stations DESC;

// 2. Speed check: scan all 1.8 million measurements and summarise the most measured determinands
MATCH ()-[m:MEASURED]->(d:Determinand)
WHERE m.value > 0
RETURN d.name AS determinand, d.unit AS unit, count(m) AS measurements, avg(m.value) AS mean
ORDER BY measurements DESC
LIMIT 10;

// 3. Ammonia hotspots: river stations with the most samples above 0.6 mg/l
MATCH (st:Station {kind: "river"})-[:HAS_SAMPLE]->(s:Sample)-[m:MEASURED]->(:Determinand {name: "Ammoniacal Nitrogen as N"})
WHERE m.value > 0.6
RETURN st.name AS station, count(s) AS high_ammonia_samples, max(m.value) AS worst_mg_per_l
ORDER BY high_ammonia_samples DESC
LIMIT 10;

// 4. Which invertebrate sites lost the most pollution-sensitive families
//    (mayflies, stoneflies, caddisflies) between before 2008 and after 2015?
MATCH (s:InvertSite)-[:SURVEYED]->(v:InvertSurvey)-[:FOUND]->(t:Taxon {pollution_sensitive: true})
WITH s,
     count(DISTINCT CASE WHEN v.year < 2008 THEN t.family END) AS families_before,
     count(DISTINCT CASE WHEN v.year >= 2015 THEN t.family END) AS families_after
WHERE families_before > 0 AND families_after > 0
RETURN s.name AS site, families_before, families_after, families_before - families_after AS lost
ORDER BY lost DESC
LIMIT 10;

// 5. Pollution trace: which discharges sit upstream of a site? (walks up to 25 river reaches)
MATCH (s:InvertSite {name: "At peterston-super-ely"})-[:ON_REACH]->(:Reach)
      <-[:FLOWS_INTO*1..25]-(:Reach)<-[:DISCHARGES_INTO]-(d:Station)
RETURN DISTINCT d.name AS discharge, d.kind AS type, d.samples AS samples
ORDER BY type, discharge;

// 6. Downstream exposure: invertebrate sites within 15 reaches downstream of each mine-water outfall
MATCH (d:Station {kind: "mine water"})-[:DISCHARGES_INTO]->(:Reach)-[:FLOWS_INTO*0..15]->(:Reach)<-[:ON_REACH]-(s:InvertSite)
RETURN d.name AS mine_outfall, count(DISTINCT s) AS sites_downstream
ORDER BY sites_downstream DESC
LIMIT 10;

// 7. River distance from a mine outfall to an invertebrate site (weighted shortest path, metres)
MATCH (d:Station {name: "Taff Merthyr Mine Upper Outfall"})-[:DISCHARGES_INTO]->(r1:Reach),
      (s:InvertSite {name: "D/s cynon stw"})-[:ON_REACH]->(r2:Reach)
shortestPath(r1, r2, distance_m, dist, path)
RETURN dist, path;

// 8. Chemistry next to biology: phosphate at stations within 2 km of each invertebrate site
MATCH (s:InvertSite)-[:NEAR_STATION]->(st:Station)-[:HAS_SAMPLE]->(x:Sample)-[m:MEASURED]->(:Determinand {name: "Orthophosphate, reactive as P"})
WHERE x.year >= 2015
RETURN s.name AS site, count(DISTINCT st) AS stations, count(m) AS measurements, avg(m.value) AS mean_phosphate
ORDER BY mean_phosphate DESC
LIMIT 10;

// 9. Trend: yearly mean phosphate per catchment (aggregates over every sample, 4 hops)
MATCH (c:Catchment)<-[:IN_CATCHMENT]-(:Station {kind: "river"})-[:HAS_SAMPLE]->(s:Sample)
      -[m:MEASURED]->(:Determinand {name: "Orthophosphate, reactive as P"})
RETURN c.name AS catchment, s.year AS year, avg(m.value) AS mean_phosphate, count(m) AS n
ORDER BY catchment, year;

// 10. Monitoring gaps: invertebrate sites with no chemistry station within 2 km
MATCH (s:InvertSite)-[:IN_CATCHMENT]->(c:Catchment)
WHERE NOT EXISTS { MATCH (s)-[:NEAR_STATION]->() }
RETURN c.name AS catchment, count(s) AS sites_without_nearby_chemistry
ORDER BY sites_without_nearby_chemistry DESC;
