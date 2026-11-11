"""Shared helpers for the EcoHack graph builders.

Every builder reads raw files from ../sources/<dataset>/ and returns a GraphBuilder.
build.py writes the JSONL and loads it into a TuringDB server.
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources"
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `tc`

from tc.jsonl_bulk_builder import GraphBuilder  # noqa: E402

_bng = None


def bng_to_latlon(easting: float, northing: float) -> tuple[float, float]:
    """British National Grid (EPSG:27700) -> WGS84 lat, lon."""
    global _bng
    if _bng is None:
        from pyproj import Transformer
        _bng = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
    lon, lat = _bng.transform(easting, northing)
    return round(lat, 6), round(lon, 6)


def read_csv(path: Path, encoding: str = "utf-8-sig", **kw) -> list[dict]:
    with open(path, encoding=encoding, newline="") as f:
        return [{(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                for row in csv.DictReader(f, **kw)]


def num(v) -> float | None:
    """Parse a number; NA / empty / junk -> None."""
    if v is None:
        return None
    v = str(v).strip()
    if v in ("", "NA", "N/A", "na", "-", "NaN"):
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return None if math.isnan(x) else x


def clean(v) -> str | None:
    v = (v or "").strip()
    return None if v in ("", "NA", "N/A") else v


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def new_builder(doubles=()) -> GraphBuilder:
    return GraphBuilder(doubles=set(doubles) | {"lat", "lon"}, key_prop="sid")
