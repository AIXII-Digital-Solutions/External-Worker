"""Forecast model — forecast_panel step 3. Reads forecast.acys_actuals ONLY, writes per-(forecast)flight
rows to forecast.acys_forecast (same per-flight shape as acys_actuals); step 4 merges acys_actuals +
acys_forecast into acys_summary_by_day (geo-enriched, Age, Data Type = 'Actuals'/'Forecast').

Premise (fixed observed fleet on a frozen network): monthly VOLUME comes from a recent-activity anchor,
optionally GROWN by the forward fleet (future-delivery aircraft stubs that the assemble step now writes
into acys_actuals with Date NULL); the per-flight STRUCTURE comes from replicating a real recent template
month — so the grouped "# Of Flights" and the summed metrics stay faithful.

Per sub-fleet (Master Series), from acys_actuals FLIGHTS (Date NOT NULL):
  * seasonal[cal] = shrunk month-of-year factor; level = median of last LEVEL_L deseasonalized months.
  * base_fleet    = median flown-tail count of the last few months ≤ frontier.
Forward fleet from the STUB rows (Date NULL, Delivery Date > frontier): per sub-fleet, aircraft arriving by
month m. Forecast month m: flights_hat_sf(m) = level_sf × seasonal_sf[cal(m)] × growth_sf(m), where
growth_sf(m) = (base_fleet_sf + delivered_by_m_sf) / base_fleet_sf (capped) — the monthly VOLUME. The per-flight
STRUCTURE (routes) comes from a YEAR-ROBUST route pool for cal(m) (see _pool_tier1_sql): each route weighted
by the median of its per-year counts in that calendar month, one-off single-year bursts dropped, so a spike
that occurred in only one occurrence of a month is NOT replayed into every forecast year. Window: history
HISTORY_START → coverage frontier; forecast frontier+1 → as_of + FORECAST_HORIZON_YEARS. Signed v1 limits: no
calendar trend beyond fleet growth; low route-structure confidence for thin sub-fleets; a brand-new sub-fleet
with deliveries but no prior flights has no level and is not forecast.
"""
import statistics as st
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text

from Config import setup_logger
from settings import FORECAST_HORIZON_YEARS, FORECAST_PAX_LOAD_FACTOR

logger = setup_logger("forecast_model")

HISTORY_START = date(2022, 7, 1)
LEVEL_L = 3           # trailing months for the deseasonalized recent level (predictive: 9.7% MAPE)
SEAS_K = 3.0          # seasonal-factor shrinkage toward 1.0 by month support. Lowered 6->3 so seasonality is
                      # VISIBLE (forecast month-of-year amplitude ~6.7%->9.8%, ~40% of the actual ~24%). 3 is
                      # the safe floor: below it a THIN/growing sub-fleet (the neo ramp 1->10 tails) overfits
                      # its early low months as a seasonal trough (factor -> 0.5-0.67); at 3 the widest spread
                      # is the stable A320-232, so no ramp-as-season artefact. (measured via the SEAS_K sweep)
FRONTIER_FRAC = 0.6   # a recent month is "complete" if its flights ≥ this × the trailing-window median
FRONTIER_WINDOW = 9   # trailing months the frontier threshold is measured against
# The last few months of the flight source are typically FR24-INCOMPLETE (ingestion lag) — their volume is
# undercounted, so their DESEASONALIZED value sits below the true level. A plain median of the last LEVEL_L
# months would inherit that undercount and drag the WHOLE forecast ~20-30% too low (flat + negative growth vs
# the complete actuals). The robust level (see _robust_level) estimates the level from the recent COMPLETE
# months only: months within LEVEL_COMPLETE_FRAC of a high-quantile reference of the window.
LEVEL_WINDOW = 9          # recent months the level is estimated over
LEVEL_COMPLETE_FRAC = 0.85  # a window month counts toward the level iff its deseasonalized volume ≥ this × ref
# NOTE: there is deliberately NO idle-aircraft retirement and NO growth cap. The fleet comes straight from
# Cirium's latest revision (delivery-dated), and per the product rule we never PREDICT that an aircraft
# retires — an in-service tail keeps flying to the horizon (invariant: future aircraft never leave). A dead
# aircraft is dropped only when Cirium itself marks it Retired/Written off (see _NOT_DEAD), not by idleness.

# The monthly history, grouped by {key}. Run TWICE: once keyed on Aircraft Sub Series (the forecast's own
# grain) and once on Master Series — an operator can take delivery of a sub-series it has NEVER flown (Air
# Arabia is getting 12 A321-253N neo ACF with zero flights on the type), which leaves nothing to fit and no
# route pool. Those sub-fleets fall back to their MASTER SERIES history.
_MONTHLY_SQL = """
SELECT {key}                                      AS sf,
       date_trunc('month',"Date")::date          AS mon,
       count(*)                                   AS flights,
       count(DISTINCT "Registration")             AS tails
FROM forecast.acys_actuals
WHERE "Operator" = :op AND "Date" IS NOT NULL AND "Date" >= :start {scope}
GROUP BY 1, 2
"""

_KEY_SF = """coalesce(nullif("Aircraft Sub Series",''),'NA')"""
_KEY_MS = """coalesce(nullif("Master Series",''),'NA')"""

# Minimum DISTINCT routes a route pool must have before it is trusted as-is. Below this, the pool is
# "degenerate" (e.g. a barely-flown new type whose seasonal template month holds a single long-haul route),
# and _insert_sql's tier cascade broadens it (sub-series all-history -> master all-history). Keeps one
# aircraft from being pinned onto one route for a whole month (36x Toulouse-Sharjah = 226 flight-hours).
_MIN_ROUTE_POOL = 5

# DEPRECATED (unused): the fleet + future deliveries now come from the reference (§ run_forecast_model),
# not from Date-NULL stub rows. Kept only to avoid churn; sub-fleet key would be "Aircraft Sub Series".
_FUTURE_SQL = """
SELECT coalesce(nullif("Aircraft Sub Series",''),'NA') AS sf,
       date_trunc('month',"Delivery Date")::date      AS deliv,
       count(*)                                        AS n
FROM forecast.acys_actuals
WHERE "Operator" = :op AND "Date" IS NULL AND "Delivery Date" IS NOT NULL {scope}
GROUP BY 1, 2
"""


def _add_months(d: date, n: int) -> date:
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def _days_in_month(first_of_month: date) -> int:
    return (_add_months(first_of_month, 1) - first_of_month).days


def _quantile(xs, q):
    """Simple index-based quantile (no interpolation) — robust for tiny samples, avoids statistics edge cases."""
    s = sorted(xs)
    if not s:
        return 0.0
    return s[min(len(s) - 1, int(q * len(s)))]


