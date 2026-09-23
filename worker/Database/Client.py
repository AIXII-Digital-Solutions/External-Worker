import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

from sqlalchemy import event, exc, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.util import await_only

# Pool sizing is PER PROCESS, and this process is not core-api: a worker runs batch jobs that hold a
# connection for the length of a job, so it is sized larger than the API's per-request pool. Same
# env var names as core-api so operations tunes both the same way; different defaults because the
# work is different.
_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
_POOL_RECYCLE_S = int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800"))
# A pooled connection idle for longer than this is checked before it is handed out; a busier one is not.
_PING_IDLE_S = float(os.getenv("DB_PING_IDLE_SECONDS", "30"))


def _install_idle_ping(engine: AsyncEngine) -> None:
    """Validate a pooled connection on checkout ONLY if it has been idle, in ONE round trip.

    This replaces `pool_pre_ping=True`, which pinged on EVERY checkout — and with the asyncpg
    adapter the ping is wrapped in its own transaction, measured on the wire as
    `BEGIN; ; ROLLBACK;`: three round trips before the first real statement. The database is on
    another host, so that is tens of milliseconds of pure waiting per checkout, and this worker
    opens a session per iteration in the FlightRadar polling loops.

    A connection returned to the pool moments ago is not going to be stale, so only one idle past
    DB_PING_IDLE_SECONDS is checked, with a bare `SELECT 1` on the raw asyncpg connection: no
    transaction, one round trip. A failed check raises DisconnectionError, which makes the pool
    discard that connection and hand out a fresh one — the caller never sees the dead one. The
    window this leaves (a connection that died less than DB_PING_IDLE_SECONDS after its last use,
    e.g. a database restart mid-run) fails one job per such connection and self-heals.

    Kept byte-identical to core-api's copy in `db-contract/Database/Client.py`, which is the
    source of truth for this file.
    """

    @event.listens_for(engine.sync_engine, "checkin")
    def _stamp_checkin(dbapi_connection, connection_record):
        connection_record.info["last_checkin"] = time.monotonic()

    @event.listens_for(engine.sync_engine, "checkout")
    def _ping_if_idle(dbapi_connection, connection_record, connection_proxy):
        last = connection_record.info.get("last_checkin")
        if last is None or time.monotonic() - last < _PING_IDLE_S:
            return            # freshly opened, or used moments ago
        try:
            # Runs inside the greenlet the async engine checks connections out in, so await_only is
            # legal here. The raw asyncpg connection, not the SQLAlchemy adapter: the adapter would
            # open a transaction around the ping.
            await_only(dbapi_connection._connection.execute("SELECT 1"))
        except Exception as e:
            raise exc.DisconnectionError(f"pooled connection failed its idle check: {e}") from e


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
                pool_size=_POOL_SIZE,
                max_overflow=_MAX_OVERFLOW,
                pool_recycle=_POOL_RECYCLE_S,
                pool_pre_ping=False,          # replaced by _install_idle_ping — see there
                future=True,
            )
            _install_idle_ping(engine)
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

    @asynccontextmanager
    async def pinned_session(self, db_name: str) -> AsyncGenerator[AsyncSession | Any, Any]:
        """Like ``session()``, but keeps ONE connection for its whole life — across commits.

        A normal session hands its connection back to the pool on every commit and checks one out
        again for the next statement. That is usually the same connection, but not necessarily: the
        pool is FIFO, so as soon as a second job holds a connection while this one is between
        statements, the next checkout is a DIFFERENT backend. Everything that lives on the CONNECTION
        rather than in the database is then silently gone — TEMP tables above all.

        That is what broke the forecast: the model builds its route pool and fleet once as TEMP
        tables and reads them across ~185 committed INSERTs, so any concurrent job (an FR24 poll, a
        matview refresh) could move it onto a backend where `fc_fleet_tmp` does not exist.

        Use this for work that carries connection state; plain ``session()`` for everything else, so
        long-running jobs do not hold a pooled connection for no reason.
        """
        engine = self._get_engine(db_name)
        # The session is bound to a connection it does not own, so committing does not release it;
        # the connection goes back to the pool when this context manager exits.
        async with engine.connect() as conn:
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
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
            # AsyncConnection.execution_options is a COROUTINE in SQLAlchemy 2.0 — it must be awaited before
            # .execute (else `.execute` is looked up on the coroutine object -> AttributeError). It returns
            # the connection with AUTOCOMMIT applied, so REFRESH runs outside any transaction.
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"REFRESH MATERIALIZED VIEW {mode}{qualified_view}"))

    async def dispose(self):
        for engine in self._engines.values():
            await engine.dispose()


__all__ = ['DatabaseClient']
