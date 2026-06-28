import json
import time
from datetime import datetime
from typing import List, Optional, Set

import aiohttp
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from Config import DBSettings
from settings import FLIGHT_RADAR_HEADERS, \
    FLIGHT_RADAR_MAX_REG_PER_BATCH, FLIGHT_RADAR_URL, FLIGHT_RADAR_REDIS_POLLING_KEY, FLIGHT_RADAR_REDIS_META_KEY, \
    FLIGHT_RADAR_CHECK_INTERVAL_MISS, FLIGHT_RADAR_CHECK_INTERVAL_FOUND, \
    FLIGHT_RADAR_FORCE_RECHECK_MISS, FLIGHT_RADAR_BOOTSTRAP_KEY
from Database import DatabaseClient
from Database.Models import LivePositions
from Utils import ensure_naive_utc, parse_dt, performance_timer

try:
    from .FlightSummary import logger
    from .distance import calculate_distance_metric, get_time_delta
except:
    from API.FlightRadarAPI.FlightSummary import logger
    from API.FlightRadarAPI.FlightSummary import calculate_distance_metric

_last_flights: Optional[List[dict]] = None
_last_run_date: datetime | None = None


class FlightPollingStorage:
    def __init__(self, username: str, password: str, host: str, port: int):
        self.redis = Redis(
            username=username,
            password=password,
            host=host,
            port=port,
            decode_responses=True
        )

    async def bootstrap(self, regs: list[str]):
        if not regs:
            return

        exists = await self.redis.exists(FLIGHT_RADAR_REDIS_POLLING_KEY)
        if exists:
            return

        now = time.time()
        await self.redis.zadd(
            FLIGHT_RADAR_REDIS_POLLING_KEY,
            {reg: now for reg in regs}
        )

        await self.redis.set(
            FLIGHT_RADAR_BOOTSTRAP_KEY,
            "1",
            ex=24 * 60 * 60
        )

    async def get_regs_for_cycle(self, limit: int = 1000) -> list[str]:
        now = time.time()

        scheduled = set(await self.redis.zrangebyscore(
            FLIGHT_RADAR_REDIS_POLLING_KEY,
            min=0,
            max=now,
            start=0,
            num=limit
        ))

        meta_raw = await self.redis.hgetall(FLIGHT_RADAR_REDIS_META_KEY)
        forced: set[str] = set()

        for reg, raw in meta_raw.items():
            try:
                meta = json.loads(raw)
            except Exception:
                continue

            if (
                meta.get("state") == "ground"
                and meta.get("updated_at_ts", 0) <= now - FLIGHT_RADAR_FORCE_RECHECK_MISS
            ):
                forced.add(reg)

        return list(scheduled | forced)

    async def update_reg(self, reg: str, found: bool):
        now = time.time()

        next_check = now + (
            FLIGHT_RADAR_CHECK_INTERVAL_FOUND
            if found else
            FLIGHT_RADAR_CHECK_INTERVAL_MISS
        )

        await self.redis.zadd(
            FLIGHT_RADAR_REDIS_POLLING_KEY,
            {reg: next_check}
        )

        meta = {
            "state": "airborne" if found else "ground",
            "last_seen_ts": now if found else None,
            "updated_at_ts": now
        }

        await self.redis.hset(
            FLIGHT_RADAR_REDIS_META_KEY,
            reg,
            json.dumps(meta)
        )



@performance_timer
async def live_flights_adaptive(storage_mode: str = "db"):
    logger.info("[Live Flights] Adaptive polling started")

    username, password, host, port = DBSettings().get_reddis_credentials()
    redis_storage = FlightPollingStorage(username, password, host, port)
    db_client = DatabaseClient()

    # regs now come from api.registration (active aircraft synced from cirium.asg), not the
    # defunct main.registrations. Every row is already an active tracked aircraft, so no filter.
    async with db_client.session("cirium") as session:
        result = await session.execute(text("SELECT reg FROM api.registration"))
        all_regs = [row[0] for row in result.all()]
    await redis_storage.bootstrap(all_regs)

    regs_to_check = await redis_storage.get_regs_for_cycle()

    if not regs_to_check:
        logger.info("[Live Flights] Nothing to check — skipping API call")
        return

    logger.info(f"[Live Flights] Checking {len(regs_to_check)} aircrafts")

    batches = [
        regs_to_check[i:i + FLIGHT_RADAR_MAX_REG_PER_BATCH]
        for i in range(0, len(regs_to_check), FLIGHT_RADAR_MAX_REG_PER_BATCH)
    ]

    found_regs: Set[str] = set()

    async with aiohttp.ClientSession() as http:
        for batch in batches:
            async with http.get(
                    f"{FLIGHT_RADAR_URL}/live/flight-positions/full",
                    headers=FLIGHT_RADAR_HEADERS,
                    params={
                        "registrations": ",".join(batch),
                        "limit": 20000
                    }
            ) as resp:

                if resp.status != 200:
                    logger.error(f"{resp.status}: {await resp.text()}")
                    continue

                payload = await resp.json()
                flights_data = payload.get("data", [])

                if not flights_data:
                    continue

                found_regs.update(
                    f.get("reg") for f in flights_data if f.get("reg")
                )

                if storage_mode in ("db", "both"):
                    rows = [
                        {
                            "fr24_id": f.get("fr24_id"),
                            "flight": f.get("flight"),
                            "callsign": f.get("callsign"),
                            "lat": f.get("lat"),
                            "lon": f.get("lon"),
                            "track": f.get("track"),
                            "alt": f.get("alt"),
                            "gspeed": f.get("gspeed"),
                            "vspeed": f.get("vspeed"),
                            "squawk": f.get("squawk"),
                            "timestamp": ensure_naive_utc(parse_dt(f.get("timestamp"))),
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
                            "actual_distance": await calculate_distance_metric(reg=f.get("reg"), new_flight=f),
                            "time_delta": await get_time_delta(reg=f.get("reg"), flight_number=f.get("flight")),
                        }
                        for f in flights_data
                    ]

                    async with db_client.session("flightradar") as session:
                        if rows:
                            # one position per (fr24_id, timestamp); re-polled snapshots are skipped
                            await session.execute(
                                pg_insert(LivePositions)
                                .values(rows)
                                .on_conflict_do_nothing(constraint="uq_livepositions_fr24_timestamp")
                            )
                        await session.commit()

    for reg in regs_to_check:
        await redis_storage.update_reg(
            reg=reg,
            found=reg in found_regs
        )

    logger.info(
        f"[Live Flights] Completed. Active: {len(found_regs)}, "
        f"inactive: {len(regs_to_check) - len(found_regs)}"
    )



