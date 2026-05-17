from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from routers.deps import OptionalDbSession
from services import openmeteo
from services.read_through_agri import (
    load_advisor_payload_with_cache,
    load_historical_with_cache,
    load_projection_with_cache,
    resolve_source_uuid,
)

router = APIRouter()


def _openmeteo_http_exception(exc: Exception, *, prefix: str = "Open-Meteo") -> HTTPException:
    msg = str(exc)
    low = msg.lower()
    code = 503 if "rate limit" in low or "(429)" in msg else 502
    return HTTPException(status_code=code, detail=f"{prefix}: {msg}")


def df_to_monthly_series(df):
    return [
        {
            "year": row["year"],
            "month": row["month"],
            "temp_c": row["temp_c"],
            "precip_mm": row["precip_mm"],
            "soil_moisture": row.get("soil_moisture"),
        }
        for row in df.to_dicts()
    ]


def _climate_cache_summary(hmeta: dict, pmeta: dict) -> dict:
    hc = (hmeta.get("cache") or {}).get("historical") or {}
    pc = (pmeta.get("cache") or {}).get("projected") or {}
    return {
        "historical": hc or None,
        "projected": pc or None,
        "any_stale": bool(hc.get("stale")) or bool(pc.get("stale")),
    }


@router.get("/advisor")
async def agri_advisor(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    crop_id: str = Query(..., description="Crop ID from crop_requirements.json"),
    season: str = Query("annual", description="Season: lluvias, secas, annual"),
    db: OptionalDbSession = None,
):
    """
    UC-1 — Agricultural aptitude score for a crop at a location.
    Combines climate baseline, soil moisture, and scoring.
    Si DATABASE_URL existe, usa read-through en Postgres ante fallos/upstream stale.
    """
    hist_sy, hist_ey = 2019, 2023
    try:
        result, freshness = await load_advisor_payload_with_cache(
            db,
            lat=lat,
            lon=lon,
            crop_id=crop_id,
            season=season,
            hist_start_year=hist_sy,
            hist_end_year=hist_ey,
        )
    except Exception as exc:
        raise _openmeteo_http_exception(exc) from exc
    result = dict(result)
    result["_cache"] = freshness
    return result


@router.get("/climate")
async def agri_climate(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    from_year: int = Query(1991, alias="from", description="Start year (historical)"),
    to_year: int = Query(2020, alias="to", description="End year (historical)"),
    scenario: str = Query("SSP3-7.0", description="CMIP6 scenario for projection"),
    db: OptionalDbSession = None,
):
    """
    UC-3 — Historical climate series + CMIP6 projection for a location.
    """
    om_id = None
    if db is not None:
        om_id = await resolve_source_uuid(db, "open_meteo")
    try:
        hist_df, hmeta = await load_historical_with_cache(
            db,
            lat=lat,
            lon=lon,
            start_year=from_year,
            end_year=to_year,
            om_uuid=om_id,
        )
        proj_df, pmeta = await load_projection_with_cache(
            db,
            lat=lat,
            lon=lon,
            scenario=scenario,
            om_uuid=om_id,
        )
    except Exception as exc:
        raise _openmeteo_http_exception(exc) from exc

    return {
        "lat": lat,
        "lon": lon,
        "scenario": scenario,
        "historical": df_to_monthly_series(hist_df),
        "projected": df_to_monthly_series(proj_df),
        "_cache": _climate_cache_summary(hmeta, pmeta),
        "_upstream_hints": {"historical": hmeta.get("upstream_error"), "projected": pmeta.get("upstream_error")},
    }


@router.get("/soil")
async def soil_data(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
):
    """Soil properties from SoilGrids for a location (stub — SoilGrids integration pending)."""
    return {
        "lat": lat,
        "lon": lon,
        "soil": {
            "ph": None,
            "texture": None,
            "note": "SoilGrids integration pending (CVA-13)",
        },
    }


@router.get("/geocode")
async def geocode_region(
    q: str = Query(..., description="Location name to search"),
    count: int = Query(5, description="Max results"),
):
    """UC-9 — Search for LATAM regions by name using Open-Meteo Geocoding."""
    try:
        results = await openmeteo.geocode(q, count=count)
    except Exception as exc:
        raise _openmeteo_http_exception(exc, prefix="Geocoding") from exc
    return {"results": results}
