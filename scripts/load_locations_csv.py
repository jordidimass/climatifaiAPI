#!/usr/bin/env python3
"""Carga/actualiza filas ``locations`` desde CSV generado por build_locations_from_geonames.py."""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

from pathlib import Path

from dotenv import load_dotenv

from sqlalchemy.dialects.postgresql import insert as pg_insert

import importlib

from db.models import Location
from db.session import session_scope


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_app_dotenv() -> None:
    root = repo_root()
    load_dotenv(root / ".env", override=False)
    front = root.parent / "climatifai" / ".env"
    if front.exists():
        load_dotenv(front, override=False)
    import db.config as cfg  # noqa: WPS433

    importlib.reload(cfg)


async def upsert_many(rows: list[dict]) -> None:
    if not rows:
        print("CSV vacío", flush=True)
        return
    async with session_scope() as session:
        if session is None:
            raise SystemExit("Defina DATABASE_URL")
        inserted = 0
        chunk: list[dict] = []

        async def flush_batch() -> None:
            nonlocal inserted, chunk
            if not chunk:
                return
            ins = pg_insert(Location).values(chunk)
            ins = ins.on_conflict_do_update(
                constraint="uq_locations_geonames_id",
                set_={
                    "country_iso": ins.excluded.country_iso,
                    "admin1_code": ins.excluded.admin1_code,
                    "kind": ins.excluded.kind,
                    "feature_code": ins.excluded.feature_code,
                    "name": ins.excluded.name,
                    "ascii_name": ins.excluded.ascii_name,
                    "lat": ins.excluded.lat,
                    "lon": ins.excluded.lon,
                    "catalog_version": ins.excluded.catalog_version,
                },
            )
            await session.execute(ins)
            inserted += len(chunk)
            chunk = []

        for raw in rows:
            chunk.append(
                {
                    "geonames_id": int(raw["geonames_id"]),
                    "country_iso": raw["country_iso"].strip().upper(),
                    "admin1_code": raw.get("admin1_code") or None,
                    "kind": raw.get("kind") or "capital",
                    "feature_code": raw.get("feature_code") or None,
                    "name": raw["name"],
                    "ascii_name": raw.get("ascii_name") or None,
                    "lat": raw["lat"],
                    "lon": raw["lon"],
                    "catalog_version": raw.get("catalog_version"),
                    "meta": {},
                },
            )
            if len(chunk) >= 200:
                await flush_batch()
        await flush_batch()
        await session.commit()
        print(f"Upsert locales={inserted}", flush=True)


async def main_async(args: argparse.Namespace) -> None:
    p = Path(args.csv)
    if not p.exists():
        raise SystemExit(f"No existe CSV: {p}")

    rows: list[dict] = []
    with p.open(newline="", encoding="utf-8") as fh:
        dr = csv.DictReader(fh)
        required = {"geonames_id", "country_iso", "name", "lat", "lon"}
        missing = required - set(dr.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV sin columnas: {missing}")

        iso_filter = {c.strip().upper() for c in args.countries.split(",") if c.strip()}
        limit = args.limit if args.limit and args.limit > 0 else None
        seen = 0
        for r in dr:
            if iso_filter and r["country_iso"].strip().upper() not in iso_filter:
                continue
            rows.append(
                {
                    "geonames_id": r["geonames_id"],
                    "country_iso": r["country_iso"],
                    "admin1_code": r.get("admin1_code", ""),
                    "feature_code": r.get("feature_code", ""),
                    "kind": r.get("kind", "capital"),
                    "name": r["name"],
                    "ascii_name": r.get("ascii_name"),
                    "lat": round(float(r["lat"]), 4),
                    "lon": round(float(r["lon"]), 4),
                    "catalog_version": r.get("catalog_version"),
                },
            )
            seen += 1
            if limit is not None and seen >= limit:
                break

    await upsert_many(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga locations desde CSV GeoNames-derived")
    parser.add_argument("--csv", default="data/catalogs/geonames/locations.csv")
    parser.add_argument("--countries", default="", help="Filtrar por ISO separados coma")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    load_app_dotenv()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
