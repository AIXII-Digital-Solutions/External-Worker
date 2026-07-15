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
growth_sf(m) = (base_fleet_sf + delivered_by_m_sf) / base_fleet_sf (capped). Template = the sub-fleet's
latest same-calendar-month occurrence ≤ frontier (else its latest active month); scale = flights_hat /
template_flights, replicated floor(scale)×rows + EXACTLY round(frac×template_count) extra (unbiased
row_number split), re-stamped to month m. Window: history HISTORY_START → coverage frontier; forecast
frontier+1 → as_of + FORECAST_HORIZON_YEARS. Signed v1 limits: no calendar trend beyond fleet growth;
low route-structure confidence for thin template cells; a brand-new sub-fleet with deliveries but no prior
flights has no level and is not forecast.
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
SEAS_K = 6.0          # seasonal-factor shrinkage toward 1.0 by month support
FRONTIER_FRAC = 0.6   # a recent month is "complete" if its flights ≥ this × the trailing-window median
FRONTIER_WINDOW = 9   # trailing months the frontier threshold is measured against
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


def _fit(series):
    """series = sorted [(cal_month, flights)] (≤ frontier) → (level, seasonal[1..12])."""
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
    return st.median(des[-LEVEL_L:]), seas


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
        level, seas = _fit([(mn.month, fl) for mn, fl, _ in s_tr])
        base = st.median([tl for _, _, tl in s_tr[-LEVEL_L:]]) or 1   # typical flown-tail count for the sub-fleet
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