def _robust_level_and_mask(des):
    """Recent per-sub-fleet level (deseasonalized flights), robust to FR24-INCOMPLETE recent months, plus the
    boolean mask marking WHICH series months were counted as COMPLETE. Incomplete months are undercounted, so
    their deseasonalized value sits BELOW the true level; a plain median of the last LEVEL_L would inherit the
    undercount. Instead: over the recent LEVEL_WINDOW, take a HIGH quantile as the complete-month reference
    (complete months dominate the top), keep the window months within LEVEL_COMPLETE_FRAC of it, and take the
    MEDIAN of those. The mask lets the caller draw the fleet `base` from the SAME complete months, so the
    per-aircraft rate (level/base) is not diluted by fleet GROWTH between the complete period and the frontier
    (a growing sub-fleet has fewer tails in the complete window than at the frontier). With <3 months of
    history there is nothing to filter — fall back to the plain last-LEVEL_L median/months."""
    n = len(des)
    mask = [False] * n
    lo = max(0, n - LEVEL_WINDOW)
    recent_idx = list(range(lo, n))
    if len(recent_idx) < 3:
        for i in recent_idx[-LEVEL_L:]:
            mask[i] = True
        return st.median(des[-LEVEL_L:]), mask
    ref = _quantile([des[i] for i in recent_idx], 0.75)
    complete_idx = [i for i in recent_idx if des[i] >= LEVEL_COMPLETE_FRAC * ref]
    if not complete_idx:
        for i in recent_idx[-LEVEL_L:]:
            mask[i] = True
        return st.median(des[-LEVEL_L:]), mask
    for i in complete_idx:
        mask[i] = True
    return st.median([des[i] for i in complete_idx]), mask


def _fit(series):
    """series = sorted [(cal_month, flights)] (≤ frontier) → (level, seasonal[1..12], complete_mask)."""
    by_cal = defaultdict(list)
    for c, f in series:
        by_cal[c].append(f)
    cal_med = {c: st.median(v) for c, v in by_cal.items()}
    base = st.fmean(list(cal_med.values())) or 1.0
    seas = {}
    for c in range(1, 13):
        if c in cal_med:
            w = len(by_cal[c]) / (len(by_cal[c]) + SEAS_K)
            seas[c] = max(w * (cal_med[c] / base) + (1 - w), 0.10)
        else:
            seas[c] = 1.0
    des = [f / seas[c] for c, f in series]
    level, mask = _robust_level_and_mask(des)
    return level, seas, mask


def _horizon_month(as_of: date) -> date:
    return date(as_of.year + FORECAST_HORIZON_YEARS, as_of.month, 1)


def _month_span(m: date, fc_start: date, as_of: date):
    """Days of month `m` the forecast covers. TWO anchors: the FIRST forecast month starts on `fc_start` (the
    day AFTER the last fact — so there is NO gap between facts and forecast), and the HORIZON month
    (as_of + horizon) ends on the CY anchor day `as_of.day`; every month between is full. proration =
    day_span / days_in_month scales the flight volume AND the day-spread identically. fc_start pins the START;
    as_of pins the HORIZON and (elsewhere) the day-precise Contract Year, which may sit later than fc_start."""
    dim = _days_in_month(m)
    # clamp both anchor days to the month length so the day-spread never emits an out-of-range date (a 29-Feb
    # as_of whose horizon Feb has 28 days would otherwise make_date(year,2,29) and crash the INSERT).
    start_day = min(fc_start.day, dim) if m == date(fc_start.year, fc_start.month, 1) else 1
    end_day = min(as_of.day, dim) if m == _horizon_month(as_of) else dim
    return start_day, end_day, max(1, end_day - start_day + 1), dim


def _plan(rows, fc_start: date, as_of: date):
    """Fit level×seasonal per sub-fleet on months ≤ the coverage frontier, then plan the forecast months
    from `fc_start`'s month (the day after the last fact) to the horizon (as_of + horizon). The Contract Year
    is cut on the as_of day, which may sit LATER than fc_start. Returns (frontier, forecast_months, plan, fits) where
    plan[m] = {sf: (template_month, k)}, k = round((level / base_fleet) × seasonal[cal(m)] × proration), and
    fits[sf] = (level, seasonal[1..12], base_fleet). The ACTIVE fleet per month is resolved in SQL from the
    latest Cirium revision (delivery ≤ month) — every in-fleet aircraft flies every month, volume grows with
    deliveries, nothing retires unless Cirium drops it. Current & final months are prorated by covered days."""
    by_sf = defaultdict(list)      # sf -> sorted [(mon, flights, tails)]
    by_mon = defaultdict(int)
    for r in rows:
        by_sf[r["sf"]].append((r["mon"], r["flights"], r["tails"]))
        by_mon[r["mon"]] += r["flights"]
    for sf in by_sf:
        by_sf[sf].sort()
    months = sorted(by_mon)
    if not months:
        return None, [], {}, {}

    trailing = [by_mon[m] for m in months[-FRONTIER_WINDOW:]]
    med = st.median(trailing) if trailing else 0
    complete = [m for m in months if by_mon[m] >= med * FRONTIER_FRAC]
    frontier = max(complete) if complete else months[-1]

    fits, sf_hist = {}, {}
    for sf, s in by_sf.items():
        s_tr = [(mn, fl, tl) for mn, fl, tl in s if mn <= frontier and fl]
        if not s_tr:
            continue
        level, seas, mask = _fit([(mn.month, fl) for mn, fl, _ in s_tr])
        # base = typical flown-tail count, taken from the SAME complete months the level came from (mask), so
        # the per-aircraft rate (level/base) is not diluted by fleet growth between the complete window and the
        # frontier — a growing sub-fleet (all the neo deliveries) had fewer tails when complete than at June.
        ct = [tl for (_, _, tl), keep in zip(s_tr, mask) if keep]
        base = (st.median(ct) if ct else st.median([tl for _, _, tl in s_tr[-LEVEL_L:]])) or 1
        fits[sf] = (level, seas, base)
        sf_hist[sf] = [(mn, fl) for mn, fl, _ in s_tr]

    fmonths, plan = [], {}
    first_month = date(fc_start.year, fc_start.month, 1)
    m = first_month
    horizon = _horizon_month(as_of)
    while m <= horizon:
        _, _, day_span, dim = _month_span(m, fc_start, as_of)
        prorate = day_span / dim                    # covered-days fraction (1.0 except current & final months)
        # The FIRST forecast month is partial (covers fc_start.day..month-end) and its proration can be tiny
        # (a late-month fc_start -> a day or two). For a low-volume sub-fleet round() would then give k=0, and
        # if EVERY sub-fleet rounds to 0 the first month is empty -> the forecast starts next month instead
        # of the day after the last fact = a GAP (invariant: no hole between facts and forecast). The floor
        # below keeps the boundary day (the min day-spread day = fc_start.day) always covered.
        sfp = {}
        for sf, (level, seas, base) in fits.items():
            full = level * seas[m.month] / base            # un-prorated per-aircraft flights this cal. month
            k = round(full * prorate)                      # actually flown this (possibly partial) month
            # Floor k at 1 for EVERY month of a fitted sub-fleet. Invariant: once an aircraft is in the
            # fleet it must be present every month to the horizon ("future aircraft never leave"). Rounding
            # would otherwise drop a sub-fleet whenever round(full*prorate)=0 — in the partial first/horizon
            # months (tiny proration) OR in a full seasonal-trough month for a THIN fleet (business jets /
            # helicopters / sparse coverage, level/base < 1). That would make an active aircraft vanish for
            # one month and reappear the next — a hole in the fleet. The floor keeps it present; the run
            # loop's `active == 0` guard still suppresses aircraft that have NOT yet been delivered, so no
            # phantom is created for a not-yet-in-fleet tail. (Also covers the Master-Series fallback grain,
            # since plan_ms is built here too.)
            if prorate > 0:
                k = max(1, k)
            if k <= 0:
                continue
            same = [mn for mn, fl in sf_hist[sf] if mn.month == m.month]
            tm = same[-1] if same else sf_hist[sf][-1][0]           # route template = type's typical network
            sfp[sf] = (tm, int(k))
        if sfp:
            plan[m] = sfp
            fmonths.append(m)
        m = _add_months(m, 1)
    return frontier, fmonths, plan, fits


