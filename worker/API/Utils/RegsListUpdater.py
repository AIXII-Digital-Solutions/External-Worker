import asyncio

from sqlalchemy import select, inspect, or_, case, literal, text, func
from sqlalchemy.dialects.postgresql import insert

from Database.Models import Airlines, CiriumAircrafts, ASGAircrafts, Registrations
from Database import DatabaseClient, AircraftRevision
from Config import setup_logger
from Utils import performance_timer

logger = setup_logger("registration_updater")

EXCLUDED_COLUMNS = {
    "id",
    "revision_id",
    "created_at",
    "updated_at",
}

EXCLUDED_STATUSES = ["Cancelled", "On order", "Retired", "Written off"]



async def regs_updater(client: DatabaseClient):
    async with client.session("cirium") as cirium_session:
        stmt = (
            select(ASGAircrafts.Registration, ASGAircrafts.Serial_Number, ASGAircrafts.Manufacturer, ASGAircrafts.Aircraft_Sub_Series)
        )
        results = await cirium_session.execute(stmt)

    async with client.session("main") as main_session:
        for row in results:
            ac_type = row.Manufacturer + " " + row.Aircraft_Sub_Series

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
                }
            )

            await main_session.execute(stmt)



@performance_timer
async def asg_regs_updater():
    client = DatabaseClient()

    logger.info("Starting query")

    async with client.session("main") as main_session:
        stmt = select(Airlines.airline_name)

        result = await main_session.execute(stmt)
        airlines = result.scalars().all()

    mapper = inspect(CiriumAircrafts)

    columns = [
        column
        for column in mapper.columns
        if column.key not in EXCLUDED_COLUMNS
    ]

    filters = []
    airline_cases = []

    for company in airlines:
        pattern = f"%{company}%"

        condition = or_(
            CiriumAircrafts.Operator.ilike(pattern),
            CiriumAircrafts.Sub_Lessor.ilike(pattern),
            CiriumAircrafts.Owner.ilike(pattern),
        )

        filters.append(condition)

        airline_cases.append(
            (condition, literal(company))
        )

    airline_case_expr = case(
        *airline_cases,
        else_=None
    ).label("Airline")

    async with client.session("cirium") as cirium_session:
        await cirium_session.execute(
            text('TRUNCATE TABLE "asgaircraft" RESTART IDENTITY')
        )

        select_stmt = (
            select(
                airline_case_expr,
                *columns
            )
            .where(
                or_(*filters),

                CiriumAircrafts.Registration.isnot(None),

                ~CiriumAircrafts.Status.in_(
                    EXCLUDED_STATUSES
                )
            )
            .distinct(
                CiriumAircrafts.Registration,
                CiriumAircrafts.Serial_Number
            )
            .order_by(
                CiriumAircrafts.Registration,
                CiriumAircrafts.Serial_Number
            )
        )

        insert_stmt = (
            insert(ASGAircrafts)
            .from_select(
                ["Airline"] + [col.name for col in columns],
                select_stmt
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "Registration",
                    "Serial Number"
                ]
            )
        )

        await cirium_session.execute(insert_stmt)

        await cirium_session.commit()

    await regs_updater(client=client)

    logger.info("Query completed")



if __name__ == "__main__":
    asyncio.run(asg_regs_updater())
