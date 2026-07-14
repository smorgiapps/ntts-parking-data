"""Map SFMTA violation descriptions to app-facing risk categories and subtypes.

Category keys are shared with the iOS app (ParkingRisk.swift). Each violation
maps to a subtype with a reader-friendly label and optional grouping note when
similar violations are bucketed together.
"""

from __future__ import annotations

SOURCE_PARKING_SFMTA = "parking_sfmta"
SOURCE_SPEED_SFMTA = "speed_sfmta"
SOURCE_SPEED_CHP = "speed_chp"

STREET_CLEANING = "sweep"
METER = "meter"
RESIDENTIAL_PERMIT = "rpp"
COLOR_ZONE = "zone"
TOW_HAZARD = "tow"
PROHIBITED = "prohib"
OTHER = "other"

# subtype -> (category, display_label, group_note or None)
_SUBTYPE_META: dict[str, tuple[str, str, str | None]] = {
    "sweep": (STREET_CLEANING, "Street sweeping", None),
    "meter_expired": (METER, "Expired meter", None),
    "meter_other": (METER, "Meter violation", "Grouped meter-related tickets"),
    "rpp_overtime": (RESIDENTIAL_PERMIT, "Residential permit overtime", None),
    "rpp_other": (RESIDENTIAL_PERMIT, "Permit zone violation", None),
    "zone_yellow": (COLOR_ZONE, "Yellow zone", None),
    "zone_red": (COLOR_ZONE, "Red zone", None),
    "zone_white": (COLOR_ZONE, "White zone (passenger loading)", None),
    "zone_green": (COLOR_ZONE, "Green zone (short-term)", None),
    "zone_blue": (COLOR_ZONE, "Blue zone (accessible)", None),
    "zone_truck": (COLOR_ZONE, "Truck loading zone", None),
    "zone_bus": (COLOR_ZONE, "Bus zone", None),
    "zone_transit": (COLOR_ZONE, "Transit-only lane", None),
    "zone_other": (COLOR_ZONE, "Colored curb zone", "Grouped color-zone tickets"),
    "tow_away": (TOW_HAZARD, "Tow-away zone", None),
    "tow_hydrant": (TOW_HAZARD, "Fire hydrant zone", None),
    "tow_restricted": (TOW_HAZARD, "Restricted tow zone", None),
    "posted_no_parking": (
        TOW_HAZARD,
        "Posted no parking (tow-away hours)",
        "Parked during posted no-stopping or commuter-lane hours — not an expired meter.",
    ),
    "double_park": (PROHIBITED, "Double parking", None),
    "driveway": (PROHIBITED, "Blocking driveway", None),
    "sidewalk": (PROHIBITED, "Parked on sidewalk", None),
    "crosswalk": (PROHIBITED, "Blocking crosswalk", None),
    "bike_lane": (PROHIBITED, "Blocking bike lane", None),
    "intersection": (PROHIBITED, "Too close to intersection", None),
    "prohibited_general": (PROHIBITED, "Prohibited parking", None),
    "oversized": (PROHIBITED, "Oversized vehicle restriction", None),
    "time_limit": (PROHIBITED, "Time limit exceeded", None),
    "overnight": (PROHIBITED, "Overnight parking limit", None),
    "plate_display": (PROHIBITED, "Plate / registration display", "Grouped registration & plate tickets"),
    "plate_other": (PROHIBITED, "Missing or improper plates", None),
    "sign_disobey": (PROHIBITED, "Disobeyed posted sign", None),
    "construction": (PROHIBITED, "Construction / temp restriction", None),
    "commercial": (PROHIBITED, "Commercial vehicle restriction", None),
    "fare_transit": (PROHIBITED, "Transit fare / MTA violation", "Grouped transit-fare tickets"),
    "general_parking": (PROHIBITED, "General parking violation", None),
    "other": (OTHER, "Other parking violation", None),
}

