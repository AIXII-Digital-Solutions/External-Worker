from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

import asyncio
import aiohttp
import orjson
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from Config import setup_logger
from settings import AVIATION_EDGE_API_KEY, AVIATION_EDGE_URL, AVIATION_EDGE_MAX_BATCH_SIZE, \
    AVIATION_EDGE_MAX_RANGE_DAYS, AVIATION_EDGE_PATH, AVIATION_EDGE_EXTRA_API_KEY
from Database import DatabaseClient
from Database.Models import HistoricalSchedule
from Utils import parse_date_or_datetime, parse_dt, write_csv, performance_timer, ensure_utc


logger = setup_logger("aviationedge_historical")


MAX_CONCURRENT_REQUESTS = 10
BULK_INSERT_SIZE = 5000 # TODO: Move to config
DB_INSERT_BATCH_SIZE = 650


def chunked(iterable, size):
    for i in range(0, len(iterable), size):
        yield iterable[i:i + size]


def split_batches(
        data: Optional[List[str]],
        batch_size: int
) -> List[List[str]]:
    if not data:
        return [[]]

    return [
        data[i:i + batch_size]
        for i in range(0, len(data), batch_size)
    ]


def chunk_date_ranges(
        start_date: datetime,
        end_date: datetime,
        max_days: int
) -> List[tuple[datetime, datetime]]:
    ranges = []

    current = start_date

    while current <= end_date:
        chunk_end = min(
            current + timedelta(days=max_days - 1),
            end_date
        )

        ranges.append((current, chunk_end))

        current = chunk_end + timedelta(days=1)

    return ranges


async def bulk_insert_historical_schedule(
        session: AsyncSession,
        rows: List[dict]
) -> int:

    total_inserted = 0

    for batch in chunked(rows, DB_INSERT_BATCH_SIZE):

        stmt = insert(HistoricalSchedule).values(batch)

        stmt = stmt.on_conflict_do_nothing(
            index_elements=[
                "type",
                "departure_scheduled_time",
                "departure_iata_code",
                "arrival_iata_code",
                "flight_number"
            ]
        )

        result = await session.execute(stmt)

        total_inserted += result.rowcount or 0

    return total_inserted


async def fetch_historical_schedule_chunk(
        airport_code: str,
        schedule_type: str,
        range_from: datetime,
        range_to: datetime,
        http: aiohttp.ClientSession,
        airline_iata: Optional[str] = None,
        flight_num: Optional[str] = None,
        storage_mode: str = "db",
        csv_path: Optional[Path] = None
) -> int:
    """
    storage_mode:
        - db
        - csv
        - both
    """

    params = {
        "key": AVIATION_EDGE_API_KEY,
        "code": airport_code,
        "type": schedule_type,
        "date_from": range_from.strftime("%Y-%m-%d"),
        "date_to": range_to.strftime("%Y-%m-%d"),
        "extra_key": AVIATION_EDGE_EXTRA_API_KEY
    }

    if airline_iata:
        params["airline_iata"] = airline_iata

    if flight_num:
        params["flight_num"] = flight_num

    async with http.get(
            f"{AVIATION_EDGE_URL}/flightsHistory",
            params=params
    ) as resp:

        if resp.status != 200:
            logger.error(
                f"[Historical Schedule] "
                f"HTTP {resp.status}: {await resp.text()}"
            )
            return 0

        try:
            raw = await resp.read()
            data = orjson.loads(raw)

        except Exception as e:
            logger.error(
                f"[Historical Schedule] JSON parse error: {e}"
            )
            return 0

        if not isinstance(data, list):
            logger.warning(
                f"[Historical Schedule] Unexpected response: {data}"
            )
            return 0

        if not data:
            logger.debug(
                f"[Historical Schedule] Empty response "
                f"for {airport_code}"
            )
            return 0

        rows_to_insert = []
        csv_rows = []

        parse_errors = 0

        for item in data:

            try:
                departure = item.get("departure", {})
                arrival = item.get("arrival", {})
                airline = item.get("airline", {})
                flight = item.get("flight", {})
                codeshared = item.get("codeshared", {})

                codeshared_airline = codeshared.get("airline", {})
                codeshared_flight = codeshared.get("flight", {})

                departure_scheduled = ensure_utc(
                    parse_dt(departure.get("scheduledTime"))
                )

                row_data = {
                    # Base
                    "type": item.get("type"),
                    "status": item.get("status"),

                    # Departure
                    "departure_iata_code": departure.get("iataCode"),
                    "departure_icao_code": departure.get("icaoCode"),
                    "departure_terminal": departure.get("terminal"),
                    "departure_gate": departure.get("gate"),
                    "departure_delay": departure.get("delay"),

                    "departure_scheduled_time": departure_scheduled,
                    "departure_estimated_time": ensure_utc(
                        parse_dt(departure.get("estimatedTime"))
                    ),
                    "departure_actual_time": ensure_utc(
                        parse_dt(departure.get("actualTime"))
                    ),
                    "departure_estimated_runway": ensure_utc(
                        parse_dt(departure.get("estimatedRunway"))
                    ),
                    "departure_actual_runway": ensure_utc(
                        parse_dt(departure.get("actualRunway"))
                    ),

                    # Arrival
                    "arrival_iata_code": arrival.get("iataCode"),
                    "arrival_icao_code": arrival.get("icaoCode"),
                    "arrival_terminal": arrival.get("terminal"),
                    "arrival_baggage": arrival.get("baggage"),
                    "arrival_gate": arrival.get("gate"),
                    "arrival_delay": arrival.get("delay"),

                    "arrival_scheduled_time": ensure_utc(
                        parse_dt(arrival.get("scheduledTime"))
                    ),
                    "arrival_estimated_time": ensure_utc(
                        parse_dt(arrival.get("estimatedTime"))
                    ),
                    "arrival_actual_time": ensure_utc(
                        parse_dt(arrival.get("actualTime"))
                    ),
                    "arrival_estimated_runway": ensure_utc(
                        parse_dt(arrival.get("estimatedRunway"))
                    ),
                    "arrival_actual_runway": ensure_utc(
                        parse_dt(arrival.get("actualRunway"))
                    ),

                    # Airline
                    "airline_name": airline.get("name"),
                    "airline_iata_code": airline.get("iataCode"),
                    "airline_icao_code": airline.get("icaoCode"),

                    # Flight
                    "flight_number": flight.get("number"),
                    "flight_iata_number": flight.get("iataNumber"),
                    "flight_icao_number": flight.get("icaoNumber"),

                    # Codeshare Airline
                    "codeshared_airline_name": codeshared_airline.get("name"),
                    "codeshared_airline_iata_code": codeshared_airline.get("iataCode"),
                    "codeshared_airline_icao_code": codeshared_airline.get("icaoCode"),

                    # Codeshare Flight
                    "codeshared_flight_number": codeshared_flight.get("number"),
                    "codeshared_flight_iata_number": codeshared_flight.get("iataNumber"),
                    "codeshared_flight_icao_number": codeshared_flight.get("icaoNumber")
                }

                if storage_mode in ("db", "both"):
                    rows_to_insert.append(row_data)

                if storage_mode in ("csv", "both"):
                    csv_rows.append(row_data)

            except Exception as e:
                parse_errors += 1

                logger.warning(
                    f"[Historical Schedule] Parse error: {e}"
                )

        inserted = 0


        if rows_to_insert and storage_mode in ("db", "both"):

            try:
                client = DatabaseClient()

                async with client.session("aviationedge") as session:

                    inserted = await bulk_insert_historical_schedule(
                        session=session,
                        rows=rows_to_insert
                    )

                    await session.commit()

                logger.info(
                    f"[Historical Schedule] "
                    f"Inserted {inserted}/{len(rows_to_insert)} "
                    f"rows | "
                    f"{airport_code=} | "
                    f"{schedule_type=} | "
                    f"{range_from.date()} -> {range_to.date()}"
                )

            except Exception as e:
                logger.exception(
                    f"[Historical Schedule] Bulk insert failed: {e}"
                )

        if csv_rows and storage_mode in ("csv", "both") and csv_path:

            try:
                write_csv(csv_rows, csv_path.as_posix())

                logger.info(
                    f"[Historical Schedule] "
                    f"Written {len(csv_rows)} rows to CSV"
                )

            except Exception as e:
                logger.exception(
                    f"[Historical Schedule] CSV write failed: {e}"
                )

        if parse_errors:
            logger.warning(
                f"[Historical Schedule] "
                f"Parse errors: {parse_errors}"
            )

        return inserted


