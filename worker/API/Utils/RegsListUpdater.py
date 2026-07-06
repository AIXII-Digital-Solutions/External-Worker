from sqlalchemy import text

from Database import DatabaseClient
from Config import setup_logger
from Utils import performance_timer

logger = setup_logger("registration_updater")

AIRLINES_VIEW = "cirium.airlines"
REGISTRATIONS_VIEW = "cirium.registrations"   # latest Operator+Status per unique Registration
# asg / delta are now per-plan_type variants; *_full = UNION of the two, so refresh the two plan
# variants FIRST, then the _full view that reads them (order matters for CONCURRENTLY).
ASG_VIEWS = ["cirium.asg_commercial", "cirium.asg_business_helicopters", "cirium.asg_full"]
DELTA_VIEWS = ["cirium.delta_commercial", "cirium.delta_business_helicopters", "cirium.delta_full"]
PLANTYPE_VIEWS = ["cirium.all_commercial", "cirium.all_business_helicopters",
                  "cirium.historical_commercial", "cirium.historical_business_helicopters"]


async def regs_updater(client: DatabaseClient):
    """Rebuild api.registration from cirium.asg_full (is_active aircraft).

    The TRUNCATE + INSERT (joining api.airlines for airline_id) lives in the DB function
    api.sync_registration_from_asg() — owned by core-api's Alembic (now reads cirium.asg_full) — so
    this is a single call.
    """
    async with client.session("cirium") as session:
        await session.execute(text("SELECT api.sync_registration_from_asg()"))


@performance_timer
async def asg_regs_updater():
    """Refresh the asg matviews (commercial + business_helicopters, then full), cirium.airlines, and
    rebuild api.registration from asg_full. The airline match / is_active / per-plan revision scoping
    is the matview DEFINITION; this is just the refresh + the api.sync_registration_from_asg() call."""
    client = DatabaseClient()
    for v in ASG_VIEWS:
        logger.info("Refreshing %s", v)
        await client.refresh_materialized_view("cirium", v)
    await client.refresh_materialized_view("cirium", AIRLINES_VIEW)
    await client.refresh_materialized_view("cirium", REGISTRATIONS_VIEW)
    await regs_updater(client=client)
    logger.info("asg_* + cirium.airlines + cirium.registrations refresh + api.registration sync complete")


@performance_timer
async def refresh_cirium_delta():
    """Refresh the delta matviews (commercial + business_helicopters, then full)."""
    client = DatabaseClient()
    for v in DELTA_VIEWS:
        logger.info("Refreshing %s", v)
        await client.refresh_materialized_view("cirium", v)
    logger.info("delta_* refreshed")


@performance_timer
async def refresh_plantype_matviews():
    """Weekly refresh of the plan_type aircraft-data matviews (all_* + historical_*). The live
    latest_* are plain VIEWS (always current). all_* also get refreshed on-collapse by
    collapse_completed_revisions; this keeps them fresh between month rollovers."""
    client = DatabaseClient()
    for v in PLANTYPE_VIEWS:
        logger.info("Refreshing %s", v)
        await client.refresh_materialized_view("cirium", v)
    logger.info("all_* + historical_* refreshed")


ALL_COMMERCIAL_VIEW = "cirium.all_commercial"
ALL_BUSINESS_VIEW = "cirium.all_business_helicopters"


@performance_timer
async def collapse_completed_revisions():
    """Auto-collapse completed-month LIVE Cirium revisions (per plan_type), then refresh the
    plan_type matviews if anything actually changed.

    The merge + stable-key dedup logic is the cirium.collapse_completed_months() DB function (owned
    by core-api's Alembic); this just drives it on schedule. It collapses every past-month group that
    still has >1 revision (or a NULL period) and leaves the current month alone. When it collapses
    something, cirium.all_commercial / cirium.all_business_helicopters are refreshed; the historical_*
    matviews (historical revisions never collapse) and the live latest_* views need no refresh.
    """
    client = DatabaseClient()
    async with client.session("cirium") as session:
        collapsed = (await session.execute(text("SELECT cirium.collapse_completed_months()"))).scalar() or 0
    logger.info("collapse_completed_months collapsed %d month-group(s)", collapsed)
    if collapsed:
        await client.refresh_materialized_view("cirium", ALL_COMMERCIAL_VIEW)
        await client.refresh_materialized_view("cirium", ALL_BUSINESS_VIEW)
        logger.info("refreshed %s + %s after collapse", ALL_COMMERCIAL_VIEW, ALL_BUSINESS_VIEW)
    return collapsed


@performance_timer
async def ensure_livepositions_partitions():
    """Pre-create the current + next-2 monthly partitions of flightradar.livepositions so incoming
    positions always land in a real monthly partition (the DEFAULT is only a safety net). Idempotent
    (IF NOT EXISTS) — a no-op on most runs. Logic is the flightradar.ensure_livepositions_partitions()
    DB function (owned by core-api's Alembic)."""
    client = DatabaseClient()
    async with client.session("cirium") as session:
        made = (await session.execute(text("SELECT flightradar.ensure_livepositions_partitions()"))).scalar() or 0
    logger.info("ensure_livepositions_partitions created %d partition(s)", made)
    return made
