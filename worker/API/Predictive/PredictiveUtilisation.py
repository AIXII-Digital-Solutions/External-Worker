"""Predictive-utilisation pipeline (stage 1) — the heavy steps 3–6 of the feature, run as one
External-Worker job (ARQ `predictive_utilisation`, enqueued by core-api).

Flow for an airline (icao/iata) + reference date `d`:
  3. past window = [d - 2y, d]; resolve the airline's aircraft (active = Status not in
     'On order'/'On option') -> reg list.
  4. collect: replace this airline's rows in api.predictive_utilisation with every
     flightradar.flightsummary row of those regs in the window, joined with the aircraft fields.
  5. per-reg gaps: days in the window with no flightsummary for that reg, grouped into <=14-day runs.
  6. backfill: regs sharing an identical set of ranges are batched; for each batch+range we await
     fetch_all_ranges (writes into flightradar.flightsummary). Then re-run step 4 once.

`cron_predictive_cleanup` TRUNCATEs the whole table (reset ids) once it has been idle for 3h.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import text

from Config import setup_logger
from Database import DatabaseClient
from API.FlightRadarAPI.FlightSummary import fetch_all_ranges

logger = setup_logger("predictive_utilisation")

INACTIVE = ("On order", "On option")
MAX_RANGE_DAYS = 14

# wide INSERT ... SELECT: every flightsummary row of the airline's active aircraft in the window,
# joined with the aircraft (step-3.3) fields. Server-side — no rows pulled into Python.
COLLECT_SQL = text("""
INSERT INTO api.predictive_utilisation
  (airline_icao, fr24_id, flight, callsign, operating_as, painted_as, type, reg, orig_icao, orig_iata,
   datetime_takeoff, runway_takeoff, dest_icao, dest_iata, dest_icao_actual, dest_iata_actual,
   datetime_landed, runway_landed, flight_time, actual_distance, circle_distance, category, hex,
   first_seen, last_seen, flight_ended,
   msn, airline, status, delivery_date, in_service_date, first_flight_date, indicative_value, num_of_seats)
SELECT :icao,
  fs.fr24_id, fs.flight, fs.callsign, fs.operating_as, fs.painted_as, fs.type, fs.reg, fs.orig_icao, fs.orig_iata,
  fs.datetime_takeoff, fs.runway_takeoff, fs.dest_icao, fs.dest_iata, fs.dest_icao_actual, fs.dest_iata_actual,
  fs.datetime_landed, fs.runway_landed, fs.flight_time, fs.actual_distance, fs.circle_distance, fs.category, fs.hex,
  fs.first_seen, fs.last_seen, fs.flight_ended,
  ac."Serial Number", ac."Operator", ac."Status", ac."Delivery Date", ac."In Service Date",
  ac."First Flight Date", ac."Indicative Market Value (US$m)", ac."Number of Seats"
