# Rebuilding the graphs from the original data

You do not need this to use the graphs: `graphs/*.jsonl.gz` are already built. Use it to change how a graph is modelled, or to add data.

## 1. Get the source files into `sources/`

| Folder | What | Where from |
|---|---|---|
| `sources/norwood/norwood.csv` | Norwood Farm interactions | [Dryad doi:10.5061/dryad.3s36r118](https://doi.org/10.5061/dryad.3s36r118), download `norwood.csv` in a browser |
| `sources/eidc_flora/` | WrEN insects, plants, trees | `curl -L -o x.zip https://data-package.ceh.ac.uk/data/8c997943-1f90-4897-87b3-491eaef534ec.zip` then unzip |
| `sources/eidc_soil/` | WrEN soil microbes | the same with `a21b8ed1-124b-4b2a-adb4-c3fdcc9f95a2` |
| `sources/eidc_acoustic/` | WrEN acoustic indices | the same with `33b6dea2-94c5-4e33-a1b3-9a6bd1c8776a` |
| `sources/kieco/*.csv` | KiEco river invertebrates | `curl -L -o kieco.zip https://api.gbif.org/v1/occurrence/download/request/0008659-260921141020460.zip` then unzip |
| `sources/nrw_wq/*.csv` | NRW water quality | [NRW Water Quality Archive](https://datamap.gov.wales/layers/geonode:nrw_water_quality_archive_stations), follow the data link, then "By Catchment". Download and unzip the 9 files for `South_East_Valleys`, `Carmarthen_Bay_and_the_Gower` and `Usk`. |
| `sources/os_rivers/Data/oprvrs_gb.gpkg` | OS Open Rivers | `curl -L -o oprvrs.zip "https://api.os.uk/downloads/v1/products/OpenRivers/downloads?area=GB&format=GeoPackage&redirect"` then unzip |

## 2. Build and check

```bash
uv sync --group build
cd build
uv run python build_norwood.py ../out/norwood.jsonl
uv run python build_woodland.py ../out/woodland_soil.jsonl
uv run python build_rivers.py ../out/welsh_rivers.jsonl     # about 1 minute, 4 GB RAM
cd ..
```

Each builder writes `out/<graph>.jsonl` and a `.manifest.json` with node and edge counts per label and type.

## 3. Load and verify against a server

```bash
uv run python build/load.py out/woodland_soil.jsonl --graph woodland_soil --turing-dir .turing --port 6666 --reload
```

`--reload` stops the server, removes the old graph from `.turing/graphs/`, restarts the server and loads the new file. It is needed because `LOAD JSONL` only creates new graphs. The script then checks every label and edge type count against the manifest.

## 4. Ship it

```bash
gzip -9 -c out/woodland_soil.jsonl > graphs/woodland_soil.jsonl.gz
```

Then update the counts in `docs/<graph>.md`.

## How the builders are made

- `common.py` holds the shared helpers. `tc/` is a vendored copy of the [turingdb-components](https://github.com/turing-db) `jsonl-bulk-builder`, which writes the JSONL that `LOAD JSONL` reads, with one type per property name.
- Nodes are keyed by a business key (`sid` property), for example `site:EW01` or `reach:1234`.
- Modelling choices are explained in the docstring at the top of each `build_*.py`.
