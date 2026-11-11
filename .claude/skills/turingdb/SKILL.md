---
name: turingdb
description: >
  Query, explore and extend the EcoHack TuringDB graphs (norwood, woodland_soil, welsh_rivers) with
  TuringDB 3.1 and its Cypher dialect. TRIGGER when: the user asks a question about species, food webs,
  keystone species, soil microbes, woodland restoration, river chemistry, invertebrates, pollution or
  any of the three graphs; code imports `turingdb`; the user mentions TuringDB, Cypher or the visualizer;
  the user wants to start the server, run or write a query, add data or build a new graph.
  SKIP: questions unrelated to these graphs or to TuringDB.
---

# TuringDB 3.1 for the EcoHack graphs

Every rule below was tested on TuringDB 3.1 against these graphs. When something here
disagrees with older TuringDB docs or blog posts, trust this file.

## 1. Make sure the server is up

```bash
./start.sh     # idempotent: starts TuringDB on :6666 (visualizer :8080) and loads all graphs
./stop.sh
```

If `./start.sh` fails with "no wheel for this platform", the machine is macOS 14 or older, an Intel
Mac, or Windows. Use Docker instead: `docker compose up -d` (same ports). The server is never started any other way in this repo.

## 2. Connect and query from Python

```python
from turingdb import TuringDB
c = TuringDB(host="localhost", port=6666)   # 3.1: host name + port, NOT "http://..."
c.set_graph("welsh_rivers")                 # norwood | woodland_soil | welsh_rivers
df = c.query("MATCH (s:Station) RETURN s.kind, count(s) AS n ORDER BY n DESC")   # -> pandas DataFrame
df = c.query("MATCH (t:Taxon {name: $n}) RETURN t.guild", {"n": "Bombus terrestris"})  # parameters work
```

- After a server restart the graphs are on disk but not in memory. `./start.sh` loads them. In your own
  code, call `c.load_graph(name)` and catch `TuringDBException` if it is already loaded.
- Run the examples with `uv run python queries/run.py <graph> [query numbers]`.

## 3. Before writing a query

1. Read `docs/<graph>.md`. It has the schema, the property names and the edge directions.
2. Check names live if unsure:
   `CALL db.labels()`, `CALL db.edgeTypes()`, `CALL db.propertyTypes()`.
3. **An unknown label, edge type or property returns 0 rows or nulls, not an error.** If a query
   returns nothing, check the spelling and the arrow direction first.
4. Edge directions in these graphs:
   - `norwood`: consumer `-[:DEPENDS_ON]->` resource. Find dependents by walking arrows backwards.
   - `welsh_rivers`: `-[:FLOWS_INTO]->` points downstream. Go upstream with `<-[:FLOWS_INTO*1..N]-`.
   - `woodland_soil`: `Site -> Plot -> Plant/TreeGenus/Invertebrate`, and `Invertebrate -[:FOUND_ON]-> TreeGenus`.

## 4. Cypher that works on 3.1

`MATCH`, `WHERE`, `RETURN`, `ORDER BY`, `SKIP`, `LIMIT`, `DISTINCT`, `count/sum/avg/min/max/collect`,
`WITH ... WHERE`, `OPTIONAL MATCH`, `UNWIND`, `IN [...]`, `CASE WHEN`, `EXISTS { MATCH ... }`,
`NOT EXISTS { ... }`, `STARTS WITH`, `CONTAINS`, `IS NULL`, `labels(n)`, `size()`, `toLower()`,
`round()`, `abs()`, list slicing `collect(x)[0..3]`, `[n IN nodes(path) | n.name]`, `length(path)`,
alternation `[:A|B]`, and variable-length paths `-[:T*1..4]->`, `-[*1..3]-`, `-[:T*0..15]->`.

Multi-hop patterns are the point of these graphs:

