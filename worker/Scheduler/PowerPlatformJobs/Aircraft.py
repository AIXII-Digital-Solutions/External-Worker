from typing import Sequence, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from Database import DatabaseClient, ASGAircrafts, Aircraft, Airline, Engine, AircraftTemplate, AircraftEngine, \
    AircraftTechnicalData, AircraftLesseeLessor, AircraftPolicy, CiriumAircrafts, AircraftManual
from Schemas import AircraftInsuredStatusEnum, EnginePositionEnum
from Schemas.Enums import AircraftDataSourceEnum
from AircraftHelpers import load_references, load_lessee_lessors, get_engine_positions


async def update_aircrafts():
    client: DatabaseClient = DatabaseClient()

    async with client.session("cirium") as cirium_session:
        cirium_stmt = select(ASGAircrafts).order_by(ASGAircrafts.Airline.desc())

        result = await cirium_session.execute(cirium_stmt)

        cirium_aircrafts: Sequence[ASGAircrafts] = result.scalars().all()

    async with client.session("powerplatform") as pp_session:
        stmt = (
            select(Aircraft)
            .options(
                joinedload(Aircraft.technical_data),
                joinedload(Aircraft.airline),
                joinedload(Aircraft.template),

                selectinload(Aircraft.engines)
                .joinedload(AircraftEngine.engine),

                selectinload(Aircraft.lessee_lessors),
                selectinload(Aircraft.policy),
            )
        )

        result = await pp_session.execute(stmt)

        pp_aircrafts = {
            a.msn: a
            for a in result.scalars().unique().all()
        }

        refs = await load_references(pp_session)

        for cirium_aircraft in cirium_aircrafts:
            pp_aircraft = pp_aircrafts.get(int(cirium_aircraft.Serial_Number))

            if not pp_aircraft:
                pp_aircraft = Aircraft(
                    registration=cirium_aircraft.Registration,
                    msn=int(cirium_aircraft.Serial_Number),
                )

                pp_aircraft.engines = []
                pp_aircraft.lessee_lessors = []
                pp_aircraft.engines = []

                pp_aircraft.technical_data = AircraftTechnicalData(
                    data_source=AircraftDataSourceEnum.CIRIUM,
                    data_source_row_id=cirium_aircraft.id,
                    status=AircraftInsuredStatusEnum.NOT_INSURED,
                    av_fixed=True,
                    in_dashboard=True
                )

                pp_session.add(pp_aircraft)

            technical_data: AircraftTechnicalData = pp_aircraft.technical_data

            if technical_data and technical_data.data_source != AircraftDataSourceEnum.CIRIUM:
                continue

            airline: Optional[Airline] = refs.get("airlines", {}).get(cirium_aircraft.Airline, None)
            if airline:
                pp_aircraft.airline = airline

            template: Optional[AircraftTemplate] = refs.get("templates", {}).get(
                f"{cirium_aircraft.Manufacturer} {cirium_aircraft.Series}", None)
            if template:
                pp_aircraft.template = template

            engine: Engine = refs.get("engines").get(cirium_aircraft.Engine_Master_Series)

            engine_positions = get_engine_positions(
                cirium_aircraft.Number_Of_Engines if cirium_aircraft.Number_Of_Engines else 2)

            aircraft_engines = []
            for engine_position in engine_positions:
                aircraft_engines.append(
                    AircraftEngine(
                        aircraft=pp_aircraft,
                        engine=engine,
                        position=engine_position,
                    )
                )

            pp_aircraft.engines = aircraft_engines

            lessee_lessor = next(
                (
                    item
                    for item in pp_aircraft.lessee_lessors
                    if item.lessee == cirium_aircraft.Airline
                       and item.lessor == cirium_aircraft.Operational_Lessor
                ),
                None
            )

            if not lessee_lessor:
                for item in pp_aircraft.lessee_lessors:
                    item.active = False

                pp_aircraft.lessee_lessors.append(
                    AircraftLesseeLessor(
                        lessee=cirium_aircraft.Airline,
                        lessor=cirium_aircraft.Operational_Lessor,
                        active=True,
                    )
                )
            else:
                lessee_lessor.active = True

            pp_aircraft.mtow = round((cirium_aircraft.Operating_MTOW_lbs or 0) * 0.45359237)
            pp_aircraft.agreed_value_result = cirium_aircraft.Indicative_Market_Value_USm

        await pp_session.commit()


async def update_aircraft_templates():
    client: DatabaseClient = DatabaseClient()

    stmt = (
        select(CiriumAircrafts.Manufacturer, CiriumAircrafts.Series)
        .distinct()
    )

    async with client.session('cirium') as cirium_session:
        cirium_result = await cirium_session.execute(stmt)
        _cirium_templates = cirium_result.all()

    cirium_templates = []
    for manufacturer, series in _cirium_templates:
        cirium_templates.append(f"{manufacturer} {series}")

    existing_stmt = (
        select(AircraftTemplate.template_name)
    )

    async with client.session('powerplatform') as pp_session:
        pp_result = await pp_session.execute(existing_stmt)
        existing_templates = pp_result.scalars().all()

        new_templates = []
        for template in cirium_templates:
            if template not in existing_templates:
                new_templates.append(
                    AircraftTemplate(
                        template_name=template,
                    )
                )

        pp_session.add_all(new_templates)

        await pp_session.commit()


