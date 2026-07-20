import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
from uuid import UUID

import aiohttp
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from Config import setup_logger
from settings import FLIGHT_RADAR_HEADERS, FLIGHT_RADAR_SECONDS_BETWEEN_REQUESTS, \
    FLIGHT_RADAR_MAX_REG_PER_BATCH, FLIGHT_RADAR_RANGE_DAYS, FLIGHT_RADAR_URL, FLIGHT_RADAR_PATH, \
    FLIGHT_RADAR_MAX_CONCURRENCY
from Database import DatabaseClient
from Database.Models import FlightSummary
from Utils import parse_dt, ensure_naive_utc, write_csv, parse_date_or_datetime, performance_timer

logger = setup_logger("flightradar")


class _RateLimiter:
    """Spaces the START of every FR24 request by at least `min_interval` seconds — GLOBALLY across all
    concurrent fetchers, not per-task. This is what lets requests run concurrently (to hide FR24's network
    latency) while never exceeding the API's request RATE: with N tasks in flight, the limiter still hands
    out one start-slot per interval, so the effective rate is the same ceiling as the old serial pre-sleep
    (default 90/min) — it is simply actually reached instead of being (rate ⊕ latency)-serialised.

    A monotonic-time cursor, not a real token bucket: no bursting, exact even pacing. `min_interval <= 0`
    disables pacing entirely."""

    def __init__(self, min_interval: float):
        self._min = max(0.0, float(min_interval))
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._min <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = (now if wait < 0 else self._next) + self._min
        if wait > 0:
            await asyncio.sleep(wait)

    async def penalize(self, seconds: float) -> None:
        """After a rate-limit hit, push the next-allowed slot out by `seconds` so EVERY concurrent waiter
        backs off together — not just the unit that got the 429."""
        if seconds <= 0:
            return
        async with self._lock:
            self._next = max(self._next, time.monotonic() + seconds)


# ONE limiter for the whole process. FR24's rate limit is per-API-key, so every fetch — across all
# concurrent fetch_all_ranges calls, all operators, and the live/on-demand paths — MUST share the same
# pacing budget. A per-call limiter reset its cursor each call, so back-to-back small groups fired their
# first request with no delay and burst past the limit -> 429 storms.
_RATE_LIMITER = _RateLimiter(FLIGHT_RADAR_SECONDS_BETWEEN_REQUESTS)

# Retry the SAME range on a rate-limit / transient server error instead of dropping it.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_FETCH_RETRIES = 6
_RETRY_BASE_S = 1.0
_RETRY_CAP_S = 30.0


def _retry_after_seconds(resp) -> "float | None":
    """FR24's Retry-After, in seconds, if it sent one (numeric form only; an HTTP-date falls back to backoff)."""
    ra = resp.headers.get("Retry-After")
    if not ra:
        return None
    try:
        return max(0.0, float(ra))
    except (TypeError, ValueError):
        return None


