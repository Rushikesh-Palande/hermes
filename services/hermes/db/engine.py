"""
Async SQLAlchemy engine and session factory.

A single engine is created lazily on first use and cached for the lifetime
of the process. Pool configuration is deliberately conservative (a Pi 4
with 2 GB RAM is the target hardware); tune via env vars in production.

Usage:

    from hermes.db.engine import async_session

    async with async_session() as session:
        result = await session.execute(select(User).where(...))
        users = result.scalars().all()

The `async_session()` returned is an `AsyncSession` with `expire_on_commit=False`
so objects stay usable after a commit — the alternative caused us a surprising
number of "DetachedInstanceError" bugs in the legacy codebase.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from hermes.config import get_settings


@lru_cache(maxsize=1)
def _engine() -> AsyncEngine:
    """
    Lazy-init the process-wide async engine.

    pool_size + max_overflow capped at values suitable for a Pi 4.
    pool_pre_ping detects broker-flapped connections without waiting
    for SQLAlchemy's idle-check cycle.
    """
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,  # recycle after 30 min; dodges edge-case NAT resets
        echo=settings.hermes_dev_mode,  # verbose SQL only in dev
    )


@lru_cache(maxsize=1)
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=_engine(),
        class_=AsyncSession,
        expire_on_commit=False,  # keep objects usable after commit
        autoflush=False,  # explicit flushes only; predictable perf
    )


@asynccontextmanager
async def async_session() -> AsyncIterator[AsyncSession]:
    """
    Context-managed AsyncSession. Commits on clean exit, rolls back on
    exception. Use for short-lived request-scoped work.

    For streaming long-running queries (e.g. SSE feed), acquire the
    session manually and close it when the stream terminates.
    """
    session = _session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    """
    Close all pool connections. Call from the FastAPI shutdown hook so
    the process exits cleanly without dangling Postgres connections.
    """
    engine = _engine()
    await engine.dispose()
