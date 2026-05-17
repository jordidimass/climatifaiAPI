#!/usr/bin/env python3
"""Herramienta para BD: cargar `.env` de forma fija, comprobar conexión y aplicar Alembic.

El venv solo define *qué* Python y paquetes usas; **no** pone ``DATABASE_URL``. Esa URL sale de:

1. Variables ya exportadas en la shell (tienen prioridad si están definidas).
2. ``climatifaiAPI/.env`` (primer ``load_dotenv``, sin pisar las ya definidas).
3. ``../climatifai/.env`` si existe.

Uso (desde ``climatifaiAPI/``), con el venv activado o usando la ruta completa al intérprete::

    PYTHONPATH=. .venv/bin/python scripts/supabase_db.py check
    PYTHONPATH=. .venv/bin/python scripts/supabase_db.py migrate
    PYTHONPATH=. .venv/bin/python scripts/supabase_db.py migrate --sql   # sólo imprime SQL, no aplica

Para “empezar de cero” en **Supabase** (borrar tablas de ``public`` y volver a migrar), hazlo desde el SQL Editor del proyecto (**riesgo**: borras datos reales); este script incluye el subcomando ``reset-public-help`` (sólo imprime SQL para pegar en el panel).
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_app_dotenv() -> None:
    root = repo_root()
    load_dotenv(root / ".env", override=False)
    front = root.parent / "climatifai" / ".env"
    if front.exists():
        load_dotenv(front, override=False)


def ensure_database_url() -> str:
    load_app_dotenv()
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print(
            "DATABASE_URL no está definida. Ponla en climatifaiAPI/.env o en ../climatifai/.env "
            "o exporta DATABASE_URL='postgresql://…' antes de ejecutar.",
            file=sys.stderr,
        )
        sys.exit(1)
    return url


def parse_pg_endpoint(sync_url: str) -> tuple[str, int, str]:
    pu = urlparse(sync_url.replace("postgresql+psycopg://", "postgresql://", 1))
    dbname = (pu.path or "/").strip("/") or "?"
    host = pu.hostname or "?"
    port = pu.port or 5432
    return host, port, dbname


def safe_target_line(sync_url: str) -> str:
    host, port, dbname = parse_pg_endpoint(sync_url)
    return f"Destino (host/puerto/db, sin usuario ni contraseña): {host}:{port} / {dbname}"


def diagnose_dns(hostname: str, port: int) -> bool:
    """Devuelve True si el hostname resolvió; si no, imprime ayuda por stderr."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        print(
            (
                "\nFallo DNS: no se pudo resolver el hostname del servidor "
                f"'{hostname}' ({exc}). Tu red (VPN, firewall, DNS corporativo, "
                "Pi-hole, etc.) puede estar bloqueando o no encaminando consultas "
                "externas.\n\n"
                "Qué probar:\n"
                "  • En Terminal: ping -c 1 1.1.1.1 (internet básico)\n"
                f"  • dig {hostname} +short    o    nslookup {hostname}\n"
                "  • Cambiar DNS del Wi‑Fi a 8.8.8.8 / 1.1.1.1 temporalmente\n"
                "  • Desactivar VPN / filtro DNS tipo adblock un momento\n"
                "  • macOS (varía por versión): dscacheutil -flushcache "
                "; sudo killall -HUP mDNSResponder\n"
                "\nSi el navegador y Supabase funcionan pero Python no, revisa variables "
                "HTTP_PROXY/HTTPS_PROXY/ALL_PROXY y usa el mismo host que indica Dashboard → "
                "Database (directa db.<ref>.supabase.co vs pooler aws-*.pooler.supabase.com)."
            ),
            file=sys.stderr,
        )
        return False

    v4 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET})
    v6 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET6})
    if v4:
        print(f"DNS OK (IPv4): {', '.join(v4)}")
    if v6:
        print(f"DNS OK (IPv6): {', '.join(v6)}")
    if not v4 and not v6:
        print("DNS: socket.getaddrinfo no devolvió direcciones (caso raro).", file=sys.stderr)
        return False
    return True


def cmd_check(sync_url: str) -> int:
    print(safe_target_line(sync_url))
    host, port, _ = parse_pg_endpoint(sync_url)
    if host in ("?", ""):
        print("DATABASE_URL sin hostname válido.", file=sys.stderr)
        return 1
    if not diagnose_dns(host, port):
        return 1

    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        conn_ctx = engine.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"\nConexión falló después de resolver DNS: {exc}", file=sys.stderr)
        return 1

    with conn_ctx as conn:
        row = conn.execute(
            text(
                "SELECT current_database(), current_setting('server_version'), "
                "(SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public')"
            ),
        ).one()
        print(f"current_database()      = {row[0]!r}")
        print(f"server_version          = {row[1]!r}")
        print(f"COUNT(pg_tables.public) = {row[2]}")

        names = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"),
        ).fetchall()
        print(f"Tablas en public: {len(names)}")
        for (n,) in names:
            print(f"  - {n}")

        try:
            ver = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
            print(f"alembic_version        = {ver!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"alembic_version        = (no existe o error: {exc})")
    return 0


def cmd_migrate(sql_only: bool) -> int:
    root = repo_root()
    sync = _sync_url()
    host, port, _ = parse_pg_endpoint(sync)
    if host in ("?", ""):
        print("DATABASE_URL sin hostname válido.", file=sys.stderr)
        return 1
    if not diagnose_dns(host, port):
        return 1

    argv = [sys.executable, "-m", "alembic", "upgrade", "head"]
    if sql_only:
        argv.append("--sql")
    env = {**os.environ, "PYTHONPATH": str(root)}
    print(safe_target_line(sync))
    print(f"Ejecutando en {root}: {sys.executable} -m alembic upgrade head{' --sql' if sql_only else ''}", flush=True)
    proc = subprocess.run(argv, cwd=str(root), env=env)
    return proc.returncode


def _sync_url() -> str:
    raw = os.environ["DATABASE_URL"]
    if "+asyncpg" in raw:
        return raw.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw


RESET_PUBLIC_SQL = """-- Ejecutar en Supabase SQL Editor del MISMO proyecto que tu DATABASE_URL.
-- CUIDADO: borra todo lo tuyo en public (y dependencias típicas).

DROP SCHEMA public CASCADE;
CREATE SCHEMA public;

GRANT USAGE ON SCHEMA public TO postgres, anon, authenticated, service_role;
GRANT CREATE ON SCHEMA public TO postgres;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO postgres, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON FUNCTIONS TO postgres, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES TO postgres, anon, authenticated, service_role;
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="BD Supabase/Postgres: env, check y migraciones Alembic.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="Conecta y lista tablas en public (+ alembic_version si existe)")

    p_migrate = sub.add_parser("migrate", help="Alembic upgrade head (usa los mismos .env que Alembic env.py)")
    p_migrate.add_argument(
        "--sql",
        dest="sql_only",
        action="store_true",
        help="Modo sólo-SQL (no escribe en la base; igual que alembic --sql)",
    )

    sub.add_parser(
        "reset-public-help",
        help="Imprime SQL sugerido para vaciar schema public en Supabase (tú lo pegas en el SQL Editor)",
    )

    args = parser.parse_args()

    if args.cmd == "reset-public-help":
        sys.stdout.write(RESET_PUBLIC_SQL)
        return 0

    ensure_database_url()

    if args.cmd == "check":
        return cmd_check(_sync_url())

    if args.cmd == "migrate":
        return cmd_migrate(sql_only=args.sql_only)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
