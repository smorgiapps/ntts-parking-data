"""Thin client for the DataSF Socrata (SODA 2.1) API."""

import os
import time

import requests

BASE = "https://data.sfgov.org/resource"

# Dataset IDs on data.sfgov.org
CITATIONS = "ab4h-6ztd"        # SFMTA Parking Citations & Fines
ADDRESSES = "ramy-di5m"        # Addresses - Enterprise Addressing System
SWEEPING = "yhqp-riqs"         # Street Sweeping Schedule
METERS = "8vzz-qzz9"           # Parking Meters
METER_SCHEDULES = "6cqg-dxku"  # Meter Operating Schedules

PAGE_SIZE = 50_000
MAX_RETRIES = 5


def _session() -> requests.Session:
    s = requests.Session()
    token = os.environ.get("SODA_APP_TOKEN")
    if token:
        s.headers["X-App-Token"] = token
    s.headers["Accept"] = "application/json"
    return s


def fetch_all(dataset_id: str, select: str | None = None, where: str | None = None,
              order: str = ":id", page_size: int = PAGE_SIZE, max_rows: int | None = None):
    """Yield every row of a dataset, paging with $offset."""
    session = _session()
    offset = 0
    while True:
        params = {"$limit": page_size, "$offset": offset, "$order": order}
        if select:
            params["$select"] = select
        if where:
            params["$where"] = where
        rows = _get(session, f"{BASE}/{dataset_id}.json", params)
        yield from rows
        offset += len(rows)
        if len(rows) < page_size or (max_rows and offset >= max_rows):
            return


def _get(session: requests.Session, url: str, params: dict) -> list:
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=180)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503):
                time.sleep(2 ** attempt * 2)
                continue
            resp.raise_for_status()
        except requests.RequestException:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"failed after {MAX_RETRIES} retries: {url}")
