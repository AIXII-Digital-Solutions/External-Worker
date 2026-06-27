from Database import DatabaseClient, Engine, CiriumAircrafts
from sqlalchemy import select

async def sync_engines_from_cirium():
    client: DatabaseClient = DatabaseClient()

    cirium_stmt = (
        select(CiriumAircrafts.Engine_Manufacturer, CiriumAircrafts.Engine_Master_Series)
        .where(
            CiriumAircrafts.Engine_Manufacturer.is_not(None),
            CiriumAircrafts.Engine_Master_Series.is_not(None),
        )
        .distinct()
    )

    async with client.session('cirium') as cirium_session:
        cirium_result = await cirium_session.execute(cirium_stmt)
        cirium_engines = cirium_result.all()


    existing_stmt = select(Engine.engine_model)

    async with client.session('powerplatform') as pp_session:
        existing_result = await pp_session.execute(existing_stmt)
        existing_models = set(existing_result.scalars().all())
        new_engines = []

        for manufacture, model in cirium_engines:
            if model not in existing_models:
                new_engines.append(
                    Engine(
                        engine_manufacture=manufacture,
                        engine_model=model,
                    )
                )

        pp_session.add_all(new_engines)

        await pp_session.commit()


if __name__ == '__main__':
    import asyncio
    asyncio.run(sync_engines_from_cirium())

