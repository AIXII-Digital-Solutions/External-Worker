"""Production forward forecast (acys-only) — the shape the engine ships in.

Window per spec: history = HISTORY_START (2022-07-01) → the auto-detected coverage frontier (NOT literally
"yesterday": the most recent months are under-covered by FR24 lag and back-fill on later extractions, so
fitting on them would bias the level low). Forecast = frontier+1 → launch_date + 2 years. Each new full
extraction appends to forecast.acys_actuals (rows never duplicate), the frontier advances, and the fit
re-trains on the longer history — the algorithm trains itself.

Per (sub-fleet, month): flights = level × seasonal (backtest.py anchor), then deterministic conversion to
km / block-hours / pax (convert.py rates). An 80% band, √h horizon-inflated, widens out over the 2 years.

Run: python -m predictive.forecast --carrier "SCAT Airlines" [--launch 2026-07-10]
"""
from __future__ import annotations

import argparse
import asyncio
import math
from collections import defaultdict
from datetime import date

from predictive.backtest import Z80, _fit_subfleet, _hat, _logstats
from predictive.convert import DEFAULT_LF, _block_h
from predictive.slice import HISTORY_START, coverage_frontier, subfleet_month_panel


def _month_range(after: date, end: date):
    """First-of-month dates strictly after `after`, up to and including `end`'s month."""
    y, m = after.year, after.month
    out = []
    while True:
        m += 1
        if m == 13:
            y, m = y + 1, 1
        d = date(y, m, 1)
        if d > end:
            break
        out.append(d)
    return out


async def forward(carrier: str, launch: str = "2026-07-10", lf: float = DEFAULT_LF):
    as_of = await coverage_frontier()                       # dynamic: last well-covered month 'YYYY-MM'
    cutd = date(int(as_of[:4]), int(as_of[5:7]), 1)
    end = date(int(launch[:4]) + 2, int(launch[5:7]), 1)    # launch + 2 years
    rows = await subfleet_month_panel(carrier)              # from HISTORY_START (2022-07-01)
    train = [r for r in rows if r["mon"] <= cutd]
    fit = _fit_subfleet(train)
    by_mon = defaultdict(list)
    for r in rows:
        by_mon[r["mon"]].append(r)

    def carrier_hat(mon):
        return sum(h for r in train_sf if (h := _hat(fit, r, mon)))

    train_sf = list(fit.keys())
    # 80% band from in-sample ratios (carrier total), horizon-inflated per forecast month
    ratios = []
    for m in sorted({r["mon"] for r in train}):
        a = sum(r["cycles"] for r in by_mon[m] if r["cycles"])
        h = carrier_hat(m)
        if a and h:
            ratios.append(a / h)
    mu, sd = _logstats(ratios)

    months = _month_range(cutd, end)
    print(f"=== {carrier}: forward forecast ===")
    print(f"  history 2022-07 → {as_of} (auto frontier) · forecast {months[0]:%Y-%m} → {months[-1]:%Y-%m} "
          f"(launch {launch} + 2y) · {len(months)} months")
    print(f"\n  {'month':8} {'flights':>9} {'80% band':>19} {'km(000)':>9} {'block-h':>9} {'pax(000)':>9}")
    for i, m in enumerate(months, start=1):
        fl = km = bh = pax = 0.0
        for sf in train_sf:
            f = _hat(fit, sf, m)
            if not f:
                continue
            kmpf = fit[sf].get("kmpf") or 0.0
            fl += f; km += f * kmpf; bh += f * _block_h(kmpf); pax += f * (fit[sf].get("seats") or 0) * lf
        sig = sd * math.sqrt(max(1, i))
        lo, hi = fl * math.exp(mu - Z80 * sig), fl * math.exp(mu + Z80 * sig)
        if i <= 6 or i % 3 == 0 or i == len(months):   # print first 6 then quarterly
            print(f"  {m:%Y-%m}  {fl:9.0f} [{lo:8.0f}..{hi:8.0f}] {km/1000:9.0f} {bh:9.0f} {pax/1000:9.1f}")
    print(f"\n  (band widens with horizon; each new extraction advances the frontier & re-trains — self-training)")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="acys-only forward forecast to launch_date + 2 years.")
    p.add_argument("--carrier", required=True)
    p.add_argument("--launch", default="2026-07-10", help="'указанная дата' at launch; horizon = +2 years")
    p.add_argument("--lf", type=float, default=DEFAULT_LF)
    a = p.parse_args(argv)
    asyncio.run(forward(a.carrier, a.launch, a.lf))


if __name__ == "__main__":
    main()
