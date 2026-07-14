"""Bind parking rules to streets inside each risk-grid cell (pipeline v5).

Rules are attributed offline using tight spatial matching, segment-quality
filters, citation corroboration, and schedule deduplication. The iOS app
displays only the pre-bound ``rules`` list on each street entry — no runtime
geometry matching.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict

import categories
from geocoder import Geocoder, address_number

# Spatial binding — parallel SF streets are ~25 m apart.
TIGHT_M = 25.0
WEAK_M = 40.0
# Tow-away corridors (commuter lanes) may be encoded as many short curb segments.
STREET_NP_REACH_M = 40.0
METER_REACH_M = 50.0
MIN_AGGREGATE_NP_M = 25.0

# Segment quality (Digital Curb micro-zones).
MIN_NP_SEGMENT_M = 15.0

# Citations in matching hour-band needed to mark a tow-away "confirmed".
CORROBORATION_MIN = 2

_SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR",
    "COURT": "CT", "PLACE": "PL", "LANE": "LN", "ROAD": "RD",
    "TERRACE": "TER", "CIRCLE": "CIR", "ALLEY": "ALY", "HIGHWAY": "HWY",
    "PLAZA": "PLZ",
}


def canonical_street(name: str) -> str:
    words = name.upper().split()
    if words and words[-1] in _SUFFIX:
        words[-1] = _SUFFIX[words[-1]]
    return " ".join(words)


def _meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot((a[1] - b[1]) * 85_000, (a[0] - b[0]) * 111_000)


def _segment_length(line: list) -> float:
    total = 0.0
    for i in range(len(line) - 1):
        total += _meters((line[i][0], line[i][1]), (line[i + 1][0], line[i + 1][1]))
    return total


def _point_line_dist(point: tuple[float, float], line: list) -> float:
    if not line:
        return 1e9
    if len(line) == 1:
        return _meters(point, (line[0][0], line[0][1]))
    best = 1e9
    px, py = point[1], point[0]
    m_per_lat = 111_000.0
    m_per_lon = math.cos(point[0] * math.pi / 180) * 111_320.0

    def proj(c):
        return ((c[1] - px) * m_per_lon, (c[0] - py) * m_per_lat)

    pp = proj(point)
    bp = [proj(c) for c in line]
    for i in range(len(bp) - 1):
        s0, s1 = bp[i], bp[i + 1]
        dx, dy = s1[0] - s0[0], s1[1] - s0[1]
        lsq = dx * dx + dy * dy
        if lsq == 0:
            best = min(best, math.hypot(pp[0] - s0[0], pp[1] - s0[1]))
            continue
        t = max(0.0, min(1.0, ((pp[0] - s0[0]) * dx + (pp[1] - s0[1]) * dy) / lsq))
        best = min(best, math.hypot(pp[0] - (s0[0] + t * dx), pp[1] - (s0[1] + t * dy)))
    return best


def _closest_cell_street(line: list, geocodes: dict[str, list[float]]) -> tuple[str | None, float]:
    best_name, best_d = None, 1e9
    for name, g in geocodes.items():
        d = _point_line_dist((g[0], g[1]), line)
        if d < best_d:
            best_name, best_d = name, d
    return best_name, best_d


def _days_label(days: list[int]) -> str:
    if not days or len(days) == 7:
        return "Daily"
    if days == [0, 1, 2, 3, 4]:
        return "Mon–Fri"
    if days == [0, 1, 2, 3, 4, 5]:
        return "Mon–Sat"
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return ",".join(names[d] for d in days)


def _schedule_key(rule: dict) -> tuple:
    return (
        rule.get("s"), rule.get("k"),
        tuple(rule.get("d") or []),
        rule.get("f"), rule.get("t"),
        rule.get("m"), rule.get("rpp"), rule.get("lim"),
        rule.get("side"),
    )


def _day_sweep_overlaps(day: int, f: int, t: int, sweeps: list[dict]) -> bool:
    return any(
        s["day"] == day and f < s["to"] * 60 and t > s["from"] * 60
        for s in sweeps
    )


def _should_drop_morning_np_as_sweep(
    days: list[int], f: int, t: int, sweeps: list[dict],
    *, has_afternoon_commute: bool = False,
) -> bool:
    """Drop weekday-morning np rows that mostly duplicate street-sweeping schedules."""
    if f >= 660:
        return False
    if has_afternoon_commute and f < 600:
        return True
    if not sweeps:
        return False
    rule_days = [d for d in (days or list(range(5))) if d < 5]
    if len(rule_days) < 2:
        return False
    overlap = sum(1 for day in rule_days if _day_sweep_overlaps(day, f, t, sweeps))
    return overlap >= max(len(rule_days) - 1, 3)


def _coincides_with_sweeping(days: list[int], f: int | None, t: int | None,
                             sweeps: list[dict]) -> bool:
    if f is None or t is None or f > t:
        return False
    rule_days = days if days else list(range(7))
    return all(
        any(s["day"] == day and f < s["to"] * 60 and t > s["from"] * 60 for s in sweeps)
        for day in rule_days
    )


def _min_dist_to_refs(line: list, refs: list[tuple[float, float]]) -> float:
    return min(_point_line_dist(p, line) for p in refs)


def _corroboration_count(street_ch: dict, kind: str, days: list[int],
                         f: int | None, t: int | None) -> int:
    """Count citations on this street during the rule window."""
    if kind == "np":
        cats = (categories.TOW_HAZARD, categories.PROHIBITED)
        # Do not count street-sweeping tickets as tow-away corroboration.
    elif kind in ("ms", "zone"):
        cats = (categories.COLOR_ZONE, categories.PROHIBITED, categories.METER)
    elif kind in ("rpp", "tl"):
        cats = (categories.RESIDENTIAL_PERMIT, categories.PROHIBITED)
    elif kind == "sweep":
        cats = (categories.STREET_CLEANING,)
    else:
        cats = (categories.OTHER,)

    rule_days = set(days if days else range(7))
    total = 0
    for cat in cats:
        for hour_str, n in (street_ch.get(cat) or {}).items():
            hw = int(hour_str)
            day, hour = hw // 24, hw % 24
            if day not in rule_days:
                continue
            mins = hour * 60
            if f is None or t is None:
                total += n
            elif f <= t:
                if f <= mins < t:
                    total += n
            elif mins >= f or mins < t:
                total += n
    return total


def load_detail_shards(output_dir: str) -> dict[str, dict]:
    """Load on-disk citation detail shards for side-aware corroboration."""
    details = os.path.join(output_dir, "details")
    shards: dict[str, dict] = {}
    if not os.path.isdir(details):
        return shards
    for fname in os.listdir(details):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(details, fname)) as f:
            import json
            shards[fname[:-5]] = json.load(f)
    return shards


def _build_citation_index(shards: dict[str, dict]) -> dict[tuple[str, str], list]:
    index: dict[tuple[str, str], list] = defaultdict(list)
    for payload in shards.values():
        streets = payload.get("streets") or []
        for cell_key, rows in (payload.get("cells") or {}).items():
            for row in rows:
                if len(row) >= 5 and isinstance(row[3], int) and row[3] < len(streets):
                    index[(cell_key, streets[row[3]])].append(row)
    return index


_COMPASS_FULL = {"N": "North", "S": "South", "E": "East", "W": "West"}


def _derive_parity_map(
    geocoder: Geocoder | None,
    street_name: str,
    block: int,
    geocode: list[float],
    street_sweeps: list[dict],
) -> dict[str, str] | None:
    """Map odd/even address parity to compass curb side from EAS geometry."""
    if geocoder is None or not geocode or len(geocode) < 2:
        return None
    odd_c, even_c = geocoder.block_parity_centroids(street_name, block)
    if not odd_c or not even_c:
        return None

    has_ns = any((s.get("side") or "").startswith(("N", "S")) for s in street_sweeps)
    has_ew = any((s.get("side") or "").startswith(("E", "W")) for s in street_sweeps)
    lat_sep = abs(odd_c[0] - even_c[0])
    lon_sep = abs(odd_c[1] - even_c[1])

    if (has_ns or not has_ew) and lat_sep >= lon_sep and lat_sep > 1e-6:
        if odd_c[0] > even_c[0]:
            return {"o": "N", "e": "S"}
        if odd_c[0] < even_c[0]:
            return {"o": "S", "e": "N"}
    if has_ew and lon_sep >= lat_sep and lon_sep > 1e-6:
        if odd_c[1] > even_c[1]:
            return {"o": "E", "e": "W"}
        if odd_c[1] < even_c[1]:
            return {"o": "W", "e": "E"}
    return None


def _parity_compass_side(number: int, parity_map: dict[str, str] | None) -> str | None:
    if not parity_map:
        return None
    key = "o" if number % 2 else "e"
    c = parity_map.get(key)
    return _COMPASS_FULL.get(c, c) if c else None


def _convert_meter_parity_side(side_str: str | None, parity_map: dict[str, str] | None) -> str | None:
    if not side_str or not parity_map:
        return side_str
    s = side_str.lower()
    if s.startswith("odd"):
        c = parity_map.get("o")
    elif s.startswith("even"):
        c = parity_map.get("e")
    else:
        return side_str
    return _COMPASS_FULL.get(c, side_str) if c else side_str


def _side_corroboration_count(
    citations: list,
    parity_map: dict[str, str] | None,
    rule_side: str | None,
    kind: str,
    days: list[int],
    f: int | None,
    t: int | None,
) -> int:
    """Count citations whose address parity matches the rule's curb side."""
    if not parity_map or not rule_side or not citations:
        return 0
    rule_letter = rule_side.strip()[0].upper()

    if kind == "np":
        cats = {categories.TOW_HAZARD, categories.PROHIBITED}
    elif kind == "sweep":
        cats = {categories.STREET_CLEANING}
    elif kind == "meter":
        cats = {categories.METER}
    else:
        return 0

    rule_days = set(days if days else range(7))
    total = 0
    for row in citations:
        if len(row) < 5:
            continue
        cat = row[1]
        if cat not in cats:
            continue
        location_raw = row[8] if len(row) >= 9 else None
        num = address_number(location_raw or "")
        if num is None:
            continue
        side = _parity_compass_side(num, parity_map)
        if not side or side[0].upper() != rule_letter:
            continue
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(row[0])
            day, hour = ts.weekday(), ts.hour
        except (ValueError, TypeError):
            continue
        if day not in rule_days:
            continue
        mins = hour * 60
        if f is None or t is None:
            total += 1
        elif f <= t:
            if f <= mins < t:
                total += 1
        elif mins >= f or mins < t:
            total += 1
    return total