FROM flightradar.flightsummary fs
JOIN (
  SELECT DISTINCT ON ("Registration")
     "Registration", "Serial Number", "Operator", "Status", "Delivery Date",
     "In Service Date", "First Flight Date", "Indicative Market Value (US$m)", "Number of Seats"
  FROM cirium.ciriumaircrafts
  WHERE ("Operator ICAO" = :icao OR "Operator IATA" = :iata)
    AND ("Status" IS NULL OR "Status" NOT IN ('On order', 'On option'))
  ORDER BY "Registration", revision_id DESC
) ac ON ac."Registration" = fs.reg
WHERE fs.datetime_takeoff >= :start AND fs.datetime_takeoff < :end
""")

DELETE_SQL = text("DELETE FROM api.predictive_utilisation WHERE airline_icao = :icao")


def _minus_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year - n)
    except ValueError:               # 29 Feb -> 28 Feb in a non-leap target year
        return d.replace(year=d.year - n, day=28)


def _group_runs(missing_days, max_len: int = MAX_RANGE_DAYS):
    """sorted list[date] of missing days -> list of [start, end] runs of CONSECUTIVE days,
    each at most `max_len` days long (a run longer than that is split)."""
    if not missing_days:
        return []
    runs = []
    run_start = prev = missing_days[0]
    for d in missing_days[1:]:
        if d == prev + timedelta(days=1) and (d - run_start).days + 1 <= max_len:
            prev = d
        else:
            runs.append([run_start, prev])
            run_start = prev = d
    runs.append([run_start, prev])
    return runs


async def _collect(session, icao: str, iata: str, start_dt: datetime, end_dt: datetime) -> None:
    await session.execute(DELETE_SQL, {"icao": icao})
    await session.execute(COLLECT_SQL, {"icao": icao, "iata": iata, "start": start_dt, "end": end_dt})


async def predictive_utilisation_pipeline(icao: str, iata: str, date: str, **_) -> None:
    d = datetime.strptime(date, "%Y-%m-%d").date()
    past_start = _minus_years(d, 2)
    past_end = d                                   # inclusive day d
    start_dt = datetime.combine(past_start, datetime.min.time())
    end_dt = datetime.combine(past_end + timedelta(days=1), datetime.min.time())   # exclusive

    client = DatabaseClient()

    # step 2/3.3 — active aircraft regs (one row per reg, latest revision, excl. On order/On option)
    async with client.session("cirium") as session:
        rows = (await session.execute(text("""
            SELECT DISTINCT ON ("Registration") "Registration" AS reg, "Status" AS status
            FROM cirium.ciriumaircrafts
            WHERE ("Operator ICAO" = :icao OR "Operator IATA" = :iata)
            ORDER BY "Registration", revision_id DESC
        """), {"icao": icao, "iata": iata})).all()
    active_regs = [r.reg for r in rows if r.reg and (r.status is None or r.status not in INACTIVE)]

    if not active_regs:
        logger.info("[predictive] no active aircraft for icao=%s iata=%s — clearing rows", icao, iata)
        async with client.session("cirium") as session:
            await session.execute(DELETE_SQL, {"icao": icao})
        return

    # step 4 — collect (first pass)
    async with client.session("cirium") as session:
        await _collect(session, icao, iata, start_dt, end_dt)

    # step 5 — per-reg gaps in the window
    all_days, cur = [], past_start
    while cur <= past_end:
        all_days.append(cur)
        cur += timedelta(days=1)
    all_days_set = set(all_days)

    async with client.session("cirium") as session:
        covered_rows = (await session.execute(text("""
            SELECT reg, datetime_takeoff::date AS d
            FROM flightradar.flightsummary
            WHERE reg = ANY(:regs) AND datetime_takeoff >= :start AND datetime_takeoff < :end
            GROUP BY reg, datetime_takeoff::date
        """), {"regs": active_regs, "start": start_dt, "end": end_dt})).all()
    covered = defaultdict(set)
    for r in covered_rows:
        covered[r.reg].add(r.d)

    reg_ranges = {}
    for reg in active_regs:
        runs = _group_runs(sorted(all_days_set - covered.get(reg, set())))
        if runs:
            reg_ranges[reg] = runs

    # step 6 — batch regs that share an identical set of ranges, then backfill each (batch, range)
    batches = defaultdict(list)
    for reg, runs in reg_ranges.items():
        key = tuple((s.isoformat(), e.isoformat()) for s, e in runs)
        batches[key].append(reg)

    total = sum(len(ranges) for ranges in batches)
    logger.info("[predictive] icao=%s regs=%d gap-batches=%d fetch-calls=%d",
                icao, len(active_regs), len(batches), total)

    for key, regs in batches.items():
        for s_iso, e_iso in key:
            try:
                await fetch_all_ranges(start_date=s_iso, end_date=e_iso,
                                       icao=[icao], registrations=regs, storage_mode="db")
            except Exception as ex:
                logger.error("[predictive] backfill failed (%d regs, %s..%s): %s",
                             len(regs), s_iso, e_iso, ex)

    # step 4 again — re-collect (one pass; remaining holes stay empty)
    async with client.session("cirium") as session:
        await _collect(session, icao, iata, start_dt, end_dt)
    logger.info("[predictive] done icao=%s", icao)


async def predictive_cleanup(**_) -> None:
    """TRUNCATE api.predictive_utilisation (reset ids) once it's been idle > 3 hours."""
    client = DatabaseClient()
    async with client.session("cirium") as session:
        stale = (await session.execute(text(
            "SELECT (max(created_at) IS NULL OR max(created_at) < now() - interval '3 hours') "
            "FROM api.predictive_utilisation"
        ))).scalar()
        if stale:
            await session.execute(text("SELECT api.cleanup_predictive_utilisation()"))
            logger.info("[predictive] table idle > 3h -> truncated (ids reset)")
