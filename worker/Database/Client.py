from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine, AsyncSession


class DatabaseClient:
    """Async session factory keyed by PHYSICAL database.

    After the AIXII consolidation there are TWO physical databases: `aixii` (every aviation
    domain as a schema) and `service`. The public API is unchanged — callers still pass the
    logical name (`main`/`cirium`/`airlabs`/`flightradar`/`aviationedge`/`service`); DBSettings
    maps it to a physical DB (``physical_db``) and the engine/session cache is keyed by that
    PHYSICAL name, so the five aviation logical names SHARE one pooled engine. Which table a
    query hits is decided by the model's schema (see Database/config.py), not by the session.
    """

    def __init__(self):
        # DBSettings is resolved lazily from the host service's own Config so that importing the
        # Database package never requires a Config to be present (e.g. for Alembic).
        from Config import DBSettings
        self.settings = DBSettings()
        self._engines: dict[str, AsyncEngine] = {}            # keyed by physical DB
        self._session_factories: dict[str, async_sessionmaker] = {}

    def _get_engine(self, db_name: str) -> AsyncEngine:
        """Return (creating on first use) the engine for the PHYSICAL DB behind ``db_name``."""
        phys = self.settings.physical_db(db_name)
        if phys not in self._engines:
            engine = create_async_engine(
                self.settings.get_db_url(db_name),
                echo=False,
                pool_size=10,
                max_overflow=20,
                future=True,
                pool_pre_ping=True,
            )
            self._engines[phys] = engine
            self._session_factories[phys] = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
        return self._engines[phys]

    @asynccontextmanager
    async def session(self, db_name: str) -> AsyncGenerator[AsyncSession | Any, Any]:
        """Context-managed session for the physical DB behind ``db_name`` (auto commit/rollback)."""
        phys = self.settings.physical_db(db_name)
        if phys not in self._session_factories:
            self._get_engine(db_name)

        session_factory = self._session_factories[phys]

        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def refresh_materialized_view(
        self, db_name: str, qualified_view: str, concurrently: bool = True
    ) -> None:
        """REFRESH a materialized view OUTSIDE any transaction.

        ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` cannot run inside a transaction block, and
        ``session()`` is transactional — so run it on a dedicated AUTOCOMMIT connection.
        CONCURRENTLY needs a UNIQUE index on the view and the view to already hold data (the
        cirium.asg / cirium.delta views are created WITH DATA, so this holds from the start).
        """
        engine = self._get_engine(db_name)
        mode = "CONCURRENTLY " if concurrently else ""
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT").execute(
                text(f"REFRESH MATERIALIZED VIEW {mode}{qualified_view}")
            )

    async def dispose(self):
        for engine in self._engines.values():
            await engine.dispose()


__all__ = ['DatabaseClient']