def _apply_side_corroboration(
    entry: dict,
    citations: list,
    parity_map: dict[str, str] | None,
    kind: str,
    days: list[int],
    f: int | None,
    t: int | None,
) -> None:
    """Replace block-wide corroboration with side-matched counts for sided rules."""
    side = entry.get("side")
    if not side or not parity_map:
        return
    corr = _side_corroboration_count(citations, parity_map, side, kind, days, f, t)
    entry["n"] = corr
    if corr < CORROBORATION_MIN:
        entry["x"] = "posted"
    elif corr >= CORROBORATION_MIN:
        entry["x"] = "confirmed"


def _confidence(*, dist: float, seg_len: float, kind: str,
                days: list[int], f: int | None, t: int | None,
                corroboration: int, is_reg: bool) -> str | None:
    """Return 'confirmed', 'posted', or None (hide)."""
    if kind in ("rpp", "tl", "sweep", "meter"):
        if kind == "meter":
            if dist <= METER_REACH_M:
                return "confirmed" if corroboration >= CORROBORATION_MIN else "posted"
            return None
        reg_reach = 45.0 if is_reg or kind in ("rpp", "tl") else TIGHT_M
        if dist <= reg_reach:
            return "confirmed" if corroboration >= CORROBORATION_MIN else "posted"
        if dist <= WEAK_M and corroboration >= CORROBORATION_MIN:
            return "confirmed"
        return None

    # Tow-away / max-stay from Digital Curb.
    if kind == "np":
        if f is None or t is None:
            return None  # never surface all-day "Daily" tow-away segments
        # Single-segment check OR aggregate corridor length (commuter lanes).
        if seg_len < MIN_NP_SEGMENT_M and seg_len < MIN_AGGREGATE_NP_M:
            return None
        if dist > STREET_NP_REACH_M:
            return None
        if corroboration >= CORROBORATION_MIN:
            return "confirmed"
        if dist <= STREET_NP_REACH_M and seg_len >= MIN_AGGREGATE_NP_M:
            return "posted"
        if dist <= TIGHT_M:
            return "posted"
        return None

    if kind == "ms":
        if seg_len < MIN_NP_SEGMENT_M and dist > TIGHT_M:
            return None
        if corroboration >= CORROBORATION_MIN or dist <= TIGHT_M:
            return "confirmed" if corroboration >= CORROBORATION_MIN else "posted"
        return None

    return "posted" if dist <= TIGHT_M else None


