"""Archetype bucket classifier (handoff step 1 → step 2 routing): schedule vs on-demand.

The step-1 archetype found PC1 (regularity) is BIMODAL — discrete schedule vs on-demand buckets, valley
near PC1≈−0.30, driven by S1/S3/S4 (recurrence, dormancy, route-dispersion). We reproduce that split on
the clean acys_actuals with observable proxies (no flight-number key, so S1 is proxied by route recurrence):

    reg_index = z(log fl_per_pair) + z(top10_share) + z(log fl_per_tail_month)   (higher = schedule)

  * fl_per_pair       — flights ÷ distinct (origin,dest); schedule repeats a route, on-demand flies it once
    (this is the S4/route-dispersion signal, the archetype's cleanest on-demand discriminator).
  * top10_share       — share of flights on the 10 busiest pairs (schedule concentrates, on-demand scatters).
  * fl_per_tail_month — monthly utilisation regularity.

Routing (why the fork matters — measured, backtest.compute_backtest): the schedule-anchor scores median
MAPE 9.4% on schedule carriers but 45.9% on-demand, because on-demand activity is contract-driven, not
network-driven. The failure scales with fleet SIZE (VistaJet, 102 tails, is aggregate-stable at 7%; small
ACMI swing ±100%) — so the honest on-demand model needs its own volatility band (see ondemand.py).

Run: python -m predictive.bucket [--min-flights 1500] [--min-months 12]
"""
from __future__ import annotations

import argparse
import asyncio
import statistics as st

from predictive.db import DB

_REG_SQL = """
WITH f AS (
  SELECT "Operator" op, "Registration" reg, "Date" d,
         "IATA Origin"||'-'||coalesce(nullif("IATA Destination Actual",''),"IATA Destination") pair
  FROM forecast.acys_actuals
  WHERE "Operator" IS NOT NULL AND "Operator" <> '' AND "Date" >= date '2023-07-01'
),
perpair AS (SELECT op, pair, count(*) c FROM f GROUP BY 1,2),
agg AS (SELECT op, count(*) flights, count(DISTINCT reg) tails,
               count(DISTINCT date_trunc('month',d)) months, count(DISTINCT pair) pairs
        FROM f GROUP BY 1),
top10 AS (SELECT op, sum(c) t10 FROM
            (SELECT op, c, row_number() OVER (PARTITION BY op ORDER BY c DESC) rn FROM perpair) x
          WHERE rn<=10 GROUP BY 1)
SELECT a.op, a.flights, a.tails, a.months, a.pairs,
       (a.flights::float/a.pairs)                          AS fl_per_pair,
       (t.t10::float/a.flights)                            AS top10_share,
       (a.flights::float/nullif(a.tails*a.months,0))       AS fl_per_tail_mo
FROM agg a JOIN top10 t ON t.op=a.op
WHERE a.flights >= {minf} AND a.months >= {minm}
"""


def _z(xs):
    m, s = st.fmean(xs), (st.pstdev(xs) or 1.0)
    return [(x - m) / s for x in xs]


async def classify(min_flights=1500, min_months=12):
    """Return per-carrier regularity index + bucket ('schedule'|'ondemand'), split at the KDE valley."""
    import math
    async with DB(statement_timeout_ms=0) as db:
        rows = await db.fetch(_REG_SQL.replace("{minf}", str(min_flights)).replace("{minm}", str(min_months)))
    rows = [dict(r) for r in rows]
    zf = _z([math.log(r["fl_per_pair"]) for r in rows])
    zt = _z([r["top10_share"] for r in rows])
    zu = _z([math.log(r["fl_per_tail_mo"]) for r in rows])
    for r, a, b, c in zip(rows, zf, zt, zu):
        r["reg_index"] = a + b + c
    # split threshold: the archetype's antimode sits below the schedule mass. Take the gap in the sorted
    # index nearest the lower third — robust to the exact KDE. Reference on-demand ⇒ negative index.
    idx = sorted(r["reg_index"] for r in rows)
    thr = _valley(idx)
    for r in rows:
        r["bucket"] = "schedule" if r["reg_index"] >= thr else "ondemand"
    return rows, thr


def _valley(sorted_idx):
    """Largest gap between adjacent sorted indices within the central band = the bimodal valley."""
    lo, hi = int(len(sorted_idx) * 0.15), int(len(sorted_idx) * 0.7)
    best_gap, best_mid = -1.0, 0.0
    for i in range(max(1, lo), min(len(sorted_idx) - 1, hi)):
        gap = sorted_idx[i + 1] - sorted_idx[i]
        if gap > best_gap:
            best_gap, best_mid = gap, (sorted_idx[i + 1] + sorted_idx[i]) / 2
    return best_mid


async def _main(min_flights, min_months):
    rows, thr = await classify(min_flights, min_months)
    rows.sort(key=lambda r: r["reg_index"])
    od = [r for r in rows if r["bucket"] == "ondemand"]
    sc = [r for r in rows if r["bucket"] == "schedule"]
    print(f"=== archetype buckets ({len(rows)} carriers, valley reg_index={thr:+.2f}) ===")
    print(f"  on-demand: {len(od)}   schedule: {len(sc)}\n")
    print(f"  {'carrier':30} {'bucket':>9} {'regIdx':>7} {'fl/pair':>7} {'top10':>6} {'tails':>5}")
    for r in rows:
        if r["reg_index"] <= thr + 0.6 or r["reg_index"] in (rows[-1]["reg_index"], rows[-2]["reg_index"]):
            mark = "◄on-demand" if r["bucket"] == "ondemand" else ""
            print(f"  {r['op'][:30]:30} {r['bucket']:>9} {r['reg_index']:>+7.2f} "
                  f"{r['fl_per_pair']:>7.1f} {r['top10_share']:>6.2f} {r['tails']:>5} {mark}")
    print(f"  … {len(sc)} schedule carriers above the valley (Turkish/Transavia/FSC/LCC).")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Schedule vs on-demand archetype bucket classifier.")
    p.add_argument("--min-flights", type=int, default=1500)
    p.add_argument("--min-months", type=int, default=12)
    a = p.parse_args(argv)
    asyncio.run(_main(a.min_flights, a.min_months))


if __name__ == "__main__":
    main()
