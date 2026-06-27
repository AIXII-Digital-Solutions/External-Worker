from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine, AsyncSession


class DatabaseClient:
    def __init__(self):
        # DBSettings is resolved lazily from the host service's own Config so
        # that importing the Database package never requires a Config to be
        # present (e.g. for Alembic, which only needs the ORM metadata).
        from Config import DBSettings
        self.settings = DBSettings()
        self._engines: dict[str, AsyncEngine] = {}
        self._session_factories: dict[str, async_sessionmaker] = {}

    def _get_engine(self, db_name: str) -> AsyncEngine:
        """Creates or returns an engine for the specified database."""
        if db_name not in self._engines:
            db_url = self.settings.get_db_url(db_name)
            engine = create_async_engine(
                db_url,
                echo=False,
                pool_size=10,
                max_overflow=20,
                future=True,
                pool_pre_ping=True,
            )
            self._engines[db_name] = engine
            self._session_factories[db_name] = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
        return self._engines[db_name]

    @asynccontextmanager
    async def session(self, db_name: str) -> AsyncGenerator[AsyncSession | Any, Any]:
        """Context manager for working with a session of a specific database"""
        if db_name not in self._session_factories:
            self._get_engine(db_name)

        session_factory = self._session_factories[db_name]

        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self):
        for engine in self._engines.values():
            await engine.dispose()


__all__ = ['DatabaseClient']