# Exact SFMTA violation_desc -> subtype (all strings seen in 12-month citation data).
_EXACT_SUBTYPE: dict[str, str] = {
    "STR CLEAN": "sweep",
    "MTR OUT DT": "meter_expired",
    "METER DTN": "meter_expired",
    "OT MTR PK": "meter_other",
    "OT PK DT": "meter_other",
    "MTR DWNTWN": "meter_expired",
    "EXP. METER": "meter_expired",
    "EXP METER": "meter_expired",
    "PRK METER": "meter_other",
    "RES/OT": "rpp_overtime",
    "RES OT": "rpp_overtime",
    "OT OUT DT": "rpp_overtime",
    "NO PERMIT": "rpp_other",
    "P U C PEMT": "rpp_other",
    "YEL ZONE": "zone_yellow",
    "RED ZONE": "zone_red",
    "WHITE ZONE": "zone_white",
    "GRN ZONE": "zone_green",
    "GREEN ZONE": "zone_green",
    "BLUE ZONE": "zone_blue",
    "WHLCHR ACC": "zone_blue",
    "3 FT WLCHR": "zone_blue",
    "TRK ZONE": "zone_truck",
    "BUS ZONE": "zone_bus",
    "TRNST ONLY": "zone_transit",
    "B ZN NO DP": "zone_bus",
    "B ZN XHTCH": "zone_bus",
    "MAR GRN PK": "zone_green",
    "TOW AWAY": "tow_away",
    "TWAWY ZN": "tow_away",
    "TOWAWAY ZONE": "tow_away",
    "NO PRK ZN": "tow_away",
    "NOPRK 10P6": "tow_away",
    "FIRE HYD": "tow_hydrant",
    "HYDRANT": "tow_hydrant",
    "SAFE/RED Z": "tow_hydrant",
    "RESTRICTED": "tow_restricted",
    "TMP PK RES": "tow_restricted",
    "DBL PARK": "double_park",
    "DRIVEWAY": "driveway",
    "ON SIDEWLK": "sidewalk",
    "SIDEWALK": "sidewalk",
    "20FT XWALK": "crosswalk",
    "PK/CROSS": "crosswalk",
    "CROSSWALK": "crosswalk",
    "BLK BIKE L": "bike_lane",
    "BIC PATHS": "bike_lane",
    "BIKE LANE": "bike_lane",
    "PRK GRADE": "intersection",
    "PK STANDS": "intersection",
    "15FT FR ST": "intersection",
    "100FT OVER": "intersection",
    "PK INTER": "intersection",
    "BLK/INTER": "intersection",
    "PRK ON RGT": "intersection",
    "PRK PROHIB": "posted_no_parking",
    "PK PHB OTD": "posted_no_parking",
    "PKG PROHIB": "posted_no_parking",
    "PARK": "prohibited_general",
    "ON STREET": "prohibited_general",
    "OFF STREET": "prohibited_general",
    "ON ST LST": "prohibited_general",
    "OFF ST LST": "prohibited_general",
    "ANGLE PARK": "prohibited_general",
    "WRG WY PKG": "prohibited_general",
    "ONEWAY RD": "prohibited_general",
    "PK FR LN": "prohibited_general",
    "MED DIVIDE": "prohibited_general",
    "RR TRACKS": "prohibited_general",
    "ONSTCARSH": "prohibited_general",
    "N/ W/I SPC": "prohibited_general",
    "NOPL/PRDSP": "prohibited_general",
    "LARGE VEHI": "oversized",
    "OVR 18 \" C": "oversized",
    'OVR 18 \\" C': "oversized",
    "PK OVR 72H": "overnight",
    "FCL OT PK": "overnight",
    "OT OUT DT": "rpp_overtime",
    "FAIL DISPL": "plate_display",
    "NO PLATES": "plate_other",
    "FAILRPLPLA": "plate_other",
    "PLATECOVER": "plate_other",
    "ALT PLATES": "plate_other",
    "PLT F/R": "plate_other",
    "PLT LEF/AT": "plate_other",
    "REG TABS": "plate_other",
    "IMP DPL PL": "plate_other",
    "RMV CHLK": "plate_other",
    "DISOB SIGN": "sign_disobey",
    "CNSTR TEMP": "construction",
    "EXCAVATION": "construction",
    "BK CHG BAY": "construction",
    "CM VEH RES": "commercial",
    "MC PRKING": "commercial",
    "FOR HIRE": "commercial",
    "SGTSEE BUS": "commercial",
    "CM PASSGRS": "commercial",
    "OBSTRCT TF": "fare_transit",
    "MTA NONPAY": "fare_transit",
    "UNAUTHFARE": "fare_transit",
    "CNTRFTFARE": "fare_transit",
    "FARE EVASI": "fare_transit",
    "FR/EVA/YTH": "fare_transit",
    "NO EV REG": "general_parking",
    "FACIL CRG": "general_parking",
    "PUB PROP": "general_parking",
    "FCL BLK SP": "general_parking",
    "SCH/PUB GD": "general_parking",
    "SAFETY ZN": "general_parking",
    "ILL PKG": "general_parking",
    "INVALD PMT": "general_parking",
    "ILL PKG": "general_parking",
    "ENG IDLING": "general_parking",
    "SMOKNG ETC": "general_parking",
    "SOUNDEQUIP": "general_parking",
    "DISTURBAN": "general_parking",
    "AD SIGNS": "general_parking",
    "FOR SALE": "general_parking",
    "RPRING VEH": "general_parking",
    "WRK ON CAR": "general_parking",
    "LKD VEHICL": "general_parking",
    "CAR ALM 15": "general_parking",
    "ALM TME 15": "general_parking",
    "FIRETRAIL": "general_parking",
    "BLKEV ST": "general_parking",
    "CHGEV S": "general_parking",
    "BL ZNE BLK": "general_parking",
    "NO VIOL": "other",
    "V5204": "other",
    "V4457": "other",
    "": "other",
}

