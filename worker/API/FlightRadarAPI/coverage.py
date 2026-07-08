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
from datetime import date, timedelta

from sqlalchemy import text

from Config import setup_logger
from settings import FLIGHT_RADAR_COVERAGE_GAP_DAYS
from API.FlightRadarAPI.FlightSummary import fetch_all_ranges

logger = setup_logger("fr24_coverage")

_TBL = "flightradar.flightsummary_coverage"


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


async def fetch_missing_ranges(db_client, regs, w_start: date, w_end: date, gap_days: int = None) -> dict:
    """For each reg: seed the ledger if absent, compute the missing ranges within [w_start, w_end],
    fetch ONLY those from FR24 (best-effort per range), and record each fetched range. Never raises —
    a per-reg / per-range failure is logged and skipped (the range stays un-recorded so it retries)."""
    gap_days = FLIGHT_RADAR_COVERAGE_GAP_DAYS if gap_days is None else gap_days
    regs = list(regs)
    ranges_fetched, tail_days, regs_touched = 0, 0, 0
    for reg in regs:
        try:
            async with db_client.session("flightradar") as s:
                await _seed_if_absent(s, reg, gap_days)
                await s.commit()
                covered = await _covered(s, reg)
            gaps = _complement(covered, w_start, w_end)
            if gaps:
                regs_touched += 1
            for gf, gt in gaps:
                try:
                    await fetch_all_ranges(start_date=gf.isoformat(), end_date=gt.isoformat(),
                                           registrations=[reg], storage_mode="db")
                except Exception as e:
                    logger.warning("FR24 fetch %s [%s..%s] failed: %s", reg, gf, gt, e)
                    continue  # leave un-recorded so it retries next run
                async with db_client.session("flightradar") as s:
                    await _record(s, reg, gf, gt)
                    await s.commit()
                ranges_fetched += 1
                tail_days += (gt - gf).days + 1
        except Exception as e:
            logger.warning("coverage refresh failed for %s: %s", reg, e)
    summary = {"regs": len(regs), "regs_with_gaps": regs_touched,
               "ranges_fetched": ranges_fetched, "tail_days": tail_days}
    logger.info("FR24 coverage refresh: %s", summary)
    return summary