# ── Fleet identity ─────────────────────────────────────────────────────────────────────────────────────
# THE AIRFRAME IS THE SERIAL NUMBER, NOT THE REGISTRATION.
#
# An ordered airframe has no registration yet, so Cirium parks every one of them under a bare country
# prefix ('A6-'). Air Arabia's 113 on-order aircraft therefore share ONE registration string: keying the
# fleet on Registration collapses the whole order book into a SINGLE aircraft per sub-series and
# undercounts fleet growth by an order of magnitude.
#
# Keying orders on the serial but the in-service fleet on the registration does NOT work either: Cirium
# leaves the stale 'On order' row in place after an aircraft is delivered, so A6-ARI exists twice (serial
# 13343, once 'On order' and once 'In Service') and the two keys would count ONE airframe TWICE.
#
# So: identify by (Serial Number, Aircraft Sub Series) whenever a serial exists — that is the airframe —
# and fall back to Registration only when it does not. For the in-service fleet this changes nothing
# (54 registrations <-> 54 serials, 1:1); it only makes the delivered/ordered duplicate resolve to one.
# The ONLY statuses that are an aircraft. Two buckets, and the bucket answers exactly ONE question: if the
# row has NO delivery date, is the aircraft already flying, or not there yet?
#   LIVE  — In Service / Storage. It is in the fleet; a missing delivery date just means the reference never
#           recorded one, so it flies from month one.
#   ORDER — not yet delivered. It joins the fleet in its DELIVERY MONTH, and with no delivery date it cannot
#           be placed in any month at all, so it is dropped rather than assumed to be flying.
# Everything else is explicitly NOT fleet: 'Cancelled' (never arrives), 'Written off' (destroyed),
# 'Retired' (scrapped), 'Unknown'. A whitelist on purpose — a NEW status appearing in the reference must not
# silently start flying.
#
# 'Type swap' and 'Reengineered' are ORDER, not LIVE, and that placement is load-bearing: in the latest
# commercial revision ALL 3,490 'Type swap' rows have NO delivery date whatsoever (and 0 of 27 'Reengineered'
# registrations are ever seen flying). Putting them in LIVE would hand the forecast 3,490 aircraft with an
# unknown arrival date, each flying EVERY month of the horizon — precisely the phantom-fleet bug that
# 'Cancelled' caused. In ORDER they are counted the moment the reference gives them a delivery date.
_LIVE_STATUS = ("'In Service','Storage'")
_ORDER_STATUS = ("'On order','On option','LOI to Order','LOI to Option','Type swap','Reengineered'")

_ALLOWED = f"""ca."Status" IN ({_LIVE_STATUS}, {_ORDER_STATUS})"""
_ORDER = f"""ca."Status" IN ({_ORDER_STATUS})"""

# The operator's latest revision OF EACH plan_type. A Cirium revision is a single-plan snapshot
# (plan_type = Commercial | Business&Helicopters), so an operator with aircraft in BOTH plans has rows in
# TWO different revisions. `max(revision_id)` collapses that to ONE revision -> one plan -> the OTHER plan's
# aircraft get active=0 and are never forecast (a mixed-plan operator's fleet is silently incomplete). This
# picks the latest revision per plan_type the operator actually appears in; callers filter with
# `revision_id IN (SELECT mr FROM latest)`. Mirrors panel._future_aircraft_sql's DISTINCT ON (plan_type).
_LATEST_CTE = """SELECT DISTINCT ON (r.plan_type) r.id AS mr
    FROM cirium.aircraftrevision r
    WHERE EXISTS (SELECT 1 FROM cirium.ciriumaircrafts c
                  WHERE c.revision_id = r.id AND c."Operator" = :op)
    ORDER BY r.plan_type, to_date(r.period,'MM-YYYY') DESC, r.id DESC"""

# Per-AIRFRAME dead check. _ALLOWED is a per-ROW predicate: Cirium can carry SEVERAL rows for one airframe
# in the same revision, so a tail that is genuinely Retired/Written off but still has a STALE 'In Service'
# row would slip through (_ALLOWED keeps the stale row, the newer dead row is filtered out before dedup) and
# fly in the forecast — violating "a retired aircraft can never fly". This anti-join drops any airframe that
# has ANY Retired/Written off row under the same identity (serial when present, else registration), so a
# dead airframe cannot be resurrected by a stale active row. ('Cancelled' is deliberately NOT here — it is
# an order state, not an airframe death; a delivered tail with a stale cancelled order must still fly.)
_NOT_DEAD = """NOT EXISTS (
        SELECT 1 FROM cirium.ciriumaircrafts cd
        WHERE cd.revision_id IN (SELECT mr FROM latest) AND cd."Operator" = :op
          AND cd."Status" IN ('Retired','Written off')
          AND ( (coalesce(ca."Serial Number",'') <> '' AND cd."Serial Number" = ca."Serial Number")
             OR (coalesce(ca."Serial Number",'') =  '' AND cd."Registration"  = ca."Registration") ))"""

_IDENT = """CASE WHEN coalesce(ca."Serial Number",'') <> ''
            THEN 'SN:' || ca."Serial Number" || '|' || coalesce(nullif(ca."Aircraft Sub Series",''),'NA')
            ELSE 'REG:' || coalesce(ca."Registration",'') END"""

# Tie-break: when one airframe has BOTH a delivered row and a leftover order row, the delivered row wins
# (it carries the real delivery date and status, not the order's estimate).
_PREFER_DELIVERED = f"""(CASE WHEN {_ORDER} THEN 1 ELSE 0 END)"""

# The short serial: the reference's serial for an ORDER is a synthetic string ('ABY-A320-124349') whose only
# meaningful part is the trailing number, while a delivered aircraft carries the bare MSN ('13343'). Take
# whatever follows the LAST dash — that normalises both to just the number.
_SERIAL_SHORT = """regexp_replace(coalesce(ca."Serial Number",''), '^.*-', '')"""

