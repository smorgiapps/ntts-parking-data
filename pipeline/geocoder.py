"""Street-address geocoder backed by SF's Enterprise Addressing System (EAS).

The SFMTA citation dataset ships raw address strings ("244 04TH ST",
"1125 VALENICA STREET") with no coordinates, so we resolve them against the
~390k EAS address points: exact street match first, then fuzzy match to absorb
officer typos, then nearest address number on the block.
"""

import bisect
import difflib
import re
from collections import defaultdict

# Common spelled-out or variant street types -> EAS abbreviations
STREET_TYPE_ALIASES = {
    "STREET": "ST", "ST": "ST", "STR": "ST",
    "AVENUE": "AVE", "AVE": "AVE", "AV": "AVE",
    "BOULEVARD": "BLVD", "BLVD": "BLVD", "BL": "BLVD",
    "DRIVE": "DR", "DR": "DR",
    "COURT": "CT", "CT": "CT",
    "PLACE": "PL", "PL": "PL",
    "LANE": "LN", "LN": "LN",
    "ROAD": "RD", "RD": "RD",
    "TERRACE": "TER", "TER": "TER", "TERR": "TER",
    "CIRCLE": "CIR", "CIR": "CIR",
    "WAY": "WAY", "WY": "WAY",
    "ALLEY": "ALY", "ALY": "ALY",
    "HIGHWAY": "HWY", "HWY": "HWY",
    "PLAZA": "PLZ", "PLZ": "PLZ",
    "PARK": "PARK",
    "WALK": "WALK",
    "LOOP": "LOOP",
    "ROW": "ROW",
}

_NUMBERED = re.compile(r"^(\d+)(ST|ND|RD|TH)$")
_LOCATION = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def _norm_street(name: str) -> str:
    """Uppercase, collapse spaces, zero-pad numbered streets (4TH -> 04TH)."""
    name = re.sub(r"\s+", " ", name.upper().strip())
    parts = name.split(" ")
    m = _NUMBERED.match(parts[0])
    if m and len(m.group(1)) == 1:
        parts[0] = "0" + parts[0]
    return " ".join(parts)


class Geocoder:
    def __init__(self):
        # (street, type) -> sorted list of (address_number, lat, lon)
        self._by_street_type: dict[tuple[str, str], list] = defaultdict(list)
        # street -> set of types that exist
        self._types_for_street: dict[str, set] = defaultdict(set)
        self._fuzzy_cache: dict[str, str | None] = {}
        self._street_names: list[str] = []

    def add_address(self, row: dict):
        try:
            num = int(row["address_number"])
            lat = float(row["latitude"])
            lon = float(row["longitude"])
        except (KeyError, ValueError, TypeError):
            return
        street = _norm_street(row.get("street_name") or "")
        stype = (row.get("street_type") or "").upper().strip()
        if not street:
            return
        self._by_street_type[(street, stype)].append((num, lat, lon))
        self._types_for_street[street].add(stype)

    def finalize(self):
        for key in self._by_street_type:
            self._by_street_type[key].sort()
        self._street_names = sorted(self._types_for_street.keys())

    def geocode(self, location: str) -> tuple[float, float] | None:
        r = self.geocode_full(location)
        return (r[0], r[1]) if r else None

    def geocode_full(self, location: str) -> tuple[float, float, str, int] | None:
        """Returns (lat, lon, canonical street label e.g. 'PINE ST', block number)."""
        m = _LOCATION.match(location or "")
        if not m:
            return None
        num = int(m.group(1))
        rest = _norm_street(m.group(2))

        street, stype = self._split_street_type(rest)
        resolved = self._resolve_street(street)
        if resolved is None:
            return None
        hit = self._lookup(resolved, stype, num)
        if hit is None:
            return None
        lat, lon, used_type = hit
        label = f"{resolved} {used_type}".strip()
        return lat, lon, label, (num // 100) * 100

    def _split_street_type(self, rest: str) -> tuple[str, str | None]:
        parts = rest.split(" ")
        if len(parts) >= 2 and parts[-1] in STREET_TYPE_ALIASES:
            return " ".join(parts[:-1]), STREET_TYPE_ALIASES[parts[-1]]
        return rest, None

    def _resolve_street(self, street: str) -> str | None:
        if street in self._types_for_street:
            return street
        if street in self._fuzzy_cache:
            return self._fuzzy_cache[street]
        match = difflib.get_close_matches(street, self._street_names, n=1, cutoff=0.82)
        result = match[0] if match else None
        self._fuzzy_cache[street] = result
        return result

    def _lookup(self, street: str, stype: str | None, num: int) -> tuple[float, float, str] | None:
        types = self._types_for_street[street]
        candidates = []
        if stype and stype in types:
            candidates = [stype]
        else:
            # No/unknown type: try every type this street name has, prefer the
            # one whose address range actually contains the number.
            candidates = list(types)

        best = None  # (distance, lat, lon, street_type)
        for t in candidates:
            pts = self._by_street_type.get((street, t))
            if not pts:
                continue
            i = bisect.bisect_left(pts, (num,))
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(pts):
                    d = abs(pts[j][0] - num)
                    if best is None or d < best[0]:
                        best = (d, pts[j][1], pts[j][2], t)
        # Reject matches more than ~2 blocks of house numbers away.
        if best is None or best[0] > 200:
            return None
        return best[1], best[2], best[3]
