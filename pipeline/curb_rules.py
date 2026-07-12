"""Fetch SFMTA's Digital Curb inventory (ArcGIS) and build tile-sharded
curb-rule files.

This is the authoritative, maintained source for no-parking / tow-away
windows and max-stay limits; the older Socrata regulations dataset misses
most of them (e.g. commute-hour tow-aways).

Output shards: {tileRow}_{tileCol}.json ->
  {"zones": [{"line": [[lat, lon], ...],
              "rules": [{"a": "np"|"ms", "d": [0..6], "f": mins, "t": mins,
                         "m": maxStayMinutes (ms only)}]}]}
"""

import gzip
import json
import math
import os
import time
from collections import defaultdict

import requests

QUERY_URL = ("https://services.sfmta.com/arcgis/rest/services/Parking/"
             "digitalcurb/MapServer/0/query")
PAGE = 2_000  # server maxRecordCount
CACHE_MAX_AGE_DAYS = 7

_KEEP_ATTRS = ("CURB_ZONE_ID", "RULES_ACTIVITY", "RULES_MAX_STAY",
               "RULES_MAX_STAY_UNIT", "TIME_SPANS_DAYS_OF_WEEK",
               "TIME_SPANS_TIME_OF_DAY_START", "TIME_SPANS_TIME_OF_DAY_END",
               "STREET_NAME")

_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _fetch_pages(cache_path: str, log) -> list[dict]:
    """Fetch all relevant curb policy rows (GeoJSON features), with a local cache."""
    if os.path.exists(cache_path):
        age_days = (time.time() - os.path.getmtime(cache_path)) / 86_400
        if age_days < CACHE_MAX_AGE_DAYS:
            with gzip.open(cache_path, "rt") as f:
                return [json.loads(line) for line in f]

    where = "IS_ACTIVE='Y' AND (RULES_ACTIVITY='no parking' OR RULES_MAX_STAY>0)"
    features: list[dict] = []
    offset = 0
    session = requests.Session()
    while True:
        # NB: this MapServer is picky - it rejects f=geojson AND any named
        # outFields list; only outFields=* works. Trim attrs after download.
        params = {
            "where": where,
            "outFields": "*",
            "outSR": 4326,
            "geometryPrecision": 5,
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": PAGE,
        }
        for attempt in range(5):
            try:
                resp = session.get(QUERY_URL, params=params, timeout=300)
                resp.raise_for_status()
                payload = resp.json()
                if "error" in payload:
                    raise ValueError(str(payload["error"]))
                break
            except (requests.RequestException, ValueError):
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt * 3)
        page = payload.get("features", [])
        # Keep only the attributes we use before caching (outFields=* is big).
        for feat in page:
            attrs = feat.get("attributes") or {}
            feat["attributes"] = {k: attrs.get(k) for k in _KEEP_ATTRS}
        features.extend(page)
        if offset % 20_000 == 0:
            log(f"  curb: fetched {len(features):,} rows")
        if len(page) < PAGE and not payload.get("exceededTransferLimit", False):
            break
        offset += len(page)
    if not features:
        raise RuntimeError("digital curb query returned no rows - check query params")
    log(f"  curb: fetched {len(features):,} rows total")

    with gzip.open(cache_path, "wt") as f:
        for feat in features:
            f.write(json.dumps(feat) + "\n")
    return features


def _parse_days(raw) -> list[int]:
    if not raw:
        return list(range(7))
    try:
        tokens = json.loads(raw)
    except (ValueError, TypeError):
        return list(range(7))
    days = sorted({_DAYS[t] for t in tokens if t in _DAYS})
    return days or list(range(7))


def _parse_minutes(raw) -> int | None:
    """'15:00' -> 900."""
    if not raw:
        return None
    try:
        h, m = str(raw).split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def _length_meters(line: list) -> float:
    total = 0.0
    for i in range(len(line) - 1):
        total += math.hypot((line[i + 1][0] - line[i][0]) * 111_000,
                            (line[i + 1][1] - line[i][1]) * 88_000)
    return total


def _max_stay_minutes(attrs) -> int | None:
    stay = attrs.get("RULES_MAX_STAY") or 0
    if stay <= 0:
        return None
    unit = (attrs.get("RULES_MAX_STAY_UNIT") or "minute").lower()
    if unit.startswith("hour"):
        return int(stay * 60)
    if unit.startswith("day"):
        return int(stay * 1440)
    return int(stay)


def build_curb_shards(cache_dir: str, lat_step: float, lon_step: float,
                      tile_factor: int, log) -> dict[str, dict]:
    features = _fetch_pages(os.path.join(cache_dir, "curb.jsonl.gz"), log)
    log(f"  curb: {len(features):,} policy rows")

    zones: dict[str, dict] = {}
    for feat in features:
        # Esri JSON: {"attributes": {...}, "geometry": {"paths": [[[x, y], ...]]}}
        attrs = feat.get("attributes") or {}
        geom = feat.get("geometry") or {}
        zone_id = attrs.get("CURB_ZONE_ID")
        if not zone_id:
            continue

        if zone_id not in zones:
            paths = geom.get("paths") or []
            coords = paths[0] if paths else []
            if len(coords) < 2:
                continue
            zones[zone_id] = {
                "line": [[round(c[1], 5), round(c[0], 5)] for c in coords],
                "rules": [],
            }

        activity = (attrs.get("RULES_ACTIVITY") or "").lower()
        max_stay = _max_stay_minutes(attrs)
        if activity == "no parking":
            kind = "np"
        elif max_stay:
            kind = "ms"
        else:
            continue

        days = _parse_days(attrs.get("TIME_SPANS_DAYS_OF_WEEK"))
        f = _parse_minutes(attrs.get("TIME_SPANS_TIME_OF_DAY_START"))
        t = _parse_minutes(attrs.get("TIME_SPANS_TIME_OF_DAY_END"))

        # All-day, every-day "no parking" is mostly driveways/red curbs -
        # only keep it for segments long enough to be real block restrictions.
        if kind == "np" and f is None and t is None and len(days) == 7:
            if _length_meters(zones[zone_id]["line"]) < 40:
                continue

        rule = {"a": kind, "d": days, "f": f, "t": t}
        if kind == "ms":
            rule["m"] = max_stay
        if rule not in zones[zone_id]["rules"]:
            zones[zone_id]["rules"].append(rule)

    # Assign each zone to a tile by its midpoint.
    shards: dict[str, dict] = defaultdict(lambda: {"zones": []})
    kept = 0
    for zone in zones.values():
        if not zone["rules"]:
            continue
        mid = zone["line"][len(zone["line"]) // 2]
        row = math.floor(mid[0] / lat_step) // tile_factor
        col = math.floor(mid[1] / lon_step) // tile_factor
        shards[f"{row}_{col}"]["zones"].append(zone)
        kept += 1
    log(f"  curb: {kept:,} zones with rules across {len(shards):,} tiles")
    return dict(shards)
