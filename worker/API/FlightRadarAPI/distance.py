import math
from datetime import datetime, timedelta, UTC
from typing import Optional

from sqlalchemy import select, text, desc

from Database import DatabaseClient
from Database.Models import LivePositions
from Utils import ensure_naive_utc

try:
    from .FlightSummary import logger
except:
    from API.FlightRadarAPI.FlightSummary import logger


def haversine_distance_km(
        lat1: float, lon1: float,
        lat2: float, lon2: float
) -> float:
    R = 6371.0

    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
            math.sin(dlat / 2) ** 2 +
            math.cos(lat1) * math.cos(lat2) *
            math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


async def get_time_delta(start: Optional[datetime] = None, end: Optional[datetime] = None,
                         reg: Optional[str] = None, flight_number: Optional[str] = None) -> timedelta:
    if reg and flight_number:
        client = DatabaseClient()
        prev = await get_latest_live_position(client=client, reg=reg, flight_number=flight_number)
        if prev:
            start = prev.created_at

    if end is None:
        end = ensure_naive_utc(datetime.now(UTC))

    if start is None:
        return timedelta(0)

    try:
        return end - start
    except Exception as _ex:
        logger.warning(f"[Distance calculator] Fallback to timedelta = 0. Exception: {_ex}")
        return timedelta(0)


async def get_latest_live_position(client, reg: str, flight_number: str):
    now = ensure_naive_utc(datetime.now(UTC))
    one_hour_ago = now - timedelta(hours=2)

    async with client.session("flightradar") as session:
        stmt = (
            select(LivePositions)
            .where(
                LivePositions.reg == reg,
                LivePositions.flight == flight_number,
                LivePositions.created_at >= one_hour_ago,
            )
            .order_by(desc(LivePositions.created_at))
            .limit(1)
        )

        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_airport_coords_by_iata(client, iata: str):
    async with client.session("main") as session:
        stmt = text("""
            SELECT
                "Latitude"  AS latitude,
                "Longitude" AS longitude
            FROM virtual_airport_list
            WHERE "IATA Code" = :iata
            LIMIT 1
        """)

        result = await session.execute(stmt, {"iata": iata})
        row = result.first()

        if not row:
            return None

        return row.latitude, row.longitude


async def calculate_distance_metric(reg: str, new_flight: dict) -> float:
    client = DatabaseClient()

    lat = new_flight.get("lat")
    lon = new_flight.get("lon")
    gspeed = new_flight.get("gspeed")
    orig_iata = new_flight.get("orig_iata")

    logger.info(
        f"[Distance calculator] Calculating distance metric, lon: {lon}, lat: {lat}, gspeed: {gspeed}, orig_iata: {orig_iata}")

    if lat is None or lon is None:
        logger.info("[Distance calculator] Fallback to 0.0")
        return 0.0

    prev = await get_latest_live_position(
        client=client,
        reg=reg,
        flight_number=new_flight.get("flight"),
    )

    if prev and prev.lat is not None and prev.lon is not None:
        logger.info(f"[Distance calculator] Return by calc: {haversine_distance_km(prev.lat, prev.lon, lat, lon)}")
        return haversine_distance_km(
            prev.lat, prev.lon,
            lat, lon
        )

    if orig_iata and prev is None:
        airport = await get_airport_coords_by_iata(client, orig_iata)
        if airport:
            a_lat, a_lon = airport
            logger.info(f"[Distance calculator] Return by airport: {haversine_distance_km(a_lat, a_lon, lat, lon)}")
            return haversine_distance_km(
                a_lat, a_lon,
                lat, lon
            )

    if orig_iata is None and prev is None:
        if gspeed >= 120:
            logger.info(f"[Distance calculator] Return by gspeed: {gspeed * 1.825 / 5}")
            return gspeed * 1.825 / 5
        if gspeed < 120:
            logger.info(f"[Distance calculator] Fallback by gspeed: 0.0")
            return 0.0

    return 0.0


if __name__ == "__main__":
    import asyncio

    client = DatabaseClient()
    asyncio.run(get_airport_coords_by_iata(client=client, iata="DXB"))
