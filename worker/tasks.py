"""
ARQ task wrappers for external_worker.

- On-demand tasks are enqueued by the API Server (flight summary, airports,
  subscription refresh).
- cron_* coroutines are registry-driven schedulable jobs (see scheduler.py / main.py).

Function __name__ values must match the names the API Server / dispatcher enqueue.
"""
import functools

from Config import setup_logger
from status import publish_status

from API.FlightRadarAPI.FlightSummary import fetch_all_ranges
from API.FlightRadarAPI.AirportsAPI import load_airports as _load_airports
from API.FlightRadarAPI.LiveFlightsAPI import live_flights_adaptive
from API.Utils import (create_or_update_subscription, asg_regs_updater, refresh_cirium_delta,
                       collapse_completed_revisions, refresh_plantype_matviews,
                       ensure_livepositions_partitions)
from API.Predictive.PredictiveUtilisation import predictive_utilisation_pipeline, predictive_cleanup
from API.ForecastAPI import run_forecast_panel

logger = setup_logger("external_worker_tasks")


def status_task(func):
    """Wrap a task with running→success/error status publishing.

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
async def predictive_utilisation(ctx, icao, iata, date, deep_research=False, **_):
    await predictive_utilisation_pipeline(icao=icao, iata=iata, date=date, deep_research=deep_research)


@status_task
async def load_airports(ctx, codes):
    await _load_airports(codes=codes)


@status_task
async def refresh_subscription(ctx, subscription_id: str | None = None):
    await create_or_update_subscription(
        db_proxy=ctx["db_proxy"],
        change_type="created",
        resource="users",
        subscription_id=subscription_id,
    )


# NOT @status_task-wrapped: it publishes its OWN sequential per-step statuses (validating ->
# preparing -> fr24 check -> assembling -> merging -> done) so the portal can render progress live.
async def forecast_panel(ctx, operator: str | None = None, registrations: list[str] | None = None,
                         as_of: str | None = None, correlation_id=None, **_):
    from datetime import date
    _as_of = date.fromisoformat(as_of) if as_of else None
    await run_forecast_panel(
        db_client=ctx["db_client"],
        redis=ctx.get("redis_client"),
        job_id=ctx.get("job_id") or "forecast_panel",
        ref="forecast_panel",
        operator=operator,
        registrations=registrations,
        as_of=_as_of,
    )


# -----------------------------
# Scheduled (cron) jobs
# -----------------------------
# status_task-wrapped so scheduled AND forced runs publish status; registered in
# WorkerSettings.functions (see SCHEDULED below) so scheduler.dispatch_due / run-now
# can enqueue them by name.
@status_task
async def cron_live_flights(ctx, **_):
    await live_flights_adaptive()


@status_task
async def cron_asg_regs(ctx, **_):
    await asg_regs_updater()


@status_task
async def cron_refresh_delta(ctx, **_):
    await refresh_cirium_delta()


@status_task
async def cron_predictive_cleanup(ctx, **_):
    await predictive_cleanup()


@status_task
async def cron_collapse_revisions(ctx, **_):
    await collapse_completed_revisions()


@status_task
async def cron_refresh_plantype_matviews(ctx, **_):
    await refresh_plantype_matviews()


@status_task
async def cron_ensure_livepositions_partition(ctx, **_):
    await ensure_livepositions_partitions()


# On-demand tasks (enqueued by the API Server).
ON_DEMAND = [
    fetch_flight_summary,
    predictive_utilisation,
    load_airports,
    refresh_subscription,
    forecast_panel,
]

# Registry-driven schedulable jobs. Registered in WorkerSettings.functions so the
# scheduler dispatcher (and the /scheduler run-now endpoint) can enqueue them by name.
SCHEDULED = [
    cron_live_flights,
    cron_asg_regs,
    cron_refresh_delta,
    cron_predictive_cleanup,
    cron_collapse_revisions,
    cron_refresh_plantype_matviews,
    cron_ensure_livepositions_partition,
]
