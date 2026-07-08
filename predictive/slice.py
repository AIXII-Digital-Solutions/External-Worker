"""Thin vertical slice (handoff_v2 §5.2) — the observable anchor panel for ONE schedule-bucket carrier.

Anchor decomposition (top-down, fleet-anchored), read straight off the data per (sub-fleet, month):
    cycles = N_insvc × availability × active_days_per_tail × cyc_per_active_day
where
  * N_insvc            = in-service tails per month (Cirium, clean per-month operator attribution),
  * availability       = flown tails / in-service tails,
  * active_days_per_tail = mean days a flown tail actually flew that month (carries seasonality),
  * cyc_per_active_day = cycles per flown-tail-day (the binding intensity for SHORT-haul; use bh/day
                         for LONG-haul — see leg class from med_haul).
The forward forecast (next module) = replicate the plateau (last ~12 mo, held flat) × N(t) × the
seasonal active-days shape. This module only BUILDS + READS the observable panel; no forecast yet.

Run: python -m predictive.slice --carrier "SCAT Airlines"
"""
from __future__ import annotations

import argparse
import asyncio

from predictive.db import DB

HISTORY_START = "2023-07-01"   # clean Cirium monthly attribution starts here (handoff_v2 §1)
GRAIN = "Master Series"        # anchor unit: type × generation, STABLE month-to-month (not the churny
                               # "Aircraft Sub Series" — ACF/P2F/XLR relabelling broke per-unit availability)

# per (sub-fleet, month) observable panel. $1 = carrier (Cirium Operator = acys_actuals Operator).
# BOTH sides derive the sub-fleet grain from Cirium by (reg, month) so the in-service and flown keys
# ALWAYS align — acys_actuals' own stored sub-series is NOT trusted (it drifts vs the current revision).
_PANEL_SQL = """
WITH cm AS (   -- per (reg, month) latest Cirium revision -> status + grain + seats
    SELECT DISTINCT ON (ca."Registration", to_date(r.period,'MM-YYYY'))
        ca."Registration"          AS reg,
        to_date(r.period,'MM-YYYY') AS mon,
        ca."Status"                AS status,
        coalesce(nullif(ca."{grain}",''), 'NA') AS sf,
        ca."Number of Seats"       AS seats
    FROM cirium.ciriumaircrafts ca
    JOIN cirium.aircraftrevision r ON r.id = ca.revision_id
    WHERE ca."Operator" = $1 AND to_date(r.period,'MM-YYYY') >= date '{start}'
    ORDER BY ca."Registration", to_date(r.period,'MM-YYYY'), ca.revision_id DESC
),
insvc AS (   -- in-service fleet N and representative seats per (grain, month)
    SELECT sf, mon, count(*) AS n_insvc,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY seats) AS seats_med
    FROM cm WHERE status = 'In Service' GROUP BY sf, mon
),
fa AS (   -- acys flights re-keyed to the SAME Cirium grain via (reg, month)
    SELECT cm.sf AS sf, a.mon, a.reg, a.fdate, a.ft, a.km
    FROM (SELECT "Registration" AS reg, to_date("Period",'MM-YYYY') AS mon,
                 "Date" AS fdate, "Flight Time" AS ft, "Circle Distance" AS km
          FROM forecast.acys_actuals WHERE "Operator" = $1) a
    JOIN cm ON cm.reg = a.reg AND cm.mon = a.mon
),
fl AS (   -- monthly flight aggregates per (grain, month)
    SELECT sf, mon,
           count(*)                              AS cycles,
           count(DISTINCT reg)                   AS flown,
           count(DISTINCT (reg, fdate))          AS active_tail_days,
           sum(extract(epoch FROM ft))/3600.0    AS block_hours,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY km) AS med_haul,
           sum(km)                               AS sum_km
    FROM fa GROUP BY sf, mon
)
SELECT to_char(i.mon,'YYYY-MM')                              AS ym,
       i.mon, i.sf, i.n_insvc, i.seats_med,
       fl.flown, fl.cycles, fl.active_tail_days, fl.block_hours, fl.med_haul, fl.sum_km,
       (fl.flown::float / nullif(i.n_insvc,0))               AS availability,
       (fl.active_tail_days::float / nullif(fl.flown,0))     AS active_days_per_tail,
       (fl.cycles::float / nullif(fl.active_tail_days,0))    AS cyc_per_active_day,
       (fl.block_hours   / nullif(fl.active_tail_days,0))    AS bh_per_active_day
FROM insvc i
LEFT JOIN fl ON fl.sf = i.sf AND fl.mon = i.mon
ORDER BY i.sf, i.mon
"""


async def subfleet_month_panel(carrier: str, start: str = HISTORY_START, grain: str = GRAIN):
    """Return the per (sub-fleet, month) observable anchor panel rows for `carrier`."""
    async with DB(statement_timeout_ms=0) as db:
        return await db.fetch(_PANEL_SQL.replace("{start}", start).replace("{grain}", grain), carrier)


def _fmt(v, nd=2):
    return "  .  " if v is None else f"{v:.{nd}f}"


async def _main(carrier: str):
    rows = await subfleet_month_panel(carrier)
    # group by sub-fleet, print a compact monthly view + the trailing-12 plateau
    subfleets: dict = {}
    for r in rows:
        subfleets.setdefault(r["sf"], []).append(r)
    print(f"=== {carrier}: sub-fleet monthly anchor panel ({len(subfleets)} sub-fleet(s)) ===")
    for sf, rs in subfleets.items():
        flown_rs = [x for x in rs if x["cycles"] is not None]
        print(f"\n--- sub-fleet: {sf}  ({len(rs)} months, {len(flown_rs)} with flights) ---")
        for r in rs[-14:]:
            print(f"  {r['ym']}  N={r['n_insvc']:>3} avail={_fmt(r['availability'])} "
                  f"cyc={str(r['cycles'] or '.'):>5} cyc/d={_fmt(r['cyc_per_active_day'])} "
                  f"bh/d={_fmt(r['bh_per_active_day'])} adays={_fmt(r['active_days_per_tail'],1)} "
                  f"medkm={_fmt(r['med_haul'],0)} seats={_fmt(r['seats_med'],0)}")
        # plateau = last 12 months WITH flights
        plateau = [x for x in flown_rs][-12:]
        if plateau:
            avail = [x["availability"] for x in plateau if x["availability"]]
            cycd = [x["cyc_per_active_day"] for x in plateau if x["cyc_per_active_day"]]
            bhd = [x["bh_per_active_day"] for x in plateau if x["bh_per_active_day"]]
            med = [x["med_haul"] for x in plateau if x["med_haul"]]
            m = lambda xs: sum(xs) / len(xs) if xs else float("nan")
            leg = "short(cyc/day binding)" if m(med) < 2500 else "long(bh/day binding)"
            print(f"  PLATEAU(12mo): availability={m(avail):.3f}  cyc/day={m(cycd):.2f}  "
                  f"bh/day={m(bhd):.2f}  med_haul={m(med):.0f}km -> {leg}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Thin-slice observable anchor panel for one carrier.")
    p.add_argument("--carrier", required=True, help='Cirium/acys_actuals "Operator" value, e.g. "SCAT Airlines"')
    a = p.parse_args(argv)
    asyncio.run(_main(a.carrier))


if __name__ == "__main__":
    main()
