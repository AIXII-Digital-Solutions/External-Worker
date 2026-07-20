"""FR24 flight-summary coverage ledger — fetch ONLY the missing date ranges per tail.

flightradar.flightsummary_coverage records, per registration, which [covered_from, covered_to] date
ranges have already been fetched from FR24 (so whatever flights exist for them are in flightsummary).
The forecast panel fetches only the request window MINUS this ledger (the missing ranges), then records
each fetched range so it is never re-fetched. Coverage advances only to where flights ACTUALLY landed;
an EMPTY range is finalized (recorded covered anyway, to bound token spend) ONLY once it is older than
COVERAGE_REVALIDATE_DAYS — the recent tail stays re-fetchable so a lagging source or late flights are
caught. This keeps the ledger from ever running ahead of the real data and blocking the catch-up fetch.

Two phases, kept SEPARATE so the panel can show them as distinct steps:
  * plan_missing_ranges  — STEP 1 "searching": pure DB/planning, spends NO API budget. Folds in what we
                           already hold, computes each tail's missing ranges, clusters them into batches.
  * fetch_planned_ranges — STEP 2 "fetching": spends API budget, newest-first, records each range.

THE TOKEN INVARIANT: we never re-fetch a day we already have data for. FR24 bills per RETURNED ROW, before
our de-dup, so re-fetching a covered range re-pays for rows we own. plan_missing_ranges therefore complements
the request window against (ledger ∪ the tail's own flightsummary days) — so a range whose data we hold is
never planned, even if the LEDGER never recorded it (a partial/empty ledger over bulk-loaded history would
otherwise re-bill the whole company). A never-fetched tail has no data to fold, so its window is fetched in
full — genuinely-new data is still paid for, which is the point. The tail's data-days are coalesced across
no-fly gaps shorter than FLIGHT_RADAR_COVERAGE_GAP_DAYS (we hold data on both sides, so the tiny gap is
assumed seen); the ledger's OWN gaps are never bridged (a genuinely un-fetched stretch stays fetchable). The
fold is capped at the finalize cut, so the recent tail stays re-fetchable for late-arriving flights.
"""
import time
from collections import defaultdict
from datetime import date, timedelta
from math import ceil

from sqlalchemy import text

from Config import setup_logger
from settings import (FLIGHT_RADAR_COVERAGE_GAP_DAYS, FLIGHT_RADAR_RANGE_DAYS,
                      FLIGHT_RADAR_MAX_REG_PER_BATCH)
from API.FlightRadarAPI.FlightSummary import fetch_all_ranges

logger = setup_logger("fr24_coverage")

_TBL = "flightradar.flightsummary_coverage"

# The trailing window (this many days back from today-1) is NEVER finalized as "covered" on an EMPTY fetch:
# a source that lags the clock, or late-arriving flights, must be re-fetched until the data actually shows
# up. Days OLDER than this that came back empty ARE finalized (they will never gain data), so token spend
# stays bounded. Without this, one empty fetch of a recent range records it "covered" and permanently blocks
# the catch-up (the bug: ledger claimed coverage to 14-Jul while flightsummary held data only to 30-Jun).
COVERAGE_REVALIDATE_DAYS = 2


class _BudgetReached(Exception):
    """Raised from the per-request callback to stop the fetch once the time budget is spent."""


class JobCancelled(Exception):
    """Raised from the per-request callback when the user requested cancellation of this job."""


def _estimate_requests(gf: date, gt: date) -> int:
    """Estimate the FR24 requests a gap costs: one per FLIGHT_RADAR_RANGE_DAYS-day chunk (>= 1)."""
    return max(1, ceil(((gt - gf).days + 1) / FLIGHT_RADAR_RANGE_DAYS))


def _segment_groups(groups: dict):
    """Decompose the tails' missing intervals into MAXIMAL (from, to, [regs]) fetch groups so each request
    carries as many tails AND as wide a range as possible without ever re-fetching a day a tail already has.

    A line sweep over the timeline: every elementary date-segment is assigned EXACTLY the set of tails missing
    it, then consecutive segments carrying the same tail-set are merged. Consequences (exactly the intended
    behaviour):
      * tails missing the SAME range are fetched together in one group;
      * tails whose ranges only OVERLAP split at the overlap boundaries — the shared part becomes one group
        for all of them, each private remainder its own group — so no tail re-pays for a day it already holds;
      * a fleet never fetched (all tails missing the whole window) collapses to a SINGLE group of all tails
        over the whole window, chunked 14 days × 15 tails — not one-reg-one-day fragments.

    `groups` is {(gf, gt): [regs]} — one entry per tail's each missing interval. The 14-day / 15-tail chunking
    and newest-first ordering happen in the caller. Runs in O(events log events)."""
    starts, ends = defaultdict(list), defaultdict(list)   # date -> tails; end key is EXCLUSIVE (gt + 1 day)
    for (gf, gt), regs in groups.items():
        starts[gf].extend(regs)
        ends[gt + timedelta(days=1)].extend(regs)

    active, out, prev = set(), [], None
    for d in sorted(set(starts) | set(ends)):
        if prev is not None and active:
            out.append([prev, d - timedelta(days=1), frozenset(active)])
        for reg in ends.get(d, ()):        # ends before starts, so a same-day hand-off keeps the tail active
            active.discard(reg)
        for reg in starts.get(d, ()):
            active.add(reg)
        prev = d

    merged = []   # coalesce touching segments that carry an identical tail-set
    for s, e, rs in out:
        if merged and merged[-1][2] == rs and (s - merged[-1][1]).days == 1:
            merged[-1][1] = e
        else:
            merged.append([s, e, rs])
    return [(s, e, sorted(rs)) for s, e, rs in merged]


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


