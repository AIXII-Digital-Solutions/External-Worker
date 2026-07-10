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
from datetime import date

from sqlalchemy import text

from Config import setup_logger
from settings import FORECAST_HORIZON_YEARS

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


def _plan(rows, future, as_of: date):
    """Forecast months + per (forecast_month, sub-fleet) (template_month, scale). Anchor + template use only
    months ≤ the coverage frontier; the forward fleet (future deliveries) grows the per-sub-fleet volume."""
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

    # future deliveries per sub-fleet (sorted by delivery month), for months after the frontier
    deliv = defaultdict(list)
    for r in future:
        if r["deliv"] and r["deliv"] > frontier:
            deliv[r["sf"]].append((r["deliv"], r["n"]))
    for sf in deliv:
        deliv[sf].sort()

    fits, sf_hist, base_fleet = {}, {}, {}
    for sf, s in by_sf.items():
        s_tr = [(mn, fl, tl) for mn, fl, tl in s if mn <= frontier and fl]
        if not s_tr:
            continue
        fits[sf] = _fit([(mn.month, fl) for mn, fl, _ in s_tr])
        sf_hist[sf] = [(mn, fl) for mn, fl, _ in s_tr]
        base_fleet[sf] = st.median([tl for _, _, tl in s_tr[-LEVEL_L:]]) or 1

    # Horizon END = last month of the last FULL contract year (as_of's CY + HORIZON-1). CYs are
    # month-aligned on as_of.month, so the next CY would start at (as_of.month, as_of.year+HORIZON);
    # stop ONE month before that so the forecast never spills a stray 1-month CY (e.g. Jul-2028 -> CY2028).
    end = _add_months(date(as_of.year + FORECAST_HORIZON_YEARS, as_of.month, 1), -1)
    fmonths, plan = [], {}
    m = _add_months(frontier, 1)
    while m <= end:
        sfp = {}
        for sf, (level, seas) in fits.items():
            delivered = sum(n for dm, n in deliv.get(sf, []) if dm <= m)
            growth = min((base_fleet[sf] + delivered) / base_fleet[sf], GROWTH_CAP)
            n_hat = level * seas[m.month] * growth
            if n_hat <= 0:
                continue
            same = [(mn, fl) for mn, fl in sf_hist[sf] if mn.month == m.month]
            tm, tc = same[-1] if same else sf_hist[sf][-1]
            if tc > 0:
                sfp[sf] = (tm, n_hat / tc)
        if sfp:
            plan[m] = sfp
            fmonths.append(m)
        m = _add_months(m, 1)
    return frontier, fmonths, plan


def _insert_sql(scope: str) -> str:
    """One forecast month's replication INSERT into acys_forecast (raw per-flight shape; step 4 enriches).
    Per-sub-fleet scale as a VALUES list bound via :sfk_i/:scale_i; each template flight replicated
    floor(scale) times + EXACTLY round(frac×template_count) extra copies (unbiased row_number split)."""
    sfkey = 'coalesce(nullif(t."Master Series",\'\'),\'NA\')'
    return f"""
INSERT INTO forecast.acys_forecast
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "ICAO Origin","ICAO Destination","ICAO Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time",
     "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR",
     "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor")
WITH plan(sf, scale) AS (VALUES {{plan_values}}),
rep AS (
    SELECT t.*,
           (floor(p.scale)
            + CASE WHEN row_number() OVER (PARTITION BY {sfkey} ORDER BY t.id)
                        <= round((p.scale - floor(p.scale)) * count(*) OVER (PARTITION BY {sfkey}))
                   THEN 1 ELSE 0 END)::int AS copies
    FROM forecast.acys_actuals t
    JOIN plan p ON p.sf = {sfkey}
    WHERE t."Operator" = :op AND t."Date" IS NOT NULL
      AND date_trunc('month', t."Date")::date = :template_month {scope}
      -- only project aircraft STILL in the fleet at the frontier (precomputed live-reg list — a fast hash
      -- filter, not a per-flight subquery). A tail idle >= LIVE_WINDOW_MONTHS before the frontier is
      -- retired and must NOT be revived in the forecast.
      AND t."Registration" = ANY(:live_regs)
)
SELECT
    r."Registration", :period, :fdate, NULL, NULL,
    r."IATA Origin", r."IATA Destination", r."IATA Destination Actual",
    r."ICAO Origin", r."ICAO Destination", r."ICAO Destination Actual",
    r."Operator", r."Master Series", r."Manufacturer", r."Aircraft Sub Series", r."Primary Usage",
    :contract_year, r."Circle Distance", r."Flight Time",
    CASE WHEN r."Lease Dry Wet" = 'Wet' THEN 0 ELSE r."Agreed Value" END,
    r."Total Seats", r."Total PAX", r."Actual Distance FR", r."Flight Time FR",
    r."Delivery Date", r."Lease Type", r."Lease Dry Wet", r."Operational Lessor"
FROM rep r
CROSS JOIN LATERAL generate_series(1, r.copies) g
WHERE r.copies > 0
"""


def _contract_year(d: date, anchor: date) -> str:
    # MONTH-aligned (day ignored) so a CY is exactly 12 calendar months, labelled by its START year.
    before = d.month < anchor.month
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
    future = [dict(r._mapping) for r in (await session.execute(
        text(_FUTURE_SQL.format(scope=scope_sql)), {"op": operator, **sp})).all()]

    frontier, fmonths, plan = _plan(rows, future, as_of)
    # live fleet: aircraft that flew within LIVE_WINDOW_MONTHS up to the frontier (else retired) — computed
    # ONCE so the replication filters by a small reg list (fast) instead of a per-flight correlated subquery.
    live_regs = []
    if frontier:
        live_since = _add_months(frontier, -(LIVE_WINDOW_MONTHS - 1))
        live_regs = [r[0] for r in (await session.execute(
            text('SELECT DISTINCT "Registration" FROM forecast.acys_actuals '
                 'WHERE "Operator" = :op AND "Date" IS NOT NULL '
                 f'AND date_trunc(\'month\',"Date")::date >= :ls {scope_sql}'),
            {"op": operator, "ls": live_since, **sp})).all()]
    sql = _insert_sql(scope_sql)
    total = 0
    nfm = max(1, len(fmonths))
    for fi, m in enumerate(fmonths):
        by_tm = defaultdict(dict)
        for sf, (tm, scale) in plan.get(m, {}).items():
            by_tm[tm][sf] = scale
        for tm, scales in by_tm.items():
            vals = []
            params = {"op": operator, "template_month": tm, "period": m.strftime("%m-%Y"),
                      "fdate": m, "contract_year": _contract_year(m, as_of),
                      "live_regs": live_regs, **sp}
            for i, (sf, sc) in enumerate(scales.items()):
                vals.append(f"(:sfk_{i}, CAST(:scale_{i} AS double precision))")
                params[f"sfk_{i}"] = sf
                params[f"scale_{i}"] = float(sc)
            res = await session.execute(text(sql.replace("{plan_values}", ", ".join(vals))), params)
            total += res.rowcount or 0
        if on_progress is not None:
            try:
                await on_progress((fi + 1) / nfm)
            except Exception:
                pass
    await session.commit()
    logger.info("forecast_model: %s → %d forecast rows over %d months (frontier %s, %d future-fleet cells)",
                operator, total, len(fmonths), frontier, len(future))
    return {"forecast_rows": total, "months": len(fmonths),
            "frontier": frontier.isoformat() if frontier else None}
