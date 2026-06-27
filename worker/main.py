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

from arq import cron
from redis.asyncio import Redis

from Config import setup_logger, DBSettings
from Database import DatabaseClient
from Queue import get_redis_settings, EXTERNAL_QUEUE
from Utils import DBProxy

import tasks

logger = setup_logger("external_worker")

SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "false").lower() in ("1", "true", "yes", "on")


async def startup(ctx):
    username, password, host, port = DBSettings().get_reddis_credentials()
    ctx["redis_client"] = Redis(username=username or None, password=password or None, host=host, port=port, decode_responses=True)
    ctx["db_proxy"] = DBProxy(ctx["redis_client"])
    ctx["db_client"] = DatabaseClient()
    logger.info(f"external_worker started (scheduler_enabled={SCHEDULER_ENABLED})")


async def shutdown(ctx):
    try:
        await ctx["redis_client"].aclose()
    except Exception:
        pass
    await ctx["db_client"].dispose()
    logger.info("external_worker shut down")


# Cron schedule mirrors the original (disabled) APScheduler jobs.
_CRON_JOBS = [
    cron(tasks.cron_live_flights, minute=set(range(0, 60, 10))),       # every 10 min
    cron(tasks.cron_update_users, minute=set(range(0, 60, 10))),       # every 10 min
    cron(tasks.cron_asg_regs, weekday="mon", hour=9, minute=0),
    cron(tasks.cron_update_ac_types, weekday="mon", hour=9, minute=5),
    cron(tasks.cron_update_engines, weekday="mon", hour=9, minute=5),
    cron(tasks.cron_update_airlines, weekday="mon", hour=9, minute=5),
    cron(tasks.cron_update_aircrafts, weekday="mon", hour=9, minute=10),
]


class WorkerSettings:
    redis_settings = get_redis_settings()
    queue_name = EXTERNAL_QUEUE
    functions = list(tasks.ON_DEMAND)
    cron_jobs = _CRON_JOBS if SCHEDULER_ENABLED else []
    on_startup = startup
    on_shutdown = shutdown
