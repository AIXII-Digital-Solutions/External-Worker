"""FR24 flight-summary coverage ledger — fetch ONLY the missing date ranges per tail.

flightradar.flightsummary_coverage records, per registration, which [covered_from, covered_to] date
ranges have already been fetched from FR24 (so whatever flights exist for them are in flightsummary).
The forecast panel fetches only the request window MINUS this ledger (the missing ranges), then records
each fetched range — even if it returned no flights — so it is never re-fetched. This bounds FR24 token
spend to genuinely-new date ranges.

Bootstrap (a reg with no ledger row yet): seed the ledger from the reg's existing flightsummary days,
coalescing no-fly gaps shorter than FLIGHT_RADAR_COVERAGE_GAP_DAYS into a covered span. Longer internal
gaps stay UNCOVERED and get fetched once (then recorded, so a genuinely-empty stretch is not re-fetched).
"""
import time
from datetime import date, timedelta
from math import ceil

from sqlalchemy import text

from Config import setup_logger
from settings import (FLIGHT_RADAR_COVERAGE_GAP_DAYS, FLIGHT_RADAR_RANGE_DAYS,
                      FR24_SECONDS_PER_REQUEST_EST)
from API.FlightRadarAPI.FlightSummary import fetch_all_ranges

logger = setup_logger("fr24_coverage")

_TBL = "flightradar.flightsummary_coverage"


def _estimate_requests(gf: date, gt: date) -> int:
    """Estimate the FR24 requests a gap costs: one per FLIGHT_RADAR_RANGE_DAYS-day chunk (>= 1)."""
    return max(1, ceil(((gt - gf).days + 1) / FLIGHT_RADAR_RANGE_DAYS))


def _coalesce(ranges, max_gap_days):
    """Merge sorted (from, to) date ranges whose inter-range gap is <= max_gap_days days."""
    merged = []
    for f, t in sorted(ranges):
        if merged and (f - merged[-1][1]).days <= max_gap_days:
            merged[-1] = (merged[-1][0], max(merged[-1][1], t))
        else:
            merged.append((f, t))
    return merged


def _complement(covered, w_start, w_end):
    """Sub-ranges of [w_start, w_end] NOT covered by the `covered` ranges (the ranges to fetch)."""
    gaps, cur = [], w_start
    for f, t in sorted(covered):
        if t < w_start or f > w_end:
            continue
        f, t = max(f, w_start), min(t, w_end)
        if f > cur:
            gaps.append((cur, f - timedelta(days=1)))
        cur = max(cur, t + timedelta(days=1))
    if cur <= w_end:
        gaps.append((cur, w_end))
    return gaps


async def _covered(session, reg):
    rows = (await session.execute(
        text(f"SELECT covered_from, covered_to FROM {_TBL} WHERE reg = :r ORDER BY covered_from"),
        {"r": reg})).all()
    return [(r[0], r[1]) for r in rows]


async def _seed_if_absent(session, reg, gap_days):
    """Seed the ledger from existing flightsummary days if the reg has no ledger rows yet."""
    if (await session.execute(text(f"SELECT 1 FROM {_TBL} WHERE reg = :r LIMIT 1"), {"r": reg})).first():
        return
    days = (await session.execute(
        text("SELECT DISTINCT first_seen::date FROM flightradar.flightsummary WHERE reg = :r ORDER BY 1"),
        {"r": reg})).scalars().all()
    if not days:
        return  # no data -> no coverage; the whole window is a gap
    for f, t in _coalesce([(d, d) for d in days], max_gap_days=gap_days):
        await session.execute(
            text(f"INSERT INTO {_TBL}(reg, covered_from, covered_to) VALUES (:r, :f, :t) "
                 "ON CONFLICT (reg, covered_from) DO NOTHING"), {"r": reg, "f": f, "t": t})


