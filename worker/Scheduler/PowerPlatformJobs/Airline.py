from sqlalchemy import select, func

from Database import DatabaseClient, Airline, CiriumAircrafts


async def sync_airlines():
    client: DatabaseClient = DatabaseClient()

    stmt = (
        select(CiriumAircrafts.Operator, CiriumAircrafts.Operator_ICAO, CiriumAircrafts.Operator_IATA)
        .order_by(CiriumAircrafts.Operator)
        .distinct()
        .where(
            CiriumAircrafts.revision_id == (
                select(func.max(CiriumAircrafts.revision_id))
                .scalar_subquery()
            )
        )
    )
    async with client.session("cirium") as cirium_session:
        result = await cirium_session.execute(stmt)
        airlines = result.all()


    existing_stmt = (
        select(Airline.airline_name)
    )
    async with client.session('powerplatform') as pp_session:
        existing_result = await pp_session.execute(existing_stmt)
        existing_airlines = set(existing_result.scalars().all())

        new_airlines = []
        for airline_name, airline_icao, airline_iata in airlines:
            if airline_name not in existing_airlines:
                new_airlines.append(
                    Airline(
                        airline_name=airline_name,
                        icao=airline_icao,
                        iata=airline_iata,
                    )
                )

        pp_session.add_all(new_airlines)

        await pp_session.commit()


if __name__ == '__main__':
    import asyncio
    asyncio.run(sync_airlines())
