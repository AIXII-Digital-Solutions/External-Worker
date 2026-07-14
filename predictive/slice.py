"""Observable anchor panel — built from forecast.acys_actuals ONLY (hard constraint: the forecast reads
no other table; rows never duplicate, so the growing dump is safe to re-fit on = self-training).

Anchor decomposition per (sub-fleet, month), read straight off acys_actuals:
    flights = flown_tails × active_days_per_tail × cyc_per_active_day
where
  * flown_tails         = distinct Registration that flew  (the observable active fleet — there is NO
                          in-service register here, so availability drops out; only flown tails are seen),
  * active_days_per_tail = mean days a flown tail actually flew that month  (carries seasonality),
  * cyc_per_active_day  = cycles per flown-tail-day  (binding intensity; bh/day for long-haul).
Sub-fleet grain = Cirium `Master Series` string as stored IN acys_actuals (stable; no Cirium lookup).

Forward forecast (next module): project the flown-fleet + intensity plateau (recent months, held flat) ×
the typical-year seasonal shape — all from acys. There is no forward fleet register to lean on, so the
fleet itself is projected from recent activity (this is the honest cost of the acys-only constraint).

Window (dynamic): floor = HISTORY_START (2022-07-01); the usable frontier is auto-detected because the
most recent months are always under-covered by FR24 lag and back-fill on later extractions.

Run: python -m predictive.slice --carrier "SCAT Airlines"
"""
from __future__ import annotations

import argparse
import asyncio

from predictive.db import DB

HISTORY_START = "2022-07-01"   # CY2022 floor (fixed by spec); acys has no rows before this

# per (sub-fleet, month) observable panel, forecast.acys_actuals ONLY. $1 = Operator.
_PANEL_SQL = """
SELECT to_char(date_trunc('month',"Date"),'YYYY-MM')                 AS ym,
       date_trunc('month',"Date")::date                             AS mon,
       coalesce(nullif("Master Series",''),'NA')                    AS sf,
       count(DISTINCT "Registration")                               AS flown,
       count(DISTINCT ("Registration","Date"))                      AS active_tail_days,
       count(*)                                                     AS cycles,
       sum("Flight Time")                                           AS block_hours,
       sum("Circle Distance")                                       AS sum_km,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY "Circle Distance") AS med_haul,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY "Total Seats")    AS seats_med,
       (count(DISTINCT ("Registration","Date"))::float
          / nullif(count(DISTINCT "Registration"),0))               AS active_days_per_tail,
       (count(*)::float
          / nullif(count(DISTINCT ("Registration","Date")),0))      AS cyc_per_active_day,
       (sum("Flight Time")
          / nullif(count(DISTINCT ("Registration","Date")),0))      AS bh_per_active_day
FROM forecast.acys_actuals
WHERE "Operator" = $1 AND "Date" >= date '{start}'
GROUP BY 1, 2, 3
ORDER BY 3, 2
"""

# global coverage frontier: the last month whose flights are still ≥ FRONTIER_FRAC of the trailing plateau
# (recent months are under-covered by FR24 lag; this is where a fit should stop trusting the tail).
FRONTIER_FRAC = 0.6
_FRONTIER_SQL = """
WITH m AS (
  SELECT date_trunc('month',"Date")::date mon, count(*) fl
  FROM forecast.acys_actuals WHERE "Date" >= date '{start}' GROUP BY 1),
med AS (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY fl) v FROM m)
SELECT max(m.mon) FROM m, med WHERE m.fl >= med.v * {frac}
"""


async def subfleet_month_panel(carrier: str, start: str = HISTORY_START):
    """Per (sub-fleet, month) observable anchor panel rows for `carrier`, from acys_actuals only."""
    async with DB(statement_timeout_ms=0) as db:
        return await db.fetch(_PANEL_SQL.replace("{start}", start), carrier)


_FORWARD_SQL = """
SELECT coalesce(nullif("Master Series",''),'NA')     AS sf,
       date_trunc('month',"Delivery Date")::date     AS deliv,
       count(DISTINCT "Registration")                AS n
FROM forecast.acys_actuals
WHERE "Operator" = $1 AND "Delivery Date" IS NOT NULL AND "Delivery Date" > $2
GROUP BY 1, 2
"""


async def forward_fleet(carrier: str, after):
    """Per (sub-fleet, delivery month) tail count for aircraft delivered AFTER `after` (the forward fleet
    known from Cirium's order book — here the acys_actuals Delivery Date). Drives the growth correction."""
    async with DB(statement_timeout_ms=0) as db:
        return await db.fetch(_FORWARD_SQL, carrier, after)


async def coverage_frontier(start: str = HISTORY_START) -> str:
    """Auto-detected last well-covered month 'YYYY-MM' (the usable history end; the sparse FR24-lag tail
    beyond it back-fills on later extractions). Dynamic — advances as the dump grows."""
    async with DB(statement_timeout_ms=0) as db:
        rows = await db.fetch(_FRONTIER_SQL.replace("{start}", start).replace("{frac}", str(FRONTIER_FRAC)))
    d = rows[0][0]
    return f"{d:%Y-%m}" if d else None


def _fmt(v, nd=2):
    return "  .  " if v is None else f"{v:.{nd}f}"


async def _main(carrier: str):
    frontier = await coverage_frontier()
    rows = await subfleet_month_panel(carrier)
    subfleets: dict = {}
    for r in rows:
        subfleets.setdefault(r["sf"], []).append(r)
    print(f"=== {carrier}: acys-only anchor panel ({len(subfleets)} sub-fleet(s)) · frontier={frontier} ===")
    for sf, rs in subfleets.items():
        print(f"\n--- {sf}  ({len(rs)} months) ---")
        for r in rs[-14:]:
            print(f"  {r['ym']}  flown={r['flown']:>3} cyc={str(r['cycles']):>6} "
                  f"cyc/d={_fmt(r['cyc_per_active_day'])} bh/d={_fmt(r['bh_per_active_day'])} "
                  f"adays={_fmt(r['active_days_per_tail'],1)} medkm={_fmt(r['med_haul'],0)} "
                  f"seats={_fmt(r['seats_med'],0)}")
        plateau = rs[-12:]
        m = lambda k: (lambda xs: sum(xs) / len(xs) if xs else float("nan"))([x[k] for x in plateau if x[k]])
        leg = "short(cyc/day)" if (m("med_haul") or 0) < 2500 else "long(bh/day)"
        print(f"  PLATEAU(12mo): flown={m('flown'):.1f}  cyc/day={m('cyc_per_active_day'):.2f}  "
              f"bh/day={m('bh_per_active_day'):.2f}  adays={m('active_days_per_tail'):.1f}  "
              f"med_haul={m('med_haul'):.0f}km → {leg}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="acys-only observable anchor panel for one carrier.")
    p.add_argument("--carrier", required=True, help='acys_actuals "Operator" value, e.g. "SCAT Airlines"')
    a = p.parse_args(argv)
    asyncio.run(_main(a.carrier))


if __name__ == "__main__":
    main()
