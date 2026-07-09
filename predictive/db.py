"""Standalone DB helper for the External-Worker forecasting-model development package.

Reads credentials straight from the repo-root ``.env`` (no secrets in code) and talks to the shared
``aixii`` Postgres via ``asyncpg`` — mirrors Core-API's ``predictive/db.py`` (the archetype step-1
harness) but points at THIS repo's .env. Read/DDL: the model (re)builds ``api.af_*`` / ``forecast.*``
helper matviews and reads ``forecast.acys_actuals`` + ``cirium.*`` + ``flightradar.flightsummary``.
"""
from __future__ import annotations

from pathlib import Path

import asyncpg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SQL_DIR = Path(__file__).resolve().parent / "sql"


def conn_params(database: str | None = None) -> dict:
    """asyncpg connection kwargs from .env; default DB = DB_AIXII_NAME (the aviation cluster)."""
    cfg = dotenv_values(ENV_PATH)
    if not cfg.get("DB_USER") or not cfg.get("DB_PASSWORD"):
        raise RuntimeError(f"DB_USER/DB_PASSWORD missing in {ENV_PATH}")
    return dict(
        user=cfg["DB_USER"],
        password=cfg["DB_PASSWORD"],
        host=cfg.get("DB_HOST", "localhost"),
        port=int(cfg.get("DB_PORT", 5432)),
        database=database or cfg.get("DB_AIXII_NAME") or "aixii",
    )


class DB:
    """A single long-lived asyncpg connection with small helpers (no ORM, no app imports)."""

    def __init__(self, database: str | None = None, statement_timeout_ms: int = 0):
        self._params = conn_params(database)
        self._statement_timeout_ms = statement_timeout_ms
        self._conn: asyncpg.Connection | None = None

    async def __aenter__(self) -> "DB":
        self._conn = await asyncpg.connect(**self._params, timeout=60)
        await self._conn.execute(f"SET statement_timeout = {int(self._statement_timeout_ms)}")
        return self

    async def __aexit__(self, *exc) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> asyncpg.Connection:
        if self._conn is None:
            raise RuntimeError("DB not connected; use `async with DB() as db:`")
        return self._conn

    async def execute(self, sql: str) -> str:
        return await self.conn.execute(sql)

    async def execute_file(self, name_or_path: str, **subs: str) -> str:
        """Run a .sql file. ``subs`` does literal ``{key}`` replacement (avoid str.format so SQL
        braces / $$ never collide)."""
        path = Path(name_or_path)
        if not path.is_absolute():
            path = SQL_DIR / name_or_path
        sql = path.read_text(encoding="utf-8")
        for k, v in subs.items():
            sql = sql.replace("{" + k + "}", v)
        return await self.conn.execute(sql)

    async def fetch(self, sql: str, *args) -> list[asyncpg.Record]:
        return await self.conn.fetch(sql, *args)

    async def fetch_val(self, sql: str, *args):
        return await self.conn.fetchval(sql, *args)

    async def fetch_df(self, sql: str, *args):
        """Rows as a pandas DataFrame (pandas imported lazily — not needed for pure-SQL steps)."""
        import pandas as pd
        rows = await self.conn.fetch(sql, *args)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows], columns=list(rows[0].keys()))
