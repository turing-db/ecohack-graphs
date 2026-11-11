"""welsh_rivers: river chemistry + river invertebrates + the river network they sit on.

Sources (all Open Government Licence or CC BY 4.0):
  - NRW Open Water Quality Archive, "By Catchment" extracts for South East Valleys,
    Carmarthen Bay and the Gower, and Usk, 2000-2026            sources/nrw_wq/*.csv
  - KiEco freshwater river macroinvertebrates (GBIF download 10.15468/dl.t8kmy5)
                                                                   sources/kieco/*.csv
  - OS Open Rivers (watercourse links with flow direction)       sources/os_rivers/Data/oprvrs_gb.gpkg

The river network is contracted to reaches: a reach is a maximal run of watercourse
links with no confluence or split in it, so FLOWS_INTO hops are junction to junction.
"""
from __future__ import annotations

import glob
import re
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from common import SOURCES, new_builder

NRW = SOURCES / "nrw_wq"
KIECO = SOURCES / "kieco"
RIVERS = SOURCES / "os_rivers" / "Data" / "oprvrs_gb.gpkg"

BBOX_BUFFER_M = 3000        # river network kept around the stations
SNAP_STATION_M = 250        # station -> reach
SNAP_DISCHARGE_M = 1000     # effluent outfall -> receiving reach
SNAP_SITE_M = 250           # invertebrate site -> reach
NEAR_STATION_M = 2000       # invertebrate site -> nearby chemistry station
SITE_MAX_FROM_STATION_M = 5000  # invertebrate sites further than this from any river station are dropped
EPT = {"Ephemeroptera", "Plecoptera", "Trichoptera"}  # pollution-sensitive orders

DISCHARGE_MEDIA = ("SEWAGE", "EFFLUENT", "MINEWATER", "LEACHATE", "SURFACE DRAINAGE")


def station_kind(medium: str) -> str:
    m = medium.upper()
    if "SEWAGE" in m:
        return "sewage"
    if "TRADE EFFLUENT" in m:
        return "trade effluent"
    if "MINEWATER" in m:
        return "mine water"
    if "LEACHATE" in m:
        return "landfill leachate"
    if "RIVER" in m:
        return "river"
    if "LAKE" in m or "POND" in m or "RESERVOIR" in m:
        return "lake"
    if "ESTUAR" in m or "SALINE" in m or "SEA" in m or "COASTAL" in m:
        return "estuary or coast"
    if "GROUND" in m or "SPRING" in m or "BOREHOLE" in m:
        return "groundwater"
    return "other"


def load_nrw() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(NRW / "*_Water_Quality_Archive.csv"))):
        catchment = re.sub(r"_\d{4}_.*", "", f.split("/")[-1]).replace("_", " ")
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
        df["catchment"] = catchment
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["value"] = pd.to_numeric(df["sample_value"], errors="coerce")
    return df