# Registration for the report. A placeholder ('A6-', i.e. a prefix with nothing after the dash) is shared by
# EVERY unregistered airframe of the operator, so it cannot be shown as-is: expand it to
# Registration + Sub Series + short serial  ->  'A6-A320-251N neo-124349'.
# An aircraft that already HAS a real registration keeps it untouched — mangling A6-ARI into
# 'A6-ARIA320-251N neo-13343' helps nobody, and it is already unique.
_REG_OUT = f"""CASE WHEN coalesce(ca."Registration",'') = '' OR ca."Registration" ~ '-$'
               THEN coalesce(ca."Registration",'') || coalesce(ca."Aircraft Sub Series",'')
                    || '-' || {_SERIAL_SHORT}
               ELSE ca."Registration" END"""

# A row can only BE an aircraft if it carries a key at all; an ORDER additionally needs a delivery date,
# because without one it cannot be placed in any month (and must NOT fall through to "flies from month 1").
_HAS_KEY = f"""CASE WHEN {_ORDER}
               THEN (coalesce(ca."Serial Number",'') <> '' AND ca."Delivery Date" IS NOT NULL)
               ELSE (coalesce(ca."Registration",'') <> '' OR coalesce(ca."Serial Number",'') <> '') END"""

# In the fleet at month-end :m_end? An order joins ON its delivery month (and, per _HAS_KEY, must have a
# date at all). An in-service aircraft with no delivery date is simply already flying.
_DELIVERED = f"""CASE WHEN {_ORDER}
                 THEN (ca."Delivery Date" <= :m_end)
                 ELSE (ca."Delivery Date" IS NULL OR ca."Delivery Date" <= :m_end) END"""


# ── Route pool + fleet PRECOMPUTE (the forecast step's performance backbone) ───────────────────────────
# The model INSERTs once per (forecast month x sub-fleet) — 185 statements for a 27-month Air Arabia run.
# Building the route pool and the Cirium fleet INSIDE that statement re-scanned forecast.acys_actuals (301k
# operator rows) THREE times and cirium.ciriumaircrafts several more times PER STATEMENT: measured 5.3 s each,
# 974 s total = 94% of the whole request (the `forecast_step_timings` ledger shows the step going 120 s -> 1097 s
# when the tier cascade + the year-robust pool were added). Both are loop-INVARIANT: the pool depends only on
# (sub-fleet, calendar month) and the fleet only on the sub-fleet — the delivery cutoff :m_end is a cheap
# per-month filter over ~180 fleet rows. So both are materialised ONCE into TEMP tables and each per-month
# INSERT becomes a small indexed read of them. Identical output, ~2 orders of magnitude less work.
#
# The pool is a BAG of route rows sampled proportionally by `gen`; a route's WEIGHT (row count) sets its share
# of the forecast month. Tier 1 makes that weight ROBUST ACROSS YEARS so a ONE-OFF burst (a geopolitical /
# wet-lease / incomplete-month spike that appears in only one occurrence of a calendar month) is NOT replayed
# into every forecast year, while a RECURRING seasonal pattern is kept — the product rule "repeat it only if
# it recurs".

# One row per pool member. (tier, k, cm) identifies the pool; `rn` is 1..N WITHIN that pool, which is what
# `gen` cycles through with (global_flight_index % pool_size) + 1.
#   tier 1 -> k = sub-series, cm = calendar month   (the year-robust seasonal pool)
#   tier 2 -> k = sub-series, cm = 0                (raw all-history: robust pool degenerate)
#   tier 3 -> k = master series, cm = 0             (raw all-history: sub-series sparse everywhere)
_POOL_DDL = """
DROP TABLE IF EXISTS fc_pool_tmp;
CREATE TEMP TABLE fc_pool_tmp (
    tier smallint NOT NULL,
    k    text     NOT NULL,
    cm   smallint NOT NULL,
    io text, idd text, ida text, ico text, icd text, ica text,
    cd double precision, ft double precision, adf double precision, ftf double precision,
    rn integer NOT NULL
);
"""

_POOL_INDEX = "CREATE INDEX ON fc_pool_tmp (tier, k, cm, rn)"

_FLEET_DDL = """
DROP TABLE IF EXISTS fc_fleet_tmp;
CREATE TEMP TABLE fc_fleet_tmp (
    sf text NOT NULL, reg text, mseries text, manuf text, usage text,
    av double precision, seats double precision, deliv date,
    ltype text, ldw text, lessor text,
    is_order boolean NOT NULL, is_sup boolean NOT NULL
);
"""

_FLEET_INDEX = "CREATE INDEX ON fc_fleet_tmp (sf)"

# The six route-identity columns, and the NOT-DISTINCT-FROM join on them (NULLs must match NULLs).
_RCOLS = ("io", "idd", "ida", "ico", "icd", "ica")


def _rjoin(a: str, b: str) -> str:
    return " AND ".join(f"{a}.{c} IS NOT DISTINCT FROM {b}.{c}" for c in _RCOLS)


def _pool_tier1_sql(scope: str) -> str:
    """Build the TIER-1 pool for EVERY (sub-series, calendar month) in ONE pass (this used to be re-derived per
    forecast month, 185x). Semantics are unchanged from the per-execute version — TWO robustness rules:
      (A) SPAN GATE — a route must have operated across >= 2 calendar years globally (any month). A route
          confined to a SINGLE calendar year is a one-off burst (the intra-Pakistan KHI-ISB routes, only
          Apr-Jul 2026) and is dropped outright; a route spanning >= 2 years is established and passes.
      (B) MEDIAN with gap-zeros — for a passing route, weight = MEDIAN of its per-YEAR flight counts in that
          calendar month, padding a 0 for every candidate year FROM its first appearance onward that it was
          NOT flown (years before it existed are excluded, so a new sustained route is not penalised for
          pre-existence zeros). A spike year is out-voted by the normal years; a discontinued route decays to 0.
    Each surviving route is expanded into round(typical) bag rows in pseudo-random (md5) order, so `gen` samples
    it in proportion to its typical frequency. `rn` is numbered WITHIN each (sub-series, calendar month) pool,
    exactly as the old per-execute `row_number() OVER (ORDER BY md5(...))` did.
    """
    return f"""
INSERT INTO fc_pool_tmp (tier, k, cm, io, idd, ida, ico, icd, ica, cd, ft, adf, ftf, rn)
WITH base AS (
    SELECT {_KEY_SF} sf,
           extract(month from "Date")::int cm, extract(year from "Date")::int yr,
           "IATA Origin" io, "IATA Destination" idd, "IATA Destination Actual" ida,
           "ICAO Origin" ico, "ICAO Destination" icd, "ICAO Destination Actual" ica,
           "Circle Distance" cd, "Flight Time" ft, "Actual Distance FR" adf, "Flight Time FR" ftf
    FROM forecast.acys_actuals
    WHERE "Operator" = :op AND "Date" IS NOT NULL {scope}
),
occ AS (   -- per (sub-series, calendar month, route, YEAR): how many flights
    SELECT sf, cm, io, idd, ida, ico, icd, ica, yr, count(*) c
    FROM base GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
),
span AS (  -- per (sub-series, route): GLOBAL year span across ALL months — the single-year-burst gate
    SELECT sf, io, idd, ida, ico, icd, ica, min(yr) gy0, max(yr) gy1
    FROM base GROUP BY 1, 2, 3, 4, 5, 6, 7
),
rte AS (   -- per (sub-series, calendar month, route): first-seen year + representative (median) metrics
    SELECT sf, cm, io, idd, ida, ico, icd, ica, min(yr) first_yr,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY cd)  cd,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ft)  ft,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY adf) adf,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ftf) ftf
    FROM base GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
),
cy AS (    -- the candidate years per (sub-series, calendar month)
    SELECT sf, cm, array_agg(DISTINCT yr) yrs FROM base GROUP BY 1, 2
),
grid AS (  -- route x candidate years FROM its first-seen year onward; a GAP year -> 0 (dilutes a one-off)
    SELECT r.sf, r.cm, r.io, r.idd, r.ida, r.ico, r.icd, r.ica, y.yr, coalesce(o.c, 0) c
    FROM rte r
    JOIN span sp ON sp.sf = r.sf AND {_rjoin('sp', 'r')} AND sp.gy1 > sp.gy0
    JOIN cy ON cy.sf = r.sf AND cy.cm = r.cm
    CROSS JOIN LATERAL unnest(cy.yrs) AS y(yr)
    LEFT JOIN occ o ON o.sf = r.sf AND o.cm = r.cm AND o.yr = y.yr AND {_rjoin('o', 'r')}
    WHERE y.yr >= r.first_yr
),
typ AS (   -- robust typical count per route = median of the (gap-padded) per-year counts since first seen
    SELECT sf, cm, io, idd, ida, ico, icd, ica,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY c) tc
    FROM grid GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
)
SELECT 1, t.sf, t.cm, t.io, t.idd, t.ida, t.ico, t.icd, t.ica, r.cd, r.ft, r.adf, r.ftf,
       row_number() OVER (PARTITION BY t.sf, t.cm
                          ORDER BY md5(coalesce(t.io,'') || '>' || coalesce(t.idd,'') || '#' || gs::text))
FROM typ t
JOIN rte r ON r.sf = t.sf AND r.cm = t.cm AND {_rjoin('r', 't')}
CROSS JOIN LATERAL generate_series(1, round(t.tc)::int) gs
"""


