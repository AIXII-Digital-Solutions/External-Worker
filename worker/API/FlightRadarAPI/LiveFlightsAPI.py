import time
from typing import List, Set

import aiohttp
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from Config import DBSettings
from settings import (
    FLIGHT_RADAR_HEADERS, FLIGHT_RADAR_MAX_REG_PER_BATCH, FLIGHT_RADAR_URL,
    FLIGHT_RADAR_REDIS_POLLING_KEY,
    FLIGHT_RADAR_CHECK_INTERVAL_MISS, FLIGHT_RADAR_CHECK_INTERVAL_FOUND,
    FLIGHT_RADAR_SCHEDULE_NAME, FLIGHT_RADAR_FOUND_INTERVAL_MULTIPLIER,
)
from Database import DatabaseClient
from Database.Models import LivePositions
from Utils import ensure_naive_utc, parse_dt, performance_timer

try:
    from .FlightSummary import logger
    from .distance import (get_previous_positions_bulk, get_airport_coords_bulk,
                           compute_cumulative_distance, compute_time_delta)
except ImportError:  # pragma: no cover - import shim for running as a script
    from API.FlightRadarAPI.FlightSummary import logger
    from API.FlightRadarAPI.distance import (get_previous_positions_bulk, get_airport_coords_bulk,
                                            compute_cumulative_distance, compute_time_delta)

# FR24 requests must not hang the whole cycle: bound connect + total time.
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)


class FlightPollingStorage:
    """Round-robin polling state in a Redis sorted set (FLIGHT_RADAR_REDIS_POLLING_KEY): member = reg,
    score = next-check epoch. A cycle pulls every reg whose score is due (<= now), polls FR24, then
    pushes each checked reg's score forward (found -> longer interval, missed -> shorter) so every reg
    is covered while active flights are re-sampled less often (token saving)."""

    def __init__(self, username: str, password: str, host: str, port: int):
        self.redis = Redis(
            username=username, password=password, host=host, port=port, decode_responses=True
        )

    async def close(self) -> None:
        try:
            await self.redis.aclose()
        except Exception:
            pass

    async def reconcile(self, regs: list[str]) -> None:
        """Sync the zset membership to the CURRENT fleet every cycle: add newly-registered regs
        (due now) and drop de-registered ones. Replaces the old one-shot bootstrap, so roster changes
        from the weekly cirium.asg -> api.registration sync take effect without a worker restart and
        without wasting polls on retired tails."""
        if not regs:
            return
        now = time.time()
        current = set(await self.redis.zrange(FLIGHT_RADAR_REDIS_POLLING_KEY, 0, -1))
        target = set(regs)
        to_add = target - current
        to_remove = current - target
        if not (to_add or to_remove):
            return
        pipe = self.redis.pipeline()
        if to_add:
            pipe.zadd(FLIGHT_RADAR_REDIS_POLLING_KEY, {r: now for r in to_add})
        if to_remove:
            pipe.zrem(FLIGHT_RADAR_REDIS_POLLING_KEY, *to_remove)
        await pipe.execute()

    async def get_regs_for_cycle(self, limit: int = 1000) -> list[str]:
        """Regs due for a check (score <= now), soonest-due first."""
        now = time.time()
        return await self.redis.zrangebyscore(
            FLIGHT_RADAR_REDIS_POLLING_KEY, min=0, max=now, start=0, num=limit
        )

    async def reschedule(self, found_regs: Set[str], checked_regs,
                         found_interval: int, miss_interval: int) -> None:
        """Push each SUCCESSFULLY-checked reg's next-check time forward in ONE pipelined zadd:
        found -> +found_interval, missed -> +miss_interval (both derived from the scheduler cadence,
        see _resolve_intervals). Regs from a failed batch are not passed here, so they stay past-due
        and get retried next cycle."""
        now = time.time()
        updates = {
            reg: now + (found_interval if reg in found_regs else miss_interval)
            for reg in checked_regs
        }
        if updates:
            await self.redis.zadd(FLIGHT_RADAR_REDIS_POLLING_KEY, updates)


