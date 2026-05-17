from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from routers.deps import OptionalDbSession
from services.read_through_agri import resolve_source_uuid
from services.read_through_firms import hotspots_with_cache
from services import firms

router = APIRouter()


@router.get("/hotspots")
async def fire_hotspots(
    lat: float = Query(..., description="Center latitude"),
    lon: float = Query(..., description="Center longitude"),
    radius_km: float = Query(100, description="Search radius in km"),
    days: int = Query(5, ge=1, le=5, description="Días hacia atrás (FIRMS Area solo admite 1–5)"),
    source: str = Query("VIIRS_SNPP_NRT", description="FIRMS data source"),
    db: OptionalDbSession = None,
):
    """
    UC-5 — Active fire hotspots near a location from NASA FIRMS.
    """
    firms_id = None
    if db is not None:
        firms_id = await resolve_source_uuid(db, "nasa_firms")
    try:
        hotspots, freshness = await hotspots_with_cache(
            db,
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            days=days,
            source=source,
            firms_uuid=firms_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FIRMS error: {exc}") from exc

    return {
        "lat": lat,
        "lon": lon,
        "radius_km": radius_km,
        "days": days,
        "count": len(hotspots),
        "hotspots": hotspots,
        "_cache": freshness,
    }


@router.get("/risk")
async def fire_risk(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    radius_km: float = Query(100, description="Search radius in km"),
    db: OptionalDbSession = None,
):
    """
    UC-5 — Aggregated fire risk score (0-100) for a location.
    """
    firms_id = None
    if db is not None:
        firms_id = await resolve_source_uuid(db, "nasa_firms")
    try:
        hotspots, freshness = await hotspots_with_cache(
            db,
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            days=5,
            source="VIIRS_SNPP_NRT",
            firms_uuid=firms_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FIRMS error: {exc}") from exc

    risk_score = firms.compute_fire_risk_score(hotspots, radius_km=radius_km)
    risk_label = "Alto" if risk_score >= 60 else ("Medio" if risk_score >= 30 else "Bajo")

    return {
        "lat": lat,
        "lon": lon,
        "radius_km": radius_km,
        "risk_score": risk_score,
        "risk_label": risk_label,
        "hotspot_count": len(hotspots),
        "_cache": freshness,
    }


@router.get("/")
async def list_fires(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    db: OptionalDbSession = None,
):
    """Alias for /hotspots with default parameters."""
    firms_id = None
    if db is not None:
        firms_id = await resolve_source_uuid(db, "nasa_firms")
    hotspots, _fresh = await hotspots_with_cache(db, lat=lat, lon=lon, radius_km=100, days=5, source="VIIRS_SNPP_NRT", firms_uuid=firms_id)
    return {"fires": hotspots}
