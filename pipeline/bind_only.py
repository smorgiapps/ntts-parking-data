#!/usr/bin/env python3
"""Re-run street-rule binding on an existing pipeline output (fast)."""

import gzip
import json
import os
import sys

import street_rules
from geocoder import Geocoder

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "output")
CACHE = os.path.join(HERE, "cache")
LAT_STEP = 0.001
LON_STEP = 0.00125
TILE_FACTOR = 5


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(name: str) -> dict:
    with open(os.path.join(OUTPUT, name)) as f:
        return json.load(f)


def load_curb_shards() -> dict[str, dict]:
    curb_dir = os.path.join(OUTPUT, "curb")
    shards = {}
    for fname in os.listdir(curb_dir):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(curb_dir, fname)) as f:
            shards[fname[:-5]] = json.load(f)
    return shards


def load_geocoder() -> Geocoder:
    cache_file = os.path.join(CACHE, "addresses.jsonl.gz")
    gc = Geocoder()
    if os.path.exists(cache_file):
        with gzip.open(cache_file, "rt") as f:
            for line in f:
                gc.add_address(json.loads(line))
        gc.finalize()
    return gc


def backfill_block_refs(risk: dict, gc: Geocoder, log_fn) -> int:
    """Add ``gr`` sample points for streets missing corridor refs."""
    added = 0
    for cell in risk.get("cells") or []:
        for street in cell.get("s") or []:
            if street.get("gr") or not street.get("g") or not street.get("b"):
                continue
            block = street["b"]
            name = street["n"]
            extra = []
            for off in (-100, 100):
                hit = gc.geocode_full(f"{block + off} {name}")
                if hit:
                    extra.append([round(hit[0], 5), round(hit[1], 5)])
            if extra:
                street["gr"] = extra
                added += 1
    if added:
        log_fn(f"  backfilled block refs on {added:,} streets")
    return added


def main() -> int:
    log("Loading existing pipeline output...")
    risk = load_json("risk_grid.json")
    regulations = load_json("regulations.json")
    sweeping = load_json("sweeping.json")
    meters = load_json("meters.json")
    curb_shards = load_curb_shards()

    gc = load_geocoder()
    if gc._street_names:
        backfill_block_refs(risk, gc, log)

    log("Binding rules...")
    detail_shards = street_rules.load_detail_shards(OUTPUT)
    risk = street_rules.bind_street_rules(
        risk, regulations, sweeping, meters, curb_shards, log,
        lat_step=LAT_STEP, lon_step=LON_STEP, tile_factor=TILE_FACTOR,
        geocoder=gc, detail_shards=detail_shards)

    errors = street_rules.validate_known_blocks(risk)
    if errors:
        for err in errors:
            log(f"VALIDATION FAIL: {err}")
        return 1
    block_errors = street_rules.validate_block_side_rules(risk)
    if block_errors:
        for err in block_errors:
            log(f"VALIDATION FAIL: {err}")
        return 1
    log("Validation passed.")

    out = os.path.join(OUTPUT, "risk_grid.json")
    with open(out, "w") as f:
        json.dump(risk, f, separators=(",", ":"))
    log(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
