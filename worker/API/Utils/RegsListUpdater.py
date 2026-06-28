from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from Database.Models import Asg, Registrations
from Database import DatabaseClient
from Config import setup_logger
from Utils import performance_timer

logger = setup_logger("registration_updater")

ASG_VIEW = "cirium.asg"
DELTA_VIEW = "cirium.delta"


async def regs_updater(client: DatabaseClient):
    """Sync the cirium.asg materialized view into main.registrations (upsert by msn)."""
    async with client.session("cirium") as cirium_session:
        rows = (
            await cirium_session.execute(
                select(
                    Asg.Registration,
                    Asg.Serial_Number,
                    Asg.Manufacturer,
                    Asg.Aircraft_Sub_Series,
                )
                # asg now also holds older inactive rows (is_active=False); registrations only
                # track the currently active aircraft.
                .where(Asg.is_active.is_(True))
            )
        ).all()

    async with client.session("main") as main_session:
        for row in rows:
            ac_type = f"{row.Manufacturer or ''} {row.Aircraft_Sub_Series or ''}".strip()

            insert_stmt = insert(Registrations).values(
                reg=row.Registration,
                msn=row.Serial_Number,
                aircraft_type=ac_type,
                indashboard=True,
                status="Insured",
            )

            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[Registrations.msn],
                set_={
                    "reg": insert_stmt.excluded.reg,
                    "msn": insert_stmt.excluded.msn,
                    "aircraft_type": insert_stmt.excluded.aircraft_type,
                    "indashboard": insert_stmt.excluded.indashboard,
                    "status": insert_stmt.excluded.status,
                },
            )

            await main_session.execute(stmt)


@performance_timer
async def asg_regs_updater():
    """Refresh cirium.asg, then sync it into main.registrations.

    The airline-filtering / dedup / labelling that used to live here (iterating main.Airlines and
    building ILIKE filters + a CASE label, then TRUNCATE+INSERT) is now the DEFINITION of the
    cirium.asg materialized view (joined against api.airlines). So this is just a refresh + sync.
    """
    client = DatabaseClient()
    logger.info("Refreshing %s", ASG_VIEW)
    await client.refresh_materialized_view("cirium", ASG_VIEW)
    await regs_updater(client=client)
    logger.info("ASG refresh + registrations sync complete")


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
