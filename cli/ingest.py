#!/usr/bin/env python3
"""
CLI de ingesta/normalización (`climatifai-ingest`).

Requiere `DATABASE_URL` (postgresql+asyncpg).

Ver `DATABASE.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv


def _checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


async def cmd_openmeteo_yearly(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from db.models import IngestionJob
    from db.session import session_scope
    from services import openmeteo
    from services.query_keys import openmeteo_historical_key
    from services.read_through_agri import archive_base_for_keys, resolve_source_uuid
    from services.read_through_storage import upsert_raw_payload

    load_dotenv()
    async with session_scope() as session:
        if session is None:
            print("Defina DATABASE_URL para usar la CLI de ingesta.")
            return 1
        oid = await resolve_source_uuid(session, "open_meteo")
        if oid is None:
            print("No se encontró data_sources.slug=open_meteo (¿migraciones aplicadas?).")
            return 1

        if args.resume_job_id:
            jr = await session.execute(
                select(IngestionJob).where(IngestionJob.id == UUID(args.resume_job_id)),
            )
            job = jr.scalar_one_or_none()
            if job is None:
                print("Job no encontrado.")
                return 1
        else:
            job = IngestionJob(
                source_id=oid,
                job_type=args.job_type,
                status="running",
                cursor={"last_completed_year": None, "lat": args.lat, "lon": args.lon},
                started_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(job)
            await session.flush()

        cy = dict(job.cursor)
        lat, lon = float(cy.get("lat", args.lat)), float(cy.get("lon", args.lon))
        last_done = cy.get("last_completed_year")

        for year in range(args.from_year, args.to_year + 1):
            if (
                last_done is not None
                and isinstance(last_done, int)
                and year <= last_done
                and not args.force
            ):
                continue
            params, qk = openmeteo_historical_key(lat, lon, year, year, archive_base_for_keys())
            data = await openmeteo.fetch_historical_daily_json(lat, lon, year, year)
            await upsert_raw_payload(
                session,
                source_id=oid,
                query_key=qk,
                params=params,
                http_status=200,
                body=data,
                job_id=job.id,
                is_stale=False,
            )
            cy["last_completed_year"] = year
            cy["lat"], cy["lon"] = lat, lon
            job.cursor = cy
            progress = dict(job.progress or {})
            progress["years_done"] = int(progress.get("years_done", 0)) + 1
            job.progress = progress
            job.updated_at = datetime.now(UTC)
            job.status = "partial" if year < args.to_year else "completed"
            await session.commit()
            print(f"año={year} OK — job cursor={job.cursor}")

        await session.refresh(job)
        print(f"Fin — job={job.id} status={job.status} cursor={job.cursor}")
    return 0


async def cmd_firms_hotspots(args: argparse.Namespace) -> int:
    from db.models import IngestionJob
    from db.session import session_scope
    from services import firms as firms_svc
    from services.query_keys import firms_hotspots_key
    from services.read_through_agri import resolve_source_uuid
    from services.read_through_storage import upsert_raw_payload

    load_dotenv()
    async with session_scope() as session:
        if session is None:
            print("Defina DATABASE_URL.")
            return 1
        fid = await resolve_source_uuid(session, "nasa_firms")
        if fid is None:
            return 1

        job = IngestionJob(
            source_id=fid,
            job_type="firms_hotspots_area",
            status="running",
            cursor={
                "lat": args.lat,
                "lon": args.lon,
                "radius_km": args.radius_km,
                "days": args.days,
                "source": args.source,
            },
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(job)
        await session.flush()

        hotspots = await firms_svc.get_hotspots(
            args.lat,
            args.lon,
            radius_km=args.radius_km,
            days=args.days,
            source=args.source,
        )
        params, qk = firms_hotspots_key(args.lat, args.lon, args.radius_km, args.days, args.source)
        await upsert_raw_payload(
            session,
            source_id=fid,
            query_key=qk,
            params=params,
            http_status=200,
            body={"hotspots": hotspots},
            job_id=job.id,
            is_stale=False,
        )
        job.status = "completed"
        job.updated_at = datetime.now(UTC)
        await session.commit()
        print(f"FIRMS job={job.id} hotspots={len(hotspots)}")
    return 0


async def cmd_normalize_climate(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from db.models import ClimateMonthlyCell, DataSource, RawPayload
    from db.session import session_scope
    from services import openmeteo

    load_dotenv()
    async with session_scope() as session:
        if session is None:
            return 1
        stmt = (
            select(RawPayload)
            .join(DataSource, RawPayload.source_id == DataSource.id)
            .where(DataSource.slug == "open_meteo")
        )
        r = await session.execute(stmt)
        rows = list(r.scalars().all())

        touched = 0
        src_key = args.source_key
        for row in rows:
            pr = row.params or {}
            if pr.get("resource") != "historical_archive":
                continue
            if not isinstance(row.body, dict):
                continue
            df_m = openmeteo.df_from_daily_archive_payload(row.body)
            lat_r = float(pr["latitude"]) if pr.get("latitude") is not None else None
            lon_r = float(pr["longitude"]) if pr.get("longitude") is not None else None
            if lat_r is None or lon_r is None:
                continue
            for rec in df_m.to_dicts():
                y, mth = int(rec["year"]), int(rec["month"])
                ins = pg_insert(ClimateMonthlyCell).values(
                    source_key=src_key,
                    lat_round=lat_r,
                    lon_round=lon_r,
                    year=y,
                    month=mth,
                    temp_c=float(rec["temp_c"]) if rec["temp_c"] is not None else None,
                    precip_mm=float(rec["precip_mm"]) if rec["precip_mm"] is not None else None,
                    soil_moisture=(
                        float(rec["soil_moisture"]) if rec["soil_moisture"] is not None else None
                    ),
                    raw_payload_id=row.id,
                    quality_flag="derived_from_daily_archive",
                )
                ins = ins.on_conflict_do_update(
                    index_elements=["source_key", "lat_round", "lon_round", "year", "month"],
                    set_={
                        "temp_c": ins.excluded.temp_c,
                        "precip_mm": ins.excluded.precip_mm,
                        "soil_moisture": ins.excluded.soil_moisture,
                        "raw_payload_id": ins.excluded.raw_payload_id,
                        "quality_flag": ins.excluded.quality_flag,
                    },
                )
                await session.execute(ins)
                touched += 1
        await session.commit()
        print(f"Filas escritas/intentadas: {touched}")
    return 0


async def cmd_hf_register(args: argparse.Namespace) -> int:
    from db.models import DataArtifact
    from db.session import session_scope
    from services.read_through_agri import resolve_source_uuid

    load_dotenv()
    path = Path(args.path).resolve()
    byte_size = path.stat().st_size
    chk = args.checksum or _checksum(path)
    root = Path(os.getenv("ARTIFACT_STORAGE_ROOT", "./data/artifacts")).resolve()
    try:
        rel_final = str(path.relative_to(root))
    except ValueError:
        rel_final = args.rel_path or path.name

    async with session_scope() as session:
        if session is None:
            return 1
        hid = await resolve_source_uuid(session, "huggingface_hub")
        if hid is None:
            return 1
        art = DataArtifact(
            source_id=hid,
            rel_path=rel_final,
            kind=args.kind,
            revision=args.revision,
            checksum_sha256=chk,
            byte_size=int(byte_size),
            artifact_meta={"absolute_path": str(path), "repo": args.repo or ""},
        )
        session.add(art)
        await session.commit()
        print(f"data_artifacts id={art.id} rel_path={rel_final}")
    return 0


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Climatifai — ingesta y normalización")
    sub = parser.add_subparsers(dest="command", required=True)

    p_om = sub.add_parser("openmeteo-yearly", help="Un payload raw por año (Archive API)")
    p_om.add_argument("--lat", type=float, required=True)
    p_om.add_argument("--lon", type=float, required=True)
    p_om.add_argument("--from-year", type=int, required=True)
    p_om.add_argument("--to-year", type=int, required=True)
    p_om.add_argument("--job-type", default="openmeteo_archive_yearly_cell")
    p_om.add_argument("--resume-job-id", default=None)
    p_om.add_argument("--force", action="store_true")
    p_om.set_defaults(func=cmd_openmeteo_yearly)

    p_f = sub.add_parser("firms-hotspots", help="Descarga FIRMS y raw_payload")
    p_f.add_argument("--lat", type=float, required=True)
    p_f.add_argument("--lon", type=float, required=True)
    p_f.add_argument("--radius-km", type=float, default=100)
    p_f.add_argument("--days", type=int, default=7)
    p_f.add_argument("--source", default="VIIRS_SNPP_NRT")
    p_f.set_defaults(func=cmd_firms_hotspots)

    p_n = sub.add_parser("normalize-climate", help="climate_monthly_cell desde raw archive")
    p_n.add_argument("--source-key", default="openmeteo_monthly_v1")
    p_n.set_defaults(func=cmd_normalize_climate)

    p_h = sub.add_parser("hf-register", help="Registra archivo en data_artifacts")
    p_h.add_argument("--path", required=True)
    p_h.add_argument("--rel-path", default=None)
    p_h.add_argument("--kind", default="parquet")
    p_h.add_argument("--revision", default=None)
    p_h.add_argument("--repo", default=None)
    p_h.add_argument("--checksum", default=None)
    p_h.set_defaults(func=cmd_hf_register)

    args = parser.parse_args()
    raise SystemExit(asyncio.run(args.func(args)))


if __name__ == "__main__":
    main()
