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


async def load_bulk_location_rows(session, args: argparse.Namespace, repo: Path) -> list[tuple[float, float, UUID, str]]:
    """Misma selección que ``locations-backfill-openmeteo``: tabla ``locations`` o CSV + DB."""
    import csv

    from sqlalchemy import asc, select

    from db.models import Location

    iso_allow = {c.strip().upper() for c in args.countries.split(",") if c.strip()}
    csv_path = Path(args.locations_csv) if getattr(args, "locations_csv", None) else None

    loc_rows: list[tuple[float, float, UUID, str]] = []
    if csv_path:
        csv_full = csv_path if csv_path.is_absolute() else repo / csv_path
        if not csv_full.exists():
            raise FileNotFoundError(str(csv_full))
        with csv_full.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            idx = 0
            for r in reader:
                if iso_allow and r.get("country_iso", "").strip().upper() not in iso_allow:
                    continue
                gid = int(r["geonames_id"])
                q = await session.execute(select(Location).where(Location.geonames_id == gid))
                loc_row = q.scalar_one_or_none()
                if loc_row is None:
                    print(f"No hay locations.id para geonames_id={gid} — ejecute scripts/load_locations_csv.py", flush=True)
                    continue
                loc_rows.append((float(loc_row.lat), float(loc_row.lon), loc_row.id, loc_row.name))
                idx += 1
                if args.limit > 0 and idx >= args.limit:
                    break
    else:
        q = select(Location).order_by(asc(Location.country_iso), asc(Location.name))
        if iso_allow:
            q = q.where(Location.country_iso.in_(iso_allow))
        if args.start_offset > 0:
            q = q.offset(args.start_offset)
        if args.limit > 0:
            q = q.limit(args.limit)
        r = await session.execute(q)
        for loc_row in r.scalars().all():
            loc_rows.append((float(loc_row.lat), float(loc_row.lon), loc_row.id, loc_row.name))

    return loc_rows


