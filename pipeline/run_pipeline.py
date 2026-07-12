#!/usr/bin/env python3
"""Build the No Time to Speed parking-risk data bundle for San Francisco.

Outputs (written to pipeline/output/):
  risk_grid.json  - citation counts per ~100m grid cell x hour-of-week x category
  sweeping.json   - street sweeping schedule blockfaces with geometry
  meters.json     - metered blocks with operating hours
  manifest.json   - version + freshness metadata

Run:  python3 run_pipeline.py [--months 12] [--skip-fetch]
"""

import argparse
import gzip
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import categories
import curb_rules
import sf_open_data as soda
from geocoder import Geocoder

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
OUTPUT = os.path.join(HERE, "output")

# Grid geometry - MUST stay in sync with ParkingRisk.swift on the iOS side.
LAT_STEP = 0.001    # ~111 m
LON_STEP = 0.00125  # ~110 m at SF latitude

# Detail shards group TILE_FACTOR x TILE_FACTOR cells (~550 m squares) so the
# app can fetch citation-level records on demand per neighborhood.
TILE_FACTOR = 5
MAX_STREETS_PER_CELL = 8

WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tues": 1, "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def log(msg: str):
    print(msg, flush=True)
    sys.stdout.flush()


# ---------------------------------------------------------------- citations

def _month_starts(months: int, now: datetime) -> list[datetime]:
    first = datetime(now.year, now.month, 1)
    starts = [first]
    for _ in range(months):
        prev = starts[-1] - timedelta(days=1)
        starts.append(datetime(prev.year, prev.month, 1))
    return list(reversed(starts))


def fetch_citations(months: int, now: datetime, use_cache: bool) -> list[dict]:
    """Fetch citation rows month-by-month with a local cache for dev reruns."""
    os.makedirs(CACHE, exist_ok=True)
    fields = "citation_issued_datetime,violation,violation_desc,citation_location,fine_amount"
    rows: list[dict] = []
    starts = _month_starts(months, now)
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else now + timedelta(days=1)
        tag = start.strftime("%Y%m")
        cache_file = os.path.join(CACHE, f"citations_{tag}.jsonl.gz")
        # Recent months keep changing (late-added citations); only trust cache
        # for months that ended more than 45 days ago.
        stable = (now - end).days > 45
        if use_cache and stable and os.path.exists(cache_file):
            with gzip.open(cache_file, "rt") as f:
                month_rows = [json.loads(line) for line in f]
        else:
            where = (f"citation_issued_datetime >= '{start.isoformat()}' AND "
                     f"citation_issued_datetime < '{end.isoformat()}'")
            month_rows = list(soda.fetch_all(soda.CITATIONS, select=fields, where=where))
            if stable:
                with gzip.open(cache_file, "wt") as f:
                    for r in month_rows:
                        f.write(json.dumps(r) + "\n")
        log(f"  {tag}: {len(month_rows):,} citations")
        rows.extend(month_rows)
    return rows


def build_geocoder(use_cache: bool) -> Geocoder:
    os.makedirs(CACHE, exist_ok=True)
    cache_file = os.path.join(CACHE, "addresses.jsonl.gz")
    gc = Geocoder()
    if use_cache and os.path.exists(cache_file):
        with gzip.open(cache_file, "rt") as f:
            for line in f:
                gc.add_address(json.loads(line))
    else:
        fields = "address_number,street_name,street_type,latitude,longitude"
        with gzip.open(cache_file, "wt") as f:
            for row in soda.fetch_all(soda.ADDRESSES, select=fields):
                gc.add_address(row)
                f.write(json.dumps(row) + "\n")
    gc.finalize()
    return gc


