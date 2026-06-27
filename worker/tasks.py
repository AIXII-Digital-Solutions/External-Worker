"""
ARQ task wrappers for external_worker.

- On-demand tasks are enqueued by the API Server (flight summary, airports,
  guest invite, subscription refresh, manual aircraft create/update).
- cron_* coroutines are scheduled by ARQ cron (see main.py). They mirror the
  previously APScheduler-defined jobs.

Function __name__ values must match the names the API Server enqueues.
"""
import functools

from Config import setup_logger
from Schemas import InviteUserSchema
from status import publish_status

from API.FlightRadarAPI.FlightSummary import fetch_all_ranges
from API.FlightRadarAPI.AirportsAPI import load_airports as _load_airports
from API.FlightRadarAPI.LiveFlightsAPI import live_flights_adaptive
from API.MSGraphAPI.Users import invite_guest_user
from API.Utils import create_or_update_subscription, asg_regs_updater

from Scheduler.PowerPlatformJobs import update_users_job, sync_engines_from_cirium
from Scheduler.PowerPlatformJobs.Aircraft import (
    update_aircraft_templates,
    update_aircrafts as _update_aircrafts,
    update_create_aircraft_manual as _update_create_aircraft_manual,
)
from Scheduler.PowerPlatformJobs.Airline import sync_airlines

logger = setup_logger("external_worker_tasks")


def status_task(func):
    """Wrap an on-demand task with running→success/error status publishing.

    Preserves __name__ so ARQ still enqueues/registers the task by the same name.
    Uses the ARQ job id (ctx["job_id"]) as the status job_id and the function name
    as the human ref. Status is best-effort — failures to publish never mask the task.
    """
    @functools.wraps(func)
    async def wrapper(ctx, *args, **kwargs):
        db_client = ctx.get("db_client")
        redis = ctx.get("redis_client")
        job_id = ctx.get("job_id") or func.__name__
        ref = func.__name__
        if db_client is not None:
            await publish_status(db_client, redis, job_id=job_id, kind="external",
                                 ref=ref, state="running", progress=0)
        try:
            result = await func(ctx, *args, **kwargs)
        except Exception as e:
            if db_client is not None:
                await publish_status(db_client, redis, job_id=job_id, kind="external",
                                     ref=ref, state="error", message=str(e))
            raise
        if db_client is not None:
            await publish_status(db_client, redis, job_id=job_id, kind="external",
                                 ref=ref, state="success", progress=100)
        return result

    return wrapper


# -----------------------------
# On-demand tasks (enqueued by API Server)
# -----------------------------
@status_task
async def fetch_flight_summary(ctx, **kwargs):
    await fetch_all_ranges(**kwargs)


@status_task
async def load_airports(ctx, codes):
    await _load_airports(codes=codes)


@status_task
async def invite_guest(ctx, data: dict) -> bool:
    invitation = await invite_guest_user(data=InviteUserSchema(**data))
    return invitation is not None


@status_task
async def refresh_subscription(ctx, subscription_id: str | None = None):
    await create_or_update_subscription(
        db_proxy=ctx["db_proxy"],
        change_type="created",
        resource="users",
        subscription_id=subscription_id,
    )


@status_task
async def update_create_aircraft_manual(ctx, target: int):
    await _update_create_aircraft_manual(target)


# -----------------------------
# Scheduled (cron) jobs
# -----------------------------
async def cron_live_flights(ctx):
    await live_flights_adaptive()


async def cron_update_users(ctx):
    await update_users_job()


async def cron_asg_regs(ctx):
    await asg_regs_updater()


async def cron_update_ac_types(ctx):
    await update_aircraft_templates()


async def cron_update_engines(ctx):
    await sync_engines_from_cirium()


async def cron_update_airlines(ctx):
    await sync_airlines()


async def cron_update_aircrafts(ctx):
    await _update_aircrafts()


ON_DEMAND = [
    fetch_flight_summary,
    load_airports,
    invite_guest,
    refresh_subscription,
    update_create_aircraft_manual,
]
