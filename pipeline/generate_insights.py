#!/usr/bin/env python3
"""Generate pre-computed parking insight baselines for the iOS bundle.

Reads risk_grid.json + detail shards and emits insights.json with template
summaries per block. When OPENAI_API_KEY is set, optionally refines copy via LLM.

Run: python3 generate_insights.py [--min-citations 10]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "output")

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def log(msg: str) -> None:
    print(msg, flush=True)


def peak_from_ch(ch: dict | None) -> tuple[int | None, int | None]:
    if not ch:
        return None, None
    best_how, best_n = None, 0
    for _cat, bands in ch.items():
        for h_raw, n in (bands or {}).items():
            h = int(h_raw)
            if n > best_n:
                best_how, best_n = h, n
    if best_how is None:
        return None, None
    return best_how // 24, best_how % 24


def concern_level(count: int) -> str:
    if count >= 20:
        return "high"
    if count >= 10:
        return "moderate"
    if count >= 3:
        return "low"
    return "minimal"


def template_summary(street: str, block: int, total: int, ch: dict | None, rules: list) -> str:
    name = street.title()
    parts = [f"{block} block of {name}: {total} tickets in the last year."]
    wd, hr = peak_from_ch(ch)
    if wd is not None and hr is not None:
        parts.append(f"Peak enforcement around {WEEKDAYS[wd]} {hr}:00–{hr + 1}:00.")
    kinds = {r.get("k") for r in rules or []}
    if "meter" in kinds:
        parts.append("Metered spaces on at least one side — pay during posted hours.")
    if "rpp" in kinds or "tl" in kinds:
        parts.append("Residential/time-limit rules apply — watch move-by times.")
    if any(r.get("k") == "np" for r in rules or []):
        parts.append("Tow-away or no-parking windows are posted.")
    return " ".join(parts)


def maybe_llm_refine(entry: dict) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return entry
    try:
        import urllib.request

        prompt = (
            "Rewrite this SF parking block summary in 2 plain sentences for a driver "
            "who cannot read signs. Keep facts only:\n"
            + entry["baselineSummary"]
        )
        body = json.dumps(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 120,
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        if text:
            entry["baselineSummary"] = text
    except Exception as exc:
        log(f"  LLM skip ({entry['blockKey']}): {exc}")
    return entry


def build_insights(risk: dict, min_citations: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for cell in risk.get("cells") or []:
        for street in cell.get("s") or []:
            name = street["n"]
            block = street["b"]
            total = street.get("t") or 0
            if total < min_citations:
                continue
            key = f"{name}|{block}"
            if key in seen:
                continue
            seen.add(key)
            ch = street.get("ch")
            rules = street.get("rules") or []
            concerns: dict[str, str] = {}
            for cat_raw, n in (street.get("c") or {}).items():
                if int(n) >= 3:
                    concerns[cat_raw] = concern_level(int(n))
            entry = {
                "blockKey": key,
                "baselineSummary": template_summary(name, block, total, ch, rules),
                "concernLevel": concerns or None,
            }
            out.append(maybe_llm_refine(entry))
    out.sort(key=lambda e: e["blockKey"])
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-citations", type=int, default=10)
    args = parser.parse_args()

    risk_path = os.path.join(OUTPUT, "risk_grid.json")
    if not os.path.exists(risk_path):
        log(f"missing {risk_path} — run run_pipeline.py first")
        return 1

    with open(risk_path) as f:
        risk = json.load(f)

    insights = build_insights(risk, args.min_citations)
    out_path = os.path.join(OUTPUT, "insights.json")
    with open(out_path, "w") as f:
        json.dump(insights, f, indent=2)
    log(f"Wrote {len(insights):,} insight baselines → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