async def _resolve_intervals(db_client: DatabaseClient) -> tuple[int, int]:
    """(found_interval, miss_interval) in seconds, DERIVED from this job's scheduler cadence so the
    rotation follows whatever interval the operator sets via the /scheduler API — no code change,
    no redeploy.

        miss  = schedule_registry.interval_seconds        (re-poll a missed/ground reg next tick)
        found = miss * FLIGHT_RADAR_FOUND_INTERVAL_MULTIPLIER   (re-poll an active reg every Nth tick)

    e.g. operator sets the schedule to 5 min -> miss = 300 s, found = 600 s (multiplier 2).

    Falls back to the FLIGHT_RADAR_CHECK_INTERVAL_* settings when the cron_live_flights schedule has
    no interval_seconds (it's cron-driven) or the row isn't seeded yet.
    """
    try:
        async with db_client.session("service") as session:
            row = (await session.execute(
                text("SELECT interval_seconds FROM schedule_registry WHERE name = :n"),
                {"n": FLIGHT_RADAR_SCHEDULE_NAME},
            )).first()
        if row and row[0]:
            miss = int(row[0])
            return miss * int(FLIGHT_RADAR_FOUND_INTERVAL_MULTIPLIER), miss
    except Exception as e:
        logger.warning(f"[Live Flights] scheduler interval unavailable, using fallback intervals: {e}")
    return FLIGHT_RADAR_CHECK_INTERVAL_FOUND, FLIGHT_RADAR_CHECK_INTERVAL_MISS


async def _write_positions(db_client: DatabaseClient, flights: List[dict]) -> int:
    """Persist a cycle's live positions with cumulative actual_distance + telemetry time_delta.

    Metrics are computed from BULK lookups (one query for every flight's previous position, one for
    all first-point origin airports) instead of 2-3 remote round-trips per flight. Each record is
    built defensively: a single malformed record is skipped, never aborting the whole batch insert.
    Rows without fr24_id or timestamp are dropped (timestamp is the partition key + part of the PK).
    """
    fr24_ids = [f.get("fr24_id") for f in flights if f.get("fr24_id")]
    # 1) previous position per flight (flightradar session, closed before the next session opens —
    #    'main' and 'flightradar' share the physical aixii engine, so no overlapping/nested sessions)
    async with db_client.session("flightradar") as session:
        prev_map = await get_previous_positions_bulk(session, fr24_ids)

    # 2) origin airport coords are only needed to seed the total for a flight's FIRST stored point
    missing_orig = [f.get("orig_iata") for f in flights
                    if f.get("fr24_id") not in prev_map and f.get("orig_iata")]
    airport_coords = await get_airport_coords_bulk(db_client, missing_orig)

    # 3) build rows in memory (no I/O), skipping any single malformed record
    rows: List[dict] = []
    for f in flights:
        try:
            fr = f.get("fr24_id")
            ts = ensure_naive_utc(parse_dt(f.get("timestamp")))
            if not fr or ts is None:
                logger.debug("[Live Flights] skipping record without fr24_id/timestamp: %s", fr)
                continue
            prev = prev_map.get(fr)
            rows.append({
                "fr24_id": fr,
                "flight": f.get("flight"),
                "callsign": f.get("callsign"),
                "lat": f.get("lat"),
                "lon": f.get("lon"),
                "track": f.get("track"),
                "alt": f.get("alt"),
                "gspeed": f.get("gspeed"),
                "vspeed": f.get("vspeed"),
                "squawk": f.get("squawk"),
                "timestamp": ts,
                "source": f.get("source"),
                "hex": f.get("hex"),
                "type": f.get("type"),
                "reg": f.get("reg"),
                "painted_as": f.get("painted_as"),
                "operating_as": f.get("operating_as"),
                "orig_iata": f.get("orig_iata"),
                "orig_icao": f.get("orig_icao"),
                "dest_iata": f.get("dest_iata"),
                "dest_icao": f.get("dest_icao"),
                "eta": ensure_naive_utc(parse_dt(f.get("eta"))),
                "actual_distance": compute_cumulative_distance(prev, f, airport_coords),
                "time_delta": compute_time_delta(prev, ts),
            })
        except Exception as e:
            logger.warning(f"[Live Flights] skipping bad record {f.get('fr24_id')}: {e}")

    # 4) single bulk insert; one position per (fr24_id, timestamp) — re-polled snapshots skipped
    if rows:
        async with db_client.session("flightradar") as session:
            await session.execute(
                pg_insert(LivePositions)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_livepositions_fr24_timestamp")
            )
            await session.commit()
    return len(rows)


