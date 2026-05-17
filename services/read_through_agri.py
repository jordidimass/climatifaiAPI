"""
Read-through Postgres para endpoints /agri: Open-Meteo histórico, proyección y advisor.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DataSource
from services import openmeteo, scoring
from services.query_keys import agri_advisor_key, openmeteo_historical_key, openmeteo_projection_key
from services.read_through_storage import fetch_raw_latest, policies, upsert_raw_payload


def archive_base_for_keys() -> str:
    return os.getenv("OPEN_METEO_BASE_URL", "https://archive-api.open-meteo.com/v1")


def _fresh_wrap(row: Any | None, *, stale: bool) -> dict[str, Any]:
    return {
        "from_cache": row is not None,
        "stale": stale,
        "fetched_at": row.fetched_at.isoformat() if row else None,
        "payload_id": str(row.id) if row else None,
    }


_resolved_positive: dict[str, UUID] = {}


async def resolve_source_uuid(session: AsyncSession, slug: str) -> UUID | None:
    uid_cached = _resolved_positive.get(slug)
    if uid_cached:
        return uid_cached
    stmt = select(DataSource).where(DataSource.slug == slug)
    r = await session.execute(stmt)
    row = r.scalar_one_or_none()
    if row:
        _resolved_positive[slug] = row.id
        return row.id
    return None


async def load_historical_with_cache(
    session: AsyncSession | None,
    *,
    lat: float,
    lon: float,
    start_year: int,
    end_year: int,
    om_slug: str = "open_meteo",
    om_uuid: UUID | None = None,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    params, qk = openmeteo_historical_key(lat, lon, start_year, end_year, archive_base_for_keys())
    pol_sec = policies()["openmeteo"]
    meta: dict[str, Any] = {"cache": {}, "upstream_error": None}

    if session is not None and om_uuid is not None:
        fresh = await fetch_raw_latest(session, source_slug=om_slug, query_key=qk, max_age_sec=pol_sec)
        if fresh and isinstance(fresh.body, dict):
            df = openmeteo.df_from_daily_archive_payload(fresh.body)
            meta["cache"]["historical"] = _fresh_wrap(fresh, stale=False)
            return df, meta

    try:
        data = await openmeteo.fetch_historical_daily_json(lat, lon, start_year, end_year)
        df = openmeteo.df_from_daily_archive_payload(data)
        if session is not None and om_uuid is not None:
            row = await upsert_raw_payload(
                session,
                source_id=om_uuid,
                query_key=qk,
                params=params,
                http_status=200,
                body=data,
                is_stale=False,
            )
            await session.commit()
            meta["cache"]["historical"] = _fresh_wrap(row, stale=False)
        return df, meta
    except Exception as exc:
        meta["upstream_error"] = ("historical", str(exc))
        if session is not None and om_uuid is not None:
            st = await fetch_raw_latest(session, source_slug=om_slug, query_key=qk, max_age_sec=pol_sec, accept_stale=True)
            if st and isinstance(st.body, dict):
                df = openmeteo.df_from_daily_archive_payload(st.body)
                await session.commit()
                meta["cache"]["historical"] = _fresh_wrap(st, stale=True)
                return df, meta
        raise


async def load_projection_with_cache(
    session: AsyncSession | None,
    *,
    lat: float,
    lon: float,
    scenario: str,
    start_year: int = 2024,
    end_year: int = 2030,
    model: str = "MRI_AGCM3_2_S",
    om_slug: str = "open_meteo",
    om_uuid: UUID | None = None,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    params, qk = openmeteo_projection_key(lat, lon, scenario, start_year, end_year, model)
    pol_sec = policies()["openmeteo"]
    meta: dict[str, Any] = {"cache": {}, "upstream_error": None}

    if session is not None and om_uuid is not None:
        fresh = await fetch_raw_latest(session, source_slug=om_slug, query_key=qk, max_age_sec=pol_sec)
        if fresh and isinstance(fresh.body, dict):
            df = openmeteo.df_from_climate_projection_payload(fresh.body)
            meta["cache"]["projected"] = _fresh_wrap(fresh, stale=False)
            return df, meta

    try:
        data = await openmeteo.fetch_climate_projection_json(lat, lon, scenario, start_year, end_year, model)
        df = openmeteo.df_from_climate_projection_payload(data)
        if session is not None and om_uuid is not None:
            row = await upsert_raw_payload(
                session,
                source_id=om_uuid,
                query_key=qk,
                params=params,
                http_status=200,
                body=data,
                is_stale=False,
            )
            await session.commit()
            meta["cache"]["projected"] = _fresh_wrap(row, stale=False)
        return df, meta
    except Exception as exc:
        meta["upstream_error"] = ("projected", str(exc))
        if session is not None and om_uuid is not None:
            st = await fetch_raw_latest(session, source_slug=om_slug, query_key=qk, max_age_sec=pol_sec, accept_stale=True)
            if st and isinstance(st.body, dict):
                df = openmeteo.df_from_climate_projection_payload(st.body)
                await session.commit()
                meta["cache"]["projected"] = _fresh_wrap(st, stale=True)
                return df, meta
        raise


def _merge_advisor_meta(h_meta: dict, adv_row: Any, hist_stale: bool) -> dict[str, Any]:
    combined = dict(h_meta.get("cache") or {})
    combined["historical"] = combined.get("historical")
    combined["advisor"] = _fresh_wrap(adv_row, stale=hist_stale)
    return {"cache": combined, "upstream_error": h_meta.get("upstream_error")}


async def load_advisor_payload_with_cache(
    session: AsyncSession | None,
    *,
    lat: float,
    lon: float,
    crop_id: str,
    season: str,
    hist_start_year: int,
    hist_end_year: int,
    adv_slug: str = "agri_cached",
) -> tuple[dict[str, Any], dict[str, Any]]:
    params, qk = agri_advisor_key(lat, lon, crop_id, season, hist_start_year, hist_end_year)
    pol_adv = policies()["agri_advisor"]

    if session is not None:
        om_uuid = await resolve_source_uuid(session, "open_meteo")
        adv_uuid = await resolve_source_uuid(session, adv_slug)
    else:
        om_uuid = adv_uuid = None

    if session is not None and adv_uuid is not None:
        fresh = await fetch_raw_latest(session, source_slug=adv_slug, query_key=qk, max_age_sec=pol_adv)
        if fresh and isinstance(fresh.body, dict) and isinstance(fresh.body.get("result"), dict):
            await session.commit()
            return dict(fresh.body["result"]), {
                "cache": {"advisor": _fresh_wrap(fresh, stale=False)},
                "upstream_error": None,
            }

    stale_row = None
    if session is not None and adv_uuid is not None:
        stale_row = await fetch_raw_latest(
            session, source_slug=adv_slug, query_key=qk, max_age_sec=pol_adv, accept_stale=True,
        )

    hist_stale_marker = False
    try:
        hist_df, h_meta = await load_historical_with_cache(
            session,
            lat=lat,
            lon=lon,
            start_year=hist_start_year,
            end_year=hist_end_year,
            om_uuid=om_uuid,
        )
        hcache = h_meta.get("cache", {}).get("historical")
        if isinstance(hcache, dict) and hcache.get("stale"):
            hist_stale_marker = True
        result = scoring.score_advisor(crop_id=crop_id, historical_df=hist_df)
        meta_out: dict[str, Any]

        if session is not None and adv_uuid is not None:
            adv_row = await upsert_raw_payload(
                session,
                source_id=adv_uuid,
                query_key=qk,
                params=params,
                http_status=200,
                body={"result": result, "openmeteo_freshness": h_meta},
                is_stale=hist_stale_marker,
            )
            await session.commit()
            meta_out = _merge_advisor_meta(h_meta, adv_row, hist_stale_marker)
            meta_out["upstream_error"] = h_meta.get("upstream_error")
        else:
            meta_out = {
                "cache": {
                    **(h_meta.get("cache") or {}),
                    **{"advisor": {"from_cache": False, "stale": hist_stale_marker}},
                },
                "upstream_error": h_meta.get("upstream_error"),
            }

        return result, meta_out
    except Exception:
        if session is not None and adv_uuid is not None and stale_row and isinstance(stale_row.body, dict):
            rb = stale_row.body.get("result")
            if isinstance(rb, dict):
                await session.commit()
                return dict(rb), {
                    "cache": {"advisor": _fresh_wrap(stale_row, stale=True)},
                    "upstream_error": None,
                }
        raise