async def _data_days(session, regs, w_start, w_end, up_to):
    """The days each reg ALREADY has flightsummary rows on, within [w_start, min(w_end, up_to)] — one bulk
    query for the whole batch. A day with data is, by definition, already fetched: including it in the
    covered set makes re-billing it structurally impossible, no matter what the ledger says. Capped at
    `up_to` (the finalize cut) so the recent tail is NOT folded — recent days stay re-fetchable to catch
    late-arriving flights (same window the ledger's empty-finalization respects)."""
    hi = min(w_end, up_to)
    out: dict = {}
    if hi < w_start or not regs:
        return out
    rows = (await session.execute(text(
        "SELECT reg, first_seen::date AS d "
        "FROM flightradar.flightsummary "
        "WHERE reg = ANY(:regs) AND first_seen::date BETWEEN :f AND :t "
        "GROUP BY reg, first_seen::date ORDER BY 1, 2"),
        {"regs": list(regs), "f": w_start, "t": hi})).all()
    for reg, d in rows:
        out.setdefault(reg, []).append(d)
    return out


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


async def plan_missing_ranges(db_client, regs, w_start: date, w_end: date, gap_days: int = None,
                              on_progress=None, should_cancel=None, reg_starts: dict = None) -> dict:
    """STEP 1 (spends NO API budget): plan what must be fetched.

    For each tail, complement [its start, w_end] against (ledger ∪ days we already hold) to get its MISSING
    ranges, then `_segment_groups` decomposes all tails' missing intervals into MAXIMAL (from, to, [tails])
    fetch groups — each request carries as many tails and as wide a range as possible, tails sharing a range
    batch together, overlaps split so no day is re-fetched. Sorted newest-first. Returns
    {plan, total_requests, regs, groups}.

    `reg_starts` optionally gives a PER-TAIL window start (e.g. delivery-date − buffer): the tail's window
    becomes [max(w_start, reg_starts[reg]), w_end]. This skips the empty pre-delivery stretch of a tail that
    entered service after w_start — a big fleet of young aircraft would otherwise spend thousands of 0-token
    requests confirming those pre-delivery windows empty. Tails absent from the map use the global w_start.
    `on_progress(done, total)` reports tails planned (for the live step-1 fraction)."""
    gap_days = FLIGHT_RADAR_COVERAGE_GAP_DAYS if gap_days is None else gap_days
    reg_starts = reg_starts or {}
    regs = [r for r in regs if r]   # drop NULL/empty registrations — cannot be fetched from FR24
    total = len(regs)

    # The days each tail ALREADY holds are folded into its covered set below, so a range we already have is
    # never re-planned (hence never re-billed) — regardless of whether the ledger recorded it. This is the
    # token guarantee: on a partial/empty ledger over data we hold, the plan collapses to genuinely-missing
    # ranges only; a never-fetched tail (no data) folds nothing and is fetched in full, as it should be.
    # Folded only up to the finalize cut (recent tail stays re-fetchable for late flights).
    finalize_cut = date.today() - timedelta(days=1 + COVERAGE_REVALIDATE_DAYS)
    async with db_client.session("flightradar") as s:
        data_days = await _data_days(s, regs, w_start, w_end, finalize_cut)

    groups: dict = {}   # (gf, gt) -> [regs]
    for i, reg in enumerate(regs):
        if should_cancel is not None and await should_cancel():
            raise JobCancelled()
        try:
            async with db_client.session("flightradar") as s:
                covered = await _covered(s, reg)
            # Bridge only the DATA-days with gap_days (short no-fly gaps between real flights — we hold data on
            # both sides, so assuming the tiny gap is "seen" is safe and saves empty requests). Do NOT bridge
            # the ledger's own gaps: a gap between two ledger ranges is a genuinely un-fetched stretch and must
            # stay fetchable — so union with the ledger at gap=1 (touching/overlapping only). Mirrors the old
            # bootstrap seed, but applied every run and to the plan directly.
            data_ranges = _coalesce([(d, d) for d in data_days.get(reg, ())], max_gap_days=gap_days)
            folded = _coalesce(covered + data_ranges, max_gap_days=1)
            ws = max(w_start, reg_starts.get(reg, w_start))   # per-tail start (delivery − buffer), never < w_start
            if ws > w_end:
                continue   # delivered after the window (no data can exist yet) — nothing to fetch
            for gf, gt in _complement(folded, ws, w_end):
                groups.setdefault((gf, gt), []).append(reg)
        except JobCancelled:
            raise
        except Exception as e:
            logger.warning("coverage plan failed for %s: %s", reg, e)
        if on_progress is not None and (i % 10 == 0 or i == total - 1):
            try:
                await on_progress(i + 1, total)
            except Exception:
                pass

    plan = _segment_groups(groups)
    plan.sort(key=lambda c: c[1], reverse=True)
    total_requests = sum(_estimate_requests(gf, gt) * ceil(len(rs) / FLIGHT_RADAR_MAX_REG_PER_BATCH)
                         for gf, gt, rs in plan)
    return {"plan": plan, "total_requests": total_requests, "regs": total, "groups": len(plan)}


