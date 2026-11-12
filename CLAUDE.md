# EcoHack graphs: notes for Claude

This repo gives hackathon teams three ready-to-query TuringDB graphs built from the EcoHack 2026 AI-track open datasets.
Most users are scientists, not database people. Answer in plain language and show the query you ran.

## Start here

1. `./start.sh` starts TuringDB 3.1 on :6666 (visualizer :8080) and loads every graph from `graphs/*.jsonl.gz`.
   It is safe to run again. If the platform has no TuringDB wheel (macOS 14, an Intel Mac, Windows), use `docker compose up -d`.
2. Use the `turingdb` skill in `.claude/skills/turingdb/` for every query. It holds the tested 3.1 dialect rules.
3. Read `docs/<graph>.md` for the schema before writing a query.

## The graphs

| Graph | What | Docs | Examples |
|---|---|---|---|
| `norwood` | Farm food web: 549 species, 7 linked networks (pollinators, aphids, parasitoids, seed eaters, birds, rodents) | `docs/norwood.md` | `queries/norwood.cypher`, `queries/norwood_cascade.py` |
| `woodland_soil` | 84 restoration, ancient and rewilding sites: soil microbes, plants, trees, insects, soundscape | `docs/woodland_soil.md` | `queries/woodland_soil.cypher` |
| `welsh_rivers` | 1.8M river chemistry measurements, river invertebrates and the river network for 3 South Wales catchments | `docs/welsh_rivers.md` | `queries/welsh_rivers.cypher` |

Run examples: `uv run python queries/run.py <graph> [numbers]` (or plain `python` if TuringDB was installed with pip and uv is not installed).

## Rules

- Never stop or delete another TuringDB server, and never delete `.turing/` unless the user asks. `./stop.sh` stops only this repo's server.
- Do not hand-edit `graphs/*.jsonl.gz`. To change how a graph is built, edit `build/build_<graph>.py` and follow `build/README.md`.
- For a new analysis script, put it in `analysis/` and keep it runnable with `uv run python analysis/<name>.py`.
- Bound every variable-length path. Use `count(DISTINCT ...)` when counting entities reached by paths.
- When a result looks empty, check label, property and edge names first: unknown names return 0 rows, not an error.