async def update_create_aircraft_manual(target: int):
    client: DatabaseClient = DatabaseClient()

    async with client.session("powerplatform") as session:
        manual_stmt = (
            select(AircraftManual)
            .options(
                selectinload(AircraftManual.engines)
            )
            .where(AircraftManual.id == target)
        )

        manual_result = await session.execute(manual_stmt)

        manual_aircraft: Optional[AircraftManual] = (
            manual_result.scalars().one_or_none()
        )

        if not manual_aircraft:
            raise ValueError(
                f"AircraftManual with id={target} not found"
            )

        stmt = (
            select(Aircraft)
            .options(
                joinedload(Aircraft.technical_data),
                joinedload(Aircraft.airline),
                joinedload(Aircraft.template),

                selectinload(Aircraft.engines)
                .joinedload(AircraftEngine.engine),

                selectinload(Aircraft.lessee_lessors),
                selectinload(Aircraft.policy),
            )
            .where(Aircraft.id == manual_aircraft.aircraft_id)
        )

        result = await session.execute(stmt)

        aircraft: Optional[Aircraft] = (
            result.scalars().one_or_none()
        )

        if not aircraft:
            aircraft = Aircraft(
                registration=manual_aircraft.registration,
                msn=manual_aircraft.msn,
            )

            aircraft.technical_data = None
            aircraft.policy = []
            aircraft.lessee_lessors = []
            aircraft.engines = []

            session.add(aircraft)

            await session.flush()

        aircraft.registration = manual_aircraft.registration
        aircraft.msn = manual_aircraft.msn

        aircraft.airline_id = manual_aircraft.airline_id
        aircraft.template_id = manual_aircraft.template_id

        aircraft.mtow = manual_aircraft.mtow

        aircraft.agreed_value = manual_aircraft.agreed_value
        aircraft.agreed_value_result = manual_aircraft.agreed_value_result

        aircraft.combined_single_limit = manual_aircraft.combined_single_limit

        aircraft.hsl_deductible = manual_aircraft.hsl_deductible

        aircraft.hd_deductible = manual_aircraft.hd_deductible

        aircraft.depreciation_rate = manual_aircraft.depreciation_rate

        aircraft.depreciation_start_date = manual_aircraft.depreciation_start_date

        if not aircraft.technical_data:
            aircraft.technical_data = AircraftTechnicalData(
                aircraft_id=aircraft.id,
                data_source=AircraftDataSourceEnum.MANUAL,
                data_source_row_id=manual_aircraft.id,
                status=AircraftInsuredStatusEnum.NOT_INSURED,
            )

        else:
            aircraft.technical_data.data_source = manual_aircraft.data_source
            aircraft.technical_data.data_source_row_id = manual_aircraft.id
            aircraft.technical_data.status = manual_aircraft.status

        for policy in aircraft.policy:
            policy.active = False

        existing_policy = next(
            (
                policy for policy in aircraft.policy
                if policy.policy_from == manual_aircraft.policy_start and policy.policy_to == manual_aircraft.policy_end
            ),
            None
        )

        if manual_aircraft.policy_start or manual_aircraft.policy_end:
            if existing_policy:
                existing_policy.active = True

            else:
                aircraft.policy.append(
                    AircraftPolicy(
                        policy_from=manual_aircraft.policy_start,
                        policy_to=manual_aircraft.policy_end,
                        active=True
                    )
                )

        for item in aircraft.lessee_lessors:
            item.active = False

        existing_lessee_lessor = next(
            (
                item for item in aircraft.lessee_lessors
                if item.lessee == manual_aircraft.lessee and item.lessor == manual_aircraft.lessor
            ),
            None
        )

        if manual_aircraft.lessee or manual_aircraft.lessor:
            if existing_lessee_lessor:
                existing_lessee_lessor.active = True

            else:
                aircraft.lessee_lessors.append(
                    AircraftLesseeLessor(
                        lessee=manual_aircraft.lessee,
                        lessor=manual_aircraft.lessor,
                        active=True
                    )
                )

        aircraft.engines.clear()

        for engine_manual in manual_aircraft.engines:
            if not engine_manual.engine_id:
                continue

            aircraft.engines.append(
                AircraftEngine(
                    engine_id=engine_manual.engine_id,
                    position=engine_manual.position,
                    engine_msn=engine_manual.engine_msn
                )
            )

        if manual_aircraft.aircraft_id is None:
            manual_aircraft.aircraft_id = aircraft.id

        await session.commit()

        return aircraft


if __name__ == "__main__":
    import asyncio

    asyncio.run(update_aircrafts())
    # asyncio.run(update_aircraft_templates())