async def fetch_planned_ranges(db_client, plan, total_requests: int, time_budget_s: float = None,
                               on_progress=None, should_cancel=None) -> dict:
    """STEP 2 (spends API budget): fetch each planned group's missing range.

    Groups are fetched newest-first, each recorded after so it is never re-fetched, UNTIL `time_budget_s`
    elapses (checked per API request) — unfetched groups retry on the next run. `on_progress(done_requests,
    total_requests)` is called live per request (for the step-2 fraction). Never raises except JobCancelled
    (propagated to the caller) — the internal budget stop is handled here."""
    started = time.monotonic()
    done_requests = 0

    async def _on_request():
        nonlocal done_requests
        done_requests += 1
        if should_cancel is not None and await should_cancel():
            raise JobCancelled()
        if time_budget_s is not None and (time.monotonic() - started) >= time_budget_s:
            raise _BudgetReached()
        if on_progress is not None:
            try:
                await on_progress(done_requests, total_requests)
            except Exception:
                pass

    ranges_fetched, tail_days, incomplete = 0, 0, False
    for gf, gt, grp_regs in plan:
        if should_cancel is not None and await should_cancel():
            raise JobCancelled()
        if time_budget_s is not None and (time.monotonic() - started) >= time_budget_s:
            incomplete = True
            break
        try:
            # Upper bound = END of gt (gt 23:59:59), NOT the next day's midnight. The old `gt + 1 day` made
            # the request's flight_datetime_to land on the NEXT calendar day, so a gap ending on real-yesterday
            # asked FR24 for TODAY (00:00:00) — a day the source cannot return yet (today is incomplete). Using
            # gt's end-of-day keeps from < to for a single-day gap (FR24 400s on from == to) while never
            # emitting today. gt is already <= today-1 (the plan's w_end), so the max queried instant is
            # yesterday 23:59:59.
            await fetch_all_ranges(start_date=gf.isoformat(),
                                   end_date=f"{gt.isoformat()} 23:59:59",
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
        # Advance the ledger only to where flights ACTUALLY landed — never past the real data. Days beyond
        # the last real flight are finalized (recorded covered even though empty) ONLY once they are older
        # than the revalidate window; the recent tail stays uncovered so it is re-fetched until data arrives.
        # This stops an empty fetch from claiming coverage of recent/future-of-the-source days and silently
        # blocking the trailing catch-up, while still bounding token spend on genuinely-empty old ranges.
        finalize_to = date.today() - timedelta(days=1 + COVERAGE_REVALIDATE_DAYS)
        async with db_client.session("flightradar") as s:
            for reg in grp_regs:
                actual_to = (await s.execute(text(
                    "SELECT max(first_seen::date) FROM flightradar.flightsummary "
                    "WHERE reg = :r AND first_seen::date BETWEEN :f AND :t"),
                    {"r": reg, "f": gf, "t": gt})).scalar()
                settled = min(gt, finalize_to)          # finalize (even if empty) no later than here
                record_to = settled if actual_to is None else max(actual_to, settled)
                record_to = min(record_to, gt)
                if record_to >= gf:
                    await _record(s, reg, gf, record_to)
            await s.commit()
        ranges_fetched += 1
        tail_days += ((gt - gf).days + 1) * len(grp_regs)

    summary = {"groups": len(plan), "ranges_fetched": ranges_fetched, "tail_days": tail_days,
               "requests_est": total_requests, "incomplete": incomplete}
    logger.info("FR24 coverage refresh: %s", summary)
    return summary
