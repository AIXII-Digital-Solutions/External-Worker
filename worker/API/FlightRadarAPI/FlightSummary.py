import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
from uuid import UUID

import aiohttp
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from Config import setup_logger
from settings import FLIGHT_RADAR_HEADERS, FLIGHT_RADAR_SECONDS_BETWEEN_REQUESTS, \
    FLIGHT_RADAR_MAX_REG_PER_BATCH, FLIGHT_RADAR_RANGE_DAYS, FLIGHT_RADAR_URL, FLIGHT_RADAR_PATH
from Database import DatabaseClient
from Database.Models import FlightSummary, Registrations
from Utils import parse_dt, ensure_naive_utc, write_csv, parse_date_or_datetime, performance_timer

logger = setup_logger("flightradar")


async def fetch_date_range(
        icao: Optional[List[str]],
        regs: Optional[List[str]],
        callsigns: Optional[List[str]],
        range_from: datetime,
        range_to: datetime,
        http: aiohttp.ClientSession,
        storage_mode: str = "db",
        csv_path: Optional[str] = None
) -> List[dict] | None:
    client: DatabaseClient = DatabaseClient()

    logger.debug("[Flight Summary] Starting query Fetch Date Range")

    async with client.session("flightradar") as session:
        logger.debug(f"[Flight Summary] Range Processing: {range_from} - {range_to} |"
                     f" ICAO={', '.join(icao) if icao else None} |"
                     f" CALLSIGNS={', '.join(callsigns) if callsigns else None} |"
                     f" REGS={', '.join(regs) if regs else None}")
        next_from = range_from

        processing_flights: List[dict] = []
        while True:
            params = {
                "flight_datetime_from": next_from.strftime("%Y-%m-%d %H:%M:%S"),
                "flight_datetime_to": range_to.strftime("%Y-%m-%d %H:%M:%S"),
                "limit": 20000
            }
            if icao:
                params["painted_as"] = ",".join(icao)
            if regs:
                params["registrations"] = ",".join(regs)
            if callsigns:
                params["callsigns"] = ",".join(callsigns)

            await asyncio.sleep(FLIGHT_RADAR_SECONDS_BETWEEN_REQUESTS)

            async with http.get(f"{FLIGHT_RADAR_URL}/flight-summary/full", headers=FLIGHT_RADAR_HEADERS,
                                params=params) as resp:
                if resp.status != 200:
                    logger.error(f"{resp.status}: {await resp.text()}")
                    break

                flights = await resp.json()
                if not flights or not flights.get("data"):
                    logger.debug("[Flight Summary] No data for the current interval.")
                    break

                flights_data = flights["data"]
                if not flights_data:
                    break

                existing_ids = set()
                if storage_mode in ("db", "both"):
                    stmt = select(
                        FlightSummary.fr24_id,
                        FlightSummary.flight,
                        FlightSummary.reg,
                        FlightSummary.callsign
                    ).where(
                        FlightSummary.fr24_id.in_([f.get("fr24_id") for f in flights_data if f.get("fr24_id")])
                    )

                    existing_rows = (await session.execute(stmt)).all()
                    existing_ids = {
                        (row[0], row[1], row[2], row[3])
                        for row in existing_rows
                    }

                new_flights = []
                csv_rows = []
                max_takeoff = next_from

                for flight in flights_data:
                    try:
                        fr24_id = flight.get("fr24_id")
                        flight_num = flight.get("flight")
                        reg = flight.get("reg")
                        callsign = flight.get("callsign")

                        if not fr24_id or ((fr24_id, flight_num, reg, callsign) in existing_ids and storage_mode in (
                                "db", "both")):
                            logger.debug(f"[Flight Summary] Skipping duplicate: {flight_num} ({reg}/{callsign})")
                            continue

                        takeoff = parse_dt(flight.get("datetime_takeoff"))
                        max_takeoff = max(max_takeoff, takeoff) if takeoff else max_takeoff

                        row_data = {
                            "fr24_id": fr24_id,
                            "flight": flight_num,
                            "callsign": callsign,
                            "operating_as": flight.get("operating_as"),
                            "painted_as": flight.get("painted_as"),
                            "type": flight.get("type"),
                            "reg": reg,
                            "orig_icao": flight.get("orig_icao"),
                            "orig_iata": flight.get("orig_iata"),
                            "datetime_takeoff": ensure_naive_utc(takeoff),
                            "runway_takeoff": flight.get("runway_takeoff"),
                            "dest_icao": flight.get("dest_icao"),
                            "dest_iata": flight.get("dest_iata"),
                            "dest_icao_actual": flight.get("dest_icao_actual"),
                            "dest_iata_actual": flight.get("dest_iata_actual"),
                            "datetime_landed": ensure_naive_utc(parse_dt(flight.get("datetime_landed"))),
                            "runway_landed": flight.get("runway_landed"),
                            "flight_time": flight.get("flight_time"),
                            "actual_distance": flight.get("actual_distance"),
                            "circle_distance": flight.get("circle_distance"),
                            "category": flight.get("category"),
                            "hex": flight.get("hex"),
                            "first_seen": ensure_naive_utc(parse_dt(flight.get("first_seen"))),
                            "last_seen": ensure_naive_utc(parse_dt(flight.get("last_seen"))),
                            "flight_ended": flight.get("flight_ended"),
                        }

                        if row_data["flight_ended"] is False:
                            processing_flights.append(row_data)

                        if row_data["flight_ended"] is True:
                            if storage_mode in ("db", "both"):
                                new_flights.append(row_data)
                            if storage_mode in ("csv", "both"):
                                csv_rows.append(row_data)

                    except Exception as e:
                        logger.warning(f"[Flight Summary] Record processing error: {e}")

                if new_flights and storage_mode in ("db", "both"):
                    # natural-key UNIQUE (uq_flightsummary_natural) makes re-ingests idempotent
                    await session.execute(
                        pg_insert(FlightSummary)
                        .values(new_flights)
                        .on_conflict_do_nothing(constraint="uq_flightsummary_natural")
                    )
                    await session.commit()
                    logger.debug(f"[Flight Summary] Saved {len(new_flights)} new records to DB.")

                if csv_rows and storage_mode in ("csv", "both") and csv_path:
                    write_csv(csv_rows, csv_path)
                    logger.debug(f"[Flight Summary] Appended {len(csv_rows)} records to CSV.")

                if max_takeoff == next_from or max_takeoff >= range_to:
                    break

                next_from = max_takeoff + timedelta(seconds=1)

        logger.debug("[Flight Summary] Query Fetch Date Ranges completed")

        if len(processing_flights) < 1:
            return None
        return processing_flights


