"""Map SFMTA violation descriptions to app-facing risk categories.

Category keys are shared with the iOS app (ParkingRisk.swift). The `source`
dimension exists so speeding citations (SFMTA cameras, CHP highways) can be
added later without schema changes.
"""

SOURCE_PARKING_SFMTA = "parking_sfmta"
# Reserved for future expansion once CPRA data arrives:
SOURCE_SPEED_SFMTA = "speed_sfmta"
SOURCE_SPEED_CHP = "speed_chp"

STREET_CLEANING = "sweep"
METER = "meter"
RESIDENTIAL_PERMIT = "rpp"
COLOR_ZONE = "zone"
TOW_HAZARD = "tow"
PROHIBITED = "prohib"
OTHER = "other"

_EXACT = {
    "STR CLEAN": STREET_CLEANING,
    "STREET CLEANING": STREET_CLEANING,
    "MTR OUT DT": METER,
    "METER DTN": METER,
    "MTR DWNTWN": METER,
    "EXP. METER": METER,
    "EXP METER": METER,
    "PRK METER": METER,
    "RES/OT": RESIDENTIAL_PERMIT,
    "RES OT": RESIDENTIAL_PERMIT,
    "YEL ZONE": COLOR_ZONE,
    "RED ZONE": COLOR_ZONE,
    "WHITE ZONE": COLOR_ZONE,
    "GRN ZONE": COLOR_ZONE,
    "GREEN ZONE": COLOR_ZONE,
    "BLUE ZONE": COLOR_ZONE,
    "TOW AWAY": TOW_HAZARD,
    "TWAWY ZN": TOW_HAZARD,
    "TOWAWAY ZONE": TOW_HAZARD,
    "FIRE HYD": TOW_HAZARD,
    "HYDRANT": TOW_HAZARD,
    "DBL PARK": PROHIBITED,
    "DRIVEWAY": PROHIBITED,
    "ON SIDEWLK": PROHIBITED,
    "SIDEWALK": PROHIBITED,
    "PRK PROHIB": PROHIBITED,
    "PK PHB OTD": PROHIBITED,
    "PRK GRADE": PROHIBITED,
    "PK STANDS": PROHIBITED,
    "CROSSWALK": PROHIBITED,
    "BUS ZONE": PROHIBITED,
    "BIKE LANE": PROHIBITED,
}

_KEYWORDS = [
    ("CLEAN", STREET_CLEANING),
    ("SWEEP", STREET_CLEANING),
    ("METER", METER),
    ("MTR", METER),
    ("RES", RESIDENTIAL_PERMIT),
    ("PERMIT", RESIDENTIAL_PERMIT),
    ("ZONE", COLOR_ZONE),
    ("TOW", TOW_HAZARD),
    ("HYD", TOW_HAZARD),
    ("PARK", PROHIBITED),
    ("PRK", PROHIBITED),
    ("PK ", PROHIBITED),
]


def categorize(violation_desc: str | None) -> str:
    if not violation_desc:
        return OTHER
    desc = violation_desc.upper().strip()
    if desc in _EXACT:
        return _EXACT[desc]
    for kw, cat in _KEYWORDS:
        if kw in desc:
            return cat
    return OTHER
