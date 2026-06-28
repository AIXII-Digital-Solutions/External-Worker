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
from Queue import EXTERNAL_QUEUE
from Database import ScheduleEntry

logger = setup_logger("scheduler")


# Default schedule for each schedulable job — seeded into schedule_registry on startup
# (insert-if-absent; operator edits via core-api are never overwritten).
# Exactly one of interval_seconds / cron_expr should be set per entry.
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
        "name": "cron_asg_regs",
        "queue": EXTERNAL_QUEUE,
        "func_name": "cron_asg_regs",
        "interval_seconds": None,
        "cron_expr": "0 9 * * 1",           # Mondays 09:00
        "description": "Refresh cirium.asg + sync ASG registrations (Cirium -> main).",
    },
    {
        "name": "cron_refresh_delta",
        "queue": EXTERNAL_QUEUE,
        "func_name": "cron_refresh_delta",
        "interval_seconds": None,
        "cron_expr": "30 9 * * 1",          # Mondays 09:30 — AFTER cron_asg_regs (09:00) so the
                                            # two heavy ciriumaircrafts scans don't run together.
                                            # Set to "0 9 * * 1" to refresh in parallel instead.
        "description": "Refresh cirium.delta materialized view (after the ASG refresh).",
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
                    enabled=True, paused=False, run_now=False,
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