def _pool_raw_sql(scope: str, tier: int) -> str:
    """Build the TIER-2 (sub-series) / TIER-3 (master series) fallback pools: the raw all-history bag, one row
    per flight, pseudo-random (md5) order, `rn` numbered within each key's pool. Used only where the robust
    seasonal pool is degenerate (a thin / brand-new sub-fleet) — there is too little history to tell a one-off
    from a recurring route anyway, so the broad all-history network is the safest structure. Only the keys the
    run actually needs are built (:keys), so a fat all-history bag is never materialised for nothing."""
    key = _KEY_SF if tier == 2 else _KEY_MS
    return f"""
INSERT INTO fc_pool_tmp (tier, k, cm, io, idd, ida, ico, icd, ica, cd, ft, adf, ftf, rn)
SELECT {tier}, {key}, 0,
       "IATA Origin", "IATA Destination", "IATA Destination Actual",
       "ICAO Origin", "ICAO Destination", "ICAO Destination Actual",
       "Circle Distance", "Flight Time", "Actual Distance FR", "Flight Time FR",
       row_number() OVER (PARTITION BY {key} ORDER BY md5(id::text))
FROM forecast.acys_actuals
WHERE "Operator" = :op AND "Date" IS NOT NULL AND {key} = ANY(:keys) {scope}
"""


def _fleet_build_sql(scope: str) -> str:
    """Materialise the operator's WHOLE forecast fleet once (all sub-series, no month cutoff). Previously this
    ran inside every per-month INSERT, re-scanning Cirium with correlated NOT EXISTS anti-joins 185 times.

    Dedup keys are (sub-series, identity) — NOT identity alone — which reproduces EXACTLY what the old
    per-execute `WHERE ... = :sf` + `DISTINCT ON (identity)` did: the winner was chosen within one sub-series'
    subset. The `_DELIVERED` (:m_end) test is NOT applied here; instead `is_order` is stored and the cheap
    per-month filter is applied at read time (see _insert_sql), because that is the only month-dependent part.
    `is_sup` marks the carry-forward tails, which the old `sup` CTE fed into `fleet` with NO delivery test at
    all — the flag preserves that exactly.
    """
    return f"""
INSERT INTO fc_fleet_tmp (sf, reg, mseries, manuf, usage, av, seats, deliv, ltype, ldw, lessor,
                          is_order, is_sup)
WITH latest AS ({_LATEST_CTE}),
owned_regs AS (   -- every registration in the operator's OWN Cirium fleet (any sub-series) — never supplemented
    SELECT ca."Registration" reg FROM cirium.ciriumaircrafts ca
    WHERE ca."Operator" = :op AND ca.revision_id IN (SELECT mr FROM latest)
      AND {_ALLOWED} AND {_NOT_DEAD}
),
cirium_fleet AS (   -- one row per (sub-series, airframe); the delivery cutoff is applied per month at read time
    SELECT DISTINCT ON (coalesce(nullif(ca."Aircraft Sub Series",''),'NA'), {_IDENT})
           coalesce(nullif(ca."Aircraft Sub Series",''),'NA') AS sf,
           {_REG_OUT} AS reg,
           ca."Master Series" mseries, ca."Manufacturer" manuf,
           ca."Primary Usage" usage, ca."Indicative Market Value (US$m)" av, ca."Number of Seats" seats,
           ca."Delivery Date" deliv, ca."Lease Type" ltype, ca."Lease Dry / Wet" ldw,
           ca."Operational Lessor" lessor,
           ({_ORDER}) AS is_order, false AS is_sup
    FROM cirium.ciriumaircrafts ca
    WHERE ca."Operator" = :op AND ca.revision_id IN (SELECT mr FROM latest)
      AND {_ALLOWED}
      AND {_NOT_DEAD}
      AND {_HAS_KEY} {scope}
    -- The tie-break is NOT cosmetic: Cirium carries SEVERAL rows for one airframe inside the SAME revision
    -- (A6-ARF twice with two Delivery Dates; A6-ARI as both a delivered aircraft and a leftover order).
    -- Delivered beats order, then newest id — one aircraft, one identity, whole horizon.
    ORDER BY coalesce(nullif(ca."Aircraft Sub Series",''),'NA'), {_IDENT}, {_PREFER_DELIVERED}, ca.id DESC
),
sup AS (   -- carry-forward: tails that OPERATED for the operator in the last actual month but are NOT in the
           -- owned Cirium fleet (sister-airline / wet-lease airframes flying under this brand). Without this
           -- they vanish at the actuals->forecast seam even though they were just flying. Identity is the real
           -- registration; attributes come from the tail's most recent actual flight; they are already
           -- delivered (they just flew), so they fly EVERY forecast month (is_sup bypasses the cutoff). Wet
           -- leases keep ldw='Wet', so the INSERT still zeroes their Agreed Value — only activity is projected.
           -- This set MUST match run_forecast_model's fleet_deliv supplement exactly, or Active Fleet vs rows
           -- disagree.
    SELECT DISTINCT ON (coalesce(nullif(aa."Aircraft Sub Series",''),'NA'), aa."Registration")
           coalesce(nullif(aa."Aircraft Sub Series",''),'NA') AS sf,
           aa."Registration" AS reg,
           aa."Master Series" mseries, aa."Manufacturer" manuf,
           aa."Primary Usage" usage, aa."Agreed Value" av, aa."Total Seats" seats,
           aa."Delivery Date" deliv, aa."Lease Type" ltype, aa."Lease Dry Wet" ldw,
           aa."Operational Lessor" lessor,
           false AS is_order, true AS is_sup
    FROM forecast.acys_actuals aa
    WHERE aa."Operator" = :op AND aa."Date" IS NOT NULL AND aa."Date" >= :sup_since
      AND aa."Registration" NOT IN (SELECT reg FROM owned_regs) {scope}
    ORDER BY coalesce(nullif(aa."Aircraft Sub Series",''),'NA'), aa."Registration", aa."Date" DESC
)
SELECT * FROM cirium_fleet
UNION ALL
SELECT * FROM sup
"""


