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
GROWTH_CAP = 4.0      # cap the forward-fleet growth multiplier (guard against bad delivery data)
LIVE_WINDOW_MONTHS = 3  # an aircraft is forecastable only if it flew within this many months up to the
                        # frontier; a longer-idle tail is treated as retired and NOT projected forward.

_MONTHLY_SQL = """
SELECT coalesce(nullif("Master Series",''),'NA') AS sf,
       date_trunc('month',"Date")::date          AS mon,
       count(*)                                   AS flights,
       count(DISTINCT "Registration")             AS tails
FROM forecast.acys_actuals
WHERE "Operator" = :op AND "Date" IS NOT NULL AND "Date" >= :start {scope}
GROUP BY 1, 2
"""

# forward fleet: future-delivery STUB rows (Date NULL) — aircraft arriving per (sub-fleet, delivery month)
_FUTURE_SQL = """
SELECT coalesce(nullif("Master Series",''),'NA')      AS sf,
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


def _plan(rows, as_of: date):
    """Fit level×seasonal per sub-fleet on months ≤ the coverage frontier, then plan the forecast months
    from the CURRENT month (as_of) to the horizon. Returns (frontier, forecast_months, plan) where
    plan[m] = {sf: (template_month, k)} and k = PER-AIRCRAFT flights the sub-fleet flies in month m =
    (level / base_fleet) × seasonal[cal(m)] × proration. The ACTIVE fleet per month is resolved in SQL from
    the latest Cirium revision (delivery ≤ month) — so every in-fleet aircraft flies every month, the volume
    grows with deliveries, and nothing retires unless Cirium drops it. The current month is prorated from
    today (actuals cover up to yesterday)."""
    by_sf = defaultdict(list)      # sf -> sorted [(mon, flights, tails)]
    by_mon = defaultdict(int)
    for r in rows:
        by_sf[r["sf"]].append((r["mon"], r["flights"], r["tails"]))
        by_mon[r["mon"]] += r["flights"]
    for sf in by_sf:
        by_sf[sf].sort()
    months = sorted(by_mon)
    if not months:
        return None, [], {}

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

    # Forecast from the CURRENT month (prorated from today) to the horizon = anchor month HORIZON years out.
    # Horizon rows are stamped day 1 (<= anchor day) -> land in CY(as_of.year+HORIZON-1), no stray CY beyond.
    cur_month = date(as_of.year, as_of.month, 1)
    end = date(as_of.year + FORECAST_HORIZON_YEARS, as_of.month, 1)
    fmonths, plan = [], {}
    m = cur_month
    while m <= end:
        prorate = 1.0
        if m == cur_month:                          # current month: forecast only the remaining days
            dim = _days_in_month(m)
            prorate = max(0.0, dim - as_of.day + 1) / dim
        sfp = {}
        for sf, (level, seas, base) in fits.items():
            k = round(level * seas[m.month] / base * prorate)       # PER-AIRCRAFT flights this month
            if k <= 0:
                continue
            same = [mn for mn, fl in sf_hist[sf] if mn.month == m.month]
            tm = same[-1] if same else sf_hist[sf][-1][0]           # route template = type's typical network
            sfp[sf] = (tm, int(k))
        if sfp:
            plan[m] = sfp
            fmonths.append(m)
        m = _add_months(m, 1)
    return frontier, fmonths, plan


def _insert_sql(scope: str) -> str:
    """One (forecast month, sub-fleet) INSERT into acys_forecast. EVERY active Cirium-fleet aircraft of the
    sub-fleet (latest revision, delivered ≤ :m_end — nothing retires; future deliveries appear once due)
    flies :k flights, each taking a route from the sub-fleet's typical route pool (:template_month's flights
    = the type's usual network for THIS operator, so no route the operator never flew). Aircraft attributes
    (value / seats / lease / delivery) come from Cirium; the merge step later projects the Agreed Value."""
    return f"""
INSERT INTO forecast.acys_forecast
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "ICAO Origin","ICAO Destination","ICAO Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time",
     "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR",
     "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor")