async def _record(session, reg, f, t):
    """Add [f, t] to the ledger and re-coalesce the reg's ranges (touching/overlapping merge)."""
    await session.execute(
        text(f"INSERT INTO {_TBL}(reg, covered_from, covered_to) VALUES (:r, :f, :t) "
             f"ON CONFLICT (reg, covered_from) DO UPDATE SET covered_to = GREATEST({_TBL}.covered_to, EXCLUDED.covered_to)"),
        {"r": reg, "f": f, "t": t})
    merged = _coalesce(await _covered(session, reg), max_gap_days=1)
    await session.execute(text(f"DELETE FROM {_TBL} WHERE reg = :r"), {"r": reg})
    for mf, mt in merged:
        await session.execute(
            text(f"INSERT INTO {_TBL}(reg, covered_from, covered_to) VALUES (:r, :f, :t)"),
            {"r": reg, "f": mf, "t": mt})


async def fetch_missing_ranges(db_client, regs, w_start: date, w_end: date, gap_days: int = None,
                               time_budget_s: float = None, on_progress=None) -> dict:
    """Fetch ONLY the missing FR24 date ranges for `regs` within [w_start, w_end].

    Pre-pass: seed the ledger if absent, compute each reg's missing ranges, and estimate the total
    FR24 request count. Then fetch each range (best-effort, recording it after so it is never
    re-fetched) UNTIL `time_budget_s` elapses — any not-yet-fetched ranges are left for the next run
    (their absence from the ledger makes them retry). `on_progress(fetch_seconds_remaining)` is called
    with a live estimate (measured per-request time, capped by the remaining budget). Never raises."""
    gap_days = FLIGHT_RADAR_COVERAGE_GAP_DAYS if gap_days is None else gap_days
    regs = list(regs)

    # PRE-PASS: seed + compute missing ranges + estimate the total FR24 requests up front.
    plan = []  # (reg, gf, gt, est_requests)
    for reg in regs:
        try:
            async with db_client.session("flightradar") as s:
                await _seed_if_absent(s, reg, gap_days)
                await s.commit()
                covered = await _covered(s, reg)
            for gf, gt in _complement(covered, w_start, w_end):
                plan.append((reg, gf, gt, _estimate_requests(gf, gt)))
        except Exception as e:
            logger.warning("coverage plan failed for %s: %s", reg, e)
    total_requests = sum(p[3] for p in plan)

    async def _report(done, avg, started):
        if on_progress is None:
            return
        remaining = max(0, total_requests - done) * avg
        if time_budget_s is not None and started is not None:
            remaining = min(remaining, max(0.0, time_budget_s - (time.monotonic() - started)))
        await on_progress(remaining)

    await _report(0, FR24_SECONDS_PER_REQUEST_EST, None)   # initial ETA before fetching starts

    started = time.monotonic()
    avg_per_req = FR24_SECONDS_PER_REQUEST_EST
    measured_time, measured_reqs = 0.0, 0
    done_requests, ranges_fetched, tail_days = 0, 0, 0
    incomplete = False
    for reg, gf, gt, est in plan:
        if time_budget_s is not None and (time.monotonic() - started) >= time_budget_s:
            incomplete = True
            logger.info("FR24 coverage: %ss budget reached; %d/%d ranges fetched, rest next run",
                        int(time_budget_s), ranges_fetched, len(plan))
            break
        t = time.monotonic()
        try:
            await fetch_all_ranges(start_date=gf.isoformat(), end_date=gt.isoformat(),
                                   registrations=[reg], storage_mode="db")
        except Exception as e:
            logger.warning("FR24 fetch %s [%s..%s] failed: %s", reg, gf, gt, e)
            done_requests += est
            await _report(done_requests, avg_per_req, started)
            continue  # leave un-recorded so it retries next run
        measured_time += time.monotonic() - t
        measured_reqs += est
        if measured_reqs:
            avg_per_req = measured_time / measured_reqs
        async with db_client.session("flightradar") as s:
            await _record(s, reg, gf, gt)
            await s.commit()
        done_requests += est
        ranges_fetched += 1
        tail_days += (gt - gf).days + 1
        await _report(done_requests, avg_per_req, started)

    summary = {"regs": len(regs), "planned_ranges": len(plan), "ranges_fetched": ranges_fetched,
               "tail_days": tail_days, "requests_est": total_requests, "incomplete": incomplete}
    logger.info("FR24 coverage refresh: %s", summary)
    return summary
