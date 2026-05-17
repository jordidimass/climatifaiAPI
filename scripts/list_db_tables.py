#!/usr/bin/env python3
"""Lista tablas del esquema ``public``, ``alembic_version`` y la identidad de la sesión de Postgres.

Imprime sólo ``host``, ``puerto`` y ``dbname`` derivados de la URL (sin usuario ni contraseña),
y ``current_database()`` / ``server_version`` tras conectar, para poder contrastar el destino real
con el proyecto abierto en el panel de Supabase.

Uso (desde ``climatifaiAPI/``):
    PYTHONPATH=. .venv/bin/python scripts/list_db_tables.py
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    load_dotenv(repo / ".env", override=False)
    front = repo.parent / "climatifai" / ".env"
    if front.exists():
        load_dotenv(front, override=False)

    import db.config as cfg

    cfg = importlib.reload(cfg)
    if not cfg.DATABASE_URL:
        print("DATABASE_URL no definida en el entorno después de cargar .env", file=sys.stderr)
        return 1

    url = cfg.alembic_sync_url()
    pu = urlparse(url.replace("postgresql+psycopg://", "postgresql://", 1))
    dbname = (pu.path or "/").strip("/") or "?"
    host = pu.hostname or "?"
    port = pu.port or 5432
    print(f"Conexión (solo host/puerto/db, sin credenciales): {host}:{port} / db={dbname}")

    engine = create_engine(url, pool_pre_ping=True)

    with engine.connect() as conn:
        ident = conn.execute(
            text(
                "SELECT current_database(), current_setting('server_version'), "
                "COALESCE(inet_server_addr()::text, '(null)')"
            ),
        ).one()
        print(f"current_database()   = {ident[0]!r}")
        print(f"server_version         = {ident[1]!r}")
        print(f"inet_server_addr()     = {ident[2]!r}")

        try:
            ver = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
            print(f"alembic_version.version_num = {ver!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"(sin tabla alembic_version o error: {exc})", file=sys.stderr)

        rows = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"),
        ).fetchall()

        print(f"Tablas en public: {len(rows)}")
        for (name,) in rows:
            print(f"  - {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
