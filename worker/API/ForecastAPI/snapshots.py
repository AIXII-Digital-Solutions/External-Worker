"""The last month of forecast runs — save / list-scope / restore / prune.

forecast.acys_summary_by_day holds exactly ONE run: every request TRUNCATEs it and rebuilds it, so the
previous dataset is gone the moment the next request starts. This module keeps a short history of that
table (Core-API migration `forecast_run_snapshots`):

    forecast.acys_snapshots      one row per run  — when, what was asked for, what it covers
    forecast.acys_snapshot_rows  the dataset      — snapshot_id + a copy of acys_summary_by_day's columns

`save_snapshot` is called at the end of a successful panel run, `prune_snapshots` right after it drops
everything older than the retention window, and `restore_snapshot` pours a stored run back into
acys_summary_by_day for the `forecast_restore` job (no fetch, no model — the rows and the matview
refresh that follows them).

COLUMNS ARE RESOLVED AT RUNTIME, not hardcoded: the copy uses the INTERSECTION of the two tables'
column lists and logs a warning naming anything it had to skip. A column added to acys_summary_by_day
without being added to acys_snapshot_rows therefore costs that one column in the snapshot instead of
failing the run outright — the fix is the migration, and the warning is what says so.

GENERATED COLUMNS ARE NEVER COPIED, in either direction. Both tables declare the same four
("Origin City&Country", "Destination City&Country", "MERGED_KEY", "DateInt") and PostgreSQL refuses a
write to any of them; each table computes them from the columns that ARE copied, so the values match
without being moved.
"""
from sqlalchemy import text

from Config import setup_logger

logger = setup_logger("forecast_snapshots")

_LIVE = "forecast.acys_summary_by_day"
_ROWS = "forecast.acys_snapshot_rows"
_HEAD = "forecast.acys_snapshots"

_REQUEST_TYPE = "ACYS"


def _q(col: str) -> str:
    """Quote one column name — they carry spaces and capitals ("Time Departed", "MERGED_KEY")."""
    return '"' + col.replace('"', '""') + '"'


async def _shared_columns(session) -> list[str]:
    """The WRITABLE columns both tables have, in acys_summary_by_day's order.

    Generated columns are dropped here rather than at each call site: they cannot be written to in
    either table, and each side recomputes them from the columns this list does carry.

    Warns about drift in either direction: a live column missing from the snapshot table is data that
    will not be kept, and a snapshot column missing from the live table is a leftover the restore
    cannot fill."""
    rows = (await session.execute(text(
        "SELECT table_name, column_name, is_generated FROM information_schema.columns "
        "WHERE table_schema = 'forecast' "
        "  AND table_name IN ('acys_summary_by_day', 'acys_snapshot_rows') "
        "ORDER BY table_name, ordinal_position"))).all()
    generated = {c for _t, c, g in rows if g != "NEVER"}
    live = [c for t, c, _g in rows if t == "acys_summary_by_day" and c not in generated]
    snap = {c for t, c, _g in rows if t == "acys_snapshot_rows" and c not in generated}
    shared = [c for c in live if c in snap]
    missing = [c for c in live if c not in snap]
    if missing:
        logger.warning("acys_snapshot_rows is missing %d column(s) of acys_summary_by_day and will not "
                       "keep them: %s — add them in a migration", len(missing), ", ".join(missing))
    extra = sorted(snap - set(live) - {"snapshot_id"})
    if extra:
        logger.warning("acys_snapshot_rows has %d column(s) acys_summary_by_day no longer has: %s",
                       len(extra), ", ".join(extra))
    return shared


