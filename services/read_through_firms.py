"""Read-through FIRMS hotspots (lista serializada como JSON crudo)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from services import firms
from services.query_keys import firms_hotspots_key
from services.read_through_storage import fetch_raw_latest, policies, upsert_raw_payload


def _wrap(row: Any | None, *, stale: bool) -> dict[str, Any]:
    return {
        "from_cache": row is not None,
        "stale": stale,
        "fetched_at": row.fetched_at.isoformat() if row else None,
        "payload_id": str(row.id) if row else None,
    }


async def hotspots_with_cache(
    session: AsyncSession | None,
    *,
    lat: float,
    lon: float,
    radius_km: float,
    days: int,
    source: str,
    firms_slug: str = "nasa_firms",
    firms_uuid: UUID | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    days_eff = firms.clamp_firms_area_days(days)
    params, qk = firms_hotspots_key(lat, lon, radius_km, days_eff, source)
    firm_pol = policies()["firms"]
    meta: dict[str, Any] = {"hotspots": None}

    if session is not None and firms_uuid is not None:
        fresh = await fetch_raw_latest(session, source_slug=firms_slug, query_key=qk, max_age_sec=firm_pol)
        if fresh and isinstance(fresh.body, dict) and isinstance(fresh.body.get("hotspots"), list):
            meta["hotspots"] = _wrap(fresh, stale=False)
            return fresh.body["hotspots"], meta  # type: ignore[no-any-return]

    try:
        items = await firms.get_hotspots(lat, lon, radius_km=radius_km, days=days_eff, source=source)
        if session is not None and firms_uuid is not None:
            row = await upsert_raw_payload(
                session,
                source_id=firms_uuid,
                query_key=qk,
                params=params,
                http_status=200,
                body={"hotspots": items},
                is_stale=False,
            )
            await session.commit()
            meta["hotspots"] = _wrap(row, stale=False)
        return items, meta
    except Exception:
        if session is not None and firms_uuid is not None:
            st = await fetch_raw_latest(session, source_slug=firms_slug, query_key=qk, max_age_sec=firm_pol, accept_stale=True)
            if st and isinstance(st.body, dict) and isinstance(st.body.get("hotspots"), list):
                await session.commit()
                meta["hotspots"] = _wrap(st, stale=True)
                return st.body["hotspots"], meta  # type: ignore[no-any-return]
        raise