```cypher
// keystone: species supported within 3 hops of each plant
MATCH (p:Plant)<-[:DEPENDS_ON*1..3]-(a:Animal)
RETURN p.name, count(DISTINCT a) AS supported ORDER BY supported DESC LIMIT 10

// every discharge upstream of an invertebrate site
MATCH (s:InvertSite {name: "At peterston-super-ely"})-[:ON_REACH]->(:Reach)
      <-[:FLOWS_INTO*1..25]-(:Reach)<-[:DISCHARGES_INTO]-(d:Station)
RETURN DISTINCT d.name, d.kind
```

**Always bound variable-length paths** (`*1..25`, not `*`). Paths follow trail semantics and the
number of paths grows quickly. Use `count(DISTINCT x)` to count entities rather than paths.

### Weighted shortest path is a statement, not a function

```cypher
MATCH (d:Station {name: "Taff Merthyr Mine Upper Outfall"})-[:DISCHARGES_INTO]->(r1:Reach),
      (s:InvertSite {name: "D/s cynon stw"})-[:ON_REACH]->(r2:Reach)
shortestPath(r1, r2, distance_m, dist, path)
RETURN dist, path
```

- Signature: `shortestPath(source, target, weight_property, dist_var, path_var)`, followed directly by `RETURN`.
- Only `dist` and `path` may be returned. `path` is a list of internal ids alternating node, edge, node.
  Resolve names with a second query.
- It walks outgoing edges that carry the weight. Weights in this repo:
  - `FLOWS_INTO.distance_m` (metres along the river)
  - `DEPENDS_ON.hops` and `VIA_PLANT.hops` (1.0 each)
- The Neo4j form `shortestPath((a)-[*]->(b))` is a parse error.

### Not supported

`=~` regex, `keys()`, `properties()`, `substring`, `split`, `rand()`, percentiles and stdev, map
projection, `DROP GRAPH`, `FOREACH`, `SET n:Label`. Do these in pandas after the query.

### Other gotchas

- `=` between two doubles is an error. Compare doubles with `<` and `>`.
- A few reserved words (`graph`, `from`, `load`, `index`, `search`, `vector`, `push`, `pull`, `s3`) can
  break property names in `CREATE` or `{...}` maps. If a parse error points at one, wrap it in backticks.
  They work fine as `AS` aliases.
- `RETURN n` returns an internal id, not the node. Return properties by name (`n.name`, `n.kind`).

## 5. Adding data (your own edges, scores or nodes)

Writes happen inside a change and become visible on `main` after `CHANGE SUBMIT`.

```python
c.set_graph("woodland_soil")
c.new_change()
c.query('CREATE (:Note {name: "my hypothesis"})')          # 1. create nodes
c.query("COMMIT")                                          # 2. commit before matching them
c.query('MATCH (n:Note {name: "my hypothesis"}), (s:Site {code: "EW01"}) CREATE (n)-[:ABOUT]->(s)')
c.query('MATCH (s:Site {code: "EW01"}) SET s.priority = 1.0')
c.query("CHANGE SUBMIT")                                   # 3. publish to main
c.checkout()                                               # 4. back to main for reads
```

- A `MATCH` cannot see nodes created earlier in the same change until you `COMMIT`.
- For thousands of new nodes or edges, do not loop `CREATE`. Write a JSONL file like `build/` does and
  `LOAD JSONL 'file.jsonl' AS new_graph`, with the file in `.turing/data/`.
- Every commit is kept. `CALL db.history()` lists them, and `c.set_commit(hash)` reads the graph as it was.

## 6. Answering a question well

1. Restate the question as a path through the schema. For example, "which sewage works affect sites
   that lost mayflies" becomes `InvertSite -> Reach <- FLOWS_INTO* <- Reach <- Station{sewage}`.
2. Write the query, run it, and show the rows and the time.
3. If the result needs statistics, pull rows into pandas and do the maths there.
4. Say which dataset each part of the answer came from, and mention the caveats in `docs/<graph>.md`.
