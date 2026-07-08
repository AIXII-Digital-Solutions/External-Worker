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
                      FLIGHT_RADAR_MAX_REG_PER_BATCH, FR24_SECONDS_PER_REQUEST_EST)
from API.FlightRadarAPI.FlightSummary import fetch_all_ranges

logger = setup_logger("fr24_coverage")

_TBL = "flightradar.flightsummary_coverage"


class _BudgetReached(Exception):
    """Raised from the per-request callback to stop the fetch once the time budget is spent."""


class JobCancelled(Exception):
    """Raised from the per-request callback when the user requested cancellation of this job."""


_MERGE_TOL_DAYS = 1   # gap-keys whose endpoints are within this many days are fetched as ONE batch


def _estimate_requests(gf: date, gt: date) -> int:
    """Estimate the FR24 requests a gap costs: one per FLIGHT_RADAR_RANGE_DAYS-day chunk (>= 1)."""
    return max(1, ceil(((gt - gf).days + 1) / FLIGHT_RADAR_RANGE_DAYS))


def _cluster_groups(groups: dict, tol_days: int):
    """Merge (from, to) gap-keys whose BOTH endpoints are within `tol_days` of a cluster anchor into
    one batched cluster. Returns [(gf_union, gt_union, [regs]), ...]: the union range is fetched once
    for ALL the cluster's tails (e.g. trailing gaps [01-31..] and [02-01..] become a single request).
    Recording the union per tail is correct — the batched request covers that union for every tail."""
    clusters = []   # [anchor_gf, anchor_gt, gf_min, gt_max, set(regs)]
    for gf, gt in sorted(groups):
        regs = groups[(gf, gt)]
        for cl in clusters:
            if abs((gf - cl[0]).days) <= tol_days and abs((gt - cl[1]).days) <= tol_days:
                cl[2], cl[3] = min(cl[2], gf), max(cl[3], gt)
                cl[4].update(regs)
                break
        else:
            clusters.append([gf, gt, gf, gt, set(regs)])
    return [(cl[2], cl[3], sorted(cl[4])) for cl in clusters]


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
                               time_budget_s: float = None, on_progress=None, should_cancel=None) -> dict:
    """Fetch ONLY the missing FR24 date ranges for `regs` within [w_start, w_end].

    Pre-pass: seed the ledger if absent, compute each reg's missing ranges, and GROUP tails by an
    identical (from, to) gap so tails sharing a range (typically the common trailing gap) are fetched
    in ONE batched FR24 request instead of one-per-tail (which multiplied requests + rate-limit sleeps
    and stalled between them). Groups are fetched newest-first, each recorded after so it is never
    re-fetched, UNTIL `time_budget_s` elapses (checked per API request) — unfetched groups retry next
    run. `on_progress(fetch_seconds_remaining)` is called live (measured per-request time, throttled to
    ~2s, capped by the remaining budget). Never raises (except the internal budget stop, handled here).
    """
    gap_days = FLIGHT_RADAR_COVERAGE_GAP_DAYS if gap_days is None else gap_days
    regs = list(regs)

    # PRE-PASS: seed + compute each reg's missing ranges, group tails by identical (from, to).
    groups: dict = {}   # (gf, gt) -> [regs]
    for reg in regs:
        try:
            async with db_client.session("flightradar") as s:
                await _seed_if_absent(s, reg, gap_days)
                await s.commit()
                covered = await _covered(s, reg)
            for gf, gt in _complement(covered, w_start, w_end):
                groups.setdefault((gf, gt), []).append(reg)
        except Exception as e:
            logger.warning("coverage plan failed for %s: %s", reg, e)
    # merge near-identical gap ranges (±_MERGE_TOL_DAYS) into batched clusters; newest first (so fresh
    # data is prioritised if the budget runs out).
    plan = _cluster_groups(groups, _MERGE_TOL_DAYS)
    plan.sort(key=lambda c: c[1], reverse=True)

    total_requests = sum(_estimate_requests(gf, gt) * ceil(len(rs) / FLIGHT_RADAR_MAX_REG_PER_BATCH)
                         for gf, gt, rs in plan)
    started = time.monotonic()
    done_requests = 0
    last_report = -1e9

    async def _report(force=False):
        nonlocal last_report
        if on_progress is None:
            return
        elapsed = time.monotonic() - started
        if not force and elapsed - last_report < 2.0:
            return
        last_report = elapsed
        avg = elapsed / done_requests if done_requests else FR24_SECONDS_PER_REQUEST_EST
        remaining = max(0, total_requests - done_requests) * avg
        if time_budget_s is not None:
            remaining = min(remaining, max(0.0, time_budget_s - elapsed))
        await on_progress(remaining)

    async def _on_request():
        nonlocal done_requests
        done_requests += 1
        if should_cancel is not None and await should_cancel():
            raise JobCancelled()
        if time_budget_s is not None and (time.monotonic() - started) >= time_budget_s:
            raise _BudgetReached()
        await _report()

    await _report(force=True)   # initial ETA (total_requests * per-request estimate)

    ranges_fetched, tail_days, incomplete = 0, 0, False
    for gf, gt, grp_regs in plan:
        if should_cancel is not None and await should_cancel():
            raise JobCancelled()
        if time_budget_s is not None and (time.monotonic() - started) >= time_budget_s:
            incomplete = True
            break
        try:
            # end_date is EXCLUSIVE-of-next-day so a single-day gap has from < to (FR24 else 400s).
            await fetch_all_ranges(start_date=gf.isoformat(),
                                   end_date=(gt + timedelta(days=1)).isoformat(),
                                   registrations=list(grp_regs), storage_mode="db",
                                   on_request=_on_request)
        except _BudgetReached:
            incomplete = True
            logger.info("FR24 coverage: %ss budget reached mid-fetch; rest next run", int(time_budget_s or 0))
            break
        except JobCancelled:
            raise   # propagate to run_forecast_panel — user cancelled
        except Exception as e:
            logger.warning("FR24 fetch %s [%s..%s] failed: %s", grp_regs, gf, gt, e)
            continue  # leave un-recorded so it retries next run
        async with db_client.session("flightradar") as s:
            for reg in grp_regs:
                await _record(s, reg, gf, gt)
            await s.commit()
        ranges_fetched += 1
        tail_days += ((gt - gf).days + 1) * len(grp_regs)
        await _report(force=True)

    summary = {"regs": len(regs), "groups": len(plan), "ranges_fetched": ranges_fetched,
               "tail_days": tail_days, "requests_est": total_requests, "incomplete": incomplete}
    logger.info("FR24 coverage refresh: %s", summary)
    return summary
