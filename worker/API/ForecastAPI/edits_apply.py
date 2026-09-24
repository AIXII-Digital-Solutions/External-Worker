"""Apply the portal's fleet-sheet edits to the forecast report by themselves — in batches.

The portal saves every cell a user changes the moment it changes (core-api PATCH
/forecast/aircraft-details/{id}); the sheet shows it at once. The REPORT shows it once it has been
refreshed: the report chain reads acys_summary_by_day through acys_summary_by_day_effective, which
lays the edits over the model's rows (Core-API revision `acys_edits_overlay`). A refresh takes from
seconds to a minute and holds the single staging lock, so running one per saved cell would queue
refreshes behind each other and block forecast runs for nothing — only the last one would matter.

So nobody triggers it per edit. A cron tick (`cron_apply_fleet_edits`, every FLEET_EDITS_POLL_SECONDS)
asks the DATABASE whether edits are waiting — forecast.aircraft_info_edit_marks against
forecast.acys_live_state.refreshed_at, for the airlines of the run the report shows — and applies them
when EITHER

  * the editing has gone quiet: the latest edit is FLEET_EDITS_QUIET_SECONDS old, so a burst of
    edits becomes one refresh, run after the user has finished; or
  * the oldest unapplied edit has waited FLEET_EDITS_MAX_WAIT_SECONDS, so someone who never stops
    editing still sees the report catch up.

Reading the state from the database rather than being told about each edit is the point: it sees
every edit however it was made (another user, a revert of a whole airline, a script), survives a
worker or Redis restart, and needs nothing from core-api.

It STEPS ASIDE for forecast work. A forecast run or restore queued or running -> skip: a run applies
every edit itself when it finishes. The staging lock busy -> skip, try next tick. And a run that
starts while an apply holds the lock waits it out (acquire_staging_lock's wait) instead of failing.

The apply itself is `run_forecast_restore` on the snapshot the report already shows, which then skips
the pour and only refreshes — same steps, same status contract, ref `forecast_edits_apply`, so the
portal can tell an automatic apply from a user's restore.
"""
import uuid

from sqlalchemy import text

from Config import setup_logger
from settings import FLEET_EDITS_MAX_WAIT_SECONDS, FLEET_EDITS_QUIET_SECONDS

from .panel import _DB, acquire_staging_lock, release_staging_lock
from .restore import run_forecast_restore
from .snapshots import get_live_snapshot

logger = setup_logger("fleet_edits_apply")

APPLY_REF = "forecast_edits_apply"

# Edits waiting for the report, restricted to the airlines of the run it shows (an edit of an airline
# that is not loaded has nothing to refresh — its own run will pick it up). No live snapshot (a run or
# restore is rewriting the table, or the last one failed) -> no row: nothing to apply to.
_DUE_SQL = """
SELECT ls.snapshot_id,
       max(mk.changed_at)                                  AS last_change,
       min(coalesce(mk.pending_since, mk.changed_at))      AS first_pending,
       array_agg(mk.airline ORDER BY mk.airline)           AS airlines,
       clock_timestamp()                                   AS now
FROM forecast.acys_live_state ls
JOIN forecast.acys_snapshots s ON s.id = ls.snapshot_id
JOIN forecast.aircraft_info_edit_marks mk
  ON (ls.refreshed_at IS NULL OR mk.changed_at > ls.refreshed_at)
 AND mk.airline = ANY(s.covered_operators)
WHERE ls.id = 1
GROUP BY ls.snapshot_id
"""

# Forecast work queued or running (core-api writes the `queued` row before enqueuing). Same staleness
# rule as core-api's own busy check: a row not republished for 15 minutes is a dead worker, not a run.
_BUSY_SQL = """
SELECT job_id, ref, state FROM job_statuses
WHERE ref IN ('forecast_panel', 'forecast_restore')
  AND state NOT IN ('success', 'error', 'skipped', 'cancelled')
  AND updated_at > now() - interval '15 minutes'
LIMIT 1
"""


async def apply_fleet_edits_if_due(db_client, redis) -> dict:
    """One tick: apply the waiting edits if they are due; return what was decided and why."""
    async with db_client.session(_DB) as s:
        row = (await s.execute(text(_DUE_SQL))).mappings().first()
    if row is None:
        return {"applied": False, "reason": "nothing waiting"}

    quiet = (row["now"] - row["last_change"]).total_seconds()
    waited = (row["now"] - row["first_pending"]).total_seconds()
    if quiet < FLEET_EDITS_QUIET_SECONDS and waited < FLEET_EDITS_MAX_WAIT_SECONDS:
        return {"applied": False, "reason": "still editing", "quiet_s": round(quiet, 1),
                "waited_s": round(waited, 1)}

    async with db_client.session("service") as s:
        busy = (await s.execute(text(_BUSY_SQL))).mappings().first()
    if busy is not None:
        return {"applied": False, "reason": f"{busy['ref']} {busy['job_id']} is {busy['state']}"}

    try:
        lock = await acquire_staging_lock(db_client)     # no wait: busy now, try next tick
    except RuntimeError:
        return {"applied": False, "reason": "staging table busy"}

    snapshot_id = int(row["snapshot_id"])
    try:
        # Checked again UNDER the lock: a run that finished between the query and the lock has put a
        # different run in the table, and applying to the old snapshot would restore it over the new.
        async with db_client.session(_DB) as s:
            live = await get_live_snapshot(s)
    except BaseException:
        await release_staging_lock(lock)
        raise
    if live != snapshot_id:
        await release_staging_lock(lock)
        return {"applied": False, "reason": "the report changed runs meanwhile"}

    logger.info("applying fleet edits of %s to report %s (quiet %.0fs, oldest waited %.0fs)",
                ", ".join(row["airlines"]), snapshot_id, quiet, waited)
    summary = await run_forecast_restore(db_client=db_client, redis=redis,
                                         job_id=f"edits-{uuid.uuid4().hex}", ref=APPLY_REF,
                                         snapshot_id=snapshot_id, staging_lock=lock)
    return {"applied": True, "snapshot_id": snapshot_id, "airlines": list(row["airlines"]),
            "summary": summary}
