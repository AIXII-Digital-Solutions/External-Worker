from sqlalchemy import text

from Database import DatabaseClient
from Config import setup_logger
from Utils import performance_timer

logger = setup_logger("registration_updater")

ASG_VIEW = "cirium.asg"
DELTA_VIEW = "cirium.delta"
AIRLINES_VIEW = "cirium.airlines"


async def regs_updater(client: DatabaseClient):
    """Rebuild api.registration from cirium.asg (is_active aircraft).

    The TRUNCATE + INSERT (joining api.airlines for airline_id) lives in the DB function
    api.sync_registration_from_asg() — owned by core-api's Alembic — so this is a single call.
    Replaces the old per-row upsert into the now-defunct main.registrations table.
    """
    async with client.session("cirium") as session:
        await session.execute(text("SELECT api.sync_registration_from_asg()"))


@performance_timer
async def asg_regs_updater():
    """Refresh cirium.asg, then rebuild api.registration from it.

    The airline-filtering / dedup / labelling that used to live here is now the DEFINITION of the
    cirium.asg materialized view (joined against api.airlines), and the registration sync is the
    api.sync_registration_from_asg() DB function. So this is just a refresh + a function call.
    """
    client = DatabaseClient()
    logger.info("Refreshing %s", ASG_VIEW)
    await client.refresh_materialized_view("cirium", ASG_VIEW)
    await client.refresh_materialized_view("cirium", AIRLINES_VIEW)
    await regs_updater(client=client)
    logger.info("ASG refresh + cirium.airlines refresh + api.registration sync complete")


@performance_timer
async def refresh_cirium_delta():
    """Refresh the cirium.delta materialized view.

    Replaces the old fill_cirium_delta TRUNCATE + 2x INSERT rebuild — the latest-revision /
    changed-rows logic is now the view definition, so rebuilding it is a single REFRESH.
    """
    client = DatabaseClient()
    logger.info("Refreshing %s", DELTA_VIEW)
    await client.refresh_materialized_view("cirium", DELTA_VIEW)
    logger.info("%s refreshed", DELTA_VIEW)
