"""Database engine and sessions. The schema is Alembic's alone: nothing here creates tables."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from ostraka.config import Environment, get_settings

# Stable constraint names, so Alembic autogenerate produces migrations that apply cleanly
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


@lru_cache
def engine() -> AsyncEngine:
    settings = get_settings()
    if settings.env is Environment.TEST:
        # Each test runs on its own event loop; pooled connections would outlive theirs
        return create_async_engine(settings.database_url, poolclass=NullPool)
    return create_async_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def sessions() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessions()() as session:
        yield session


async def ping() -> None:
    """Raise if the database doesn't answer."""
    async with engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
