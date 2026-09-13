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

`find_reusable` is what makes a same-day repeat cheap: asked for a scope that already ran TODAY on the
same inputs, the panel pours that run back instead of rebuilding a dataset that exists. See the
function for what "the same inputs" has to mean, and why a profile NAME is not one of them.

COLUMNS ARE RESOLVED AT RUNTIME, not hardcoded: the copy uses the INTERSECTION of the two tables'
column lists and logs a warning naming anything it had to skip. A column added to acys_summary_by_day
without being added to acys_snapshot_rows therefore costs that one column in the snapshot instead of
failing the run outright — the fix is the migration, and the warning is what says so.

GENERATED COLUMNS ARE NEVER COPIED, in either direction. Both tables declare the same four
("Origin City&Country", "Destination City&Country", "MERGED_KEY", "DateInt") and PostgreSQL refuses a
write to any of them; each table computes them from the columns that ARE copied, so the values match
without being moved.
"""
import hashlib
import json

from sqlalchemy import text

from Config import setup_logger

logger = setup_logger("forecast_snapshots")

_LIVE = "forecast.acys_summary_by_day"
_ROWS = "forecast.acys_snapshot_rows"
_HEAD = "forecast.acys_snapshots"

_REQUEST_TYPE = "ACYS"


def params_fingerprint(params: dict, model_version: str) -> str:
    """A stable hash of the RESOLVED parameter values a run used, plus the model version.

    This is the third input a reuse decision has to compare, after the scope and the as-of date.
    Profile NAMES cannot stand in for it: tuning the model means editing the default profile's params
    and re-running, and both runs name no profile at all — so a name comparison would hand back the
    pre-tuning report and hide the very change being tested. Values, not labels.

    `default=str` renders the one non-JSON value (history_start, a date) and would render anything
    similar added later; sorted keys make the hash independent of dict order."""
    blob = json.dumps({"model_version": model_version, "params": params},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
                        as_of, profile: str | None, fingerprint: str | None = None) -> dict:
    """Copy the finished acys_summary_by_day into a new snapshot; return {id, rows}.

    The caller commits. Both the REQUESTED scope (operators / registrations as sent) and the COVERED
    scope (the operators and tails actually present in the dataset) are stored: an operator-scoped run
    names no tails, yet its dataset holds every tail of that operator, and "which runs cover N123AB"
    has to find it."""
    cols = await _shared_columns(session)
    col_sql = ", ".join(_q(c) for c in cols)

    snapshot_id = (await session.execute(text(
        f"INSERT INTO {_HEAD} (job_id, request_type, operators, registrations, as_of, profile, "
        "                      params_fingerprint) "
        "VALUES (:job_id, :rt, :ops, :regs, CAST(:as_of AS date), :profile, :fp) "
        "RETURNING id"),
        {"job_id": job_id, "rt": _REQUEST_TYPE,
         "ops": list(operators or []), "regs": list(registrations or []),
         # a real date object, not its isoformat(): CAST(:as_of AS date) makes the driver infer the
         # parameter's type as `date`, and asyncpg then refuses a string outright.
         "as_of": as_of, "profile": profile, "fp": fingerprint})).scalar_one()

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


# Scope equality is compared on the NORMALISED arrays, not on what was typed: ["Emirates"] and
# [" emirates "] are one request, and so are two orderings of the same pair of tails. Registrations
# fold to upper case (the model stores them that way), operator names to lower (a name's case is
# spelling, not identity). An empty list normalises to NULL rather than '{}' so IS NOT DISTINCT FROM
# matches "no operators" against "no operators" instead of comparing an empty array to NULL.
_NORMALISED = ("(SELECT array_agg({fn}(btrim(x)) ORDER BY {fn}(btrim(x))) FROM unnest(s.{col}) x)")

_REUSABLE_SQL = f"""
SELECT s.id, s.created_at, s.row_count
FROM {_HEAD} s
WHERE s.created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
  AND s.as_of = CAST(:as_of AS date)
  AND s.params_fingerprint IS NOT NULL
  AND s.params_fingerprint = :fp
  AND s.row_count > 0
  AND {_NORMALISED.format(fn='lower', col='operators')} IS NOT DISTINCT FROM CAST(:ops AS text[])
  AND {_NORMALISED.format(fn='upper', col='registrations')} IS NOT DISTINCT FROM CAST(:regs AS text[])
ORDER BY s.created_at DESC
LIMIT 1
"""


def _norm(values, *, upper: bool):
    """The scope as the reuse comparison sees it: trimmed, case-folded, sorted, empty -> None."""
    out = sorted({(v.strip().upper() if upper else v.strip().lower()) for v in (values or []) if v and v.strip()})
    return out or None


async def find_reusable(session, *, operators, registrations, as_of, fingerprint: str) -> dict | None:
    """Today's snapshot for exactly this request, or None.

    "Exactly this request" is four things, and all four have to match or the reused report would be a
    different report: the SCOPE (normalised, see _NORMALISED), the AS-OF date (it anchors the contract
    year and the fact/forecast boundary), the resolved PARAMETERS (see params_fingerprint) and the
    DAY. A run from yesterday is not reusable even if everything else agrees — the flight history has
    moved on, which is the whole reason a daily rebuild exists.

    The day is the UTC calendar day, the same one GET /forecast/snapshots filters its `date` on, so
    "runs listed under today" and "runs a repeat can reuse" can never disagree.

    An empty snapshot (row_count 0) is skipped: pouring it back would blank the report."""
    row = (await session.execute(text(_REUSABLE_SQL), {
        "as_of": as_of, "fp": fingerprint,
        "ops": _norm(operators, upper=False), "regs": _norm(registrations, upper=True),
    })).mappings().first()
    return dict(row) if row else None


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