_KEYWORD_SUBTYPE = [
    ("CLEAN", "sweep"),
    ("SWEEP", "sweep"),
    ("METER", "meter_expired"),
    ("MTR", "meter_other"),
    ("RES/OT", "rpp_overtime"),
    ("RES OT", "rpp_overtime"),
    ("PERMIT", "rpp_other"),
    ("YEL", "zone_yellow"),
    ("RED", "zone_red"),
    ("WHITE", "zone_white"),
    ("GREEN", "zone_green"),
    ("GRN", "zone_green"),
    ("BLUE", "zone_blue"),
    ("WHLCHR", "zone_blue"),
    ("TRK", "zone_truck"),
    ("BUS", "zone_bus"),
    ("TRNST", "zone_transit"),
    ("TOW", "tow_away"),
    ("TWAWY", "tow_away"),
    ("HYD", "tow_hydrant"),
    ("DBL", "double_park"),
    ("DRIVE", "driveway"),
    ("SIDEWLK", "sidewalk"),
    ("XWALK", "crosswalk"),
    ("CROSS", "crosswalk"),
    ("BIKE", "bike_lane"),
    ("PLATE", "plate_other"),
    ("FARE", "fare_transit"),
    ("MTA", "fare_transit"),
]


def classify(violation_desc: str | None) -> tuple[str, str, str, str | None]:
    """Return (category, subtype, display_label, group_note)."""
    if not violation_desc or not violation_desc.strip():
        return OTHER, "other", "Unknown violation", None
    desc = violation_desc.upper().strip()
    subtype = _EXACT_SUBTYPE.get(desc)
    if subtype is None:
        for kw, st in _KEYWORD_SUBTYPE:
            if kw in desc:
                subtype = st
                break
    if subtype is None:
        subtype = "general_parking" if any(k in desc for k in ("PRK", "PK ", "PARK")) else "other"
    cat, label, note = _SUBTYPE_META.get(subtype, (OTHER, "Other parking violation", None))
    label = contextual_label(desc, subtype, label)
    return cat, subtype, label, note


def contextual_label(desc: str, subtype: str, base_label: str) -> str:
    """Refine reader-facing labels for high-volume codes (offline; no LLM at runtime)."""
    if subtype == "posted_no_parking":
        return "Posted no parking (tow-away / commuter lane)"
    if subtype == "meter_expired":
        if "MTR OUT DT" in desc or "METER DTN" in desc:
            return "Expired meter (outside downtown)"
        return "Expired meter"
    if subtype == "bike_lane" or desc in ("BLK BIKE L", "BIKE LANE", "BIC PATHS"):
        return "Blocking bike lane"
    if subtype == "plate_other" and desc == "NO PLATES":
        return "No license plates displayed"
    return base_label


def categorize(violation_desc: str | None) -> str:
    return classify(violation_desc)[0]


def build_violation_index() -> dict:
    """Export full mapping for the iOS bundle."""
    entries = []
    for desc, subtype in sorted(_EXACT_SUBTYPE.items()):
        if not desc:
            continue
        cat, label, note = _SUBTYPE_META.get(subtype, (OTHER, "Other", None))
        entries.append({
            "d": desc,
            "c": cat,
            "s": subtype,
            "l": label,
            "g": note,
        })
    return {"subtypes": {k: {"c": v[0], "l": v[1], "g": v[2]} for k, v in _SUBTYPE_META.items()},
            "exact": entries}