async def fetch_with_semaphore(
        sem: asyncio.Semaphore,
        **kwargs
):
    async with sem:
        return await fetch_historical_schedule_chunk(**kwargs)


@performance_timer
async def fetch_historical_schedules(
        airport_codes: List[str],
        schedule_types: List[str],
        start_date: str,
        end_date: str,
        airline_iata_codes: Optional[List[str]] = None,
        storage_mode: str = "db",
        csv_path: Optional[Path] = AVIATION_EDGE_PATH / (
                f"historical_schedules_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        ),
        resume_from: int = 0
):
    """
    storage_mode:
        - db
        - csv
        - both
    """

    logger.info("[Historical Schedule] Starting fetch")

    start_dt = parse_date_or_datetime(start_date)
    end_dt = parse_date_or_datetime(end_date)

    date_ranges = chunk_date_ranges(
        start_dt,
        end_dt,
        AVIATION_EDGE_MAX_RANGE_DAYS
    )

    airline_batches = split_batches(
        airline_iata_codes,
        AVIATION_EDGE_MAX_BATCH_SIZE
    )

    airline_batch_sizes = [
        len(batch) if batch else 1
        for batch in airline_batches
    ]

    total_iterations = (
            len(date_ranges)
            * len(airport_codes)
            * len(schedule_types)
            * sum(airline_batch_sizes)
    )

    current_iteration = 0
    total_saved = 0

    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENT_REQUESTS,
        ttl_dns_cache=300
    )

    timeout = aiohttp.ClientTimeout(
        total=120
    )

    client = DatabaseClient()

    async with (
        aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        ) as http,

        client.session("aviationedge") as session
    ):

        tasks = []

        for range_start, range_end in date_ranges:

            for airport_code in airport_codes:

                for schedule_type in schedule_types:

                    for airline_batch in airline_batches:

                        if not airline_batch:
                            airline_batch = [None]

                        for airline_iata in airline_batch:

                            current_iteration += 1

                            if current_iteration < resume_from:
                                continue

                            logger.info(
                                f"[Historical Schedule] "
                                f"Progress "
                                f"{current_iteration}/{total_iterations} | "
                                f"{airport_code=} | "
                                f"{schedule_type=} | "
                                f"{airline_iata=} | "
                                f"{range_start.date()} -> "
                                f"{range_end.date()}"
                            )

                            tasks.append(
                                fetch_with_semaphore(
                                    sem=sem,
                                    airport_code=airport_code,
                                    schedule_type=schedule_type,
                                    range_from=range_start,
                                    range_to=range_end,
                                    airline_iata=airline_iata,
                                    http=http,
                                    storage_mode=storage_mode,
                                    csv_path=csv_path
                                )
                            )

        logger.info(
            f"[Historical Schedule] "
            f"Executing {len(tasks)} tasks"
        )

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        for result in results:

            if isinstance(result, Exception):
                logger.exception(
                    f"[Historical Schedule] Task failed: {result}"
                )
                continue

            total_saved += result

        await session.commit()

    logger.info(
        f"[Historical Schedule] Completed. "
        f"Total saved: {total_saved}"
    )

    return total_saved