async def cmd_openmeteo_yearly(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from db.models import IngestionJob
    from db.session import session_scope
    from services.ingest_openmeteo import run_openmeteo_archive_yearly
    from services.read_through_agri import resolve_source_uuid

    load_dotenv()
    async with session_scope() as session:
        if session is None:
            print("Defina DATABASE_URL para usar la CLI de ingesta.")
            return 1
        oid = await resolve_source_uuid(session, "open_meteo")
        if oid is None:
            print("No se encontró data_sources.slug=open_meteo (¿migraciones aplicadas?).")
            return 1

        try:
            job = await run_openmeteo_archive_yearly(
                session,
                open_meteo_source_id=oid,
                lat=args.lat,
                lon=args.lon,
                from_year=args.from_year,
                to_year=args.to_year,
                force=args.force,
                resume_job_id=UUID(args.resume_job_id) if args.resume_job_id else None,
                job_type=args.job_type,
                location_id=UUID(args.location_id) if args.location_id else None,
            )
        except ValueError as ve:
            print(str(ve), flush=True)
            return 1
        print(f"Fin — job={job.id} status={job.status} cursor={job.cursor}", flush=True)
    return 0


async def cmd_locations_backfill_openmeteo(args: argparse.Namespace) -> int:
    from db.session import session_scope
    from services.ingest_openmeteo import run_openmeteo_archive_yearly
    from services.read_through_agri import resolve_source_uuid

    load_dotenv()
    repo = Path(__file__).resolve().parents[1]

    async with session_scope() as session:
        if session is None:
            print("Defina DATABASE_URL.", flush=True)
            return 1
        oid = await resolve_source_uuid(session, "open_meteo")
        if oid is None:
            return 1

        try:
            loc_rows = await load_bulk_location_rows(session, args, repo)
        except FileNotFoundError as fe:
            print(f"CSV no encontrado: {fe}", flush=True)
            return 1

        if args.dry_run:
            print(f"dry-run ubicaciones seleccionadas={len(loc_rows)}", flush=True)
            return 0

        total = len(loc_rows)
        for i, (lat, lon, loc_id, name) in enumerate(loc_rows):
            sleep_s = args.sleep_ms / 1000.0
            if sleep_s > 0 and i > 0:
                await asyncio.sleep(sleep_s)
            print(f"[{i + 1}/{total}] {name} ({lat},{lon}) id={loc_id}", flush=True)
            await run_openmeteo_archive_yearly(
                session,
                open_meteo_source_id=oid,
                lat=lat,
                lon=lon,
                from_year=args.from_year,
                to_year=args.to_year,
                force=args.force,
                resume_job_id=None,
                job_type="openmeteo_archive_yearly_bulk_location",
                location_id=loc_id,
            )

    print("Backfill finalizado.", flush=True)
    return 0


async def cmd_firms_hotspots(args: argparse.Namespace) -> int:
    from db.models import IngestionJob
    from db.session import session_scope
    from services import firms as firms_svc
    from services.query_keys import firms_hotspots_key
    from services.read_through_agri import resolve_source_uuid
    from services.read_through_storage import upsert_raw_payload

    load_dotenv()
    repo = Path(__file__).resolve().parents[1]

    has_point = args.lat is not None and args.lon is not None
    has_one = (args.lat is None) ^ (args.lon is None)
    if has_one:
        print("Indique ambos --lat y --lon, u omita ambos para recorrer locations.", flush=True)
        return 2

    async with session_scope() as session:
        if session is None:
            print("Defina DATABASE_URL.")
            return 1
        fid = await resolve_source_uuid(session, "nasa_firms")
        if fid is None:
            return 1

        async def run_one(lat: float, lon: float, location_id: UUID | None) -> None:
            days_eff = firms_svc.clamp_firms_area_days(args.days)
            job = IngestionJob(
                source_id=fid,
                job_type="firms_hotspots_area",
                status="running",
                cursor={
                    "lat": lat,
                    "lon": lon,
                    "radius_km": args.radius_km,
                    "days": days_eff,
                    "days_requested": args.days,
                    "source": args.source,
                    "location_id": str(location_id) if location_id else None,
                },
                started_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(job)
            await session.flush()

            hotspots = await firms_svc.get_hotspots(
                lat,
                lon,
                radius_km=args.radius_km,
                days=args.days,
                source=args.source,
            )
            params, qk = firms_hotspots_key(lat, lon, args.radius_km, days_eff, args.source)
            await upsert_raw_payload(
                session,
                source_id=fid,
                query_key=qk,
                params=params,
                http_status=200,
                body={"hotspots": hotspots},
                job_id=job.id,
                location_id=location_id,
                is_stale=False,
            )
            job.status = "completed"
            job.updated_at = datetime.now(UTC)
            await session.commit()
            print(f"FIRMS job={job.id} hotspots={len(hotspots)}", flush=True)

        if has_point:
            await run_one(args.lat, args.lon, None)
            return 0

        try:
            loc_rows = await load_bulk_location_rows(session, args, repo)
        except FileNotFoundError as fe:
            print(f"CSV no encontrado: {fe}", flush=True)
            return 1

        if args.dry_run:
            print(f"dry-run ubicaciones seleccionadas={len(loc_rows)}", flush=True)
            return 0

        total = len(loc_rows)
        for i, (lat, lon, loc_id, name) in enumerate(loc_rows):
            sleep_s = args.sleep_ms / 1000.0
            if sleep_s > 0 and i > 0:
                await asyncio.sleep(sleep_s)
            print(f"[{i + 1}/{total}] {name} ({lat},{lon}) id={loc_id}", flush=True)
            await run_one(lat, lon, loc_id)

    print("FIRMS bulk finalizado.", flush=True)
    return 0


async def cmd_normalize_climate(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from db.models import ClimateMonthlyCell, DataSource, Location, RawPayload
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
        country = (args.country or "").strip().upper()
        if country:
            stmt = stmt.join(Location, RawPayload.location_id == Location.id).where(
                RawPayload.location_id.is_not(None),
                Location.country_iso == country,
            )

        r = await session.execute(stmt)
        rows = list(r.scalars().all())

        if args.dry_run:
            n_hist = sum(1 for rp in rows if (rp.params or {}).get("resource") == "historical_archive")
            print(f"dry-run: raw_payload_open_meteo={len(rows)} historical_archive_candidates={n_hist}", flush=True)
            return 0

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
        print(f"Filas escritas/intentadas: {touched}", flush=True)
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
    p_om.add_argument(
        "--location-id",
        default=None,
        help="UUID opcional en locations — se persiste como raw_payloads.location_id",
    )
    p_om.set_defaults(func=cmd_openmeteo_yearly)

    p_bl = sub.add_parser(
        "locations-backfill-openmeteo",
        help="Por cada fila de locations (o CSV contra DB) ejecuta ingest año a año Archive",
    )
    p_bl.add_argument("--from-year", type=int, required=True)
    p_bl.add_argument("--to-year", type=int, required=True)
    p_bl.add_argument("--sleep-ms", type=int, default=400)
    p_bl.add_argument("--limit", type=int, default=0, help="0 = sin límite")
    p_bl.add_argument("--start-offset", type=int, default=0, help="OFFSET SQL respecto orden país,nombre")
    p_bl.add_argument("--countries", default="", help="ISO separados coma (filtro dentro del catálogo)")
    p_bl.add_argument(
        "--locations-csv",
        default=None,
        help="Opcional CSV (mismo formato que GeoNames extractor); resuelve geonames_id → locations en DB",
    )
    p_bl.add_argument("--dry-run", action="store_true")
    p_bl.add_argument("--force", action="store_true")
    p_bl.set_defaults(func=cmd_locations_backfill_openmeteo)

    p_f = sub.add_parser(
        "firms-hotspots",
        help="Descarga FIRMS → raw_payload. Un punto (--lat/--lon) o bulk como locations-backfill-openmeteo",
    )
    p_f.add_argument(
        "--lat",
        type=float,
        default=None,
        help="Si se omite junto con --lon, recorre la tabla locations (mismo criterio que backfill Open‑Meteo)",
    )
    p_f.add_argument("--lon", type=float, default=None)
    p_f.add_argument("--radius-km", type=float, default=100)
    p_f.add_argument(
        "--days",
        type=int,
        default=5,
        help="Ventana FIRMS Area API: solo 1–5 días (valores mayores se limitan a 5)",
    )
    p_f.add_argument("--source", default="VIIRS_SNPP_NRT")
    p_f.add_argument("--sleep-ms", type=int, default=400, help="Pausa entre ubicaciones (solo bulk)")
    p_f.add_argument("--limit", type=int, default=0, help="0 = sin límite (solo bulk)")
    p_f.add_argument("--start-offset", type=int, default=0, help="OFFSET SQL país,nombre (solo bulk)")
    p_f.add_argument("--countries", default="", help="ISO separados coma (solo bulk)")
    p_f.add_argument(
        "--locations-csv",
        default=None,
        help="CSV GeoNames contra locations en DB (solo bulk)",
    )
    p_f.add_argument("--dry-run", action="store_true")
    p_f.set_defaults(func=cmd_firms_hotspots)

    p_n = sub.add_parser("normalize-climate", help="climate_monthly_cell desde raw archive")
    p_n.add_argument("--source-key", default="openmeteo_monthly_v1")
    p_n.add_argument(
        "--country",
        default=None,
        help="Sólo payloads con raw_payloads.location_id y locations.country_iso = este ISO (ej. PE)",
    )
    p_n.add_argument("--dry-run", action="store_true")
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