def build_risk_grid(rows: list[dict], gc: Geocoder, months: int, now: datetime) -> tuple[dict, dict]:
    """Returns (risk_grid, detail_shards).

    risk_grid cells carry aggregates: total (t), per-category counts (c),
    hour-of-week counts (h), per-category hour-of-week counts (ch),
    per-category last-cited date (cl), and a per-street breakdown (s).

    detail_shards maps tile key -> shard payload with citation-level records
    (newest first) for on-demand drill-down in the app.
    """
    def new_cell():
        return {
            "t": 0, "c": Counter(), "h": Counter(),
            "ch": defaultdict(Counter),          # cat -> hour-of-week -> n
            "cl": {},                            # cat -> latest datetime
            "f": 0,                              # total fines ($)
            "m": Counter(),                      # "YYYYMM" -> n (trend)
            "streets": defaultdict(lambda: {     # street -> aggregates
                "t": 0, "c": Counter(), "b": Counter(), "l": None}),
        }

    cells: dict[tuple[int, int], dict] = defaultdict(new_cell)
    # tile key -> cell key -> [(ts, cat, street, block, fine), ...]
    shards: dict[tuple[int, int], dict] = defaultdict(lambda: defaultdict(list))
    geocoded = failed = 0
    data_through = None

    for row in rows:
        try:
            ts = datetime.fromisoformat(row["citation_issued_datetime"])
        except (KeyError, ValueError):
            continue
        if ts > now + timedelta(days=1):  # bad future-dated rows exist upstream
            continue
        hit = gc.geocode_full(row.get("citation_location") or "")
        if hit is None:
            failed += 1
            continue
        geocoded += 1
        lat, lon, street, block = hit
        try:
            fine = int(float(row.get("fine_amount") or 0))
        except ValueError:
            fine = 0
        key = (math.floor(lat / LAT_STEP), math.floor(lon / LON_STEP))
        cat = categories.categorize(row.get("violation_desc"))
        hour_of_week = ts.weekday() * 24 + ts.hour

        cell = cells[key]
        cell["t"] += 1
        cell["c"][cat] += 1
        cell["h"][hour_of_week] += 1
        cell["ch"][cat][hour_of_week] += 1
        cell["f"] += fine
        cell["m"][ts.strftime("%Y%m")] += 1
        if cat not in cell["cl"] or ts > cell["cl"][cat]:
            cell["cl"][cat] = ts

        st = cell["streets"][street]
        st["t"] += 1
        st["c"][cat] += 1
        st["b"][block] += 1
        if st["l"] is None or ts > st["l"]:
            st["l"] = ts

        tile = (key[0] // TILE_FACTOR, key[1] // TILE_FACTOR)
        shards[tile][f"{key[0]}_{key[1]}"].append(
            (ts.isoformat(timespec="minutes"), cat, street, block, fine))

        if data_through is None or ts > data_through:
            data_through = ts

    log(f"  geocoded {geocoded:,} / {geocoded + failed:,} "
        f"({geocoded / max(1, geocoded + failed):.1%})")

    totals = sorted(c["t"] for c in cells.values())
    hour_values = sorted(v for c in cells.values() for v in c["h"].values())

    def p95(values):
        return values[int(len(values) * 0.95)] if values else 1

    def street_entries(cell):
        ranked = sorted(cell["streets"].items(), key=lambda kv: -kv[1]["t"])
        return [
            {
                "n": name,
                "b": st["b"].most_common(1)[0][0],
                "t": st["t"],
                "c": dict(st["c"]),
                "l": st["l"].strftime("%Y-%m-%d") if st["l"] else None,
            }
            for name, st in ranked[:MAX_STREETS_PER_CELL]
        ]

    grid = {
        "meta": {
            "source": categories.SOURCE_PARKING_SFMTA,
            "latStep": LAT_STEP,
            "lonStep": LON_STEP,
            "tileFactor": TILE_FACTOR,
            "months": months,
            "generatedAt": now.isoformat(timespec="seconds"),
            "dataThrough": data_through.isoformat(timespec="seconds") if data_through else None,
            "totalCitations": geocoded,
            "totalP95": p95(totals),
            "hourP95": p95(hour_values),
        },
        "cells": [
            {
                "k": f"{k[0]}_{k[1]}",
                "t": c["t"],
                "c": dict(c["c"]),
                "h": {str(h): n for h, n in sorted(c["h"].items())},
                "ch": {cat: {str(h): n for h, n in sorted(hours.items())}
                       for cat, hours in sorted(c["ch"].items())},
                "cl": {cat: ts.strftime("%Y-%m-%d") for cat, ts in sorted(c["cl"].items())},
                "f": c["f"],
                "m": {ym: n for ym, n in sorted(c["m"].items())},
                "s": street_entries(c),
            }
            for k, c in sorted(cells.items())
        ],
    }

    shard_payloads = {}
    for tile, tile_cells in shards.items():
        street_table: list[str] = []
        street_index: dict[str, int] = {}
        cells_out = {}
        for cell_key, records in tile_cells.items():
            records.sort(key=lambda r: r[0], reverse=True)  # newest first
            out = []
            for ts, cat, street, block, fine in records:
                if street not in street_index:
                    street_index[street] = len(street_table)
                    street_table.append(street)
                out.append([ts, cat, street_index[street], block, fine])
            cells_out[cell_key] = out
        shard_payloads[f"{tile[0]}_{tile[1]}"] = {
            "streets": street_table,
            "cells": cells_out,
        }
    return grid, shard_payloads


# ----------------------------------------------------------------- sweeping

def build_sweeping(now: datetime) -> dict:
    blocks = []
    for row in soda.fetch_all(soda.SWEEPING):
        day = WEEKDAYS.get((row.get("weekday") or "").strip().lower())
        line = row.get("line") or {}
        coords = line.get("coordinates") or []
        if day is None or not coords:
            continue
        try:
            from_hour = int(row["fromhour"])
            to_hour = int(row["tohour"])
        except (KeyError, ValueError):
            continue
        blocks.append({
            "corridor": row.get("corridor") or "",
            "limits": row.get("limits") or "",
            "side": row.get("blockside") or "",
            "day": day,
            "from": from_hour,
            "to": to_hour,
            "weeks": [int(row.get(f"week{i}") or 0) for i in range(1, 6)],
            "holidays": int(row.get("holidays") or 0),
            # GeoJSON is [lon, lat]; emit [lat, lon] rounded to ~1 m
            "line": [[round(c[1], 5), round(c[0], 5)] for c in coords],
        })
    return {
        "meta": {"generatedAt": now.isoformat(timespec="seconds")},
        "blocks": blocks,
    }


# -------------------------------------------------------------- regulations

# Normalize the dataset's inconsistent regulation labels to stable keys the
# app can reason about.
_REGULATION_KINDS = {
    "time limited": "timeLimit",
    "time limited ": "timeLimit",
    "no parking any time": "noParking",
    "no parking anytime": "noParking",
    "no stopping": "noParking",
    "limited no parking": "limitedNoParking",
    "no overnight parking": "noOvernight",
    "government permit": "governmentPermit",
    "pay or permit": "payOrPermit",
    "paid + permit": "payOrPermit",
    "no oversized vehicles": "noOversized",
}

_DAY_TOKENS = {
    "M": [0], "TU": [1], "W": [2], "TH": [3], "F": [4], "SA": [5], "SU": [6],
}


def _parse_reg_days(days: str | None) -> list[int]:
    """'M-F' / 'M-Sa' / 'Daily' / 'M,W,F' -> weekday list (Mon=0). Empty = all."""
    if not days:
        return list(range(7))
    s = days.strip().upper().replace(".", "")
    if s in ("DAILY", "EVERYDAY", "EVERY DAY", "ALL", "24/7"):
        return list(range(7))
    order = ["M", "TU", "W", "TH", "F", "SA", "SU"]
    if "-" in s:
        parts = [p.strip() for p in s.split("-", 1)]
        try:
            start = order.index(parts[0])
            end = order.index(parts[1])
            if start <= end:
                return list(range(start, end + 1))
        except ValueError:
            return list(range(7))
    result = []
    for token in s.replace("/", ",").split(","):
        result.extend(_DAY_TOKENS.get(token.strip(), []))
    return sorted(set(result)) or list(range(7))


def _parse_military(value) -> int | None:
    """'900' -> 9, '1800' -> 18; clamps to whole hours."""
    try:
        v = int(float(value))
        return max(0, min(24, v // 100))
    except (TypeError, ValueError):
        return None


def build_regulations(now: datetime) -> dict:
    blocks = []
    for row in soda.fetch_all(soda.REGULATIONS):
        kind = _REGULATION_KINDS.get((row.get("regulation") or "").strip().lower())
        if kind is None or kind == "noOversized":  # irrelevant to passenger cars
            continue
        shape = row.get("shape") or {}
        coords_multi = shape.get("coordinates") or []
        # MultiLineString -> flatten first line, [lon, lat] -> [lat, lon]
        line = coords_multi[0] if coords_multi else []
        if len(line) < 2:
            continue
        rpp_area = (row.get("rpparea1") or "").strip()
        if rpp_area in ("N", "-", ""):
            rpp_area = None
        try:
            hr_limit = float(row.get("hrlimit")) if row.get("hrlimit") else None
        except ValueError:
            hr_limit = None
        blocks.append({
            "kind": kind,
            "days": _parse_reg_days(row.get("days")),
            "from": _parse_military(row.get("hrs_begin")),
            "to": _parse_military(row.get("hrs_end")),
            "limit": hr_limit,
            "rpp": rpp_area,
            "line": [[round(c[1], 5), round(c[0], 5)] for c in line],
        })
    return {
        "meta": {
            "generatedAt": now.isoformat(timespec="seconds"),
            "note": "SFMTA warns this dataset is not comprehensively vetted",
        },
        "blocks": blocks,
    }


# ------------------------------------------------------------------- meters

def build_meters(now: datetime) -> dict:
    meter_points: dict[str, tuple[float, float]] = {}
    for row in soda.fetch_all(soda.METERS,
                              select="post_id,latitude,longitude,active_meter_flag"):
        if (row.get("active_meter_flag") or "").upper() not in ("M", "P"):
            continue
        try:
            meter_points[row["post_id"]] = (float(row["latitude"]), float(row["longitude"]))
        except (KeyError, ValueError, TypeError):
            continue

    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"posts": set(), "schedules": Counter(), "colors": Counter()})
    for row in soda.fetch_all(soda.METER_SCHEDULES):
        if row.get("schedule_type") != "Operating Schedule":
            continue
        post = row.get("post_id")
        if post not in meter_points:
            continue
        key = (row.get("street_and_block") or "", row.get("block_side") or "")
        g = groups[key]
        g["posts"].add(post)
        g["schedules"][(row.get("days_applied") or "",
                        row.get("from_time") or "",
                        row.get("to_time") or "",
                        row.get("time_limit") or "")] += 1
        g["colors"][row.get("cap_color") or "Grey"] += 1

    blocks = []
    for (block, side), g in sorted(groups.items()):
        pts = [meter_points[p] for p in g["posts"]]
        if not pts:
            continue
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        days, from_t, to_t, limit = g["schedules"].most_common(1)[0][0]
        blocks.append({
            "b": block,
            "s": side,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "n": len(g["posts"]),
            "days": days,
            "from": from_t,
            "to": to_t,
            "limit": limit,
            "color": g["colors"].most_common(1)[0][0],
        })
    return {
        "meta": {"generatedAt": now.isoformat(timespec="seconds")},
        "blocks": blocks,
    }


# --------------------------------------------------------------------- main

def write_json(name: str, payload: dict) -> str:
    os.makedirs(OUTPUT, exist_ok=True)
    path = os.path.join(OUTPUT, name)
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    log(f"  wrote {name} ({os.path.getsize(path) / 1e6:.1f} MB)")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore local cache (CI always refetches recent months anyway)")
    args = parser.parse_args()
    use_cache = not args.no_cache
    now = datetime.now()

    log("[1/6] Building geocoder from EAS address points...")
    gc = build_geocoder(use_cache)

    log(f"[2/6] Fetching {args.months} months of citations...")
    rows = fetch_citations(args.months, now, use_cache)
    log(f"  total: {len(rows):,} rows")

    log("[3/6] Geocoding + aggregating risk grid...")
    risk, shards = build_risk_grid(rows, gc, args.months, now)
    write_json("risk_grid.json", risk)

    shard_dir = os.path.join(OUTPUT, "details")
    os.makedirs(shard_dir, exist_ok=True)
    shard_bytes = 0
    for tile_key, payload in shards.items():
        path = os.path.join(shard_dir, f"{tile_key}.json")
        with open(path, "w") as f:
            json.dump(payload, f, separators=(",", ":"))
        shard_bytes += os.path.getsize(path)
    log(f"  wrote {len(shards):,} detail shards ({shard_bytes / 1e6:.1f} MB total)")

    log("[4/6] Building street sweeping schedule...")
    sweeping = build_sweeping(now)
    log(f"  {len(sweeping['blocks']):,} blockfaces")
    write_json("sweeping.json", sweeping)

    log("[5/6] Building metered blocks...")
    meters = build_meters(now)
    log(f"  {len(meters['blocks']):,} metered block-sides")
    write_json("meters.json", meters)

    log("[6/7] Building parking regulations (RPP / time limits)...")
    regulations = build_regulations(now)
    log(f"  {len(regulations['blocks']):,} regulated blockfaces")
    write_json("regulations.json", regulations)

    log("[7/7] Building SFMTA Digital Curb rule shards...")
    os.makedirs(CACHE, exist_ok=True)
    curb_shards = curb_rules.build_curb_shards(
        CACHE, LAT_STEP, LON_STEP, TILE_FACTOR, log)
    curb_dir = os.path.join(OUTPUT, "curb")
    os.makedirs(curb_dir, exist_ok=True)
    curb_bytes = 0
    for tile_key, payload in curb_shards.items():
        path = os.path.join(curb_dir, f"{tile_key}.json")
        with open(path, "w") as f:
            json.dump(payload, f, separators=(",", ":"))
        curb_bytes += os.path.getsize(path)
    log(f"  wrote {len(curb_shards):,} curb shards ({curb_bytes / 1e6:.1f} MB total)")

    manifest = {
        "version": 4,
        "generatedAt": now.isoformat(timespec="seconds"),
        "dataThrough": risk["meta"]["dataThrough"],
        "sources": [categories.SOURCE_PARKING_SFMTA],
        "files": {
            "riskGrid": "risk_grid.json",
            "sweeping": "sweeping.json",
            "meters": "meters.json",
            "regulations": "regulations.json",
            "detailsDir": "details",
            "curbDir": "curb",
        },
    }
    write_json("manifest.json", manifest)
    log("Done.")


if __name__ == "__main__":
    main()
