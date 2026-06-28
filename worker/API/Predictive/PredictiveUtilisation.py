"""Predictive-utilisation pipeline (stage 1) — the heavy steps 3–6 of the feature, run as one
External-Worker job (ARQ `predictive_utilisation`, enqueued by core-api).

Flow for an airline (icao/iata) + reference date `d`:
  3. past window = [d - 2y, d]; resolve the airline's aircraft (active = Status not in
     'On order'/'On option') -> reg list.
  4. collect: replace this airline's rows in api.predictive_utilisation with every
     flightradar.flightsummary row of those regs in the window, joined with the aircraft fields.
     Done by the SECURITY DEFINER fn api.collect_predictive_utilisation() (DELETE + INSERT..SELECT),
     so the worker needs no INSERT/DELETE grant on api — only EXECUTE (PUBLIC) + USAGE on schema api.
  (deep_research=False stops after step 4 — existing data only, no FlightRadar backfill.)
  5. per-reg gaps: days in the window with no flightsummary for that reg, grouped into <=14-day runs.
  6. backfill: regs sharing an identical set of ranges are batched; for each batch+range we await
     fetch_all_ranges (writes into flightradar.flightsummary). Then re-run step 4 once.

`cron_predictive_cleanup` TRUNCATEs the whole table (reset ids) once it has been idle for 3h.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from Config import setup_logger
from Database import DatabaseClient
from API.FlightRadarAPI.FlightSummary import fetch_all_ranges

logger = setup_logger("predictive_utilisation")

INACTIVE = ("On order", "On option")
MAX_RANGE_DAYS = 14

# DELETE this airline's rows + wide INSERT..SELECT of its active aircraft's flightsummary in the
# window — all inside a SECURITY DEFINER function (api owns it), so the worker needs no write grant.
COLLECT_FN = text("SELECT api.collect_predictive_utilisation(:icao, :iata, :start, :end)")


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
    await session.execute(COLLECT_FN, {"icao": icao, "iata": iata, "start": start_dt, "end": end_dt})


async def predictive_utilisation_pipeline(icao: str, iata: str, date: str,
                                          deep_research: bool = False, **_) -> None:
    d = datetime.strptime(date, "%Y-%m-%d").date()
    past_start = _minus_years(d, 2)
    past_end = d                                   # inclusive day d
    start_dt = datetime.combine(past_start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(past_end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)  # exclusive

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

    # step 4 — collect (always; the fn clears this airline's rows and inserts active aircraft's
    # flights in the window. No active aircraft -> just cleared.)
    async with client.session("cirium") as session:
        await _collect(session, icao, iata, start_dt, end_dt)

    if not active_regs:
        logger.info("[predictive] no active aircraft for icao=%s iata=%s — cleared rows", icao, iata)
        return

    # deep_research=False -> stop here: just expose whatever flightsummary data we already have,
    # no FlightRadar backfill (steps 5–6).
    if not deep_research:
        logger.info("[predictive] shallow (deep_research=false): collected existing data only — "
                    "icao=%s regs=%d", icao, len(active_regs))
        return

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