def build():
    from pyproj import Transformer
    import geopandas as gpd
    from shapely.geometry import box
    to_ll = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
    to_bng = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)

    g = new_builder(doubles={"value", "easting", "northing", "length_m", "distance_m", "x", "y"})

    # ---- chemistry -----------------------------------------------------------
    wq = load_nrw()
    print(f"NRW rows: {len(wq):,}")
    st = (wq.groupby("station_number")
            .agg(name=("station_name", "first"), easting=("easting", "first"), northing=("northing", "first"),
                 ngr=("ngr", "first"), local_authority=("local_authority", "first"),
                 catchment=("catchment", lambda s: s.mode().iat[0]),
                 medium=("sampling_medium", lambda s: s.mode().iat[0]),
                 first=("sampling_datetime", "min"), last=("sampling_datetime", "max"),
                 samples=("sampling_datetime", "nunique"))
            .reset_index())
    st["easting"] = pd.to_numeric(st["easting"], errors="coerce")
    st["northing"] = pd.to_numeric(st["northing"], errors="coerce")
    st = st.dropna(subset=["easting", "northing"])
    st["kind"] = st["medium"].map(station_kind)
    lon, lat = to_ll.transform(st["easting"].values, st["northing"].values)
    st["lat"], st["lon"] = np.round(lat, 6), np.round(lon, 6)

    for c in sorted(st["catchment"].unique()):
        g.node(f"catch:{c}", ["Catchment"], name=c)
    for r in st.itertuples():
        g.node(f"st:{r.station_number}", ["Station"], name=r.name.title(), station_id=r.station_number,
               kind=r.kind, medium=r.medium, ngr=r.ngr, local_authority=r.local_authority.title(),
               easting=float(r.easting), northing=float(r.northing), lat=float(r.lat), lon=float(r.lon),
               first_sample=r.first[:10], last_sample=r.last[:10], samples=int(r.samples))
        g.link(f"st:{r.station_number}", f"catch:{r.catchment}", "IN_CATCHMENT")
    stations = set(st["station_number"])

    det = wq.groupby("parameter_shortname").agg(name=("parameter_name", "first"), unit=("unit_symbol", "first"),
                                                unit_name=("unit_name", "first"), rows=("parameter_name", "size"))
    for code, r in det.iterrows():
        unit = "" if r.unit in ("---", "") else r.unit
        g.node(f"det:{code}", ["Determinand"], name=r["name"], code=code, unit=unit,
               unit_name="" if r.unit_name == "scalar" else r.unit_name, measurements=int(r.rows))

    wq = wq[wq["station_number"].isin(stations)]
    samples = wq.drop_duplicates(["station_number", "sampling_datetime"])
    for r in samples.itertuples():
        sk = f"smp:{r.station_number}:{r.sampling_datetime}"
        g.node(sk, ["Sample"], name=f"{r.station_number} {r.sampling_datetime[:10]}",
               datetime=r.sampling_datetime, date=r.sampling_datetime[:10], year=int(r.sampling_datetime[:4]),
               reason=r.reason_group, purpose=r.sampling_reason.capitalize(), medium=r.sampling_medium)
        g.link(f"st:{r.station_number}", sk, "HAS_SAMPLE")
    print(f"samples: {len(samples):,}")

    n_meas = 0
    for r in wq.itertuples():
        props = {"sign": "" if r.sign == "---" else r.sign}
        if not np.isnan(r.value):
            props["value"] = float(r.value)
        elif r.coded_value:
            props["text"] = r.coded_value
        else:
            continue
        g.link(f"smp:{r.station_number}:{r.sampling_datetime}", f"det:{r.parameter_shortname}", "MEASURED", **props)
        n_meas += 1
    print(f"measurements: {n_meas:,}")
    del wq

    # ---- river network --------------------------------------------------------
    minx, miny = st["easting"].min() - BBOX_BUFFER_M, st["northing"].min() - BBOX_BUFFER_M
    maxx, maxy = st["easting"].max() + BBOX_BUFFER_M, st["northing"].max() + BBOX_BUFFER_M
    links = gpd.read_file(RIVERS, layer="watercourse_link", bbox=(minx, miny, maxx, maxy))
    links = links[links.geometry.intersects(box(minx, miny, maxx, maxy))].reset_index(drop=True)
    print(f"watercourse links in area: {len(links):,}")

    succ = defaultdict(list)   # link -> downstream links
    by_start = defaultdict(list)
    for i, r in enumerate(links.itertuples()):
        by_start[r.start_node].append(i)
    for i, r in enumerate(links.itertuples()):
        succ[i] = by_start.get(r.end_node, [])
    preds = defaultdict(list)
    for i, js in succ.items():
        for j in js:
            preds[j].append(i)
    pred_n = Counter({j: len(ps) for j, ps in preds.items()})

    # contract: a link continues its upstream link's reach if it is that link's only
    # successor and has no other upstream link
    reach_of = {}
    heads = [i for i in range(len(links)) if not (pred_n[i] == 1 and len(succ[preds[i][0]]) == 1)]
    rid = 0
    for h in heads:
        if h in reach_of:
            continue
        rid += 1
        cur = h
        while True:
            reach_of[cur] = rid
            nxt = succ[cur]
            if len(nxt) == 1 and pred_n[nxt[0]] == 1 and nxt[0] not in reach_of:
                cur = nxt[0]
            else:
                break
    for i in range(len(links)):          # cycles with no head
        if i not in reach_of:
            rid += 1
            reach_of[i] = rid

    links["reach"] = [reach_of[i] for i in range(len(links))]
    reaches = links.dissolve(by="reach", aggfunc={"length": "sum", "watercourse_name": "first", "form": "first"})
    flows = {}
    for i, js in succ.items():
        for j in js:
            a, b = reach_of[i], reach_of[j]
            if a != b:
                flows[(a, b)] = float(reaches.at[a, "length"])   # weight: metres travelled along a

    rgeo = reaches[["geometry"]].reset_index()
    def snap(df, x, y, maxd):
        pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[x], df[y]), crs="EPSG:27700")
        return gpd.sjoin_nearest(pts, rgeo, max_distance=maxd, distance_col="distance_m", how="inner")

    st_dis = st[st["kind"].isin(["sewage", "trade effluent", "mine water", "landfill leachate"])]
    st_riv = st[~st.index.isin(st_dis.index)]
    snap_riv = snap(st_riv, "easting", "northing", SNAP_STATION_M).drop_duplicates("station_number")
    snap_dis = snap(st_dis, "easting", "northing", SNAP_DISCHARGE_M).drop_duplicates("station_number")

    # ---- invertebrate sites (KiEco), kept when a river chemistry station is near ----
    kf = glob.glob(str(KIECO / "*.csv"))[0]
    kc = pd.read_csv(kf, sep="\t", dtype=str, keep_default_na=False, quoting=3,
                     usecols=["locality", "decimalLatitude", "decimalLongitude", "eventDate", "year",
                              "scientificName", "verbatimScientificName", "taxonRank", "phylum", "class", "order",
                              "family", "genus", "species", "individualCount"])
    kc["lat"] = pd.to_numeric(kc["decimalLatitude"], errors="coerce")
    kc["lon"] = pd.to_numeric(kc["decimalLongitude"], errors="coerce")
    kc = kc.dropna(subset=["lat", "lon"])
    x, y = to_bng.transform(kc["lon"].values, kc["lat"].values)
    kc["x"], kc["y"] = x, y
    kc = kc[(kc.x >= minx) & (kc.x <= maxx) & (kc.y >= miny) & (kc.y <= maxy)]
    sites = kc.drop_duplicates(["locality", "lat", "lon"])[["locality", "lat", "lon", "x", "y"]].reset_index(drop=True)
    sites["site_id"] = [f"{r.locality}|{r.lat:.5f}|{r.lon:.5f}" for r in sites.itertuples()]
    riv_pts = st_riv[["station_number", "easting", "northing", "catchment"]].reset_index(drop=True)
    near = {}
    for r in sites.itertuples():
        d = np.hypot(riv_pts["easting"].values - r.x, riv_pts["northing"].values - r.y)
        if len(d) and d.min() <= SITE_MAX_FROM_STATION_M:
            near[r.site_id] = d
    sites = sites[sites.site_id.isin(near)].reset_index(drop=True)
    snapped = snap(sites, "x", "y", SNAP_SITE_M).drop_duplicates("site_id")
    keep = set(snapped["site_id"])

    # ---- keep only river systems that carry a station or a kept site ----------
    parent = {r: r for r in reaches.index}
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in flows:
        parent[find(a)] = find(b)
    used = set(snap_riv.reach) | set(snap_dis.reach) | set(snapped.reach)
    roots = {find(r) for r in used}
    kept = [r for r in reaches.index if find(r) in roots]
    reaches = reaches.loc[kept]
    print(f"reaches: {len(reaches):,} kept of {len(parent):,}")

    mid = reaches.geometry.interpolate(0.5, normalized=True)
    rlon, rlat = to_ll.transform(mid.x.values, mid.y.values)
    for (r_id, r), la, lo in zip(reaches.iterrows(), rlat, rlon):
        nm = r.watercourse_name or "Unnamed watercourse"
        g.node(f"reach:{r_id}", ["Reach"], name=nm, river=r.watercourse_name or "", form=r.form or "",
               length_m=float(r.length), lat=round(float(la), 6), lon=round(float(lo), 6))
    keptset = set(kept)
    nflow = 0
    for (a, b), w in flows.items():
        if a in keptset and b in keptset:
            g.link(f"reach:{a}", f"reach:{b}", "FLOWS_INTO", distance_m=w)
            nflow += 1
    print(f"FLOWS_INTO: {nflow:,}")
    for r in snap_riv.itertuples():
        g.link(f"st:{r.station_number}", f"reach:{r.reach}", "ON_REACH", distance_m=round(float(r.distance_m), 1))
    for r in snap_dis.itertuples():
        g.link(f"st:{r.station_number}", f"reach:{r.reach}", "DISCHARGES_INTO", distance_m=round(float(r.distance_m), 1))

    print(f"KiEco sites near a station and on a reach: {len(keep):,}")
    for r in sites.itertuples():
        if r.site_id not in keep:
            continue
        g.node(f"site:{r.site_id}", ["InvertSite"], name=r.locality.capitalize(), lat=float(r.lat), lon=float(r.lon),
               x=round(float(r.x), 1), y=round(float(r.y), 1))
        d = near[r.site_id]
        j = int(d.argmin())
        g.link(f"site:{r.site_id}", f"catch:{riv_pts.catchment[j]}", "IN_CATCHMENT")
        for k in np.where(d <= NEAR_STATION_M)[0]:
            g.link(f"site:{r.site_id}", f"st:{riv_pts.station_number[k]}", "NEAR_STATION",
                   distance_m=round(float(d[k]), 1))
    for r in snapped.itertuples():
        g.link(f"site:{r.site_id}", f"reach:{r.reach}", "ON_REACH", distance_m=round(float(r.distance_m), 1))

    kc["site_id"] = [f"{a}|{b:.5f}|{c:.5f}" for a, b, c in zip(kc.locality, kc.lat, kc.lon)]
    kc = kc[kc.site_id.isin(keep)]
    taxa = {}
    for r in kc.drop_duplicates("scientificName").itertuples():
        name = r.verbatimScientificName or r.scientificName
        t = f"tax:{r.scientificName}"
        taxa[r.scientificName] = t
        g.node(t, ["Taxon"], name=name, scientific_name=r.scientificName, rank=r.taxonRank.lower(),
               order=r.order, family=r.family, pollution_sensitive=r.order in EPT)
        if r.family and r.taxonRank != "FAMILY":
            g.upsert(f"fam:{r.family}", ["Family"], name=r.family, order=r.order)
            g.link(t, f"fam:{r.family}", "IN_FAMILY")
        elif r.order and r.taxonRank != "ORDER":
            g.upsert(f"ord:{r.order}", ["Order"], name=r.order, pollution_sensitive=r.order in EPT)
            g.link(t, f"ord:{r.order}", "IN_ORDER")
    for fam, order in kc.drop_duplicates("family")[["family", "order"]].itertuples(index=False):
        if fam and order and g.has(f"fam:{fam}"):
            g.upsert(f"ord:{order}", ["Order"], name=order, pollution_sensitive=order in EPT)
            g.link(f"fam:{fam}", f"ord:{order}", "IN_ORDER")

    ev = kc.drop_duplicates(["site_id", "eventDate"])
    for r in ev.itertuples():
        ek = f"isurvey:{r.site_id}:{r.eventDate}"
        g.node(ek, ["InvertSurvey"], name=f"{r.locality.capitalize()} {r.eventDate[:10]}", date=r.eventDate[:10],
               year=int(r.year) if r.year else 0)
        g.link(f"site:{r.site_id}", ek, "SURVEYED")
    found = kc.groupby(["site_id", "eventDate", "scientificName"])["individualCount"].apply(
        lambda s: int(pd.to_numeric(s, errors="coerce").fillna(0).sum()))
    for (sid, d, sci), n in found.items():
        g.link(f"isurvey:{sid}:{d}", taxa[sci], "FOUND", count=int(n))
    print(f"invert surveys: {len(ev):,}  FOUND: {len(found):,}")
    return g



if __name__ == "__main__":
    import sys
    m = build().write(sys.argv[1] if len(sys.argv) > 1 else "welsh_rivers.jsonl")
    print({k: m[k] for k in ("nodes", "relationships", "labels", "edgeTypes", "bytes")})
