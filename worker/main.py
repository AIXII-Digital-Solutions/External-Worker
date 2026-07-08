"""
external_worker — dedicated worker for all external-API interaction
(FlightRadar, Aviation Edge, Airlabs, MS Graph) and the scheduled domain jobs.

- On-demand work is enqueued by the API Server via ARQ (Redis broker).
- Recurring work runs as ARQ cron jobs (replacing the old APScheduler jobs).
  Cron jobs are enabled only when SCHEDULER_ENABLED is truthy, preserving the
  previous default (all scheduled jobs were disabled).
"""
import os

# settings must be imported first: it loads this service's own .env before the
# shared Config (and anything importing it) is initialised.
import settings  # noqa: F401

from arq import cron, func
from redis.asyncio import Redis

from Config import setup_logger, DBSettings
from Database import DatabaseClient
from Queue import get_redis_settings, EXTERNAL_QUEUE
from Utils import DBProxy

import tasks
import scheduler
import pools

logger = setup_logger("external_worker")

SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "false").lower() in ("1", "true", "yes", "on")
# Async fan-out: how many jobs this process runs concurrently. IO-bound work scales well
# here; raise it (and run more replicas) for more throughput. ARQ's own default is 10.
try:
    MAX_JOBS = int(os.getenv("MAX_JOBS", "10"))
except ValueError:
    MAX_JOBS = 10


async def startup(ctx):
    username, password, host, port = DBSettings().get_reddis_credentials()
    ctx["redis_client"] = Redis(username=username or None, password=password or None, host=host, port=port, decode_responses=True)
    ctx["db_proxy"] = DBProxy(ctx["redis_client"])
    ctx["db_client"] = DatabaseClient()
    # Seed default schedule rows (insert-if-absent) so jobs are controllable out of the
    # box. Best-effort: a transient service-DB outage must not stop the worker booting.
    try:
        await scheduler.seed_registry(ctx["db_client"])
    except Exception as e:
        logger.warning(f"schedule_registry seed skipped: {e}")
    logger.info(f"external_worker started (scheduler_enabled={SCHEDULER_ENABLED})")


async def shutdown(ctx):
    try:
        await ctx["redis_client"].aclose()
    except Exception:
        pass
    await ctx["db_client"].dispose()
    pools.shutdown_pool()
    logger.info("external_worker shut down")


# Schedules are no longer hard-coded. ARQ runs a SINGLE cron tick (every minute) — the
# registry dispatcher — which reads schedule_registry and enqueues due/forced jobs. All
# schedule changes (interval/cron/enable/disable/run-now) happen at runtime via core-api.
# Gated by SCHEDULER_ENABLED so the dispatcher runs on exactly ONE replica.
_CRON_JOBS = [
    cron(scheduler.dispatch_due),     # default arq cron => fires at second 0 of every minute
]


class WorkerSettings:
    redis_settings = get_redis_settings()
    queue_name = EXTERNAL_QUEUE
    # ON_DEMAND: enqueued by core-api. SCHEDULED: enqueued by the dispatcher / run-now —
    # both must be registered here so ARQ can resolve them by name. forecast_panel does a bounded
    # FR24 fetch that can exceed arq's default 300s job timeout, so it gets its own longer timeout.
    functions = [
        func(f, name="forecast_panel", timeout=settings.FORECAST_JOB_TIMEOUT_SECONDS)
        if f is tasks.forecast_panel else f
        for f in (list(tasks.ON_DEMAND) + list(tasks.SCHEDULED))
    ]
    cron_jobs = _CRON_JOBS if SCHEDULER_ENABLED else []
    max_jobs = MAX_JOBS                       # concurrent jobs per process (env MAX_JOBS)
    on_startup = startup
    on_shutdown = shutdown
