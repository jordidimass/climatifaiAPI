"""
Open-Meteo client — covers historical ERA5, CMIP6 projections, and short-term forecasts.
All endpoints are free and require no API key.

NOTE: The Archive API only supports `daily` resolution; we aggregate to monthly in polars.
The Climate API (CMIP6) does support monthly aggregation natively.

Open-Meteo applies public rate limits (HTTP 429). We retry with backoff and cache
identical historical requests briefly to avoid duplicate calls from the UI.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any

import httpx
import polars as pl

_ARCHIVE_URL = os.getenv("OPEN_METEO_BASE_URL", "https://archive-api.open-meteo.com/v1")
_CLIMATE_URL = "https://climate-api.open-meteo.com/v1"
_FORECAST_URL = "https://api.open-meteo.com/v1"
_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1"

_TIMEOUT = httpx.Timeout(120.0)

# In-memory cache: same coords + year range hits the Archive API once per TTL (reduces 429).
_HIST_CACHE_TTL_SEC = 300.0
_HIST_CACHE: dict[tuple[Any, ...], tuple[float, pl.DataFrame]] = {}
_HIST_CACHE_MAX = 96


def _hist_cache_get(key: tuple[Any, ...]) -> pl.DataFrame | None:
    now = time.monotonic()
    entry = _HIST_CACHE.get(key)
    if entry and entry[0] > now:
        return entry[1].clone()
    return None


def _hist_cache_set(key: tuple[Any, ...], df: pl.DataFrame) -> None:
    if len(_HIST_CACHE) >= _HIST_CACHE_MAX:
        # Drop oldest ~half (simple eviction)
        for k in list(_HIST_CACHE.keys())[: _HIST_CACHE_MAX // 2]:
            _HIST_CACHE.pop(k, None)
    exp = time.monotonic() + _HIST_CACHE_TTL_SEC
    _HIST_CACHE[key] = (exp, df.clone())


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict) and data.get("reason"):
            return str(data["reason"])
    except Exception:
        pass
    return response.text[:500] if response.text else response.status_code


async def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    GET JSON from Open-Meteo with retries on HTTP 429 (rate limit).
    """
    backoff_seconds = [1.0, 3.0, 8.0, 20.0]
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(len(backoff_seconds) + 1):
            r = await client.get(url, params=params)
            if r.status_code == 429:
                detail = _error_detail(r)
                if attempt < len(backoff_seconds):
                    wait = backoff_seconds[attempt] + random.uniform(0, 0.4)
                    await asyncio.sleep(wait)
                    continue
                raise RuntimeError(f"Open-Meteo rate limit (429): {detail}")
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Open-Meteo HTTP {r.status_code}: {_error_detail(r)}",
                ) from exc
            data = r.json()
            if isinstance(data, dict) and data.get("error") is True:
                reason = data.get("reason", "unknown error")
                raise RuntimeError(f"Open-Meteo error: {reason}")
            return data


async def fetch_historical_daily_json(
    lat: float,
    lon: float,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """
    Obtiene respuesta JSON cruda del Archive API sin agregar mensualmente.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": f"{start_year}-01-01",
        "end_date": f"{end_year}-12-31",
        "daily": "temperature_2m_mean,precipitation_sum,soil_moisture_0_to_10cm_mean",
        "timezone": "auto",
    }
    data = await _get_json(f"{_ARCHIVE_URL}/archive", params)
    return data


async def get_historical(
    lat: float,
    lon: float,
    start_year: int = 2019,
    end_year: int = 2023,
) -> pl.DataFrame:
    """
    Fetch ERA5-Land daily data and aggregate to monthly averages.
    Returns DataFrame with: year, month, temp_c, precip_mm, soil_moisture.
    """
    cache_key = ("hist", round(lat, 4), round(lon, 4), start_year, end_year)
    cached = _hist_cache_get(cache_key)
    if cached is not None:
        return cached

    data = await fetch_historical_daily_json(lat, lon, start_year, end_year)
    df = df_from_daily_archive_payload(data)
    _hist_cache_set(cache_key, df)
    return df


async def fetch_climate_projection_json(
    lat: float,
    lon: float,
    scenario: str,
    start_year: int,
    end_year: int,
    model: str = "MRI_AGCM3_2_S",
) -> dict[str, Any]:
    """
    Climate API (HighResMIP CMIP6): desde 2024 la API espera ``daily`` (no ``monthly``).
    Ver https://open-meteo.com/en/docs/climate-api — agregamos a mensual igual que ERA5 Archive.
    El parámetro ``scenario`` se conserva solo para compatibilidad / ``query_key``; no forma parte del URL público actual.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": f"{start_year}-01-01",
        "end_date": f"{end_year}-12-31",
        "daily": "temperature_2m_mean,precipitation_sum",
        "models": model,
        "timezone": "auto",
    }
    return await _get_json(f"{_CLIMATE_URL}/climate", params)


