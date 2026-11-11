# welsh_rivers: river chemistry, river animals and the river network

**Use it for:** pollution tracing, cause and effect between chemistry and biology, recovery, monitoring gaps, speed at scale.

## What the data is

Three sources sit on one river network for three South Wales catchments: South East Valleys, Carmarthen Bay and the Gower, and Usk.

- **Water chemistry, 2000-2026.** Natural Resources Wales samples water at rivers, lakes, sewage works, industrial outfalls, mine water discharges and landfill sites. Each sample measures many determinands: ammonia, phosphate, nitrate, dissolved oxygen, BOD, metals, temperature and more. That comes to 1.8 million measurements.
- **River invertebrates.** NRW kick-samples mayflies, stoneflies, caddisflies, snails and worms, which are the standard indicator of river health. Mayflies, stoneflies and caddisflies (EPT) are flagged `pollution_sensitive`.
- **The river network.** OS Open Rivers gives every watercourse with its flow direction. Here it is merged into reaches between junctions, so `FLOWS_INTO` is a real downstream step.

How the sources are joined:
- Chemistry stations and invertebrate sites are snapped to the nearest reach: within 250 m for river stations and sites, and within 1 km for discharge outfalls.
- Each invertebrate site is also linked to every river station within 2 km.
- Only river systems that contain a station or a site are kept.

Sources:
- NRW Open Water Quality Archive, NRW_DS124903, "By Catchment" extracts. Open Government Licence v3.0. [metadata](https://metadata.naturalresources.wales/geonetwork/srv/api/records/NRW_DS124903)
- KiEco freshwater river macroinvertebrates (Natural Resources Wales) via GBIF.org (26 September 2026) GBIF Occurrence Download [doi:10.15468/dl.t8kmy5](https://doi.org/10.15468/dl.t8kmy5). CC BY 4.0.
- OS Open Rivers, contains OS data © Crown copyright and database right 2026. Open Government Licence v3.0.

## Size

205,397 nodes and 2,230,699 edges, about 1.5 GB of RAM when loaded.

| Node | Count | What it is |
|---|---|---|
| `Catchment` | 3 | WFD management catchment |
| `Station` | 2,169 | Chemistry sampling point. `kind` is `river`, `sewage`, `trade effluent`, `mine water`, `landfill leachate`, `lake`, `groundwater`, `estuary or coast` or `other`. |
| `Sample` | 183,647 | One visit to a station (date, purpose) |
| `Determinand` | 949 | What was measured, with its unit |
| `Reach` | 8,464 | Stretch of river between two junctions |
| `InvertSite` | 919 | Invertebrate sampling site |
| `InvertSurvey` | 8,328 | One invertebrate sample at a site on a date |
| `Taxon` | 749 | Invertebrate taxon (mostly family level) |
| `Family`, `Order` | 127, 42 | Taxonomy |

## Schema

```
(:Station)-[:IN_CATCHMENT]->(:Catchment)
(:Station)-[:HAS_SAMPLE]->(:Sample)-[:MEASURED {value, sign, text}]->(:Determinand)
(:Station)-[:ON_REACH {distance_m}]->(:Reach)              river, lake and other monitoring points
(:Station)-[:DISCHARGES_INTO {distance_m}]->(:Reach)       sewage, trade effluent, mine water, leachate outfalls
(:Reach)-[:FLOWS_INTO {distance_m}]->(:Reach)              downstream; distance_m = length of the upstream reach
(:InvertSite)-[:ON_REACH {distance_m}]->(:Reach)
(:InvertSite)-[:NEAR_STATION {distance_m}]->(:Station)     every river station within 2 km
(:InvertSite)-[:IN_CATCHMENT]->(:Catchment)
(:InvertSite)-[:SURVEYED]->(:InvertSurvey)-[:FOUND {count}]->(:Taxon)
(:Taxon)-[:IN_FAMILY]->(:Family)-[:IN_ORDER]->(:Order)
```

To go upstream, follow `FLOWS_INTO` backwards: `(site)-[:ON_REACH]->(:Reach)<-[:FLOWS_INTO*1..25]-(up:Reach)`.
To go downstream, follow it forwards. Always give the path a maximum length.

### Key properties

| On | Properties |
|---|---|
| `Station` | `name`, `station_id`, `kind`, `medium`, `lat`, `lon`, `easting`, `northing`, `ngr`, `local_authority`, `first_sample`, `last_sample`, `samples` |
| `Sample` | `datetime`, `date`, `year`, `reason` (planned monitoring, compliance, reactive), `purpose`, `medium` |
| `MEASURED` | `value` (number), `sign` (`<` or `>` when below or above detection), `text` (coded results) |
| `Determinand` | `name` (for example `Ammoniacal Nitrogen as N`, `Orthophosphate, reactive as P`, `Oxygen, Dissolved, % Saturation`, `BOD : 5 Day ATU`), `code`, `unit`, `measurements` |
| `Reach` | `name`, `river`, `form`, `length_m`, `lat`, `lon` |
| `InvertSite` | `name`, `lat`, `lon` |
| `InvertSurvey` | `date`, `year` |
| `Taxon` | `name`, `scientific_name`, `rank`, `family`, `order`, `pollution_sensitive` |

## Questions it answers

All of these are in [`queries/welsh_rivers.cypher`](../queries/welsh_rivers.cypher).

1. **Speed.** Scan all 1.8 million measurements and summarise them in about 0.2 s.
2. **Hotspots.** Which river stations most often exceed 0.6 mg/l ammonia?
3. **Decline.** Which invertebrate sites lost the most pollution-sensitive families between before 2008 and after 2015?
4. **Pollution trace.** Which sewage works, mine outfalls and landfills sit upstream of a site that declined? The query walks up to 25 reaches upstream.
5. **Downstream exposure.** How many invertebrate sites lie downstream of each mine-water outfall?
6. **River distance.** `shortestPath` weighted by `distance_m` gives metres along the river, for example 6.4 km from the Taff Merthyr mine outfall to the site below Cynon sewage works.
7. **Chemistry next to biology.** Phosphate at stations near each invertebrate site.
8. **Trends.** Yearly mean phosphate per catchment.
9. **Monitoring gaps.** Invertebrate sites with no chemistry station within 2 km.

Ideas to go further:
- Lag analysis: do sensitive families drop in the year after an ammonia spike upstream?
- Recovery: after a pollution event, how long until EPT families return downstream?
- Prioritise interventions: which single discharge, if fixed, affects the most invertebrate sites downstream?
- Place a new monitoring station where it would cover the most unmonitored sites.

## Caveats

- Chemistry covers only the three catchments above. Invertebrate sites outside them were dropped.
- Snapping to reaches uses straight-line distance, so a few outfalls may land on the wrong small tributary.
- Some NRW data is withheld at source (groundwater, potable supply, some investigations).
- Each KiEco record is a taxon found at a site on a date. Absent taxa are not recorded, so treat a missing taxon as "not found" only for sites that were surveyed.