def _insert_sql() -> str:
    """One (forecast month, sub-fleet) INSERT into acys_forecast. EVERY active fleet aircraft of the sub-fleet
    flies :k flights, each taking a route from the route pool. Both the fleet and the pool were materialised
    ONCE (see _fleet_build_sql / _pool_tier1_sql / _pool_raw_sql), so this statement now only does the
    month-specific work: pick the delivered aircraft, cycle the pool, spread the flights over the month's days.

    The delivery cutoff (`:m_end`) is the only month-dependent fleet rule — nothing retires, future deliveries
    appear once due. `is_sup` tails (carry-forward) bypass it, exactly as the old `sup` CTE did. The pool is
    addressed by (:tier, :pool_key, :pool_cm), the tier having been decided in run_forecast_model.
    """
    return f"""
INSERT INTO forecast.acys_forecast
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "ICAO Origin","ICAO Destination","ICAO Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time",
     "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR",
     "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor")
WITH fleet AS (   -- the sub-fleet's aircraft that are in the fleet at :m_end (an ORDER joins ON its delivery
                  -- month; an in-service tail with no delivery date is already flying; carry-forward always in)
    SELECT reg, mseries, manuf, usage, av, seats, deliv, ltype, ldw, lessor
    FROM fc_fleet_tmp
    WHERE sf = :sf
      AND (is_sup
           OR CASE WHEN is_order THEN deliv <= :m_end
                   ELSE (deliv IS NULL OR deliv <= :m_end) END)
),
routes AS (SELECT io, idd, ida, ico, icd, ica, cd, ft, adf, ftf, rn
           FROM fc_pool_tmp WHERE tier = :tier AND k = :pool_key AND cm = :pool_cm),
nr AS (SELECT count(*)::int c FROM routes),
gen AS (   -- every active aircraft × :k flights. The route is picked by the GLOBAL flight index (0..N*k-1),
           -- NOT by g alone: with `(g-1) % nr.c` EVERY aircraft flew the SAME first k pool rows, so the whole
           -- fleet was pinned onto a handful of routes (one route could get N_aircraft x its early-pool count
           -- -> hundreds of flights/month on ONE route). Cycling the GLOBAL index spreads the N*k flights across
           -- the pool proportionally to each route's template frequency, and across aircraft.
    SELECT fg.reg, fg.mseries, fg.manuf, fg.usage, fg.av, fg.seats, fg.deliv, fg.ltype, fg.ldw, fg.lessor,
           r.io, r.idd, r.ida, r.ico, r.icd, r.ica, r.cd, r.ft, r.adf, r.ftf,
           fg.gi AS rn
    FROM (SELECT f.*, (row_number() OVER () - 1) AS gi     -- global 0-based flight index over fleet x :k
          FROM fleet f CROSS JOIN generate_series(1, :k) g) fg
    CROSS JOIN nr
    JOIN routes r ON nr.c > 0 AND r.rn = (fg.gi % nr.c) + 1
),
s AS (   -- SPREAD the flights across the month's days (current month: from today) so the DAY-PRECISE
         -- Contract Year splits the anchor month exactly like the actuals (Jul→Jul window, not Aug→Jul)
    SELECT gen.*, make_date(:year, :month, (:start_day + (rn % :day_span))::int) AS fdate FROM gen
)
SELECT
    s.reg, :period, s.fdate, NULL, NULL,
    s.io, s.idd, s.ida, s.ico, s.icd, s.ica,
    :op, s.mseries, s.manuf, :sf, s.usage,
    'CY' || (extract(year from s.fdate)::int - CASE
        WHEN extract(month from s.fdate)::int < :anchor_month
          OR (extract(month from s.fdate)::int = :anchor_month
              AND extract(day from s.fdate)::int <= :anchor_day)
        THEN 1 ELSE 0 END)::text,
    s.cd, s.ft,
    CASE WHEN s.ldw = 'Wet' THEN 0 ELSE s.av END,
    s.seats, s.seats * CAST(:pax_factor AS double precision), s.adf, s.ftf,
    s.deliv, s.ltype, s.ldw, s.lessor
FROM s
"""


def _contract_year(d: date, anchor: date) -> str:
    # DAY-PRECISE window ending ON the anchor day: (anchor, anchor+1y], labelled by its START year.
    # A forecast month stamped on day 1 (< anchor day) lands in the CY that the anchor day closes.
    before = (d.month, d.day) <= (anchor.month, anchor.day)
    return f"CY{d.year - (1 if before else 0)}"


