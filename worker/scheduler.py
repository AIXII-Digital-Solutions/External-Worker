"""
Registry-driven scheduler — the worker side of the schedule control plane.

External-Worker no longer hard-codes cron schedules. Instead:
  * core-api OWNS the ``schedule_registry`` table (the ``ScheduleEntry`` model) and edits
    it via its ``/scheduler`` API (enable/disable/pause, change interval/cron, run-now).
  * This worker runs ONE ARQ cron tick (``dispatch_due``, every minute) that reads the
    registry and enqueues any job whose ``next_run_at`` is due (or whose ``run_now`` flag
    is set), then advances ``next_run_at``. Schedules therefore change at runtime with no
    code edit or restart — for every existing and future job.
  * On startup the worker SEEDS a default row for each schedulable job if absent
    (insert-if-not-exists), so jobs are controllable out of the box without clobbering
    any edits an operator already made.

Single-owner: run with ``SCHEDULER_ENABLED=true`` on exactly ONE replica. ARQ's unique
cron job id also dedups the tick if more than one scheduler is accidentally enabled.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from Config import setup_logger
from Queue import EXTERNAL_QUEUE, ROBOT_QUEUE
from Database import ScheduleEntry

logger = setup_logger("scheduler")


# Default schedule for each schedulable job — seeded into schedule_registry on startup
# (insert-if-absent; operator edits via core-api are never overwritten).
# Exactly one of interval_seconds / cron_expr should be set per entry.
#
# TIMEZONE: cron_expr is evaluated in UTC (the dispatcher advances next_run_at from
# datetime.now(timezone.utc)). Operational target is Asia/Dubai (UTC+4, no DST), so a
# Dubai wall-clock time T maps to cron hour (T - 4). The Monday-morning Cirium chain runs
# after the scraper robot drops fresh files:
#   05:00 Dubai  scrape robot  -> "0 1 * * 1"   (core:robot, seeded paused until deployed)
#   06:00 Dubai  collapse      -> "0 2 * * *"   (daily; the Monday run leads the chain)
#   07:00 Dubai  asg           -> "0 3 * * 1"
#   07:30 Dubai  delta         -> "30 3 * * 1"
#   08:00 Dubai  plantype      -> "0 4 * * 1"
SCHEDULE_DEFAULTS = [
    {
        "name": "cron_live_flights",
        "queue": EXTERNAL_QUEUE,
        "func_name": "cron_live_flights",
        "interval_seconds": 600,            # every 10 minutes
        "cron_expr": None,
        "description": "FlightRadar live-flights adaptive poll tick.",
    },
    {
        "name": "cron_scrape_cirium",
        "queue": ROBOT_QUEUE,               # dedicated queue consumed only by the scraper robot
        "func_name": "scrape_cirium",
        "interval_seconds": None,
        "cron_expr": "0 1 * * 1",           # Mondays 05:00 Dubai (01:00 UTC) — before the refresh chain
        "enabled": True,
        "paused": True,                     # seeded PAUSED: the robot is a separate service; unpause
                                            # via core-api /scheduler once the robot worker is deployed
                                            # and consuming core:robot (else jobs pile up unconsumed).
        "description": "Trigger the external Cirium scraper robot (uploads Commercial + "
                       "Business&Helicopters exports to /files). Robot lives in its own repo.",
    },
    {
        "name": "cron_asg_regs",
        "queue": EXTERNAL_QUEUE,
        "func_name": "cron_asg_regs",
        "interval_seconds": None,
        "cron_expr": "0 3 * * 1",           # Mondays 07:00 Dubai (03:00 UTC)
        "description": "Refresh cirium.asg + sync ASG registrations (Cirium -> main).",
    },
    {
        "name": "cron_refresh_delta",
        "queue": EXTERNAL_QUEUE,
        "func_name": "cron_refresh_delta",
        "interval_seconds": None,
        "cron_expr": "30 3 * * 1",          # Mondays 07:30 Dubai (03:30 UTC) — AFTER cron_asg_regs
                                            # (07:00) so the two heavy ciriumaircrafts scans don't
                                            # run together. Set to "0 3 * * 1" to refresh in parallel.
        "description": "Refresh cirium.delta materialized view (after the ASG refresh).",
    },
    {
        "name": "cron_collapse_revisions",
        "queue": EXTERNAL_QUEUE,
        "func_name": "cron_collapse_revisions",
        "interval_seconds": None,
        "cron_expr": "0 2 * * *",           # daily 06:00 Dubai (02:00 UTC) — collapse any completed-
                                            # month live revisions per plan_type (no-op until a month
                                            # rolls over). The Monday run leads the refresh chain.
        "description": "Collapse completed-month live Cirium revisions per plan_type "
                       "(cirium.collapse_completed_months) + refresh all_* matviews.",
    },
    {
        "name": "cron_refresh_plantype_matviews",
        "queue": EXTERNAL_QUEUE,
        "func_name": "cron_refresh_plantype_matviews",
        "interval_seconds": None,
        "cron_expr": "0 4 * * 1",           # Mondays 08:00 Dubai (04:00 UTC) — after asg / delta
        "description": "Weekly refresh of cirium.all_* + historical_* aircraft-data matviews.",
    },
    {
        "name": "cron_ensure_livepositions_partition",
        "queue": EXTERNAL_QUEUE,
        "func_name": "cron_ensure_livepositions_partition",
        "interval_seconds": None,
        "cron_expr": "0 3 * * *",           # daily 03:00 — pre-create next monthly livepositions
                                            # partitions (idempotent; a no-op on most days)
        "description": "Pre-create current+next monthly flightradar.livepositions partitions "
                       "(flightradar.ensure_livepositions_partitions).",
    },
    {
        "name": "cron_refresh_airports",
        "queue": EXTERNAL_QUEUE,
        "func_name": "cron_refresh_airports",
        "interval_seconds": None,
        "cron_expr": "0 4 1 * *",           # monthly, 1st at 04:00 — reload main.airports from
                                            # OurAirports (open data) + re-apply city overrides
        "description": "Refresh main.airports from OurAirports (download + load + apply "
                       "main.airport_city_overrides).",
    },
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_run(entry: ScheduleEntry, after: datetime):
    """Next fire time from interval_seconds (preferred) or cron_expr; None if neither."""
    if entry.interval_seconds:
        return after + timedelta(seconds=int(entry.interval_seconds))
    if entry.cron_expr:
        try:
            from croniter import croniter
        except ImportError:
            logger.warning(
                "croniter not installed — cron_expr schedule '%s' cannot advance; "
                "set interval_seconds or `pip install croniter`", entry.name,
            )
            return None
        return croniter(entry.cron_expr, after).get_next(datetime)
    logger.warning("schedule '%s' has neither interval_seconds nor cron_expr", entry.name)
    return None


async def seed_registry(db_client) -> None:
    """Insert a default row for each schedulable job if it does not already exist."""
    async with db_client.session("service") as session:
        for d in SCHEDULE_DEFAULTS:
            stmt = (
                pg_insert(ScheduleEntry)
                .values(
                    name=d["name"], queue=d["queue"], func_name=d["func_name"],
                    interval_seconds=d.get("interval_seconds"), cron_expr=d.get("cron_expr"),
                    description=d.get("description"),
                    enabled=d.get("enabled", True), paused=d.get("paused", False), run_now=False,
                )
                .on_conflict_do_nothing(index_elements=["name"])
            )
            await session.execute(stmt)
    logger.info("schedule_registry seeded (%d default job(s))", len(SCHEDULE_DEFAULTS))


# When the next fire time cannot be computed (cron_expr without croniter, or a bad
# expression), park the row far in the future instead of leaving next_run_at None —
# otherwise it would read as "due" and re-fire on EVERY tick.
_SENTINEL_DELAY = timedelta(days=3650)


def _kwargs_for(entry: ScheduleEntry) -> dict:
    """Enqueue kwargs from the registry row, with arq-reserved (_-prefixed) keys stripped
    so an operator-edited kwargs blob can never collide with _queue_name/_job_id/etc."""
    return {k: v for k, v in (entry.kwargs or {}).items() if not str(k).startswith("_")}


def _set_next_run(entry: ScheduleEntry, now: datetime) -> None:
    """Advance next_run_at. If it cannot be computed, park the row far in the future and
    record an error status so it never re-fires every minute."""
    nxt = _next_run(entry, now)
    if nxt is None:
        entry.next_run_at = now + _SENTINEL_DELAY
        entry.last_status = "schedule-error: cannot compute next_run (croniter missing or bad cron_expr)"
    else:
        entry.next_run_at = nxt


async def dispatch_due(ctx) -> None:
    """ARQ cron tick (every minute): enqueue every due / run_now schedule.

    ``ctx['redis']`` is the ArqRedis pool, used to enqueue the job by name onto its
    queue. ``run_now`` forces one immediate dispatch without disturbing the regular
    cadence; a schedule-driven dispatch advances ``next_run_at``.
    """
    db_client = ctx["db_client"]
    arq = ctx["redis"]
    redis_client = ctx.get("redis_client")   # decoded client, for the cooperative pause flag
    now = _now()
    dispatched = 0

    async with db_client.session("service") as session:
        rows = (await session.execute(select(ScheduleEntry))).scalars().all()
        for e in rows:
            if not e.enabled or e.paused:
                continue

            # A never-scheduled CRON row: initialise its first fire time and DON'T fire
            # now (interval rows intentionally start immediately on first sight).
            if (not e.run_now) and e.next_run_at is None and e.cron_expr and not e.interval_seconds:
                _set_next_run(e, now)
                continue

            scheduled_due = e.next_run_at is None or e.next_run_at <= now
            if not (e.run_now or scheduled_due):
                continue

            # Cooperative queue pause (set by core-api /queues/{q}/pause): skip dispatch
            # while the target queue is paused, leaving the row due for the next tick.
            if redis_client is not None:
                try:
                    if await redis_client.exists(f"queue:paused:{e.queue}"):
                        continue
                except Exception:
                    pass

            try:
                await arq.enqueue_job(e.func_name, _queue_name=e.queue, **_kwargs_for(e))
                dispatched += 1
            except Exception as ex:
                logger.error("dispatch '%s' failed: %s", e.name, ex)
                e.last_status = f"dispatch-error: {ex}"
                e.run_now = False
                _set_next_run(e, now)   # bound retries to the schedule cadence; never re-fire every tick
                continue

            e.last_run_at = now
            e.last_status = "dispatched"
            e.run_now = False
            # Advance the cadence only on a schedule-driven run (a pure run_now keeps the
            # next scheduled time intact). _set_next_run guarantees next_run_at is set.
            if scheduled_due or e.next_run_at is None:
                _set_next_run(e, now)
        # session commits on context exit

    if dispatched:
        logger.info("dispatch_due: enqueued %d job(s)", dispatched)


__all__ = ["SCHEDULE_DEFAULTS", "seed_registry", "dispatch_due"]