async def save_snapshot(session, *, job_id: str | None, operators, registrations,
                        as_of, profile: str | None) -> dict:
    """Copy the finished acys_summary_by_day into a new snapshot; return {id, rows}.

    The caller commits. Both the REQUESTED scope (operators / registrations as sent) and the COVERED
    scope (the operators and tails actually present in the dataset) are stored: an operator-scoped run
    names no tails, yet its dataset holds every tail of that operator, and "which runs cover N123AB"
    has to find it."""
    cols = await _shared_columns(session)
    col_sql = ", ".join(_q(c) for c in cols)

    snapshot_id = (await session.execute(text(
        f"INSERT INTO {_HEAD} (job_id, request_type, operators, registrations, as_of, profile) "
        "VALUES (:job_id, :rt, :ops, :regs, CAST(:as_of AS date), :profile) "
        "RETURNING id"),
        {"job_id": job_id, "rt": _REQUEST_TYPE,
         "ops": list(operators or []), "regs": list(registrations or []),
         # a real date object, not its isoformat(): CAST(:as_of AS date) makes the driver infer the
         # parameter's type as `date`, and asyncpg then refuses a string outright.
         "as_of": as_of, "profile": profile})).scalar_one()

    res = await session.execute(text(
        f"INSERT INTO {_ROWS} (snapshot_id, {col_sql}) "
        f"SELECT :sid, {col_sql} FROM {_LIVE}"), {"sid": snapshot_id})
    n_rows = res.rowcount

    await session.execute(text(
        f"UPDATE {_HEAD} SET row_count = :n, "
        "    covered_operators = coalesce((SELECT array_agg(DISTINCT r.\"Operator\") "
        f"        FROM {_ROWS} r WHERE r.snapshot_id = :sid AND r.\"Operator\" IS NOT NULL), '{{}}'), "
        "    covered_registrations = coalesce((SELECT array_agg(DISTINCT r.\"Registration\") "
        f"        FROM {_ROWS} r WHERE r.snapshot_id = :sid AND r.\"Registration\" IS NOT NULL), '{{}}') "
        "WHERE id = :sid"), {"n": n_rows, "sid": snapshot_id})

    logger.info("forecast snapshot %s saved (%d rows)", snapshot_id, n_rows)
    return {"id": int(snapshot_id), "rows": n_rows}


async def prune_snapshots(session, *, retention_days: int) -> dict:
    """Drop every snapshot older than the retention window; return what went.

    Rows are deleted before the headers instead of leaning on ON DELETE CASCADE, so the big delete is
    one set-based statement rather than a per-header cascade, and the count is reportable."""
    params = {"d": int(retention_days)}
    rows = (await session.execute(text(
        f"DELETE FROM {_ROWS} r USING {_HEAD} s "
        "WHERE r.snapshot_id = s.id AND s.created_at < now() - make_interval(days => :d)"),
        params)).rowcount
    snaps = (await session.execute(text(
        f"DELETE FROM {_HEAD} WHERE created_at < now() - make_interval(days => :d)"),
        params)).rowcount
    if snaps:
        logger.info("pruned %d forecast snapshot(s) older than %d days (%d rows)",
                    snaps, retention_days, rows)
    return {"snapshots": snaps, "rows": rows}


async def get_snapshot(session, snapshot_id: int) -> dict | None:
    """One snapshot header, or None if it is gone (pruned, or never existed)."""
    row = (await session.execute(text(
        "SELECT id, created_at, job_id, request_type, operators, registrations, "
        "       covered_operators, covered_registrations, as_of, profile, row_count "
        f"FROM {_HEAD} WHERE id = :sid"), {"sid": int(snapshot_id)})).mappings().first()
    return dict(row) if row else None


async def restore_snapshot(session, snapshot_id: int) -> int:
    """Replace acys_summary_by_day's contents with the snapshot's rows; return the row count.

    TRUNCATE, not DELETE: the live table holds ONE run, and the next panel run TRUNCATEs it the same
    way — so the restored rows keep their original `id` values (the sequence is left where it is;
    nothing can collide with an empty table). The caller commits, and MUST refresh the report matviews
    afterwards or the report still shows the run being replaced."""
    cols = await _shared_columns(session)
    col_sql = ", ".join(_q(c) for c in cols)
    await session.execute(text(f"TRUNCATE {_LIVE}"))
    res = await session.execute(text(
        f"INSERT INTO {_LIVE} ({col_sql}) "
        f"SELECT {col_sql} FROM {_ROWS} WHERE snapshot_id = :sid"), {"sid": int(snapshot_id)})
    return res.rowcount


async def mark_restored(session, snapshot_id: int) -> None:
    """Record that this snapshot is the one now sitting in the live table."""
    await session.execute(text(
        f"UPDATE {_HEAD} SET restored_at = now(), restore_count = restore_count + 1 "
        "WHERE id = :sid"), {"sid": int(snapshot_id)})