async def run_forecast_model(*, session, operator: str, as_of: date,
                             scope_where: str | None = None, scope_params: dict | None = None,
                             on_progress=None) -> dict:
    """Populate forecast.acys_forecast for `operator`, restricted to the request scope. Returns counts.
    `on_progress(fraction)` (async, optional) is called after each forecast month, so a single-operator
    run still shows movement across the step instead of freezing at the band floor."""
    scope_sql = f"AND ({scope_where})" if scope_where else ""
    sp = dict(scope_params or {})

    # The forecast pivots on the REQUEST date `as_of` — that is the "today" of the export. Facts are
    # everything BEFORE it (the panel fetches the flight history up to as_of-1 and assembles it with
    # `first_seen < as_of`); the forecast runs from as_of forward to as_of + FORECAST_HORIZON_YEARS. We do
    # NOT shift the anchor to the last available actual: the request date decides where the forecast starts,
    # and any hole up to as_of-1 is closed by LOADING the missing facts from the flight source, never by
    # moving the forecast start earlier. (If those facts are genuinely unavailable — e.g. as_of is a future
    # date the flight source has not reached — a gap is the honest result, not something to paper over.)
    #
    # Keep the ACTUALS' Contract Year on the SAME anchor (as_of) as the forecast, so both halves of the
    # report bucket into identical contract years and powerbi.z_dates_acys (anchored on the first forecast
    # date = as_of) agrees. In a normal run the panel already assembled the scope's actuals with the as_of
    # anchor, so this is a no-op; it also realigns any actuals left over from a prior request at a different
    # as_of. Same day-precise rule as _insert_sql: CY = year-1 iff (month,day) <= (as_of.month, as_of.day).
    await session.execute(text(
        'UPDATE forecast.acys_actuals SET "Contract Year" = '
        '\'CY\' || (extract(year from "Date")::int - CASE WHEN '
        '(extract(month from "Date")::int, extract(day from "Date")::int) <= (:am, :ad) '
        'THEN 1 ELSE 0 END)::text '
        f'WHERE "Date" IS NOT NULL {scope_sql}'),
        {"am": as_of.month, "ad": as_of.day, "op": operator, **sp})

    base_params = {"op": operator, "start": HISTORY_START, **sp}

    async def _hist(key: str):
        return [dict(r._mapping) for r in (await session.execute(
            text(_MONTHLY_SQL.format(scope=scope_sql, key=key)), base_params)).all()]

    # Fit the history TWICE — once at the forecast's own grain (Aircraft Sub Series) and once at the broader
    # Master Series. A sub-series the operator has never flown has no fit and no route pool of its own, and
    # falls back to its master series (below). _plan is grain-agnostic: it just keys on whatever it is given.
    rows_sf = await _hist(_KEY_SF)
    if not rows_sf:
        logger.info("forecast_model: no acys_actuals flights for %s", operator)
        return {"forecast_rows": 0, "months": 0, "frontier": None}
    rows_ms = await _hist(_KEY_MS)

    # forecast START = the day AFTER the last fact, so there is NO gap between facts and forecast; the CY
    # stays anchored on the REQUEST date `as_of` (which may be LATER — the CY is simply cut on the as_of day).
    # The assemble step caps facts at `first_seen < as_of`, so last_fact <= as_of-1 and fc_start <= as_of;
    # the forecast then runs fc_start .. (as_of + FORECAST_HORIZON_YEARS).
    last_fact = (await session.execute(text(
        'SELECT max("Date") FROM forecast.acys_actuals '
        f'WHERE "Operator" = :op AND "Date" IS NOT NULL {scope_sql}'),
        {"op": operator, **sp})).scalar()
    fc_start = (last_fact + timedelta(days=1)) if last_fact is not None else as_of

    # Fleet carry-forward window: the first day of the LAST ACTUAL month. Tails that operated for the operator
    # inside this window but are NOT in the owned Cirium fleet (sister-airline / wet-lease) are supplemented
    # into the forecast fleet so they do not vanish at the seam (see _insert_sql's `sup` CTE).
    sup_since = last_fact.replace(day=1) if last_fact is not None else fc_start

    frontier, fmonths, plan_sf, fits_sf = _plan(rows_sf, fc_start, as_of)
    fr_ms, fm_ms, plan_ms, fits_ms = _plan(rows_ms, fc_start, as_of)
    fmonths = sorted(set(fmonths) | set(fm_ms))

    # Fleet delivery dates per sub-fleet (latest revision) — the active-fleet count and the coefficients.
    # MUST dedupe on exactly the same identity as _insert_sql's `fleet` CTE. It used to append one entry per
    # CIRIUM ROW, so all 113 placeholder-'A6-' order rows counted as 113 aircraft here while the INSERT
    # collapsed them to one — "Active Fleet"/"Forecast Flights" in the coefficients table then disagreed with
    # the rows actually generated. One key, one aircraft, both places.
    fleet_deliv, sf_master = defaultdict(list), {}
    for r in (await session.execute(text(
            f'WITH latest AS ({_LATEST_CTE}), '
            f'f AS (SELECT DISTINCT ON ({_IDENT}) '
            '          coalesce(nullif(ca."Aircraft Sub Series", \'\'), \'NA\') sf, '
            '          coalesce(nullif(ca."Master Series", \'\'), \'NA\') ms, ca."Delivery Date" deliv '
            '      FROM cirium.ciriumaircrafts ca '
            '      WHERE ca."Operator" = :op AND ca.revision_id IN (SELECT mr FROM latest) '
            f'        AND {_ALLOWED} AND {_NOT_DEAD} AND {_HAS_KEY} {scope_sql} '
            f'      ORDER BY {_IDENT}, {_PREFER_DELIVERED}, ca.id DESC) '
            'SELECT sf, ms, deliv FROM f'),
            {"op": operator, **sp})).all():
        fleet_deliv[r[0]].append(r[2])
        sf_master.setdefault(r[0], r[1])

    # Supplement fleet_deliv with the carry-forward tails (sister-airline / wet-lease that operated in the last
    # actual month, not owned in Cirium). MUST mirror _insert_sql's `sup` CTE exactly so the Active Fleet count
    # equals the rows actually generated. Their delivery dates are historical, so they count active every month.
    for r in (await session.execute(text(
            f'WITH latest AS ({_LATEST_CTE}), '
            'owned_regs AS (SELECT ca."Registration" reg FROM cirium.ciriumaircrafts ca '
            '               WHERE ca."Operator" = :op AND ca.revision_id IN (SELECT mr FROM latest) '
            f'                 AND {_ALLOWED} AND {_NOT_DEAD}) '
            'SELECT DISTINCT ON (aa."Registration") '
            '       coalesce(nullif(aa."Aircraft Sub Series", \'\'), \'NA\') sf, '
            '       coalesce(nullif(aa."Master Series", \'\'), \'NA\') ms, aa."Delivery Date" deliv '
            'FROM forecast.acys_actuals aa '
            'WHERE aa."Operator" = :op AND aa."Date" IS NOT NULL AND aa."Date" >= :sup_since '
            f'  AND aa."Registration" NOT IN (SELECT reg FROM owned_regs) {scope_sql} '
            'ORDER BY aa."Registration", aa."Date" DESC'),
            {"op": operator, "sup_since": sup_since, **sp})).all():
        fleet_deliv[r[0]].append(r[2])
        sf_master.setdefault(r[0], r[1])

    # ── PRECOMPUTE (see the _POOL_DDL / _fleet_build_sql block above) ──────────────────────────────────
    # The route pool and the fleet are LOOP-INVARIANT, but used to be rebuilt inside each of the ~185 per-month
    # INSERTs (measured: 2,906 ms per INSERT, of which 2,878 ms was this re-derivation and only 28 ms real work).
    # Build them ONCE here. TEMP tables live for the connection, survive the commits below, and are dropped at
    # the end; the DROP IF EXISTS guards a pooled connection that still carries them from an earlier run.
    for stmt in _POOL_DDL.strip().split(";"):
        if stmt.strip():
            await session.execute(text(stmt))
    await session.execute(text(_pool_tier1_sql(scope_sql)), {"op": operator, **sp})
    await session.execute(text(_POOL_INDEX))

    # Route-pool richness, to pick the tier per (sub-fleet, calendar month):
    #  * the TIER-1 count is read straight OFF the built pool — it is by definition "how many distinct routes
    #    does this pool actually contain", so the decision and the pool can never disagree and tier 1 is never
    #    chosen with a degenerate pool. (This also replaces a separate ~2.5 s occ/span/cy re-scan that only
    #    APPROXIMATED the survivor count with a present_yrs*2 >= n_years proxy.)
    #  * sf_all_routes[sf] = distinct routes across ALL history (the tier-2 fallback's richness).
    # Below _MIN_ROUTE_POOL a pool is degenerate and we broaden it (tier 2, then master all-history tier 3).
    sf_robust_routes, sf_all_routes = {}, {}
    for r in (await session.execute(text(
            'SELECT k, cm, count(DISTINCT (io, idd)) n FROM fc_pool_tmp WHERE tier = 1 GROUP BY 1, 2'))).all():
        sf_robust_routes[(r[0], r[1])] = r[2]
    for r in (await session.execute(text(
            f'SELECT {_KEY_SF} sf, count(DISTINCT ("IATA Origin","IATA Destination")) n '
            'FROM forecast.acys_actuals '
            f'WHERE "Operator" = :op AND "Date" IS NOT NULL {scope_sql} GROUP BY 1'),
            {"op": operator, **sp})).all():
        sf_all_routes[r[0]] = r[1]

    # Decide the pool for every (sub-fleet, CALENDAR month) the loop can ask for — the tier depends on nothing
    # else, so this hoists the decision out of the 185-iteration loop and tells us which tier-2 / tier-3 pools
    # are actually needed. Only those get built (a raw all-history bag is never materialised for nothing).
    pool_choice = {}     # (sf, cal_month) -> (tier, pool_key, pool_cm)
    for sf_k in sorted(fleet_deliv):
        ms_k = sf_master.get(sf_k) or "NA"
        for cmn in sorted({fm.month for fm in fmonths}):
            if sf_robust_routes.get((sf_k, cmn), 0) >= _MIN_ROUTE_POOL:
                pool_choice[(sf_k, cmn)] = (1, sf_k, cmn)
            elif sf_all_routes.get(sf_k, 0) >= _MIN_ROUTE_POOL:
                pool_choice[(sf_k, cmn)] = (2, sf_k, 0)
            else:
                pool_choice[(sf_k, cmn)] = (3, ms_k, 0)
    for tier_n in (2, 3):
        keys = sorted({key for (t, key, _) in pool_choice.values() if t == tier_n})
        if keys:
            await session.execute(text(_pool_raw_sql(scope_sql, tier_n)),
                                  {"op": operator, "keys": keys, **sp})

    for stmt in _FLEET_DDL.strip().split(";"):
        if stmt.strip():
            await session.execute(text(stmt))
    await session.execute(text(_fleet_build_sql(scope_sql)),
                          {"op": operator, "sup_since": sup_since, **sp})
    await session.execute(text(_FLEET_INDEX))
    await session.commit()

    sql = _insert_sql()
    total, coeff = 0, []
    nfm = max(1, len(fmonths))
    # Iterate the FLEET, not the history: a sub-series can be in the fleet with no history of its own (a new
    # type arriving), and it must still be forecast — off its master series' history.
    for fi, m in enumerate(fmonths):
        m_end = _add_months(m, 1) - timedelta(days=1)          # last day of the forecast month (delivery cutoff)
        start_day, end_day, day_span, dim = _month_span(m, fc_start, as_of)
        prorate = day_span / dim
        for sf in sorted(fleet_deliv):
            active = sum(1 for d in fleet_deliv.get(sf, []) if d is None or d <= m_end)
            if active == 0:
                continue   # nobody of this sub-series is in the fleet yet this month
            ms = sf_master.get(sf) or "NA"
            if sf in plan_sf.get(m, {}):                       # own history
                hist_key, by_master = sf, False
                tm, k = plan_sf[m][sf]
                level, seas, base = fits_sf[sf]
                fr = frontier
            elif ms in plan_ms.get(m, {}):                     # fallback: the master series' history
                hist_key, by_master = ms, True
                tm, k = plan_ms[m][ms]
                level, seas, base = fits_ms[ms]
                fr = fr_ms
            else:
                continue   # neither the sub-series nor its master series has any history -> nothing to fit
            # Route pool: already decided per (sub-fleet, calendar month) and materialised in fc_pool_tmp above,
            # so the statement just addresses it by (tier, key, calendar month) — no re-derivation.
            pool_tier, pool_key, pool_cm = pool_choice[(sf, m.month)]
            params = {"op": operator, "sf": sf,
                      "tier": pool_tier, "pool_key": pool_key, "pool_cm": pool_cm,
                      "m_end": m_end, "k": int(k),
                      "period": m.strftime("%m-%Y"), "year": m.year, "month": m.month,
                      "start_day": start_day, "day_span": day_span,
                      "anchor_month": as_of.month, "anchor_day": as_of.day,
                      "pax_factor": FORECAST_PAX_LOAD_FACTOR}
            res = await session.execute(text(sql), params)
            total += res.rowcount or 0
            coeff.append({"op": operator, "ms": sf_master.get(sf), "sf": sf, "hk": hist_key,
                          "fm": m, "cm": m.month,
                          "fr": fr, "lvl": float(level), "base": float(base), "par": float(level / base),
                          "seas": float(seas[m.month]), "pro": float(prorate), "act": active,
                          "k": int(k), "nhat": int(k) * active, "tm": tm})
        if on_progress is not None:
            try:
                await on_progress((fi + 1) / nfm)
            except Exception:
                pass
    await session.commit()

    # coefficients table (per-operator refresh) — powers "how the forecast is computed" charts
    try:
        await session.execute(
            text('DELETE FROM forecast.acys_forecast_coefficients WHERE "Operator" = :op'), {"op": operator})
        if coeff:
            await session.execute(text(
                'INSERT INTO forecast.acys_forecast_coefficients '
                '("Operator","Master Series","Aircraft Sub Series","History Key","Forecast Month",'
                ' "Calendar Month",'
                ' "Frontier","Level","Base Fleet","Per Aircraft Rate","Seasonal Factor","Proration",'
                ' "Active Fleet","Flights Per Aircraft","Forecast Flights","Template Month") '
                'VALUES (:op,:ms,:sf,:hk,:fm,:cm,:fr,:lvl,:base,:par,:seas,:pro,:act,:k,:nhat,:tm)'), coeff)
        await session.commit()
    except Exception as e:
        logger.warning("forecast coefficients write failed for %s: %s", operator, e)

    # Drop the precompute scratch: the session's connection goes back to the pool, and a stale fc_pool_tmp /
    # fc_fleet_tmp would otherwise be visible to whatever runs on it next (the DROP IF EXISTS at build time
    # already guards correctness — this just frees the temp space promptly).
    try:
        await session.execute(text("DROP TABLE IF EXISTS fc_pool_tmp"))
        await session.execute(text("DROP TABLE IF EXISTS fc_fleet_tmp"))
        await session.commit()
    except Exception as e:
        logger.warning("forecast temp cleanup failed for %s: %s", operator, e)

    logger.info("forecast_model: %s → %d forecast rows over %d months (frontier %s)",
                operator, total, len(fmonths), frontier)
    return {"forecast_rows": total, "months": len(fmonths),
            "frontier": frontier.isoformat() if frontier else None}