def split_batches(data: Optional[List[str]], batch_size: int) -> List[Optional[List[str]]]:
    if not data:
        return [None]
    return [data[i:i + batch_size] for i in range(0, len(data), batch_size)]


def get_batch(batches, index) -> Optional[List[str]]:
    if not batches:
        return None
    return batches[index] if index < len(batches) else None


@performance_timer
async def fetch_all_ranges(
        start_date: str,
        end_date: str,
        user: Optional[str] = None,
        correlation_id: Optional[UUID] = None,
        icao: Optional[List[str]] = None,
        registrations: Optional[List[str]] = None,
        callsigns: Optional[List[str]] = None,
        storage_mode: str = "db",
        csv_path: Optional[Path] = FLIGHT_RADAR_PATH / f"flights_{datetime.strftime(datetime.now(), '%Y%m%d_%H%M')}.csv"
):
    client: DatabaseClient = DatabaseClient()
    if registrations is None and icao is None and callsigns is None:
        async with client.session("main") as session:
            stmt = (
                select(
                    Registrations.reg
                )
                .where(Registrations.indashboard == True)
            )
            result = await session.execute(stmt)
            registrations = result.scalars().all()

    logger.info("[Flight Summary] Starting query Fetch All Ranges")

    start_dt = parse_date_or_datetime(start_date)
    end_dt = parse_date_or_datetime(end_date)

    date_ranges = []
    current = start_dt

    flights = []

    while current <= end_dt:
        range_end = min(current + timedelta(days=FLIGHT_RADAR_RANGE_DAYS) - timedelta(seconds=1), end_dt)
        date_ranges.append((current, range_end))
        current = range_end + timedelta(seconds=1)

    registration_batches = split_batches(registrations, FLIGHT_RADAR_MAX_REG_PER_BATCH)
    icao_batches = split_batches(icao, FLIGHT_RADAR_MAX_REG_PER_BATCH)
    callsigns_batches = split_batches(callsigns, FLIGHT_RADAR_MAX_REG_PER_BATCH)

    max_len = max(
        len(registration_batches),
        len(icao_batches),
        len(callsigns_batches),
        1
    )

    async with aiohttp.ClientSession() as http:
        total_iterations = max_len * len(date_ranges)
        current_iteration = 0

        for batch_index in range(max_len):
            reg_batch = get_batch(registration_batches, batch_index)
            icao_batch = get_batch(icao_batches, batch_index)
            callsign_batch = get_batch(callsigns_batches, batch_index)
            logger.debug(
                f"\n[Flight Summary] Processing a batch {current_iteration + 1} out of {total_iterations}")

            for i, (range_start, range_end) in enumerate(date_ranges):
                logger.debug(f"[Flight Summary] Range {i + 1} out of {len(date_ranges)}")
                current_iteration += 1

                flights.append(await fetch_date_range(
                    icao=icao_batch,
                    regs=reg_batch,
                    range_from=range_start,
                    range_to=range_end,
                    http=http,
                    storage_mode=storage_mode,
                    csv_path=csv_path,
                    callsigns=callsign_batch
                ))

        logger.info("[Flight Summary] Query Fetch All Ranges completed")

        return flights


