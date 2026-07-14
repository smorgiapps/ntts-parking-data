#!/usr/bin/env python3
"""Validate pre-bound street rules on known SF blocks (CI harness)."""

import json
import os
import sys

import street_rules

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "output")


def main() -> int:
    path = os.path.join(OUTPUT, "risk_grid.json")
    if not os.path.exists(path):
        print(f"missing {path} — run run_pipeline.py first", file=sys.stderr)
        return 1
    with open(path) as f:
        risk = json.load(f)
    errors = street_rules.validate_known_blocks(risk)
    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    block_errors = street_rules.validate_block_side_rules(risk)
    if block_errors:
        for err in block_errors:
            print(f"FAIL: {err}")
        return 1
    print("OK: validation passed (900 Pine / 1400 Pine / 800 Taylor / 800 Bush)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