WITH latest AS (SELECT max(revision_id) mr FROM cirium.ciriumaircrafts WHERE "Operator" = :op),
fleet AS (   -- active aircraft of this sub-fleet at :m_end (delivered by then; future deliveries included)
    SELECT DISTINCT ON (ca."Registration")
           ca."Registration" reg, ca."Manufacturer" manuf, ca."Aircraft Sub Series" subs,
           ca."Primary Usage" usage, ca."Indicative Market Value (US$m)" av, ca."Number of Seats" seats,
           ca."Delivery Date" deliv, ca."Lease Type" ltype, ca."Lease Dry / Wet" ldw,
           ca."Operational Lessor" lessor
    FROM cirium.ciriumaircrafts ca
    WHERE ca."Operator" = :op AND ca.revision_id = (SELECT mr FROM latest)
      AND coalesce(nullif(ca."Master Series",''),'NA') = :sf
      AND ca."Registration" IS NOT NULL AND ca."Registration" <> ''
      AND (ca."Delivery Date" IS NULL OR ca."Delivery Date" <= :m_end) {scope}
    ORDER BY ca."Registration"
),
routes AS (   -- the sub-fleet's typical route pool = a template month's real flights for this operator
    SELECT "IATA Origin" io, "IATA Destination" idd, "IATA Destination Actual" ida,
           "ICAO Origin" ico, "ICAO Destination" icd, "ICAO Destination Actual" ica,
           "Circle Distance" cd, "Flight Time" ft, "Actual Distance FR" adf, "Flight Time FR" ftf,
           row_number() OVER (ORDER BY id) rn
    FROM forecast.acys_actuals
    WHERE "Operator" = :op AND "Date" IS NOT NULL
      AND coalesce(nullif("Master Series",''),'NA') = :sf
      AND date_trunc('month',"Date")::date = :template_month {scope}
),
nr AS (SELECT count(*)::int c FROM routes),
gen AS (   -- every active aircraft × :k flights, cycling the route pool
    SELECT f.reg, f.manuf, f.subs, f.usage, f.av, f.seats, f.deliv, f.ltype, f.ldw, f.lessor,
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
    :op, :sf, s.manuf, s.subs, s.usage,
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
    base_params = {"op": operator, "start": HISTORY_START, **sp}
    rows = [dict(r._mapping) for r in (await session.execute(
        text(_MONTHLY_SQL.format(scope=scope_sql)), base_params)).all()]
    if not rows:
        logger.info("forecast_model: no acys_actuals flights for %s", operator)
        return {"forecast_rows": 0, "months": 0, "frontier": None}

    frontier, fmonths, plan = _plan(rows, as_of)
    sql = _insert_sql(scope_sql)
    total = 0
    nfm = max(1, len(fmonths))
    cur_month = date(as_of.year, as_of.month, 1)
    horizon_month = date(as_of.year + FORECAST_HORIZON_YEARS, as_of.month, 1)
    for fi, m in enumerate(fmonths):
        m_end = _add_months(m, 1) - timedelta(days=1)          # last day of the forecast month (delivery cutoff)
        start_day = as_of.day if m == cur_month else 1         # current month starts TODAY; else the 1st
        end_day = as_of.day if m == horizon_month else _days_in_month(m)  # last month stops at as_of+HORIZON day
        day_span = max(1, end_day - start_day + 1)             # flights are spread over these days of the month
        for sf, (tm, k) in plan[m].items():
            params = {"op": operator, "sf": sf, "template_month": tm, "m_end": m_end, "k": int(k),
                      "period": m.strftime("%m-%Y"), "year": m.year, "month": m.month,
                      "start_day": start_day, "day_span": day_span,
                      "anchor_month": as_of.month, "anchor_day": as_of.day,
                      "pax_factor": FORECAST_PAX_LOAD_FACTOR, **sp}
            res = await session.execute(text(sql), params)
            total += res.rowcount or 0
        if on_progress is not None:
            try:
                await on_progress((fi + 1) / nfm)
            except Exception:
                pass
    await session.commit()
    logger.info("forecast_model: %s → %d forecast rows over %d months (frontier %s)",
                operator, total, len(fmonths), frontier)
    return {"forecast_rows": total, "months": len(fmonths),
            "frontier": frontier.isoformat() if frontier else None}