async def get_climate_projection(
    lat: float,
    lon: float,
    scenario: str = "SSP3-7.0",
    start_year: int = 2024,
    end_year: int = 2030,
) -> pl.DataFrame:
    """
    Proyección climática (modelo CMIP6 downscaled): datos diarios agregados a mensual.

    Devuelve DataFrame con: year, month, temp_c, precip_mm, soil_moisture.
    """
    data = await fetch_climate_projection_json(lat, lon, scenario, start_year, end_year)
    return df_from_climate_projection_payload(data)


async def get_forecast(
    lat: float,
    lon: float,
    days: int = 16,
) -> pl.DataFrame:
    """
    Fetch short-term weather forecast (up to 16 days).
    Returns DataFrame with: date, temp_max_c, temp_min_c, precip_mm.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "forecast_days": min(days, 16),
        "timezone": "auto",
    }
    data = await _get_json(f"{_FORECAST_URL}/forecast", params)
    daily = data.get("daily", {})
    return pl.DataFrame({
        "date": daily.get("time", []),
        "temp_max_c": daily.get("temperature_2m_max", []),
        "temp_min_c": daily.get("temperature_2m_min", []),
        "precip_mm": daily.get("precipitation_sum", []),
    })


async def geocode(query: str, count: int = 5) -> list[dict[str, Any]]:
    """
    Search for locations by name (returns lat/lon + region info).
    Used for RegionSelector autocomplete in the frontend.
    """
    params = {"name": query, "count": count, "language": "es", "format": "json"}
    data = await _get_json(f"{_GEOCODING_URL}/search", params)
    return [
        {
            "id": str(item.get("id")),
            "name": item.get("name"),
            "country": item.get("country"),
            "admin1": item.get("admin1"),
            "lat": item.get("latitude"),
            "lon": item.get("longitude"),
            "elevation": item.get("elevation"),
        }
        for item in data.get("results", [])
    ]


def df_from_climate_projection_payload(data: dict[str, Any]) -> pl.DataFrame:
    """
    Interpreta respuesta Climate API: prioriza bloque ``daily`` (contrato actual);
    conserva soporte legacy ``monthly`` para filas ya cacheadas en ``raw_payloads``.
    """
    if data.get("daily", {}).get("time"):
        return df_from_daily_archive_payload(data)
    return df_from_monthly_climate_payload(data)


def df_from_daily_archive_payload(data: dict[str, Any]) -> pl.DataFrame:
    """Convert daily Open-Meteo response to monthly aggregates using polars."""
    daily = data.get("daily", {})
    times = daily.get("time", [])

    if not times:
        return pl.DataFrame({"year": [], "month": [], "temp_c": [], "precip_mm": [], "soil_moisture": []})

    df = pl.DataFrame({
        "date": pl.Series(times).str.to_date("%Y-%m-%d"),
        "temp_c": daily.get("temperature_2m_mean", [None] * len(times)),
        "precip_mm": daily.get("precipitation_sum", [None] * len(times)),
        "soil_moisture": daily.get("soil_moisture_0_to_10cm_mean", [None] * len(times)),
    })

    return (
        df
        .with_columns([
            pl.col("date").dt.year().alias("year"),
            pl.col("date").dt.month().alias("month"),
        ])
        .group_by(["year", "month"])
        .agg([
            pl.col("temp_c").mean().alias("temp_c"),
            pl.col("precip_mm").sum().alias("precip_mm"),
            pl.col("soil_moisture").mean().alias("soil_moisture"),
        ])
        .sort(["year", "month"])
        .select(["year", "month", "temp_c", "precip_mm", "soil_moisture"])
    )


def df_from_monthly_climate_payload(data: dict[str, Any]) -> pl.DataFrame:
    """Parse native monthly response from the Climate (CMIP6) API."""
    monthly = data.get("monthly", {})
    times = monthly.get("time", [])

    if not times:
        return pl.DataFrame({"year": [], "month": [], "temp_c": [], "precip_mm": [], "soil_moisture": []})

    temps = monthly.get("temperature_2m_mean", [None] * len(times))
    precips = monthly.get("precipitation_sum", [None] * len(times))

    return pl.DataFrame({
        "year": [int(t[:4]) for t in times],
        "month": [int(t[5:7]) for t in times],
        "temp_c": temps,
        "precip_mm": precips,
        "soil_moisture": [None] * len(times),
    })


def compute_gdd_monthly(df: pl.DataFrame, base_temp: float) -> pl.DataFrame:
    """Add gdd column: monthly GDD = max(0, temp_c - base_temp) * days_in_month."""
    return df.with_columns([
        (
            (pl.col("temp_c") - base_temp).clip(lower_bound=0)
            * pl.col("month").map_elements(
                lambda m: [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1],
                return_dtype=pl.Int32,
            )
        ).alias("gdd")
    ])
