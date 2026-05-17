"""
NASA FIRMS client — fetches active fire hotspots from MODIS and VIIRS.
API key required: register free at https://earthdata.nasa.gov
"""

from __future__ import annotations

import os

import httpx

_FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
_TIMEOUT = httpx.Timeout(30.0)
# FIRMS Area API: DAY_RANGE solo 1..5 (https://firms.modaps.eosdis.nasa.gov/api/area/)
FIRMS_AREA_DAY_RANGE_MAX = 5


def clamp_firms_area_days(days: int) -> int:
    """Valores fuera de 1..5 hacen que FIRMS responda HTTP 400."""
    return max(1, min(int(days), FIRMS_AREA_DAY_RANGE_MAX))


def _normalize_firms_api_key(raw: str) -> str:
    """
    Limpia el MAP_KEY Earthdata: whitespace, BOM y comillas tipográficas que suelen
    colarse al copiar/pegar o desde editores (.env con “smart quotes”) y rompen la URL FIRMS (400).
    """
    s = (raw or "").strip().strip("\ufeff")
    for ch in ("\u201c", "\u201d", "\u2018", "\u2019", "\u00a0"):
        s = s.replace(ch, "")
    return s.strip()


def _firms_api_key() -> str:
    return _normalize_firms_api_key(os.getenv("FIRMS_API_KEY", ""))


async def get_hotspots(
    lat: float,
    lon: float,
    radius_km: float = 100,
    days: int = 5,
    source: str = "VIIRS_SNPP_NRT",
) -> list[dict]:
    """
    Fetch active fire hotspots within radius_km of (lat, lon) for the past `days` days.

    La API Area solo admite ventanas de 1 a 5 días; valores mayores se tratan como 5.

    source options: VIIRS_SNPP_NRT, MODIS_NRT, VIIRS_NOAA20_NRT
    Returns a list of hotspot dicts.
    """
    key = _firms_api_key()
    if not key:
        return _mock_hotspots(lat, lon)

    days_eff = clamp_firms_area_days(days)

    # FIRMS area API uses a bounding box derived from the center + radius
    deg = radius_km / 111.0
    bbox = f"{lon - deg},{lat - deg},{lon + deg},{lat + deg}"
    url = f"{_FIRMS_BASE}/{key}/{source}/{bbox}/{days_eff}"

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