async def fetch_date_range(
        icao: Optional[List[str]],
        regs: Optional[List[str]],
        callsigns: Optional[List[str]],
        range_from: datetime,
        range_to: datetime,
        http: aiohttp.ClientSession,
        storage_mode: str = "db",
        csv_path: Optional[str] = None,
        on_request=None,
        limiter: Optional["_RateLimiter"] = None,
        client: Optional[DatabaseClient] = None,
) -> List[dict] | None:
    # Reuse the caller's DatabaseClient (one pooled engine shared by all concurrent units); only build a
    # throwaway one if called standalone. Creating a fresh DatabaseClient per unit would spin up a NEW engine
    # + pool per request and leak it (never disposed) — harmless-ish when serial, but under concurrency it
    # churns many pools and can exhaust Postgres connections.
    if client is None:
        client = DatabaseClient()

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

            # Fetch ONE page, RETRYING the same range on a rate-limit / transient error (429/5xx) with a
            # backoff — a 429 must never drop the range. Pacing is GLOBAL (`_RATE_LIMITER`): FR24's limit is
            # per-API-key, so all concurrent fetchers share one budget. On a 429 we also `penalize` the limiter
            # so the whole fetch cools down, not just this unit.
            lim = limiter or _RATE_LIMITER
            flights = None
            for _attempt in range(_MAX_FETCH_RETRIES + 1):
                await lim.acquire()
                async with http.get(f"{FLIGHT_RADAR_URL}/flight-summary/full", headers=FLIGHT_RADAR_HEADERS,
                                    params=params) as resp:
                    if resp.status == 200:
                        flights = await resp.json()
                        break
                    body_text = await resp.text()
                    if resp.status in _RETRYABLE_STATUS and _attempt < _MAX_FETCH_RETRIES:
                        delay = _retry_after_seconds(resp) or min(_RETRY_BASE_S * 2 ** _attempt, _RETRY_CAP_S)
                        await lim.penalize(delay)
                        logger.warning("[Flight Summary] FR24 %s — retrying same range in %.1fs (attempt %d/%d)",
                                       resp.status, delay, _attempt + 1, _MAX_FETCH_RETRIES)
                        await asyncio.sleep(delay)
                        continue
                    logger.error(f"{resp.status}: {body_text}")
                    break
            if flights is None:
                break  # page failed after retries (or non-retryable) — stop paginating this range

            if on_request is not None:
                await on_request()   # one API request done — drives live ETA / budget
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
        csv_path: Optional[Path] = FLIGHT_RADAR_PATH / f"flights_{datetime.strftime(datetime.now(), '%Y%m%d_%H%M')}.csv",
        on_request=None,
):
    client: DatabaseClient = DatabaseClient()
    if registrations is None and icao is None and callsigns is None:
        # regs come from api.registration (active aircraft synced from cirium.asg), not main.
        async with client.session("cirium") as session:
            result = await session.execute(text("SELECT reg FROM api.registration"))
            registrations = [row[0] for row in result.all()]

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

    # Each (batch × date-range) is one independent unit: its pagination is serial internally, but different
    # units share nothing (own DB session per fetch_date_range, one concurrency-safe aiohttp session, inserts
    # idempotent via uq_flightsummary_natural). So we run up to FLIGHT_RADAR_MAX_CONCURRENCY units at once to
    # hide FR24 latency, while the PROCESS-GLOBAL `_RATE_LIMITER` keeps the request rate at the ceiling ACROSS
    # every call — not just within this one. Net effect: the paced rate is reached, never exceeded.
    units = [(get_batch(registration_batches, b), get_batch(icao_batches, b),
              get_batch(callsigns_batches, b), rs, re)
             for b in range(max_len) for (rs, re) in date_ranges]

    sem = asyncio.Semaphore(max(1, FLIGHT_RADAR_MAX_CONCURRENCY))
    connector = aiohttp.TCPConnector(limit=max(1, FLIGHT_RADAR_MAX_CONCURRENCY), ttl_dns_cache=300)

    async with aiohttp.ClientSession(connector=connector) as http:
        async def _run_unit(reg_batch, icao_batch, callsign_batch, range_start, range_end):
            async with sem:
                return await fetch_date_range(
                    icao=icao_batch, regs=reg_batch, range_from=range_start, range_to=range_end,
                    http=http, storage_mode=storage_mode, csv_path=csv_path,
                    callsigns=callsign_batch, on_request=on_request, limiter=_RATE_LIMITER, client=client,
                )

        # The control-flow signals raised from on_request; imported lazily to avoid a circular import
        # (coverage imports this module at load time). Preferred over a generic error so a run that trips the
        # budget AND hits an unrelated error in the same step still takes the budget/retry branch.
        from API.FlightRadarAPI.coverage import _BudgetReached, JobCancelled

        tasks = [asyncio.create_task(_run_unit(*u)) for u in units]
        if not tasks:
            return flights
        # Stop the whole fetch the moment a unit raises (budget reached / job cancelled / unexpected error) —
        # matching the old serial loop, which aborted the call on the first exception. FIRST_EXCEPTION also
        # returns when ALL finish cleanly. We RE-RAISE the ORIGINAL single exception (never an ExceptionGroup),
        # so fetch_planned_ranges' `except _BudgetReached / JobCancelled / Exception` keep working. The
        # try/finally guarantees NO child task is left orphaned — not only when a unit raises, but also if
        # this coroutine is cancelled externally (worker shutdown / ARQ abort), which asyncio.wait does NOT
        # clean up on its own.
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            # Retrieve EVERY finished exception (so none is "never retrieved"), then prefer a control-flow
            # signal over a generic error; the choice is deterministic instead of set-iteration order.
            exceptions = [t.exception() for t in done if not t.cancelled() and t.exception() is not None]
            first_exc = next((e for e in exceptions if isinstance(e, (_BudgetReached, JobCancelled))),
                             exceptions[0] if exceptions else None)
            if first_exc is not None:
                raise first_exc
            flights.extend(t.result() for t in tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

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
