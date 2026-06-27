import asyncio
from datetime import timezone, datetime
from typing import List

import aiohttp
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from Config import setup_logger
from settings import AIRLABS_API_KEY, AIRLABS_API_URL
from Database import DatabaseClient, FlightSnapshot, AircraftState
from Database.Models import Registrations
from Schemas import FlightsTrackerResponseSchema
from Utils import performance_timer


logger = setup_logger("airlabs_flights_api")


@performance_timer
async def tracker_api(regs: list[str] | None = None):
    logger.info("[Tracker API] Starting query")

    client: DatabaseClient = DatabaseClient()
    if regs is None:
        async with client.session("main") as session:
            stmt = (
                select(
                    Registrations.reg
                )
                .where(Registrations.indashboard == True)
            )
            result = await session.execute(stmt)
            regs = result.scalars().all()

    params = {
        "api_key": AIRLABS_API_KEY,
        "reg_number": ",".join(regs),
    }
    async with aiohttp.ClientSession() as api_session:
        async with api_session.get(AIRLABS_API_URL + "flights", params=params,
                                   timeout=aiohttp.ClientTimeout(total=30)) as response:
            try:
                response.raise_for_status()

                raw = await response.json()
                flights: List[FlightsTrackerResponseSchema] = [
                    FlightsTrackerResponseSchema.model_validate(item)
                    for item in raw.get("response", [])
                ]
                logger.debug("[Tracker API] API response OK")
            except Exception as _ex:
                logger.warning(f"[Tracker API] API response error: {_ex}")

    async with client.session("airlabs") as session:
        returned_regs = set()
        for flight in flights:
            returned_regs.add(flight.reg_number)
            snapshot = FlightSnapshot(
                hex=flight.hex,
                reg_number=flight.reg_number,
                airline_icao=flight.airline_icao,
                airline_iata=flight.airline_iata,
                aircraft_icao=flight.aircraft_icao,
                flight_icao=flight.flight_icao,
                flight_iata=flight.flight_iata,
                flight_number=flight.flight_number,
                dep_icao=flight.dep_icao,
                dep_iata=flight.dep_iata,
                arr_icao=flight.arr_icao,
                arr_iata=flight.arr_iata,
                lat=flight.lat,
                lng=flight.lng,
                alt=flight.alt,
                dir=flight.dir,
                speed=flight.speed,
                v_speed=flight.v_speed,
                squawk=flight.squawk,
                flag=flight.flag,
                status=flight.status,
                updated=flight.updated.astimezone(timezone.utc),
            )

            session.add(snapshot)
            await session.flush()

            insert_stmt = insert(AircraftState).values(
                reg_number=flight.reg_number,
                airline_icao=flight.airline_icao,
                airline_iata=flight.airline_iata,
                status=flight.status,
                last_update=flight.updated.astimezone(timezone.utc),
                snapshot_id=snapshot.id,
            )

            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[AircraftState.reg_number],
                set_={
                    "airline_icao": insert_stmt.excluded.airline_icao,
                    "airline_iata": insert_stmt.excluded.airline_iata,
                    "status": insert_stmt.excluded.status,
                    "last_update": insert_stmt.excluded.last_update,
                    "snapshot_id": insert_stmt.excluded.snapshot_id,
                },
            )
            await session.execute(stmt)

        missing_regs = set(regs) - returned_regs
        for reg in missing_regs:
            insert_stmt_miss = insert(AircraftState).values(
                reg_number=reg,
                status="landed",
                last_update=datetime.now(timezone.utc)
            )
            stmt_miss = insert_stmt_miss.on_conflict_do_update(
                index_elements=[AircraftState.reg_number],
                set_={
                    "status": insert_stmt_miss.excluded.status,
                    "last_update": datetime.now(timezone.utc),
                },
            )
            await session.execute(stmt_miss)

        await session.commit()

    logger.info("[Tracker API] Query completed")



if __name__ == "__main__":
    asyncio.run(tracker_api())
