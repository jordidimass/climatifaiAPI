"""Lógica compartida de ingesta año a año desde Open‑Meteo Archive."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import IngestionJob
from services import openmeteo
from services.query_keys import openmeteo_historical_key
from services.read_through_agri import archive_base_for_keys
from services.read_through_storage import upsert_raw_payload


async def run_openmeteo_archive_yearly(
    session: AsyncSession,
    *,
    open_meteo_source_id: UUID,
    lat: float,
    lon: float,
    from_year: int,
    to_year: int,
    force: bool = False,
    resume_job_id: UUID | None = None,
    job_type: str = "openmeteo_archive_yearly_cell",
    location_id: UUID | None = None,
) -> IngestionJob:
    """
    Persiste payload ``historical_archive`` por año (**un query_key/año**) y actualiza cursor.
    Llama ``session.commit()`` al finalizar cada año.
    """
    if resume_job_id:
        jr = await session.execute(select(IngestionJob).where(IngestionJob.id == resume_job_id))
        job = jr.scalar_one_or_none()
        if job is None:
            raise ValueError("Job no encontrado para resume.")
    else:
        job = IngestionJob(
            source_id=open_meteo_source_id,
            job_type=job_type,
            status="running",
            cursor={"last_completed_year": None, "lat": lat, "lon": lon, "location_id": str(location_id) if location_id else None},
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(job)
        await session.flush()

    cy = dict(job.cursor)
    lat_eff, lon_eff = float(cy.get("lat", lat)), float(cy.get("lon", lon))
    last_done = cy.get("last_completed_year")

    oid = open_meteo_source_id
    archive_host = archive_base_for_keys()

    for year in range(from_year, to_year + 1):
        if last_done is not None and isinstance(last_done, int) and year <= last_done and not force:
            continue
        params, qk = openmeteo_historical_key(lat_eff, lon_eff, year, year, archive_host)
        data = await openmeteo.fetch_historical_daily_json(lat_eff, lon_eff, year, year)
        await upsert_raw_payload(
            session,
            source_id=oid,
            query_key=qk,
            params=params,
            http_status=200,
            body=data,
            job_id=job.id,
            location_id=location_id,
            is_stale=False,
        )
        cy["last_completed_year"] = year
        cy["lat"], cy["lon"] = lat_eff, lon_eff
        cy["location_id"] = str(location_id) if location_id else cy.get("location_id")
        job.cursor = dict(cy)
        last_done = year
        progress = dict(job.progress or {})
        progress["years_done"] = int(progress.get("years_done", 0)) + 1
        job.progress = dict(progress)
        job.updated_at = datetime.now(UTC)
        job.status = "partial" if year < to_year else "completed"
        await session.commit()
        last_done = year

    await session.refresh(job)
    return job