if __name__ == "__main__":
    import re

    def str_to_list(text: str | List[str] | None) -> List[str]:
        if isinstance(text, str):
            values = re.split(r"[,\n]+", text)
            values = [v.strip() for v in values if v.strip()]
            return list(set(values))
        return text


    ICAO = None
    # ICAO = ["VAJ", "VCJ", "AOJ"]


    # START_DATE = "2022-06-01"
    START_DATE = "2024-04-13"
    # START_DATE = "2025-07-09"
    END_DATE = "2025-12-31"

    storage_mode = "both"  # "db", "csv", or "both"

    # REGISTRATIONS = None
    # REGISTRATIONS = ["ASTERIX", "OBELIX", "TRUBADIX"]

    CAllSIGNS = None
#     CAllSIGNS = """
#     9H1MA
# AOJ1A
# AOJ22S
# AOJ45C
# AOJ53L
# AOJ53Z
# AOJ54F
# AOJ596
# AOJ72T
# AOJ73M
# AOJ77U
# AOJ84K
# N146MM
# OEFPJ
# OEFVG
# OELVS
# T7DUA
# T7SAHIN
# VAJ075N
# VAJ711
# VAJ75N
# VCJ046X
# VCJ050M
# VCJ1MA
# VCJ303
# VCJ39A
# VCJ46X
# VCJ50M
# VCJ778
# VCJ79X
# VCJ96E
# VCJ97N
#     """

    REGISTRATIONS = """
    9H-1MA
9H-APX
9H-BOD
9H-GKM
9H-NATHO
9H-ONE
9H-OPL
9H-PMN
9H1MA
9HGKM
9HNATHO
9HONE
OE-DNF
OE-FBL
OE-FEG
OE-FPJ
OE-FSS
OE-GAP
OE-GCL
OE-GCZ
OE-GJB
OE-GJW
OE-GLI
OE-GLY
OE-GYS
OE-GZF
OE-HIL
OE-HIM
OE-HLB
OE-HOH
OE-HOP
OE-HOZ
OE-HRS
OE-HRT
OE-HUG
OE-HWJ
OE-HXX
OE-IAA
OE-IBK
OE-IMB
OE-IMI
OE-IRK
OE-ISN
OE-ITA
OE-ITE
OE-LCA
OE-LCY
OE-LCZ
OE-LHU
OE-LIM
OE-LIO
OE-LOT
OE-LVS
OEHRT
OELVS
OK-VOS
T7-DUA
T7-SAHIN
    """


    csv_path = FLIGHT_RADAR_PATH / f"flights_12_04_2026_1.csv"

    asyncio.run(fetch_all_ranges(
        start_date=START_DATE,
        end_date=END_DATE,
        icao=str_to_list(ICAO),
        registrations=str_to_list(REGISTRATIONS),
        callsigns=str_to_list(CAllSIGNS),
        storage_mode=storage_mode,
        csv_path=csv_path.as_posix()
    ))
