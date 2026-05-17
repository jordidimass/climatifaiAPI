from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from db import config


def require_async_database_url(url: str) -> str:
    if "+asyncpg" not in url and url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine | None:
    """Motor async; None si DATABASE_URL no está definida."""
    global _engine, _session_factory
    if not config.is_database_configured():
        _engine = None
        _session_factory = None
        return None
    if _engine is None:
        url = require_async_database_url(config.DATABASE_URL or "")
        _engine = create_async_engine(url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession | None]:
    get_engine()
    if _session_factory is None:
        yield None
        return
    async with _session_factory() as session:
        yield session


async def maybe_commit(session: AsyncSession | None) -> None:
    if session is not None:
        await session.commit()