if __name__ == "__main__":
    import asyncio
    from Utils import str_to_list

    # AIRPORTS = str_to_list(
    #     """
    #     """.upper()
    # )
    AIRPORTS = ['AAC', 'AAE', 'AAL', 'AAM', 'AAN', 'AAR', 'ABB', 'ABC', 'ABD', 'ABI', 'ABJ', 'ABM', 'ABO', 'ABQ', 'ABS', 'ABT', 'ABV', 'ABX', 'ABY', 'ABZ', 'ACA', 'ACC', 'ACE', 'ACH', 'ACK', 'ACT', 'ACY', 'ADA', 'ADB', 'ADD', 'ADE', 'ADF', 'ADJ', 'ADL', 'ADS', 'ADW', 'ADX', 'ADY', 'ADZ', 'AEB', 'AEP', 'AER', 'AES', 'AEU', 'AEX', 'AEY', 'AFD', 'AFW', 'AGA', 'AGB', 'AGC', 'AGF', 'AGH', 'AGP', 'AGR', 'AGS', 'AGT', 'AGU', 'AHB', 'AHN', 'AHO', 'AHU', 'AIZ', 'AJA', 'AJF', 'AJI', 'AJL', 'AJR', 'AJU', 'AKC', 'AKF', 'AKH', 'AKL', 'AKR', 'AKT', 'AKX', 'ALA', 'ALB', 'ALC', 'ALF', 'ALG', 'ALI', 'ALL', 'ALO', 'ALW', 'AMA', 'AMD', 'AMM', 'AMQ', 'AMS', 'ANA', 'ANC', 'ANE', 'ANF', 'ANG', 'ANK', 'ANP', 'ANR', 'ANU', 'AOC', 'AOE', 'AOI', 'AOJ', 'AOK', 'AOO', 'AOR', 'AOT', 'AOY', 'APA', 'APC', 'APF', 'APG', 'API', 'APL', 'AQI', 'AQJ', 'AQP', 'ARA', 'ARB', 'ARE', 'ARI', 'ARK', 'ARM', 'ARN', 'ART', 'ARW', 'ASB', 'ASE', 'ASF', 'ASI', 'ASJ', 'ASM', 'ASP', 'ASR', 'AST', 'ASU', 'ASV', 'ASW', 'ATA', 'ATH', 'ATL', 'ATQ', 'ATZ', 'AUA', 'AUC', 'AUF', 'AUH', 'AUO', 'AUS', 'AUU', 'AUZ', 'AVB', 'AVL', 'AVN', 'AVP', 'AVV', 'AVX', 'AXA', 'AXD', 'AXJ', 'AXM', 'AYJ', 'AYK', 'AYP', 'AYQ', 'AYT', 'AZA', 'AZI', 'AZN', 'AZP', 'AZS', 'BAC', 'BAF', 'BAH', 'BAL', 'BAQ', 'BAR', 'BAX', 'BAY', 'BBA', 'BBI', 'BBK', 'BBO', 'BBQ', 'BBS', 'BBU', 'BBX', 'BCM', 'BCN', 'BCT', 'BDA', 'BDB', 'BDJ', 'BDL', 'BDM', 'BDN', 'BDO', 'BDQ', 'BDR', 'BDS', 'BDU', 'BED', 'BEG', 'BEJ', 'BEK', 'BEL', 'BEN', 'BER', 'BES', 'BEW', 'BEX', 'BEY', 'BFD', 'BFH', 'BFI', 'BFK', 'BFM', 'BFN', 'BFO', 'BFS', 'BFT', 'BGA', 'BGF', 'BGG', 'BGI', 'BGO', 'BGR', 'BGW', 'BGY', 'BHC', 'BHD', 'BHH', 'BHJ', 'BHK', 'BHM', 'BHO', 'BHQ', 'BHS', 'BHV', 'BHX', 'BIA', 'BIF', 'BIK', 'BIL', 'BIO', 'BIQ', 'BIY', 'BJA', 'BJC', 'BJJ', 'BJL', 'BJM', 'BJV', 'BJX', 'BJY', 'BJZ', 'BKG', 'BKI', 'BKK', 'BKL', 'BKN', 'BKO', 'BKV', 'BKW', 'BKY', 'BKZ', 'BLA', 'BLB', 'BLE', 'BLH', 'BLI', 'BLK', 'BLL', 'BLM', 'BLQ', 'BLR', 'BLV', 'BLZ', 'BMA', 'BME', 'BMV', 'BNA', 'BND', 'BNE', 'BNI', 'BNK', 'BNX', 'BOB', 'BOD', 'BOG', 'BOH', 'BOI', 'BOJ', 'BOM', 'BON', 'BOO', 'BOS', 'BOY', 'BPM', 'BPN', 'BPS', 'BPT', 'BQH', 'BQK', 'BQN', 'BQS', 'BRC', 'BRE', 'BRI', 'BRM', 'BRN', 'BRO', 'BRQ', 'BRS', 'BRU', 'BSA', 'BSB', 'BSG', 'BSL', 'BSR', 'BSS', 'BSZ', 'BTH', 'BTM', 'BTP', 'BTS', 'BTV', 'BUD', 'BUF', 'BUQ', 'BUR', 'BUS', 'BUZ', 'BVA', 'BVB', 'BVC', 'BVE', 'BVY', 'BWA', 'BWC', 'BWE', 'BWI', 'BWK', 'BWN', 'BWO', 'BWU', 'BXG', 'BXH', 'BXJ', 'BXK', 'BXO', 'BXR', 'BXY', 'BYF', 'BYH', 'BYJ', 'BZE', 'BZG', 'BZN', 'BZO', 'BZR', 'BZV', 'CAB', 'CAC', 'CAG', 'CAI', 'CAK', 'CAN', 'CAT', 'CAZ', 'CBB', 'CBG', 'CBM', 'CBQ', 'CBR', 'CCC', 'CCD', 'CCF', 'CCJ', 'CCL', 'CCP', 'CCS', 'CCU', 'CCY', 'CDG', 'CDK', 'CDP', 'CDT', 'CDV', 'CEB', 'CEG', 'CEI', 'CEK', 'CEN', 'CEQ', 'CER', 'CEU', 'CFB', 'CFE', 'CFP', 'CFR', 'CFS', 'CFU', 'CGB', 'CGF', 'CGH', 'CGI', 'CGK', 'CGN', 'CGO', 'CGP', 'CGQ', 'CGR', 'CGS', 'CHA', 'CHC', 'CHF', 'CHJ', 'CHL', 'CHN', 'CHO', 'CHQ', 'CHR', 'CHS', 'CIA', 'CIC', 'CID', 'CIT', 'CIW', 'CIX', 'CIY', 'CJA', 'CJB', 'CJC', 'CJJ', 'CJL', 'CJS', 'CJU', 'CKG', 'CKL', 'CKV', 'CKY', 'CKZ', 'CLD', 'CLE', 'CLJ', 'CLL', 'CLO', 'CLQ', 'CLR', 'CLT', 'CLU', 'CLW', 'CLY', 'CMB', 'CMF', 'CMH', 'CMN', 'CMQ', 'CMR', 'CMW', 'CND', 'CNF', 'CNG', 'CNN', 'CNO', 'CNS', 'CNX', 'COD', 'COE', 'COF', 'COI', 'COK', 'CON', 'COO', 'COR', 'COS', 'COV', 'CPC', 'CPH', 'CPO', 'CPR', 'CPS', 'CPT', 'CQD', 'CQF', 'CRA', 'CRD', 'CRG', 'CRK', 'CRL', 'CRP', 'CRV', 'CRW', 'CRZ', 'CSG', 'CSN', 'CSW', 'CSX', 'CTA', 'CTC', 'CTG', 'CTH', 'CTM', 'CTN', 'CTS', 'CTT', 'CTU', 'CUC', 'CUE', 'CUF', 'CUL', 'CUN', 'CUR', 'CUU', 'CUZ', 'CVF', 'CVG', 'CVM', 'CVO', 'CVS', 'CVT', 'CWB', 'CWF', 'CWL', 'CXB', 'CXJ', 'CXL', 'CXR', 'CXT', 'CZL', 'CZM', 'CZU', 'CZX', 'DAA', 'DAB', 'DAC', 'DAD', 'DAL', 'DAM', 'DAR', 'DAV', 'DAY', 'DBB', 'DBO', 'DBR', 'DBV', 'DCA', 'DCM', 'DCN', 'DDD', 'DEB', 'DED', 'DEH', 'DEL', 'DEN', 'DFI', 'DFW', 'DGH', 'DGO', 'DGX', 'DHA', 'DHF', 'DHM', 'DHN', 'DIA', 'DIB', 'DIJ', 'DIL', 'DIM', 'DIN', 'DIU', 'DIY', 'DJE', 'DJG', 'DJJ', 'DKR', 'DLA', 'DLC', 'DLE', 'DLF', 'DLH', 'DLI', 'DLM', 'DLS', 'DLU', 'DMB', 'DME', 'DMK', 'DMM', 'DMO', 'DMU', 'DNA', 'DND', 'DNH', 'DNN', 'DNR', 'DNZ', 'DOD', 'DOH', 'DOL', 'DOM', 'DOV', 'DPA', 'DPS', 'DQM', 'DRS', 'DRT', 'DRV', 'DRW', 'DSA', 'DSI', 'DSM', 'DSN', 'DSS', 'DTM', 'DTN', 'DTW', 'DUB', 'DUR', 'DUS', 'DVO', 'DVR', 'DVT', 'DWC', 'DWD', 'DWH', 'DXB', 'DXE', 'DYG', 'DYR', 'DYS', 'DYU', 'DZA', 'DZN', 'EAM', 'EAS', 'EAT', 'EAU', 'EBA', 'EBB', 'EBJ', 'EBL', 'EBU', 'ECG', 'ECN', 'ECP', 'EDI', 'EDL', 'EDM', 'EDO', 'EDR', 'EED', 'EFD', 'EFL', 'EGC', 'EGE', 'EGS', 'EIH', 'EIN', 'EIS', 'EJA', 'EJH', 'EKB', 'EKI', 'EKO', 'ELD', 'ELF', 'ELH', 'ELN', 'ELP', 'ELQ', 'ELS', 'EMA', 'EMD', 'EME', 'ENA', 'ENH', 'ENS', 'ENU', 'ENW', 'EOH', 'EPA', 'EPL', 'ERC', 'ERF', 'ERH', 'ERS', 'ERZ', 'ESB', 'ESN', 'ESS', 'ESU', 'ESW', 'ETB', 'ETM', 'ETZ', 'EUG', 'EUN', 'EVE', 'EVN', 'EVV', 'EWB', 'EWN', 'EWR', 'EXT', 'EYP', 'EYW', 'EZE', 'EZS', 'FAB', 'FAE', 'FAI', 'FAO', 'FAR', 'FAT', 'FBG', 'FBM', 'FCA', 'FCB', 'FCH', 'FCM', 'FCN', 'FCO', 'FCT', 'FDF', 'FDH', 'FDK', 'FEG', 'FEN', 'FEZ', 'FFT', 'FIH', 'FJR', 'FKB', 'FKI', 'FKL', 'FKS', 'FLA', 'FLG', 'FLL', 'FLN', 'FLO', 'FLR', 'FMH', 'FMM', 'FMO', 'FMY', 'FNA', 'FNC', 'FNI', 'FNL', 'FOC', 'FOE', 'FOG', 'FOK', 'FOR', 'FPO', 'FRA', 'FRD', 'FRG', 'FRL', 'FRS', 'FRU', 'FRW', 'FSC', 'FSZ', 'FTE', 'FTK', 'FTW', 'FTY', 'FUE', 'FUK', 'FUL', 'FUO', 'FXE', 'FZL', 'FZO', 'GAH', 'GAI', 'GAN', 'GAQ', 'GAU', 'GAY', 'GBA', 'GBB', 'GBE', 'GBK', 'GCI', 'GCJ', 'GCM', 'GCY', 'GDL', 'GDN', 'GDX', 'GDZ', 'GEA', 'GED', 'GEG', 'GEO', 'GFF', 'GFK', 'GGT', 'GHA', 'GHF', 'GHK', 'GHV', 'GIB', 'GIG', 'GIL', 'GIR', 'GIT', 'GIZ', 'GKE', 'GKT', 'GLA', 'GLD', 'GLN', 'GLO', 'GLT', 'GMD', 'GMO', 'GMP', 'GMU', 'GNB', 'GND', 'GNI', 'GNJ', 'GNV', 'GNY', 'GOA', 'GOH', 'GOI', 'GOJ', 'GOM', 'GON', 'GOP', 'GOT', 'GOU', 'GOX', 'GOZ', 'GPA', 'GPS', 'GPT', 'GRB', 'GRD', 'GRF', 'GRJ', 'GRK', 'GRO', 'GRQ', 'GRR', 'GRS', 'GRU', 'GRV', 'GRX', 'GRZ', 'GSB', 'GSM', 'GSO', 'GSP', 'GSS', 'GSV', 'GTF', 'GTO', 'GTP', 'GTR', 'GUA', 'GUF', 'GUL', 'GUM', 'GUW', 'GVA', 'GVT', 'GVW', 'GWD', 'GWI', 'GWL', 'GWO', 'GWT', 'GYB', 'GYD', 'GYE', 'GYI', 'GYN', 'GYR', 'GYY', 'GZP', 'GZT', 'HAC', 'HAD', 'HAF', 'HAH', 'HAJ', 'HAK', 'HAM', 'HAN', 'HAR', 'HAS', 'HAU', 'HAV', 'HBA', 'HBE', 'HBG', 'HBX', 'HDH', 'HDN', 'HDO', 'HDR', 'HDS', 'HDY', 'HEL', 'HER', 'HET', 'HFA', 'HFD', 'HFE', 'HGA', 'HGD', 'HGH', 'HGI', 'HGR', 'HHH', 'HHI', 'HHN', 'HHP', 'HHQ', 'HHR', 'HID', 'HII', 'HIJ', 'HIN', 'HIO', 'HJR', 'HKG', 'HKS', 'HKT', 'HKY', 'HLA', 'HLE', 'HLN', 'HLP', 'HMB', 'HME', 'HMO', 'HND', 'HNL', 'HOF', 'HOG', 'HOP', 'HOQ', 'HOR', 'HOU', 'HPH', 'HPN', 'HQM', 'HRB', 'HRE', 'HRF', 'HRG', 'HRI', 'HRK', 'HSA', 'HSH', 'HSR', 'HST', 'HSV', 'HTA', 'HTI', 'HTT', 'HTY', 'HUI', 'HUN', 'HUT', 'HUX', 'HUY', 'HUZ', 'HVN', 'HVR', 'HWD', 'HWO', 'HYA', 'HYD', 'HZL', 'HZP', 'IAB', 'IAD', 'IAH', 'IAR', 'IAS', 'IBA', 'IBE', 'IBZ', 'ICN', 'ICT', 'IDA', 'IDI', 'IDP', 'IDR', 'IEG', 'IFN', 'IFO', 'IGD', 'IGR', 'IGS', 'IGT', 'IGU', 'IJK', 'IKA', 'IKB', 'IKK', 'IKT', 'IKU', 'ILD', 'ILG', 'ILM', 'ILN', 'ILQ', 'ILR', 'ILS', 'ILY', 'IMF', 'IMM', 'IMP', 'INC', 'IND', 'INH', 'INI', 'INL', 'INN', 'INV', 'IOA', 'IOM', 'IOS', 'IOW', 'IPC', 'IPH', 'IPI', 'IPL', 'IQA', 'IQQ', 'IQT', 'IRG', 'IRI', 'ISB', 'ISE', 'ISG', 'ISJ', 'ISK', 'ISL', 'ISM', 'ISP', 'IST', 'ISU', 'ITH', 'ITM', 'ITO', 'IVG', 'IVL', 'IVR', 'IWK', 'IXA', 'IXB', 'IXC', 'IXD', 'IXE', 'IXG', 'IXJ', 'IXL', 'IXM', 'IXR', 'IXS', 'IXU', 'IXZ', 'IYO', 'IZM', 'JAC', 'JAD', 'JAF', 'JAI', 'JAL', 'JAM', 'JAN', 'JAU', 'JAX', 'JBQ', 'JCI', 'JCL', 'JCN', 'JDH', 'JDO', 'JED', 'JER', 'JFK', 'JGA', 'JGB', 'JHB', 'JHF', 'JHG', 'JHL', 'JIB', 'JJD', 'JJG', 'JJN', 'JKG', 'JLR', 'JMK', 'JNB', 'JNU', 'JOE', 'JOG', 'JOI', 'JOS', 'JOT', 'JPA', 'JQE', 'JRF', 'JRG', 'JRH', 'JRO', 'JRS', 'JSA', 'JSH', 'JSI', 'JSJ', 'JSR', 'JST', 'JTR', 'JUB', 'JUJ', 'JUL', 'JYV', 'JZI', 'KAD', 'KAN', 'KAO', 'KAT', 'KBL', 'KBP', 'KBV', 'KCF', 'KCH', 'KCM', 'KCO', 'KCY', 'KDH', 'KDI', 'KDU', 'KEF', 'KEL', 'KEN', 'KER', 'KEU', 'KFS', 'KFZ', 'KGA', 'KGD', 'KGF', 'KGL', 'KGS', 'KHB', 'KHE', 'KHH', 'KHI', 'KHN', 'KHS', 'KHV', 'KHY', 'KID', 'KIH', 'KIJ', 'KIK', 'KIM', 'KIN', 'KIR', 'KIS', 'KIT', 'KIX', 'KIY', 'KJA', 'KJB', 'KJK', 'KJT', 'KKC', 'KKN', 'KLF', 'KLH', 'KLO', 'KLU', 'KLV', 'KLW', 'KLX', 'KLZ', 'KME', 'KMG', 'KMH', 'KMJ', 'KMS', 'KND', 'KNH', 'KNO', 'KNU', 'KOA', 'KOE', 'KOF', 'KOJ', 'KOK', 'KOS', 'KOV', 'KPO', 'KQH', 'KQT', 'KRH', 'KRK', 'KRN', 'KRO', 'KRP', 'KRR', 'KRS', 'KRT', 'KRW', 'KSC', 'KSD', 'KSF', 'KSN', 'KSO', 'KSQ', 'KSU', 'KSW', 'KSY', 'KTA', 'KTG', 'KTI', 'KTL', 'KTM', 'KTN', 'KTR', 'KTT', 'KTW', 'KUF', 'KUL', 'KUN', 'KUO', 'KUT', 'KUV', 'KVA', 'KVO', 'KWE', 'KWI', 'KWJ', 'KWL', 'KWM', 'KXO', 'KYA', 'KYB', 'KYD', 'KYE', 'KZN', 'KZO', 'KZR', 'KZS', 'LAB', 'LAD', 'LAF', 'LAK', 'LAL', 'LAO', 'LAP', 'LAQ', 'LAS', 'LAW', 'LAX', 'LAY', 'LBA', 'LBB', 'LBC', 'LBD', 'LBE', 'LBG', 'LBJ', 'LBU', 'LBV', 'LCA', 'LCE', 'LCG', 'LCI', 'LCJ', 'LCK', 'LCQ', 'LCV', 'LCY', 'LDB', 'LDE', 'LDH', 'LDV', 'LDY', 'LDZ', 'LED', 'LEE', 'LEH', 'LEI', 'LEJ', 'LEN', 'LES', 'LET', 'LEX', 'LEY', 'LFN', 'LFT', 'LFW', 'LGA', 'LGB', 'LGG', 'LGK', 'LGW', 'LHA', 'LHE', 'LHR', 'LHV', 'LHW', 'LIF', 'LIG', 'LIH', 'LIL', 'LIM', 'LIN', 'LIR', 'LIS', 'LIT', 'LIX', 'LJG', 'LJU', 'LKL', 'LKO', 'LKY', 'LKZ', 'LLA', 'LLE', 'LLK', 'LLW', 'LME', 'LMM', 'LMO', 'LMP', 'LMQ', 'LMR', 'LMS', 'LNA', 'LNK', 'LNS', 'LNY', 'LNZ', 'LOP', 'LOS', 'LPA', 'LPB', 'LPI', 'LPK', 'LPL', 'LPQ', 'LPX', 'LRD', 'LRE', 'LRF', 'LRH', 'LRM', 'LRR', 'LRT', 'LSC', 'LSF', 'LST', 'LTK', 'LTN', 'LTO', 'LTQ', 'LUA', 'LUD', 'LUG', 'LUK', 'LUL', 'LUM', 'LUN', 'LUW', 'LUX', 'LUZ', 'LVI', 'LVK', 'LVM', 'LWB', 'LWC', 'LWO', 'LWS', 'LXR', 'LXS', 'LYI', 'LYN', 'LYP', 'LYR', 'LYS', 'LYX', 'LZN', 'LZU', 'MAA', 'MAB', 'MAD', 'MAF', 'MAH', 'MAM', 'MAN', 'MAO', 'MAR', 'MBA', 'MBD', 'MBI', 'MBJ', 'MBX', 'MCI', 'MCM', 'MCN', 'MCO', 'MCP', 'MCT', 'MCX', 'MCY', 'MCZ', 'MDC', 'MDE', 'MDJ', 'MDK', 'MDQ', 'MDR', 'MDT', 'MDW', 'MDZ', 'MEB', 'MEC', 'MED', 'MEI', 'MEL', 'MEM', 'MEX', 'MFA', 'MFE', 'MFG', 'MFM', 'MFR', 'MFU', 'MGA', 'MGC', 'MGE', 'MGF', 'MGH', 'MGL', 'MGM', 'MGQ', 'MGR', 'MHC', 'MHD', 'MHG', 'MHH', 'MHK', 'MHQ', 'MHT', 'MIA', 'MIC', 'MID', 'MIK', 'MIM', 'MIR', 'MIU', 'MIV', 'MIW', 'MJI', 'MJM', 'MJT', 'MJX', 'MKC', 'MKE', 'MKK', 'MKL', 'MKY', 'MKZ', 'MLA', 'MLB', 'MLE', 'MLH', 'MLI', 'MLM', 'MLO', 'MLU', 'MLX', 'MME', 'MMH', 'MMK', 'MMU', 'MMX', 'MNC', 'MNL', 'MNZ', 'MOB', 'MOC', 'MOD', 'MOK', 'MOL', 'MOT', 'MOV', 'MPH', 'MPL', 'MPM', 'MPN', 'MQF', 'MQL', 'MQM', 'MQP', 'MRA', 'MRB', 'MRC', 'MRE', 'MRS', 'MRU', 'MRV', 'MRY', 'MRZ', 'MSC', 'MSO', 'MSP', 'MSQ', 'MSR', 'MST', 'MSU', 'MSY', 'MTH', 'MTJ', 'MTN', 'MTR', 'MTS', 'MTY', 'MTZ', 'MUB', 'MUC', 'MUH', 'MUI', 'MUX', 'MUZ', 'MVD', 'MVN', 'MVY', 'MWC', 'MWH', 'MWL', 'MWS', 'MWX', 'MWZ', 'MXL', 'MXP', 'MXX', 'MYD', 'MYF', 'MYJ', 'MYL', 'MYQ', 'MYW', 'MYY', 'MZB', 'MZG', 'MZH', 'MZJ', 'MZR', 'MZT', 'MZY', 'NAA', 'NAG', 'NAJ', 'NAL', 'NAN', 'NAP', 'NAS', 'NAT', 'NAV', 'NBC', 'NBE', 'NBJ', 'NBO', 'NCE', 'NCH', 'NCL', 'NCO', 'NCU', 'NCY', 'NDB', 'NDJ', 'NDR', 'NEL', 'NEW', 'NFL', 'NGB', 'NGF', 'NGL', 'NGO', 'NGS', 'NGU', 'NHA', 'NHD', 'NHK', 'NHT', 'NIM', 'NIP', 'NJC', 'NJF', 'NJK', 'NKC', 'NKG', 'NKM', 'NKT', 'NKU', 'NLA', 'NLO', 'NLP', 'NLU', 'NMA', 'NMF', 'NMI', 'NMM', 'NNG', 'NOC', 'NOP', 'NOS', 'NOT', 'NPT', 'NPY', 'NQI', 'NQN', 'NQX', 'NQY', 'NQZ', 'NRK', 'NRN', 'NRR', 'NRT', 'NSI', 'NSK', 'NSY', 'NTD', 'NTE', 'NTG', 'NTL', 'NTR', 'NTU', 'NTY', 'NUE', 'NUM', 'NUQ', 'NVA', 'NVI', 'NVT', 'NWI', 'NYO', 'NZY', 'OAG', 'OAJ', 'OAK', 'OAX', 'OBF', 'OCC', 'OCE', 'OCF', 'ODB', 'ODE', 'ODS', 'OER', 'OGB', 'OGD', 'OGG', 'OGU', 'OGZ', 'OHD', 'OHS', 'OKA', 'OKC', 'OKJ', 'OKO', 'OKT', 'OLA', 'OLB', 'OLX', 'OMA', 'OMD', 'OMH', 'OMK', 'OMN', 'OMO', 'OMR', 'OMS', 'OND', 'ONO', 'ONQ', 'ONT', 'OOL', 'OPF', 'OPO', 'OPS', 'OQN', 'ORB', 'ORD', 'ORE', 'ORF', 'ORH', 'ORK', 'ORL', 'ORN', 'ORY', 'OSD', 'OSF', 'OSI', 'OSL', 'OSN', 'OSO', 'OSR', 'OSS', 'OST', 'OSU', 'OSW', 'OSX', 'OTG', 'OTH', 'OTP', 'OUA', 'OUD', 'OUL', 'OVB', 'OVD', 'OVS', 'OWB', 'OWD', 'OXB', 'OXC', 'OXF', 'OXR', 'OZG', 'OZH', 'OZP', 'OZZ', 'PAC', 'PAD', 'PAE', 'PAM', 'PAP', 'PAS', 'PAT', 'PBC', 'PBH', 'PBI', 'PBM', 'PBZ', 'PCL', 'PCP', 'PDG', 'PDK', 'PDL', 'PDP', 'PDS', 'PDV', 'PDX', 'PED', 'PEE', 'PEG', 'PEI', 'PEK', 'PEL', 'PEM', 'PEN', 'PER', 'PET', 'PEV', 'PEW', 'PFB', 'PFO', 'PGA', 'PGD', 'PGF', 'PGH', 'PGK', 'PGL', 'PGV', 'PGX', 'PHA', 'PHC', 'PHF', 'PHG', 'PHK', 'PHL', 'PHW', 'PHX', 'PIA', 'PIE', 'PIH', 'PIK', 'PIO', 'PIR', 'PIS', 'PIT', 'PIU', 'PIX', 'PJG', 'PKC', 'PKH', 'PKN', 'PKU', 'PKV', 'PKX', 'PKY', 'PKZ', 'PLM', 'PLQ', 'PLS', 'PLU', 'PLW', 'PLX', 'PLZ', 'PMA', 'PMC', 'PMD', 'PMF', 'PMI', 'PMO', 'PMR', 'PMW', 'PNA', 'PNE', 'PNH', 'PNK', 'PNL', 'PNQ', 'PNR', 'PNS', 'PNT', 'PNZ', 'POA', 'POB', 'POE', 'POL', 'POM', 'POP', 'POR', 'POS', 'POU', 'POW', 'POX', 'POZ', 'PPK', 'PPL', 'PPN', 'PPP', 'PPS', 'PPT', 'PQC', 'PQQ', 'PRC', 'PRG', 'PRN', 'PRY', 'PSA', 'PSC', 'PSD', 'PSE', 'PSF', 'PSM', 'PSO', 'PSP', 'PSR', 'PSS', 'PTB', 'PTG', 'PTK', 'PTP', 'PTW', 'PTY', 'PUB', 'PUF', 'PUJ', 'PUQ', 'PUS', 'PUW', 'PUY', 'PVD', 'PVG', 'PVH', 'PVK', 'PVR', 'PVU', 'PWK', 'PWM', 'PWN', 'PWQ', 'PWY', 'PXM', 'PXO', 'PXU', 'PYM', 'PZB', 'PZH', 'PZL', 'PZU', 'PZY', 'QAE', 'QBA', 'QBL', 'QBM', 'QBR', 'QBS', 'QBW', 'QCC', 'QCH', 'QCK', 'QCM', 'QCO', 'QCP', 'QCS', 'QCZ', 'QDL', 'QDN', 'QDR', 'QDV', 'QEC', 'QEF', 'QEH', 'QEJ', 'QER', 'QEW', 'QEZ', 'QFM', 'QFV', 'QGS', 'QGY', 'QHD', 'QHP', 'QIB', 'QIE', 'QIM', 'QIP', 'QIS', 'QIV', 'QIW', 'QIZ', 'QJH', 'QKE', 'QKF', 'QKH', 'QKL', 'QKM', 'QKT', 'QMT', 'QNE', 'QNM', 'QNN', 'QNS', 'QOA', 'QOG', 'QOW', 'QPG', 'QQA', 'QQC', 'QQD', 'QQE', 'QQF', 'QQL', 'QQN', 'QQO', 'QQP', 'QQQ', 'QQR', 'QQS', 'QQT', 'QQV', 'QQY', 'QRA', 'QRB', 'QRD', 'QRO', 'QRW', 'QSA', 'QSB', 'QSC', 'QSH', 'QSI', 'QSK', 'QSL', 'QSO', 'QSP', 'QSR', 'QSS', 'QSY', 'QTB', 'QTC', 'QTT', 'QTU', 'QTW', 'QTZ', 'QUA', 'QUD', 'QUO', 'QWG', 'QXB', 'QYA', 'QYF', 'QYI', 'QYK', 'QYL', 'QYM', 'QYS', 'QYU', 'QYY', 'QYZ', 'RAC', 'RAE', 'RAH', 'RAI', 'RAJ', 'RAK', 'RAS', 'RBA', 'RBD', 'RBG', 'RBK', 'RBM', 'RBR', 'RBZ', 'RCB', 'RCH', 'RCU', 'RDD', 'RDG', 'RDM', 'RDO', 'RDP', 'RDU', 'RDZ', 'REC', 'REG', 'REN', 'REP', 'RES', 'REU', 'REX', 'RFD', 'RGL', 'RGN', 'RGS', 'RHO', 'RIA', 'RIC', 'RIH', 'RIL', 'RIV', 'RIX', 'RJA', 'RJK', 'RJL', 'RJN', 'RKD', 'RKE', 'RKH', 'RKT', 'RKV', 'RLG', 'RMA', 'RMB', 'RME', 'RMF', 'RMG', 'RMI', 'RML', 'RMO', 'RMQ', 'RMS', 'RMU', 'RNB', 'RNJ', 'RNN', 'RNO', 'RNS', 'RNT', 'RNZ', 'ROA', 'ROB', 'ROC', 'ROD', 'ROG', 'ROK', 'ROO', 'ROR', 'ROS', 'ROT', 'ROV', 'ROW', 'RPR', 'RQA', 'RQY', 'RSI', 'RST', 'RSW', 'RTB', 'RTE', 'RTM', 'RTW', 'RUH', 'RUN', 'RVN', 'RVO', 'RYB', 'RYG', 'RYK', 'RZE', 'RZN', 'RZR', 'RZV', 'SAF', 'SAG', 'SAI', 'SAL', 'SAN', 'SAP', 'SAT', 'SAV', 'SAW', 'SAY', 'SBA', 'SBD', 'SBH', 'SBK', 'SBM', 'SBN', 'SBW', 'SBZ', 'SCE', 'SCF', 'SCG', 'SCK', 'SCL', 'SCN', 'SCO', 'SCQ', 'SCR', 'SCT', 'SCU', 'SCV', 'SCW', 'SCY', 'SDD', 'SDF', 'SDJ', 'SDK', 'SDL', 'SDM', 'SDQ', 'SDR', 'SDU', 'SDW', 'SEA', 'SEE', 'SEF', 'SEG', 'SEN', 'SEZ', 'SFA', 'SFB', 'SFF', 'SFG', 'SFJ', 'SFM', 'SFO', 'SFS', 'SFT', 'SFZ', 'SGD', 'SGE', 'SGF', 'SGN', 'SGR', 'SGU', 'SGX', 'SHA', 'SHE', 'SHI', 'SHJ', 'SHL', 'SHM', 'SHO', 'SHR', 'SHV', 'SHW', 'SHZ', 'SID', 'SIG', 'SIN', 'SIR', 'SIS', 'SJC', 'SJD', 'SJJ', 'SJK', 'SJO', 'SJP', 'SJT', 'SJU', 'SJW', 'SKB', 'SKD', 'SKF', 'SKG', 'SKP', 'SKQ', 'SKT', 'SKX', 'SKZ', 'SLA', 'SLC', 'SLE', 'SLK', 'SLL', 'SLM', 'SLN', 'SLP', 'SLU', 'SLV', 'SLW', 'SLZ', 'SMA', 'SMF', 'SMI', 'SML', 'SMN', 'SMO', 'SMR', 'SMV', 'SMW', 'SNA', 'SNN', 'SNO', 'SNR', 'SNS', 'SNU', 'SOB', 'SOC', 'SOF', 'SOP', 'SOQ', 'SOU', 'SPC', 'SPF', 'SPG', 'SPI', 'SPN', 'SPS', 'SPU', 'SPW', 'SPX', 'SQA', 'SQL', 'SQQ', 'SRQ', 'SRW', 'SRX', 'SSA', 'SSF', 'SSG', 'SSH', 'SSI', 'SSM', 'SSN', 'STC', 'STI', 'STL', 'STM', 'STN', 'STP', 'STR', 'STS', 'STT', 'STV', 'STY', 'SUA', 'SUB', 'SUF', 'SUJ', 'SUL', 'SUM', 'SUN', 'SUS', 'SUU', 'SUX', 'SVD', 'SVG', 'SVH', 'SVN', 'SVO', 'SVQ', 'SVX', 'SWA', 'SWF', 'SWU', 'SXB', 'SXI', 'SXM', 'SXR', 'SXV', 'SYD', 'SYQ', 'SYR', 'SYX', 'SYY', 'SYZ', 'SZB', 'SZF', 'SZG', 'SZK', 'SZX', 'SZY', 'SZZ', 'TAB', 'TAE', 'TAK', 'TAM', 'TAN', 'TAO', 'TAP', 'TAR', 'TAS', 'TAT', 'TAY', 'TAZ', 'TBB', 'TBJ', 'TBN', 'TBO', 'TBP', 'TBS', 'TBY', 'TBZ', 'TCE', 'TCL', 'TCP', 'TCQ', 'TCR', 'TDK', 'TDW', 'TEA', 'TEB', 'TEQ', 'TER', 'TET', 'TEV', 'TFN', 'TFS', 'TFT', 'TFU', 'TGD', 'TGM', 'TGN', 'TGT', 'TGU', 'TGV', 'TGZ', 'THB', 'THD', 'THE', 'THG', 'THN', 'THR', 'THS', 'THV', 'TIA', 'TIF', 'TIJ', 'TIM', 'TIP', 'TIR', 'TIV', 'TIW', 'TJH', 'TJI', 'TJK', 'TJM', 'TJQ', 'TJS', 'TKD', 'TKF', 'TKN', 'TKQ', 'TKU', 'TLC', 'TLH', 'TLL', 'TLN', 'TLS', 'TLV', 'TMB', 'TMC', 'TMJ', 'TML', 'TMP', 'TMR', 'TMS', 'TMW', 'TNA', 'TNG', 'TNJ', 'TNN', 'TNR', 'TNT', 'TNU', 'TOB', 'TOE', 'TOJ', 'TOL', 'TOS', 'TOY', 'TPA', 'TPE', 'TPP', 'TPQ', 'TPS', 'TQO', 'TRA', 'TRC', 'TRD', 'TRF', 'TRI', 'TRK', 'TRM', 'TRN', 'TRS', 'TRU', 'TRV', 'TRZ', 'TSA', 'TSF', 'TSN', 'TSR', 'TST', 'TSV', 'TTA', 'TTD', 'TTE', 'TTN', 'TTT', 'TTU', 'TUC', 'TUF', 'TUI', 'TUK', 'TUL', 'TUN', 'TUP', 'TUS', 'TUU', 'TVC', 'TVI', 'TVL', 'TVT', 'TWF', 'TWU', 'TXN', 'TYG', 'TYL', 'TYN', 'TYS', 'TZL', 'TZN', 'TZX', 'UAB', 'UAK', 'UAM', 'UAQ', 'UAR', 'UBJ', 'UBN', 'UBP', 'UBS', 'UDD', 'UDI', 'UDR', 'UEL', 'UES', 'UET', 'UFA', 'UGC', 'UGN', 'UIB', 'UIH', 'UII', 'UIO', 'UIP', 'UKA', 'UKB', 'UKI', 'UKK', 'ULD', 'ULH', 'ULN', 'ULV', 'ULX', 'ULY', 'UME', 'UNA', 'UNI', 'UNU', 'UPB', 'UPG', 'UPN', 'URA', 'URC', 'URG', 'URO', 'URT', 'URY', 'USA', 'USH', 'USJ', 'USM', 'USN', 'USQ', 'UST', 'UTG', 'UTH', 'UTN', 'UTP', 'UTT', 'UTW', 'UUD', 'UUS', 'UVE', 'UVF', 'UYN', 'UYU', 'UZR', 'VAA', 'VAF', 'VAN', 'VAR', 'VAS', 'VBG', 'VBS', 'VBY', 'VCA', 'VCE', 'VCL', 'VCP', 'VCS', 'VCT', 'VCV', 'VDA', 'VDC', 'VDF', 'VDH', 'VDO', 'VER', 'VFA', 'VGA', 'VGO', 'VGT', 'VIE', 'VII', 'VIJ', 'VIL', 'VIP', 'VIR', 'VIT', 'VIX', 'VKO', 'VKV', 'VLC', 'VLD', 'VLI', 'VLL', 'VLN', 'VNO', 'VNS', 'VNX', 'VNY', 'VOD', 'VOG', 'VOL', 'VOZ', 'VPS', 'VPZ', 'VQQ', 'VRA', 'VRB', 'VRN', 'VSA', 'VST', 'VTB', 'VTE', 'VTZ', 'VUP', 'VUU', 'VVC', 'VVI', 'VVO', 'VXC', 'VXE', 'VXO', 'VYD', 'VYS', 'WAE', 'WAT', 'WAW', 'WDB', 'WDH', 'WDR', 'WEI', 'WEL', 'WFR', 'WGA', 'WGB', 'WGO', 'WHP', 'WIC', 'WIL', 'WJF', 'WJR', 'WJU', 'WKF', 'WLC', 'WLE', 'WLP', 'WMI', 'WNS', 'WNZ', 'WOE', 'WOL', 'WRO', 'WRZ', 'WST', 'WTB', 'WTN', 'WUH', 'WUX', 'WVB', 'WWD', 'WYS', 'XAP', 'XCR', 'XFN', 'XFW', 'XIY', 'XJD', 'XLS', 'XMN', 'XNA', 'XNN', 'XPL', 'XRY', 'XSP', 'XUZ', 'YAM', 'YBG', 'YBL', 'YCD', 'YDA', 'YDT', 'YEG', 'YEI', 'YEV', 'YFB', 'YGJ', 'YGK', 'YHM', 'YHU', 'YHZ', 'YIA', 'YIP', 'YKA', 'YKM', 'YKO', 'YKS', 'YLW', 'YMA', 'YMX', 'YNB', 'YNG', 'YNT', 'YNY', 'YOL', 'YOW', 'YPA', 'YQA', 'YQB', 'YQM', 'YQR', 'YQT', 'YQU', 'YQX', 'YQY', 'YTR', 'YUL', 'YUM', 'YVC', 'YVQ', 'YVR', 'YVT', 'YWG', 'YXE', 'YXJ', 'YXT', 'YXX', 'YXY', 'YYC', 'YYD', 'YYJ', 'YYR', 'YYT', 'YYZ', 'ZAD', 'ZAG', 'ZAL', 'ZAR', 'ZAZ', 'ZBL', 'ZBR', 'ZCL', 'ZCO', 'ZDY', 'ZFN', 'ZIA', 'ZIH', 'ZIS', 'ZKB', 'ZLO', 'ZNZ', 'ZOS', 'ZPH', 'ZQN', 'ZQW', 'ZRH', 'ZSE', 'ZTH', 'ZUH', 'ZYL', 'ZZE', 'ABB', 'AQI', 'AUA', 'AYJ', 'BBX', 'BVE', 'BXJ', 'BXY', 'CAI', 'CAT', 'CKL', 'CUR', 'DGX', 'DIA', 'DXB', 'ETM', 'FAE', 'FKI', 'GBA', 'GHV', 'GIB', 'GOM', 'GOX', 'GSV', 'HAR', 'HOR', 'HSA', 'HSR', 'INI', 'ISK', 'JCL', 'JRG', 'JRS', 'KGA', 'KHB', 'KND', 'KVO', 'NCO', 'NUM', 'OPS', 'PDL', 'PGK', 'PKY', 'PLS', 'QGY', 'QKL', 'QMT', 'QQL', 'QSS', 'QYF', 'RDP', 'RKH', 'RMU', 'RQA', 'RSI', 'RYB', 'SAG', 'SCE', 'SCR', 'SMA', 'SPC', 'TER', 'TEV', 'TFN', 'TQO', 'UBN', 'USJ', 'UST', 'UZR', 'WMI', 'WTB', 'ABB', 'AUA', 'AYJ', 'BBX', 'BVE', 'BXJ', 'BXY', 'CAT', 'CKL', 'CUR', 'DGX', 'DIA', 'ETM', 'FAE', 'FKI', 'GBA', 'GHV', 'GIB', 'GOM', 'GOX', 'GSV', 'HAR', 'HOR', 'HSA', 'HSR', 'INI', 'ISK', 'JCL', 'JRG', 'KGA', 'KHB', 'KND', 'KVO', 'NCO', 'NUM', 'OPS', 'PDL', 'PGK', 'PKY', 'PLS', 'QGY', 'QKL', 'QMT', 'QQL', 'QSS', 'QYF', 'RDP', 'RMU', 'RQA', 'RSI', 'SAG', 'SCE', 'SCR', 'SMA', 'SPC', 'TER', 'TEV', 'TFN', 'TQO', 'UBN', 'USJ', 'UST', 'UZR', 'WMI', 'WTB']


    # AIRLINES = str_to_list(
    #     """
    #     EK
    #     """.upper()
    # )

    AIRLINES = ['AAE', 'AAT', 'ABB', 'ABD', 'ABK', 'ABN', 'ABV', 'ABY', 'ADV', 'ADY', 'ADZ', 'AEA', 'AEB', 'AEE', 'AEZ', 'AFR', 'AHY', 'AIA', 'AIB', 'AIC', 'AIQ', 'AIR', 'AIZ', 'AJK', 'AJO', 'AKF', 'AKJ', 'AKT', 'ALE', 'ALK', 'ALW', 'AMA', 'AMB', 'AMC', 'AME', 'AOJ', 'APF', 'APG', 'APK', 'ARA', 'ARE', 'ART', 'ARU', 'ASL', 'ASV', 'ATC', 'ATG', 'ATN', 'AUR', 'AVA', 'AVJ', 'AVL', 'AVR', 'AWC', 'AWG', 'AWK', 'AXE', 'AXJ', 'AXM', 'AXS', 'AXV', 'AYG', 'AZG', 'AZM', 'AZQ', 'AZW', 'AZY', 'BAF', 'BAV', 'BAW', 'BBG', 'BBL', 'BBT', 'BCS', 'BDR', 'BER', 'BGH', 'BIX', 'BKP', 'BLA', 'BLX', 'BOE', 'BQA', 'BRH', 'BRO', 'BTI', 'BUC', 'BUR', 'BVL', 'CAI', 'CAT', 'CCM', 'CDC', 'CEL', 'CEY', 'CFE', 'CFG', 'CGA', 'CGS', 'CHX', 'CJL', 'CKS', 'CLH', 'CMF', 'CMP', 'CMS', 'CND', 'CNV', 'CPA', 'CRL', 'CSA', 'CSW', 'CTJ', 'CTN', 'CTW', 'CUB', 'CXB', 'CXI', 'CYF', 'CYP', 'DAH', 'DAK', 'DAN', 'DAV', 'DHK', 'DIR', 'DLH', 'DML', 'DNA', 'DTR', 'DVR', 'EAF', 'EAQ', 'EDW', 'EFC', 'EFW', 'EFY', 'EIN', 'EJU', 'ELH', 'ELY', 'ENT', 'EQX', 'EST', 'ESW', 'ETD', 'ETH', 'EVE', 'EWG', 'EWL', 'EXS', 'EXV', 'EZA', 'EZE', 'EZS', 'EZY', 'EZZ', 'FAD', 'FAG', 'FBY', 'FBZ', 'FDB', 'FDR', 'FDX', 'FEG', 'FFA', 'FFT', 'FGD', 'FHM', 'FHY', 'FIA', 'FIE', 'FIN', 'FJL', 'FJW', 'FKH', 'FLN', 'FOE', 'FPK', 'FPO', 'FPY', 'FSK', 'FSQ', 'FSU', 'FTO', 'FVS', 'FXT', 'GAV', 'GBB', 'GBG', 'GBL', 'GFA', 'GHN', 'GJM', 'GJT', 'GLG', 'GLR', 'GRL', 'GUG', 'GUY', 'HAL', 'HFM', 'HFY', 'HGO', 'HLJ', 'HMJ', 'HOP', 'HRM', 'HSO', 'HST', 'HVN', 'HYS', 'HZS', 'IAW', 'IBB', 'IBE', 'IBS', 'ICE', 'IFC', 'IFY', 'IGO', 'IJM', 'ISR', 'ITY', 'JAF', 'JAG', 'JTD', 'JUP', 'KEM', 'KEW', 'KGB', 'KGK', 'KLJ', 'KMM', 'KNE', 'KON', 'KRE', 'KSZ', 'KZU', 'LAA', 'LAE', 'LAM', 'LAN', 'LAV', 'LBT', 'LDX', 'LHX', 'LID', 'LIL', 'LLR', 'LMG', 'LMU', 'LNE', 'LNI', 'LNK', 'LOL', 'LOT', 'LPE', 'LRC', 'LVU', 'LXP', 'LYM', 'LYX', 'LZB', 'MAC', 'MAI', 'MAV', 'MAY', 'MBU', 'MCK', 'MDO', 'MEC', 'MGH', 'MLD', 'MLH', 'MLT', 'MMO', 'MMZ', 'MNE', 'MSA', 'MSC', 'MSK', 'MTO', 'MVE', 'MYJ', 'MYU', 'MYX', 'NAI', 'NCB', 'NDA', 'NGL', 'NGN', 'NIA', 'NIG', 'NMA', 'NOS', 'NOZ', 'NSO', 'NSZ', 'NVD', 'NYT', 'NYX', 'OMA', 'OMD', 'OMS', 'OZW', 'PAL', 'PBD', 'PCH', 'PCM', 'PCO', 'PGA', 'PGT', 'PIA', 'PIC', 'PKW', 'PLM', 'PTW', 'PUE', 'PVG', 'PVO', 'QAZ', 'QFA', 'QFX', 'QLK', 'QNT', 'RAM', 'RBG', 'RCH', 'RDF', 'REU', 'RFR', 'RGN', 'RHD', 'RNG', 'RPB', 'RSX', 'RSY', 'RUN', 'RWD', 'RXP', 'RYR', 'RYS', 'RZO', 'SAA', 'SAS', 'SCO', 'SDR', 'SEJ', 'SEK', 'SET', 'SEU', 'SFR', 'SHT', 'SIL', 'SIO', 'SJL', 'SJY', 'SKP', 'SKU', 'SKV', 'SLJ', 'SLM', 'SLT', 'SMF', 'SMK', 'SNA', 'SPD', 'SQP', 'STT', 'STW', 'SUS', 'SVA', 'SVI', 'SVR', 'SVS', 'SWG', 'SWR', 'SWT', 'SWU', 'SXS', 'SYA', 'SYG', 'SZN', 'TAA', 'TAH', 'TAI', 'TAM', 'TAO', 'TAP', 'TAR', 'TAY', 'TBJ', 'TBN', 'TDR', 'TEU', 'TFF', 'TFL', 'THB', 'THY', 'TJS', 'TJT', 'TKJ', 'TLR', 'TMT', 'TNO', 'TNZ', 'TOK', 'TOM', 'TPA', 'TPC', 'TRA', 'TRL', 'TSC', 'TUA', 'TUI', 'TUX', 'TVF', 'TVJ', 'TVL', 'TVP', 'TVQ', 'TVS', 'TWI', 'TYA', 'UAE', 'UAF', 'UAL', 'UAU', 'UBG', 'UBT', 'UEA', 'UGD', 'UNO', 'URO', 'USA', 'USY', 'UVL', 'UZB', 'UZP', 'VAA', 'VAG', 'VAJ', 'VAW', 'VBB', 'VCJ', 'VDA', 'VID', 'VIP', 'VIR', 'VIV', 'VJC', 'VJH', 'VJT', 'VKG', 'VLG', 'VOE', 'VOI', 'VOZ', 'VRG', 'VRH', 'VSV', 'WAA', 'WEW', 'WFL', 'WHT', 'WMT', 'WPT', 'WRC', 'WUK', 'WWP', 'WZZ', 'XKY', 'XLE', 'XRC', 'XRO']

    SCHEDULE_TYPES = ["departure"]

    START_DATE = "2021-01-01"
    END_DATE = "2021-12-31"

    SAVE_MODE = "both"
    CSV_NAME = "data_11_06_26(2021-01-01 - 2021-12-31).csv"

    asyncio.run(
        fetch_historical_schedules(
            airport_codes=AIRPORTS,
            airline_iata_codes=AIRLINES if AIRLINES else None,
            schedule_types=SCHEDULE_TYPES,
            start_date=START_DATE,
            end_date=END_DATE,
            storage_mode=SAVE_MODE,
            csv_path=(AVIATION_EDGE_PATH / CSV_NAME) if CSV_NAME else None,
            resume_from=0
        )
    )