@performance_timer
async def live_flights_adaptive(storage_mode: str = "db"):
    logger.info("[Live Flights] Adaptive polling started")

    username, password, host, port = DBSettings().get_reddis_credentials()
    redis_storage = FlightPollingStorage(username, password, host, port)
    db_client = DatabaseClient()

    try:
        # regs come from api.registration (active aircraft synced from cirium.asg), not main.
        async with db_client.session("cirium") as session:
            result = await session.execute(text("SELECT reg FROM api.registration"))
            all_regs = [row[0] for row in result.all()]

        await redis_storage.reconcile(all_regs)

        regs_to_check = await redis_storage.get_regs_for_cycle()
        if not regs_to_check:
            logger.info("[Live Flights] Nothing due — skipping API call")
            return

        logger.info(f"[Live Flights] Checking {len(regs_to_check)} aircraft")

        batches = [
            regs_to_check[i:i + FLIGHT_RADAR_MAX_REG_PER_BATCH]
            for i in range(0, len(regs_to_check), FLIGHT_RADAR_MAX_REG_PER_BATCH)
        ]

        found_regs: Set[str] = set()
        checked_ok: Set[str] = set()     # regs that were part of a SUCCESSFUL batch response
        all_flights: List[dict] = []

        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as http:
            for batch in batches:
                try:
                    async with http.get(
                        f"{FLIGHT_RADAR_URL}/live/flight-positions/full",
                        headers=FLIGHT_RADAR_HEADERS,
                        params={"registrations": ",".join(batch), "limit": 20000},
                    ) as resp:
                        if resp.status != 200:
                            # leave this batch's regs due (not in checked_ok) -> retried next cycle,
                            # instead of mis-marking them "missed" and deferring them
                            logger.error(f"[Live Flights] {resp.status}: {await resp.text()}")
                            continue
                        payload = await resp.json()
                except Exception as e:
                    logger.error(f"[Live Flights] batch request failed ({len(batch)} regs): {e}")
                    continue

                checked_ok.update(batch)
                flights_data = payload.get("data", []) or []
                if not flights_data:
                    continue
                found_regs.update(f.get("reg") for f in flights_data if f.get("reg"))
                all_flights.extend(flights_data)

        if storage_mode in ("db", "both") and all_flights:
            written = await _write_positions(db_client, all_flights)
            logger.info(f"[Live Flights] Wrote {written} position row(s)")

        # intervals follow the scheduler cadence (found = N x miss); advance the rotation only for
        # regs we actually managed to query this cycle
        found_interval, miss_interval = await _resolve_intervals(db_client)
        await redis_storage.reschedule(
            found_regs=found_regs, checked_regs=checked_ok,
            found_interval=found_interval, miss_interval=miss_interval,
        )

        logger.info(
            f"[Live Flights] Completed. Checked: {len(checked_ok)}, active: {len(found_regs)}, "
            f"inactive: {len(checked_ok) - len(found_regs)}, "
            f"deferred(failed): {len(regs_to_check) - len(checked_ok)}, "
            f"intervals found={found_interval}s miss={miss_interval}s"
        )
    finally:
        await redis_storage.close()
