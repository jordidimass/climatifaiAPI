"""Creación tardía opcional de filas ``locations`` (modo lazy, sin Brasil)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Location
from services.locations_catalog import is_whitelisted_country, lazy_upsert_enabled, round_lat_lon, synthetic_geonames_id


async def upsert_lazy_api_location(
    session: AsyncSession,
    *,
    country_iso_raw: str | None,
    lat: float,
    lon: float,
) -> UUID | None:
    """
    Si lazy está habilitado y ``country_iso`` pertenece a LATAM≠BR,
    garantiza una fila ``locations`` sintética (``geonames_id`` negativo estable).
    """
    if not lazy_upsert_enabled():
        return None
    if not country_iso_raw:
        return None
    iso = country_iso_raw.strip().upper()
    if not is_whitelisted_country(iso):
        return None

    lat_r, lon_r = round_lat_lon(lat, lon)
    gid = synthetic_geonames_id(iso, lat_r, lon_r)

    stmt = select(Location).where(Location.geonames_id == gid)
    r = await session.execute(stmt)
    existing = r.scalar_one_or_none()
    if existing:
        return existing.id

    loc = Location(
        geonames_id=gid,
        country_iso=iso,
        admin1_code=None,
        kind="lazy_api_point",
        name="api_lazy_point",
        ascii_name=None,
        feature_code=None,
        lat=lat_r,
        lon=lon_r,
        catalog_version="lazy-api-upsert",
        meta={"channel": "read_through_agri"},
    )
    session.add(loc)
    await session.flush()
    return loc.id


def merge_location_meta(cache_meta: dict[str, Any], loc_id: UUID | None) -> dict[str, Any]:
    """Enriquecer bloque `_cache.location` cuando exista ubicación conocida."""
    if loc_id is None:
        return cache_meta if cache_meta is not None else {}
    out = dict(cache_meta or {})
    out["location_id"] = str(loc_id)
    return out
