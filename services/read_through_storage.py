"""Persistencia read-through sobre raw_payloads (Postgres opcional)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DataSource, RawPayload


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def policies() -> dict[str, float]:
    return {
        "openmeteo": float(os.getenv("OPENMETEO_CACHE_MAX_AGE_SEC", "86400")),
        "firms": float(os.getenv("FIRMS_CACHE_MAX_AGE_SEC", "900")),
        "agri_advisor": float(os.getenv("ADVISOR_CACHE_MAX_AGE_SEC", "86400")),
    }


async def get_source_by_slug(session: AsyncSession, slug: str) -> DataSource | None:
    stmt = select(DataSource).where(DataSource.slug == slug)
    r = await session.execute(stmt)
    return r.scalar_one_or_none()


async def fetch_raw_latest(
    session: AsyncSession,
    *,
    source_slug: str,
    query_key: str,
    max_age_sec: float,
    accept_stale: bool = False,
) -> RawPayload | None:
    stmt = (
        select(RawPayload)
        .join(DataSource, RawPayload.source_id == DataSource.id)
        .where(DataSource.slug == source_slug, RawPayload.query_key == query_key)
        .order_by(RawPayload.fetched_at.desc())
        .limit(1)
    )
    r = await session.execute(stmt)
    row = r.scalar_one_or_none()
    if row is None:
        return None
    age_sec = max(0.0, (utcnow() - row.fetched_at).total_seconds())
    if age_sec <= max_age_sec:
        return row
    if accept_stale:
        return row
    return None


async def upsert_raw_payload(
    session: AsyncSession,
    *,
    source_id: UUID,
    query_key: str,
    params: dict[str, Any],
    http_status: int | None,
    body: dict | list | str | float | int | bool | None,
    job_id: UUID | None = None,
    location_id: UUID | None = None,
    is_stale: bool = False,
    content_hash: str | None = None,
    expires_at: datetime | None = None,
) -> RawPayload:
    stmt_exist = (
        select(RawPayload)
        .where(RawPayload.source_id == source_id, RawPayload.query_key == query_key)
        .limit(1)
    )
    existing = (await session.execute(stmt_exist)).scalar_one_or_none()
    body_val = body if body is not None else {}
    if existing:
        existing.params = params
        existing.http_status = http_status
        existing.body = body_val  # type: ignore[assignment]
        existing.job_id = job_id
        existing.location_id = location_id if location_id is not None else existing.location_id
        existing.fetched_at = utcnow()
        existing.expires_at = expires_at
        existing.is_stale = is_stale
        existing.content_hash = content_hash
        session.add(existing)
        await session.flush()
        return existing

    payload = RawPayload(
        source_id=source_id,
        job_id=job_id,
        location_id=location_id,
        query_key=query_key,
        params=params,
        http_status=http_status,
        body=body_val,  # type: ignore[arg-type]
        fetched_at=utcnow(),
        expires_at=expires_at,
        is_stale=is_stale,
        content_hash=content_hash,
    )
    session.add(payload)
    await session.flush()
    return payload
