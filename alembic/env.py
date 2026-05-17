"""Alembic — migraciones sincrónicas (psycopg) sobre el mismo esquema async."""

from pathlib import Path

from dotenv import load_dotenv

_repo = Path(__file__).resolve().parent.parent
# Igual que ``scripts/list_db_tables.py``: no depender del cwd.
load_dotenv(_repo / ".env", override=False)
_front = _repo.parent / "climatifai" / ".env"
if _front.exists():
    load_dotenv(_front, override=False)

from logging.config import fileConfig

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from alembic import context

from db import models as _models  # noqa: F401
from db.base import Base
from db.config import alembic_sync_url

config = context.config
config.set_main_option("sqlalchemy.url", alembic_sync_url())

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Genera SQL sin ejecutar (modo offline)."""
    url = alembic_sync_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(alembic_sync_url(), poolclass=NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
