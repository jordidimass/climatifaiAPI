"""
NASA FIRMS client — fetches active fire hotspots from MODIS and VIIRS.
API key required: register free at https://earthdata.nasa.gov
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import httpx

_FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
_API_KEY = os.getenv("FIRMS_API_KEY", "")
_TIMEOUT = httpx.Timeout(30.0)


async def get_hotspots(
    lat: float,
    lon: float,
    radius_km: float = 100,
    days: int = 7,
    source: str = "VIIRS_SNPP_NRT",
) -> list[dict]:
    """
    Fetch active fire hotspots within radius_km of (lat, lon) for the past `days` days.

    source options: VIIRS_SNPP_NRT, MODIS_NRT, VIIRS_NOAA20_NRT
    Returns a list of hotspot dicts.
    """
    if not _API_KEY:
        return _mock_hotspots(lat, lon)

    # FIRMS area API uses a bounding box derived from the center + radius
    deg = radius_km / 111.0
    bbox = f"{lon - deg},{lat - deg},{lon + deg},{lat + deg}"
    end = date.today()
    start = end - timedelta(days=min(days, 10))

    url = f"{_FIRMS_BASE}/{_API_KEY}/{source}/{bbox}/{days}"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url)
        if r.status_code == 401:
            return _mock_hotspots(lat, lon)
        r.raise_for_status()
        return _parse_csv(r.text)


def _parse_csv(csv_text: str) -> list[dict]:
    lines = csv_text.strip().split("\n")
    if len(lines) < 2:
        return []

    headers = [h.strip() for h in lines[0].split(",")]
    results = []
    for line in lines[1:]:
        values = [v.strip() for v in line.split(",")]
        if len(values) != len(headers):
            continue
        row = dict(zip(headers, values))
        results.append({
            "lat": float(row.get("latitude", 0)),
            "lon": float(row.get("longitude", 0)),
            "brightness": float(row.get("bright_ti4", row.get("brightness", 0)) or 0),
            "confidence": row.get("confidence", "nominal"),
            "satellite": row.get("satellite", ""),
            "instrument": row.get("instrument", "VIIRS"),
            "date": row.get("acq_date", ""),
            "time": row.get("acq_time", ""),
            "frp": float(row.get("frp", 0) or 0),
        })
    return results


def compute_fire_risk_score(hotspots: list[dict], radius_km: float = 100) -> int:
    """
    Simple fire risk score (0-100) based on hotspot density and FRP.
    0 = no risk, 100 = extreme risk.
    """
    if not hotspots:
        return 0
    area_km2 = 3.14159 * radius_km ** 2
    density = len(hotspots) / area_km2 * 1000
    high_conf = sum(1 for h in hotspots if str(h.get("confidence", "")).lower() in ("high", "h", "nominal"))
    avg_frp = sum(h.get("frp", 0) for h in hotspots) / len(hotspots)

    score = min(100, int(density * 20 + (high_conf / max(1, len(hotspots))) * 30 + min(avg_frp / 5, 50)))
    return score


def _mock_hotspots(lat: float, lon: float) -> list[dict]:
    """Return empty list when no FIRMS key is configured."""
    return []
