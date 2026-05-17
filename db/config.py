"""Configuración de base de datos (DATABASE_URL opcional para dev sin Postgres)."""

from __future__ import annotations

import os

DATABASE_URL: str | None = os.getenv("DATABASE_URL") or None

# Artefactos HF / archivos grandes (rutas relativas bajo artefact_storage)
ARTIFACT_STORAGE_ROOT: str = os.getenv("ARTIFACT_STORAGE_ROOT", "./data/artifacts")


def alembic_sync_url() -> str:
    """Convierte la URL async a modo síncrono (psycopg) para migraciones Alembic."""
    raw = DATABASE_URL
    if not raw:
        # Postgres local típico (Homebrew/Mac): usuario = login del SO, sin rol ``postgres``.
        login = os.getenv("USER") or os.getenv("USERNAME") or ""
        suffix = "@localhost:5432/climatifai"
        base = login if login else "postgres"
        return f"postgresql+psycopg://{base}{suffix}"

    url = raw
    if "+asyncpg" in url:
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url



def is_database_configured() -> bool:
    return bool(DATABASE_URL)