def _insert_sql(scope: str, tier: int) -> str:
    """One (forecast month, sub-fleet) INSERT into acys_forecast. EVERY active Cirium-fleet aircraft of the
    sub-fleet (latest revision, delivered ≤ :m_end — nothing retires; future deliveries appear once due)
    flies :k flights, each taking a route from the route pool. Aircraft attributes (value / seats / lease /
    delivery) come from Cirium; the merge step later projects the Agreed Value.

    The FLEET is always the target sub-series (:sf). The ROUTE POOL is chosen by `tier` (decided in
    run_forecast_model from how many DISTINCT routes each source has, so a degenerate 1-route template month
    cannot pin an aircraft's whole month onto one long-haul route — 36x Toulouse-Sharjah = 226 h/mo):
      tier 1 — :sf in the seasonal :template_month  (the normal, seasonally-matched pool)
      tier 2 — :sf across ALL history               (template month too sparse: <  _MIN_ROUTE_POOL routes)
      tier 3 — :ms_key (master series) ALL history  (the sub-series itself is too sparse everywhere)
    """
    if tier == 1:
        route_where = f'AND {_KEY_SF} = :sf AND date_trunc(\'month\',"Date")::date = :template_month'
    elif tier == 2:
        route_where = f'AND {_KEY_SF} = :sf'
    else:
        route_where = f'AND {_KEY_MS} = :ms_key'
    return f"""
INSERT INTO forecast.acys_forecast
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "ICAO Origin","ICAO Destination","ICAO Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time",
     "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR",
     "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor")
WITH latest AS ({_LATEST_CTE}),
owned_regs AS (   -- every registration in the operator's OWN Cirium fleet (any sub-series) — never supplemented
    SELECT ca."Registration" reg FROM cirium.ciriumaircrafts ca
    WHERE ca."Operator" = :op AND ca.revision_id IN (SELECT mr FROM latest)
      AND {_ALLOWED} AND {_NOT_DEAD}
),
cirium_fleet AS (   -- active aircraft of this sub-fleet at :m_end (delivered by then; future deliveries included)
    SELECT DISTINCT ON ({_IDENT})
           {_REG_OUT} AS reg,
           ca."Master Series" mseries, ca."Manufacturer" manuf,
           ca."Primary Usage" usage, ca."Indicative Market Value (US$m)" av, ca."Number of Seats" seats,
           ca."Delivery Date" deliv, ca."Lease Type" ltype, ca."Lease Dry / Wet" ldw,
           ca."Operational Lessor" lessor
    FROM cirium.ciriumaircrafts ca
    WHERE ca."Operator" = :op AND ca.revision_id IN (SELECT mr FROM latest)
      AND coalesce(nullif(ca."Aircraft Sub Series",''),'NA') = :sf
      AND {_ALLOWED}
      AND {_NOT_DEAD}
      AND {_HAS_KEY}
      AND {_DELIVERED} {scope}
    -- The tie-break is NOT cosmetic: Cirium carries SEVERAL rows for one airframe inside the SAME revision
    -- (A6-ARF twice with two Delivery Dates; A6-ARI as both a delivered aircraft and a leftover order).
    -- With no tie-break the DISTINCT ON pick is arbitrary, and since this query re-runs PER forecast month
    -- the same aircraft could resolve to a different row (different Delivery Date / value / seats) from
    -- month to month. Delivered beats order, then newest id — one aircraft, one identity, whole horizon.
    ORDER BY {_IDENT}, {_PREFER_DELIVERED}, ca.id DESC
),
sup AS (   -- carry-forward: tails that OPERATED for :op in the last actual month but are NOT in the owned
           -- Cirium fleet (sister-airline / wet-lease airframes flying under this brand). Without this they
           -- vanish at the actuals->forecast seam even though they were just flying. Identity is the real
           -- registration; attributes come from the tail's most recent actual flight; they are already
           -- delivered (they just flew), so they fly EVERY forecast month. Wet leases keep ldw='Wet', so the
           -- INSERT still zeroes their Agreed Value — only their flight activity is projected. This set MUST
           -- match run_forecast_model's fleet_deliv supplement exactly, or Active Fleet vs rows disagree.
    SELECT DISTINCT ON (aa."Registration")
           aa."Registration" AS reg,
           aa."Master Series" mseries, aa."Manufacturer" manuf,
           aa."Primary Usage" usage, aa."Agreed Value" av, aa."Total Seats" seats,
           aa."Delivery Date" deliv, aa."Lease Type" ltype, aa."Lease Dry Wet" ldw,
           aa."Operational Lessor" lessor
    FROM forecast.acys_actuals aa
    WHERE aa."Operator" = :op AND aa."Date" IS NOT NULL AND aa."Date" >= :sup_since
      AND coalesce(nullif(aa."Aircraft Sub Series",''),'NA') = :sf
      AND aa."Registration" NOT IN (SELECT reg FROM owned_regs) {scope}
    ORDER BY aa."Registration", aa."Date" DESC
),
fleet AS (
    SELECT * FROM cirium_fleet
    UNION ALL
    SELECT * FROM sup
),
routes AS (   -- route pool for this (month, sub-fleet), scoped by the tier chosen from pool richness (above)
    SELECT "IATA Origin" io, "IATA Destination" idd, "IATA Destination Actual" ida,
           "ICAO Origin" ico, "ICAO Destination" icd, "ICAO Destination Actual" ica,
           "Circle Distance" cd, "Flight Time" ft, "Actual Distance FR" adf, "Flight Time FR" ftf,
           row_number() OVER (ORDER BY id) rn
    FROM forecast.acys_actuals
    WHERE "Operator" = :op AND "Date" IS NOT NULL
      {route_where} {scope}
),
nr AS (SELECT count(*)::int c FROM routes),
gen AS (   -- every active aircraft × :k flights, cycling the route pool
    SELECT f.reg, f.mseries, f.manuf, f.usage, f.av, f.seats, f.deliv, f.ltype, f.ldw, f.lessor,
           r.io, r.idd, r.ida, r.ico, r.icd, r.ica, r.cd, r.ft, r.adf, r.ftf,
           (row_number() OVER () - 1) AS rn
    FROM fleet f
    CROSS JOIN generate_series(1, :k) g          -- :k flights per aircraft this month
    CROSS JOIN nr
    JOIN routes r ON nr.c > 0 AND r.rn = ((g - 1) % nr.c) + 1   -- cycle the route pool
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

    # Route-pool richness, to pick the route-pool tier per (month, sub-fleet) in the loop (see _insert_sql):
    # distinct routes for each sub-series BY seasonal month (tier 1) and across ALL history (tier 2). A pool
    # below _MIN_ROUTE_POOL distinct routes is degenerate and we broaden it; the master all-history pool
    # (tier 3) is the final fallback and is assumed rich.
    sf_seasonal_routes, sf_all_routes = defaultdict(dict), {}
    for r in (await session.execute(text(
            f'SELECT {_KEY_SF} sf, date_trunc(\'month\',"Date")::date m, '
            '       count(DISTINCT ("IATA Origin","IATA Destination")) n '
            'FROM forecast.acys_actuals '
            f'WHERE "Operator" = :op AND "Date" IS NOT NULL {scope_sql} GROUP BY 1, 2'),
            {"op": operator, **sp})).all():
        sf_seasonal_routes[r[0]][r[1]] = r[2]
    for r in (await session.execute(text(
            f'SELECT {_KEY_SF} sf, count(DISTINCT ("IATA Origin","IATA Destination")) n '
            'FROM forecast.acys_actuals '
            f'WHERE "Operator" = :op AND "Date" IS NOT NULL {scope_sql} GROUP BY 1'),
            {"op": operator, **sp})).all():
        sf_all_routes[r[0]] = r[1]

    sql = {t: _insert_sql(scope_sql, tier=t) for t in (1, 2, 3)}
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
            # Route-pool tier: prefer the sub-series' SEASONAL pool, but broaden if it is degenerate so an
            # aircraft is not pinned onto one long-haul route for a whole month (see _insert_sql / _MIN_ROUTE_POOL).
            if sf_seasonal_routes.get(sf, {}).get(tm, 0) >= _MIN_ROUTE_POOL:
                pool_tier = 1
            elif sf_all_routes.get(sf, 0) >= _MIN_ROUTE_POOL:
                pool_tier = 2
            else:
                pool_tier = 3
            params = {"op": operator, "sf": sf, "ms_key": ms, "hist_key": hist_key, "template_month": tm,
                      "m_end": m_end, "k": int(k), "sup_since": sup_since,
                      "period": m.strftime("%m-%Y"), "year": m.year, "month": m.month,
                      "start_day": start_day, "day_span": day_span,
                      "anchor_month": as_of.month, "anchor_day": as_of.day,
                      "pax_factor": FORECAST_PAX_LOAD_FACTOR, **sp}
            res = await session.execute(text(sql[pool_tier]), params)
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

    logger.info("forecast_model: %s → %d forecast rows over %d months (frontier %s)",
                operator, total, len(fmonths), frontier)
    return {"forecast_rows": total, "months": len(fmonths),
            "frontier": frontier.isoformat() if frontier else None}