def _merge_weekday_morning_np(
    groups: dict[tuple, dict],
) -> dict[tuple, dict]:
    """Merge per-day morning np segments into a Mon–Fri commuter window.

    Digital Curb often encodes Bush-style commuter lanes as Tue 7–8, Wed 6–7,
    etc. instead of one Mon–Fri row.
    """
    singles = [
        (sched, g) for sched, g in groups.items()
        if len(sched[0]) == 1 and sched[1] is not None and sched[2] is not None
        and sched[1] < 660 and sched[2] <= 660
    ]
    weekdays_present = {sched[0][0] for sched, _ in singles if sched[0][0] < 5}
    if len(weekdays_present) < 3:
        return groups

    f = min(g["f"] for _, g in singles)
    t = max(g["t"] for _, g in singles)
    total_len = sum(g["seg_len"] for _, g in singles)
    min_dist = min(g["dist"] for _, g in singles)
    max_n = max(g.get("n", 0) for _, g in singles)
    merged_key = (tuple(range(5)), f, t)
    merged = {
        "f": f, "t": t, "seg_len": total_len, "dist": min_dist,
        "n": max_n, "d": list(range(5)),
    }
    # Drop the single-day rows absorbed into Mon–Fri.
    out = {k: v for k, v in groups.items() if len(k[0]) != 1 or k[0][0] not in weekdays_present}
    out[merged_key] = merged
    return out


