"""Distance / time metrics for FlightRadar live positions.

Two metrics are stored on each flightradar.livepositions row:

  * actual_distance — CUMULATIVE distance flown along the route up to this point (km). Each new
    sample adds the great-circle hop from the previous stored sample of the SAME flight (fr24_id)
    to that flight's running total. The first stored sample of a flight seeds the total from the
    departure airport (orig_iata) when known, else 0.
  * time_delta — telemetry time elapsed since the previous sample of the same flight (0 at the
    flight's first sample). It uses the FR24 "timestamp" (telemetry time) — the SAME basis as the
    lat/lon used for distance — so `actual_distance` increments and `time_delta` are coherent.

All lookups are BULK: one query fetches the previous position for every flight in a cycle, and one
query resolves all first-point origin airports — instead of 2-3 remote round-trips per flight.
"""
import math
from datetime import timedelta
from typing import Optional, Mapping

from sqlalchemy import text

from Utils import ensure_naive_utc

try:
    from .FlightSummary import logger
except ImportError:  # pragma: no cover - import shim for running as a script
    from API.FlightRadarAPI.FlightSummary import logger


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Bulk lookups — one round-trip each, for a whole polling cycle.
# ---------------------------------------------------------------------------
async def get_previous_positions_bulk(session, fr24_ids: list[str]) -> dict[str, dict]:
    """Latest stored position per flight (fr24_id): {fr24_id: {lat, lon, actual_distance, ts}}.

    ONE query (DISTINCT ON fr24_id, backed by uq_livepositions_fr24_timestamp) instead of a
    per-flight SELECT. ts is normalised to naive-UTC so it subtracts cleanly against the parsed
    telemetry timestamp. Keyed on fr24_id (the unique flight instance), not the flight number,
    so two same-numbered flights on different days never share a running total.
    """
    ids = [i for i in fr24_ids if i]
    if not ids:
        return {}
    rows = (await session.execute(
        text("""
            SELECT DISTINCT ON (fr24_id) fr24_id, lat, lon, actual_distance, timestamp
            FROM flightradar.livepositions
            WHERE fr24_id = ANY(:ids)
            ORDER BY fr24_id, timestamp DESC
        """),
        {"ids": ids},
    )).mappings().all()
    return {
        r["fr24_id"]: {
            "lat": r["lat"],
            "lon": r["lon"],
            "actual_distance": r["actual_distance"],
            "ts": ensure_naive_utc(r["timestamp"]),
        }
        for r in rows
    }


async def get_airport_coords_bulk(client, iatas: list[str]) -> dict[str, tuple]:
    """{iata: (lat, lon)} for the given IATA codes — ONE query against main.virtual_airport_list
    (legacy 18k-row reference TEMPORARILY in aixii.main, migration a2b3c4d5e6f7, until core/main is
    rebuilt). Only called for first-point flights, so usually a tiny/empty set."""
    codes = [c for c in {*iatas} if c]
    if not codes:
        return {}
    async with client.session("main") as session:
        rows = (await session.execute(
            text("""
                SELECT "IATA Code" AS iata, "Latitude" AS lat, "Longitude" AS lon
                FROM main.virtual_airport_list
                WHERE "IATA Code" = ANY(:codes)
            """),
            {"codes": codes},
        )).mappings().all()
    return {r["iata"]: (r["lat"], r["lon"]) for r in rows}


# ---------------------------------------------------------------------------
# Pure metric computation (no I/O) — fed by the bulk maps above.
# ---------------------------------------------------------------------------
def compute_cumulative_distance(
    prev: Optional[Mapping],
    cur: Mapping,
    airport_coords: Mapping[str, tuple],
) -> float:
    """CUMULATIVE distance flown along the route up to `cur` (km).

    prev = previous stored row of the same flight ({lat, lon, actual_distance}) or None.
    cur  = incoming API record (lat/lon/orig_iata/gspeed).
    """
    lat, lon = cur.get("lat"), cur.get("lon")
    prev_total = float(prev["actual_distance"] or 0.0) if prev else 0.0

    if lat is None or lon is None:
        # Can't place the aircraft this sample: carry the running total forward unchanged.
        return prev_total

    if prev is not None and prev.get("lat") is not None and prev.get("lon") is not None:
        return prev_total + haversine_distance_km(prev["lat"], prev["lon"], lat, lon)

    # First stored point of this flight -> seed the total from the departure airport when known.
    orig_iata = cur.get("orig_iata")
    if orig_iata and orig_iata in airport_coords:
        a_lat, a_lon = airport_coords[orig_iata]
        return haversine_distance_km(a_lat, a_lon, lat, lon)

    # No previous point and unknown origin -> rough one-step estimate from ground speed, else 0.
    gspeed = cur.get("gspeed")
    if gspeed is not None and gspeed >= 120:
        return gspeed * 1.825 / 5
    return 0.0


def compute_time_delta(prev: Optional[Mapping], cur_ts_naive) -> timedelta:
    """Telemetry time since the previous sample of the same flight (0 at the flight's first sample).

    Both sides are naive-UTC telemetry timestamps, so the result is the true inter-sample interval
    on the same basis as the distance hop. Negative/degenerate deltas (out-of-order samples) clamp
    to 0 rather than storing a nonsensical negative interval.
    """
    if prev is None or prev.get("ts") is None or cur_ts_naive is None:
        return timedelta(0)
    delta = cur_ts_naive - prev["ts"]
    return delta if delta.total_seconds() >= 0 else timedelta(0)