def _meter_side_refs(meters: list[dict]) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    odd = even = None
    for mb in meters:
        lat, lon = mb.get("lat"), mb.get("lon")
        if lat is None or lon is None:
            continue
        pt = (lat, lon)
        s = (mb.get("s") or "").lower()
        if s.startswith("odd"):
            odd = pt
        elif s.startswith("even"):
            even = pt
    return odd, even


def _infer_side_from_meters(
    mid: tuple[float, float],
    odd: tuple[float, float] | None,
    even: tuple[float, float] | None,
) -> str | None:
    """Odd/even meter rows approximate north/south blockfaces on E–W streets."""
    if not odd or not even:
        return None
    d_odd = _meters(mid, odd)
    d_even = _meters(mid, even)
    if abs(d_odd - d_even) < 8:
        return None
    return "North" if d_odd < d_even else "South"


def _collect_aggregated_np(
    cell_zones: list[dict],
    name: str,
    geocodes: dict[str, list[float]],
    primary_refs: list[tuple[float, float]],
    corridor_refs: list[tuple[float, float]],
    street_sweeps: list[dict],
) -> dict[tuple, dict]:
    """Group no-parking curb segments into schedule buckets for one street."""
    groups: dict[tuple, dict] = {}
    sweep_windows = [
        {"day": s["day"], "from": s["from"], "to": s["to"]} for s in street_sweeps
    ]

    for zone in cell_zones:
        line = zone.get("line") or []
        if len(line) < 2:
            continue
        closest, _ = _closest_cell_street(line, geocodes)
        if closest != name:
            continue
        for cr in zone.get("rules") or []:
            if cr.get("a") != "np":
                continue
            days = tuple(cr.get("d") or list(range(7)))
            f, t = cr.get("f"), cr.get("t")
            if f is None or t is None:
                continue
            # Morning commuter lanes need corridor sampling; afternoon tow-away
            # must stay tight to the block geocode to avoid cross-street bleed.
            reach_refs = corridor_refs if f < 660 else primary_refs
            dist = _min_dist_to_refs(line, reach_refs)
            if dist > STREET_NP_REACH_M:
                continue
            if f >= 660 and _min_dist_to_refs(line, primary_refs) > TIGHT_M:
                continue
            seg_len = _segment_length(line)
            key = (days, f, t)
            mid = line[len(line) // 2]
            mid_pt = (mid[0], mid[1]) if len(mid) >= 2 else None
            bucket = groups.setdefault(key, {
                "d": list(days), "f": f, "t": t,
                "seg_len": 0.0, "dist": dist, "mids": [],
            })
            bucket["seg_len"] += seg_len
            bucket["dist"] = min(bucket["dist"], dist)
            if mid_pt:
                bucket["mids"].append(mid_pt)

    groups = _merge_weekday_morning_np(groups)

    # Drop short buckets that only mirror street-sweeping re-encodes.
    filtered: dict[tuple, dict] = {}
    for key, g in groups.items():
        days, f, t = key
        if (g["seg_len"] < MIN_AGGREGATE_NP_M
                and _coincides_with_sweeping(list(days), f, t, sweep_windows)):
            continue
        filtered[key] = g
    return filtered


def _coalesce_weekday_morning_np(rules: list[dict]) -> list[dict]:
    """Merge scattered weekday-morning tow-away rows into one Mon–Fri corridor."""
    morning = [
        r for r in rules
        if r.get("k") == "np" and r.get("f") is not None and r.get("t") is not None
        and r["f"] < 660 and all(d < 5 for d in (r.get("d") or []))
    ]
    if len(morning) < 2:
        return rules

    weekdays = {d for r in morning for d in (r.get("d") or [])}
    total_n = sum(r.get("n", 0) for r in morning)
    if len(weekdays) < 3 and total_n < 20:
        return rules

    f = min(r["f"] for r in morning)
    t = max(r["t"] for r in morning)
    conf = "confirmed" if any(r.get("x") == "confirmed" for r in morning) else morning[0].get("x")
    merged = {
        **morning[0],
        "d": list(range(5)),
        "f": f,
        "t": t,
        "x": conf,
        "n": max(r.get("n", 0) for r in morning),
    }
    rest = [r for r in rules if r not in morning]
    return rest + [merged]


def _merge_np_windows(rules: list[dict]) -> list[dict]:
    """Merge overlapping no-parking windows on the same day-set."""
    nps = [r for r in rules if r.get("k") == "np" and r.get("f") is not None and r.get("t") is not None]
    rest = [r for r in rules if r not in nps]
    merged: list[dict] = []
    used = set()
    for i, a in enumerate(nps):
        if i in used:
            continue
        days = tuple(a.get("d") or [])
        f, t = a["f"], a["t"]
        conf = a.get("x")
        n = a.get("n", 0)
        for j, b in enumerate(nps):
            if j <= i or j in used:
                continue
            if tuple(b.get("d") or []) != days:
                continue
            if b["f"] <= t + 60 and b["t"] >= f - 60:
                f, t = min(f, b["f"]), max(t, b["t"])
                conf = "confirmed" if "confirmed" in (conf, b.get("x")) else conf
                n = max(n, b.get("n", 0))
                used.add(j)
        used.add(i)
        merged.append({**a, "f": f, "t": t, "x": conf, "n": n})
    return rest + merged


def _dedupe_rules(rules: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in rules:
        key = _schedule_key(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out = _coalesce_weekday_morning_np(_merge_np_windows(out))
    sweeps = [r for r in out if r.get("k") == "sweep"]
    sweep_windows = [
        {"day": d, "from": r["f"] // 60, "to": r["t"] // 60}
        for r in sweeps for d in (r.get("d") or [])
        if r.get("f") is not None and r.get("t") is not None
    ]
    if sweep_windows:
        has_afternoon_commute = any(
            r.get("k") == "np" and (r.get("f") or 0) >= 900 for r in out
        )
        out = [
            r for r in out
            if not (
                r.get("k") == "np"
                and r.get("f") is not None
                and _should_drop_morning_np_as_sweep(
                    r.get("d") or [], r["f"], r.get("t") or r["f"], sweep_windows,
                    has_afternoon_commute=has_afternoon_commute,
                )
            )
        ]
    # Keep one meter row per side + schedule (prefer highest corroboration).
    meters: dict[tuple, dict] = {}
    rest: list[dict] = []
    for r in out:
        if r.get("k") != "meter":
            rest.append(r)
            continue
        key = (r.get("side"), tuple(r.get("d") or []), r.get("f"), r.get("t"), r.get("m"))
        prev = meters.get(key)
        if prev is None or (r.get("n") or 0) > (prev.get("n") or 0):
            meters[key] = r
    return rest + list(meters.values())


def _grid_key(lat: float, lon: float, lat_step: float, lon_step: float) -> tuple[int, int]:
    return math.floor(lat / lat_step), math.floor(lon / lon_step)


def _neighbor_keys(row: int, col: int) -> list[tuple[int, int]]:
    return [(row + dr, col + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)]


def bind_street_rules(risk: dict, regulations: dict, sweeping: dict,
                      meters: dict, curb_shards: dict[str, dict], log,
                      lat_step: float = 0.001, lon_step: float = 0.00125,
                      tile_factor: int = 5,
                      geocoder: Geocoder | None = None,
                      detail_shards: dict[str, dict] | None = None) -> dict:
    """Attach a ``rules`` array to every street entry in the risk grid."""
    zones_by_cell: dict[tuple[int, int], list] = defaultdict(list)
    for shard in curb_shards.values():
        for zone in shard.get("zones") or []:
            line = zone.get("line") or []
            if len(line) < 2:
                continue
            mid = line[len(line) // 2]
            zones_by_cell[_grid_key(mid[0], mid[1], lat_step, lon_step)].append(zone)

    reg_blocks = regulations.get("blocks") or []
    sweep_blocks = sweeping.get("blocks") or []
    meter_blocks = meters.get("blocks") or []

    regs_by_cell: dict[tuple[int, int], list] = defaultdict(list)
    for rb in reg_blocks:
        line = rb.get("line") or []
        if not line:
            continue
        mid = line[len(line) // 2]
        regs_by_cell[_grid_key(mid[0], mid[1], lat_step, lon_step)].append(rb)

    # Pre-index sources so binding stays O(cells × streets × local), not O(all data).
    sweep_by_street: dict[str, list] = defaultdict(list)
    for sb in sweep_blocks:
        sweep_by_street[canonical_street(sb.get("corridor") or "")].append(sb)

    meters_by_street: dict[str, list] = defaultdict(list)
    for mb in meter_blocks:
        parts = (mb.get("b") or "").split()
        if len(parts) >= 2:
            meters_by_street[" ".join(parts[:-1])].append(mb)

    cells_bound = streets_bound = 0
    citation_index = _build_citation_index(detail_shards or {})

    for cell in risk.get("cells") or []:
        streets = cell.get("s") or []
        if not streets:
            continue
        geocodes = {s["n"]: s["g"] for s in streets if s.get("g")}
        if not geocodes:
            continue

        cells_bound += 1
        parts = (cell.get("k") or "0_0").split("_")
        row, col = int(parts[0]), int(parts[1])
        cell_key = cell.get("k") or f"{row}_{col}"

        cell_zones: list[dict] = []
        cell_regs: list[dict] = []
        for nk in _neighbor_keys(row, col):
            cell_zones.extend(zones_by_cell.get(nk, []))
            cell_regs.extend(regs_by_cell.get(nk, []))

        for street in streets:
            name = street["n"]
            g = street.get("g")
            if not g:
                continue
            geocode = (g[0], g[1])
            primary_refs = [geocode]
            corridor_refs = [geocode]
            for pt in street.get("gr") or []:
                if len(pt) >= 2:
                    corridor_refs.append((pt[0], pt[1]))
            street_ch = street.get("ch") or {}
            block_num = street.get("b") or 0
            street_sweeps_pre = [
                sb for sb in sweep_by_street.get(name, [])
                if _min_dist_to_refs(sb.get("line") or [], corridor_refs) <= TIGHT_M
            ]
            parity_map = _derive_parity_map(geocoder, name, block_num, g, street_sweeps_pre)
            if parity_map:
                street["sp"] = parity_map
            cell_citations = citation_index.get((cell_key, name), [])
            bound: list[dict] = []

            # --- Street sweeping (name + proximity) ---
            sweep_seen: set[tuple] = set()
            street_sweeps = []
            for sb in sweep_by_street.get(name, []):
                d = _min_dist_to_refs(sb.get("line") or [], corridor_refs)
                if d > TIGHT_M:
                    continue
                street_sweeps.append(sb)
                key = (sb["day"], sb["from"], sb["to"], sb.get("side"))
                if key in sweep_seen:
                    continue
                sweep_seen.add(key)
                days = [sb["day"]]
                corr = _corroboration_count(street_ch, "sweep", days,
                                            sb["from"] * 60, sb["to"] * 60)
                conf = _confidence(dist=d, seg_len=999, kind="sweep", days=days,
                                   f=sb["from"] * 60, t=sb["to"] * 60,
                                   corroboration=corr, is_reg=False)
                if not conf:
                    continue
                entry = {
                    "s": "sweep", "k": "sweep",
                    "d": days, "f": sb["from"] * 60, "t": sb["to"] * 60,
                    "side": sb.get("side"), "x": conf,
                }
                if corr >= CORROBORATION_MIN:
                    entry["n"] = corr
                _apply_side_corroboration(
                    entry, cell_citations, parity_map, "sweep", days,
                    sb["from"] * 60, sb["to"] * 60)
                bound.append(entry)

            # --- Meters (street name in block label + proximity) ---
            for mb in meters_by_street.get(name, []):
                block_label = mb.get("b") or ""
                parts = block_label.split()
                if len(parts) < 2:
                    continue
                pt = (mb.get("lat"), mb.get("lon"))
                if pt[0] is None:
                    continue
                d = min(_meters(ref, pt) for ref in corridor_refs)
                if d > METER_REACH_M:
                    continue
                days = _parse_meter_days(mb.get("days") or "")
                f, t = _parse_meter_hours(mb.get("from") or "", mb.get("to") or "")
                corr = _corroboration_count(street_ch, "meter", days, f, t)
                conf = _confidence(dist=d, seg_len=999, kind="meter", days=days,
                                   f=f, t=t, corroboration=corr, is_reg=False)
                if not conf:
                    continue
                meter_side = _convert_meter_parity_side(mb.get("s"), parity_map)
                entry: dict = {
                    "s": "meter", "k": "meter",
                    "d": days, "x": conf,
                    "side": meter_side, "cnt": mb.get("n"),
                }
                if f is not None:
                    entry["f"] = f
                if t is not None:
                    entry["t"] = t
                limit = _parse_meter_limit(mb.get("limit") or "")
                if limit:
                    entry["m"] = limit
                if corr >= CORROBORATION_MIN:
                    entry["n"] = corr
                _apply_side_corroboration(
                    entry, cell_citations, parity_map, "meter", days, f, t)
                bound.append(entry)

            # --- Regulations (within reach of this street's block center) ---
            reg_limits_minutes: set[int] = set()
            for rb in cell_regs:
                line = rb.get("line") or []
                d = _min_dist_to_refs(line, primary_refs)
                if d > 45.0:
                    continue
                days = rb.get("days") or list(range(7))
                f_h, t_h = rb.get("from"), rb.get("to")
                f = f_h * 60 if f_h is not None else None
                t = t_h * 60 if t_h is not None else None
                kind_raw = rb.get("kind") or ""
                if kind_raw == "timeLimit":
                    rk = "rpp" if rb.get("rpp") else "tl"
                else:
                    rk = "tl"
                corr = _corroboration_count(street_ch, rk, days, f, t)
                conf = _confidence(dist=d, seg_len=999, kind=rk, days=days,
                                   f=f, t=t, corroboration=corr, is_reg=True)
                if not conf:
                    continue
                entry = {"s": "reg", "k": rk, "d": days, "x": conf}
                if f is not None:
                    entry["f"] = f
                if t is not None:
                    entry["t"] = t
                if rb.get("rpp"):
                    entry["rpp"] = rb["rpp"]
                if rb.get("limit"):
                    entry["lim"] = rb["limit"]
                    reg_limits_minutes.add(int(rb["limit"] * 60))
                if corr >= CORROBORATION_MIN:
                    entry["n"] = corr
                bound.append(entry)

            # --- Digital Curb: aggregate tow-away corridors, then other rules ---
            np_groups = _collect_aggregated_np(
                cell_zones, name, geocodes, primary_refs, corridor_refs, street_sweeps)
            sweep_windows = [
                {"day": sb["day"], "from": sb["from"], "to": sb["to"]}
                for sb in street_sweeps
            ]
            odd_ref, even_ref = _meter_side_refs(meters_by_street.get(name, []))
            has_afternoon_commute = any(
                f >= 900 and t <= 20 * 60 for (_, f, t) in np_groups.keys()
            )
            for (_days, f, t), agg in np_groups.items():
                days = agg["d"]
                # Morning no-parking rows that mostly mirror street sweeping are not tow-away.
                if _should_drop_morning_np_as_sweep(
                    list(days), f, t, sweep_windows,
                    has_afternoon_commute=has_afternoon_commute,
                ):
                    continue
                corr = _corroboration_count(street_ch, "np", days, f, t)
                conf = _confidence(
                    dist=agg["dist"], seg_len=agg["seg_len"], kind="np",
                    days=days, f=f, t=t, corroboration=corr, is_reg=False)
                if not conf:
                    continue
                entry = {"s": "curb", "k": "np", "d": days, "x": conf, "f": f, "t": t}
                mids = agg.get("mids") or []
                if mids:
                    side = _infer_side_from_meters(mids[len(mids) // 2], odd_ref, even_ref)
                    if side:
                        entry["side"] = side
                if f >= 900 and not entry.get("side"):
                    north_days = {
                        sb["day"] for sb in street_sweeps
                        if (sb.get("side") or "").startswith("N")
                    }
                    south_days = {
                        sb["day"] for sb in street_sweeps
                        if (sb.get("side") or "").startswith("S")
                    }
                    if south_days and len(north_days) > len(south_days):
                        entry["side"] = "South"
                if corr >= CORROBORATION_MIN:
                    entry["n"] = corr
                _apply_side_corroboration(
                    entry, cell_citations, parity_map, "np", list(days), f, t)
                bound.append(entry)

            for zone in cell_zones:
                line = zone.get("line") or []
                if len(line) < 2:
                    continue
                closest, dist = _closest_cell_street(line, geocodes)
                if closest != name:
                    continue
                if _min_dist_to_refs(line, corridor_refs) > STREET_NP_REACH_M:
                    continue
                seg_len = _segment_length(line)
                for cr in zone.get("rules") or []:
                    kind = cr.get("a")
                    if kind == "np":
                        continue  # handled above via aggregation
                    days = cr.get("d") or list(range(7))
                    f, t = cr.get("f"), cr.get("t")
                    corr = _corroboration_count(street_ch, kind, days, f, t)
                    conf = _confidence(dist=dist, seg_len=seg_len, kind=kind,
                                       days=days, f=f, t=t,
                                       corroboration=corr, is_reg=False)
                    if not conf:
                        continue
                    if kind == "ms" and cr.get("m") in reg_limits_minutes:
                        continue
                    entry = {"s": "curb", "k": kind, "d": days, "x": conf}
                    if f is not None:
                        entry["f"] = f
                    if t is not None:
                        entry["t"] = t
                    if kind == "ms" and cr.get("m"):
                        entry["m"] = cr["m"]
                    if corr >= CORROBORATION_MIN:
                        entry["n"] = corr
                    bound.append(entry)

            street["rules"] = _dedupe_rules(bound)
            if street["rules"]:
                streets_bound += 1

    log(f"  bound rules on {streets_bound:,} street entries across {cells_bound:,} cells")
    return risk


def _parse_meter_days(raw: str) -> list[int]:
    mp = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
    parsed = [mp[p.strip()[:2].upper()] for p in raw.split(",") if p.strip()[:2].upper() in mp]
    return parsed if parsed else list(range(7))


def _parse_meter_hours(from_s: str, to_s: str) -> tuple[int | None, int | None]:
    def hour(s: str) -> int | None:
        parts = s.split(":")
        if not parts or not parts[0].strip().isdigit():
            return None
        h = int(parts[0])
        if "PM" in s.upper() and h != 12:
            h += 12
        if "AM" in s.upper() and h == 12:
            h = 0
        return h * 60

    return hour(from_s), hour(to_s)


def _parse_meter_limit(raw: str) -> int | None:
    parts = raw.split()
    if parts and parts[0].isdigit():
        return int(parts[0])
    return None


# ------------------------------------------------------------------ validation

_VALIDATION_CELL = "37790_-97930"
_VALIDATION = {
    "TAYLOR ST": {
        "forbid_np_hours": {(15, 18), (15, 19)},  # no Pine commute tow-away
        "require_any": [("rpp", None), ("tl", None)],
    },
    "PINE ST": {
        "require_np_hours": {(15, 18)},  # commute tow-away confirmed
        "forbid_np_hours": {(6, 8), (7, 8)},  # morning = sweeping, not tow-away
        "forbid_allday_np": True,
    },
    "BUSH ST": {
        "forbid_np_hours": {(15, 18), (15, 19)},
        "require_np_hours": {(6, 9), (7, 9)},  # morning commuter tow-away
    },
}


def validate_known_blocks(risk: dict) -> list[str]:
    """Return a list of validation error messages (empty == pass)."""
    errors: list[str] = []
    cell = next((c for c in risk.get("cells") or [] if c.get("k") == _VALIDATION_CELL), None)
    if not cell:
        return [f"validation cell {_VALIDATION_CELL} not found"]

    for street in cell.get("s") or []:
        name = street["n"]
        spec = _VALIDATION.get(name)
        if not spec:
            continue
        rules = street.get("rules") or []
        np_hours = set()
        has_allday_np = False
        kinds = {r.get("k") for r in rules}

        for r in rules:
            if r.get("k") != "np":
                continue
            f, t = r.get("f"), r.get("t")
            if f is None or t is None:
                has_allday_np = True
            else:
                np_hours.add((f // 60, t // 60))

        for fh, th in spec.get("forbid_np_hours") or []:
            if (fh, th) in np_hours or any(fh == a and th == b for a, b in np_hours):
                errors.append(f"{name}: unexpected tow-away {fh}-{th}h")

        for fh, th in spec.get("require_np_hours") or []:
            if not any(abs(a - fh) <= 1 and abs(b - th) <= 1 for a, b in np_hours):
                errors.append(f"{name}: missing expected tow-away ~{fh}-{th}h")

        if spec.get("forbid_allday_np") and has_allday_np:
            errors.append(f"{name}: all-day tow-away should be hidden")

        required = spec.get("require_any") or []
        if required and not any(req[0] in kinds for req in required):
            errors.append(f"{name}: missing required rule kinds {[r[0] for r in required]}")

    return errors


_BLOCK_VALIDATION_CELL = "37789_-97936"
_BLOCK_SIDE_VALIDATION = {
    ("PINE ST", 1400): {
        "require_kinds": {"rpp", "meter", "np", "sweep"},
        "require_np_side": "South",
        "require_meter_side": "North",
        "require_parity": {"e": "N", "o": "S"},
        "north_sweep_weekdays": {0, 2, 4},
        "south_sweep_weekdays": {3},
        "forbid_np_side": "North",
    },
}


def validate_block_side_rules(risk: dict) -> list[str]:
    """Side-specific assertions for known asymmetric blocks (e.g. 1400 Pine)."""
    errors: list[str] = []
    cell = next((c for c in risk.get("cells") or [] if c.get("k") == _BLOCK_VALIDATION_CELL), None)
    if not cell:
        return [f"block validation cell {_BLOCK_VALIDATION_CELL} not found"]

    for street in cell.get("s") or []:
        name = street["n"]
        block = street["b"]
        spec = _BLOCK_SIDE_VALIDATION.get((name, block))
        if not spec:
            continue
        rules = street.get("rules") or []
        kinds = {r.get("k") for r in rules}
        for req in spec.get("require_kinds") or set():
            if req not in kinds:
                errors.append(f"{name} {block}: missing required kind {req}")

        np_sides = {r.get("side") for r in rules if r.get("k") == "np" and r.get("side")}
        if spec.get("require_np_side") and spec["require_np_side"] not in np_sides:
            errors.append(f"{name} {block}: missing tow-away on {spec['require_np_side']} side")
        if spec.get("forbid_np_side") and spec["forbid_np_side"] in np_sides:
            errors.append(f"{name} {block}: unexpected tow-away on {spec['forbid_np_side']} side")

        meter_sides = {r.get("side") for r in rules if r.get("k") == "meter"}
        if spec.get("require_meter_side"):
            req = spec["require_meter_side"]
            if req not in meter_sides and _COMPASS_FULL.get(req, req) not in meter_sides:
                errors.append(f"{name} {block}: missing meter on {req} side")

        parity = street.get("sp") or {}
        req_parity = spec.get("require_parity") or {}
        for key, compass in req_parity.items():
            if parity.get(key) != compass:
                errors.append(
                    f"{name} {block}: parity {key} expected {compass}, got {parity.get(key)}")

        np_south = next(
            (r for r in rules if r.get("k") == "np" and r.get("side") == "South"), None)
        if np_south and parity:
            side_n = np_south.get("n") or 0
            if side_n < 2:
                errors.append(f"{name} {block}: South tow-away under-corroborated (n={side_n})")

        north_days = {
            d for r in rules
            if r.get("k") == "sweep" and r.get("side") == "North"
            for d in (r.get("d") or [])
        }
        south_days = {
            d for r in rules
            if r.get("k") == "sweep" and r.get("side") == "South"
            for d in (r.get("d") or [])
        }
        for day in spec.get("north_sweep_weekdays") or set():
            if day not in north_days:
                errors.append(f"{name} {block}: North sweep missing weekday {day}")
        for day in spec.get("south_sweep_weekdays") or set():
            if day not in south_days:
                errors.append(f"{name} {block}: South sweep missing weekday {day}")

    return errors
