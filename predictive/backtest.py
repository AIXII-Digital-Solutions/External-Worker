"""acys-only anchor forecast + layered self-backtest.

Forecast per sub-fleet: replicate the recent activity plateau, held flat, times the typical-year seasonal
shape — everything from forecast.acys_actuals (no Cirium; the flown-fleet is projected from recent activity
because there is no forward fleet register under the acys-only constraint):
    flights_hat(month) = level_sf × seasonal_sf[calendar-month]
  * level_sf     = median of the last LEVEL_L deseasonalized monthly flight counts (robust recent plateau),
  * seasonal_sf  = geometric month-of-year factor, shrunk toward 1.0 by support (a 2-yr calendar estimate is
                   noisy; the flown-fleet + intensity seasonality both land here since flights carry both).
Carrier flights_hat = Σ sub-fleet. The four-metric conversion (convert.py) multiplies this by per-flight
rates (km, seats, block-time) also read from acys.

Backtest: train ≤ cutoff, forecast the coverage-VALID held-out months, compare on the flights aggregate.
Coverage gate: a held-out month is valid only if its flights ≥ COV_FRAC of the carrier's training-median
monthly flights — the recent FR24-lag tail (which back-fills on later extractions) is not ground truth.
80% band = out-of-sample: leave-one-month-out refit ratios, lognormal. n_growth = held-out vs recent
flown-fleet, the lever for the (now acys-derived) 'activity ∝ fleet?' read.

Run: python -m predictive.backtest --carrier "SCAT Airlines" [--cutoff 2025-06]
"""
from __future__ import annotations

import argparse
import asyncio
import math
import statistics as st
from collections import defaultdict

from predictive.slice import subfleet_month_panel

COV_FRAC = 0.5
MIN_VALID_HOLDOUT = 3
SEAS_K = 6.0        # seasonal-factor shrinkage toward 1.0 by month support
LEVEL_L = 3         # trailing months for the deseasonalized recent level (robust median): short window
                    # tracks the CURRENT fleet size (a flat 12-mo plateau lags growth → systematic under-
                    # prediction, since acys-only has no forward fleet register). Swept: L=3 → 9.7% MAPE / −1.4% bias.
TREND_PHI = 0.0     # damped-trend factor (0 = flat level; superseded for growth by the forward-fleet signal).
GROWTH_CAP = 4.0    # cap the forward-fleet growth multiplier (guard against bad delivery data)
Z80 = 1.2815515594


def _cal(mon):
    return mon.month


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return st.fmean(xs) if xs else None


def _q(xs, p):
    s = sorted(x for x in xs if x is not None)
    if not s:
        return float("nan")
    if len(s) == 1:
        return s[0]
    i = p * (len(s) - 1)
    lo = int(i)
    return s[lo] if lo + 1 >= len(s) else s[lo] * (1 - (i - lo)) + s[lo + 1] * (i - lo)


def _logstats(ratios):
    """(mu, sd) of log(actual/hat); the multiplicative predictive spread, horizon-inflated at use time."""
    logs = [math.log(x) for x in ratios if x and x > 0]
    if len(logs) < 2:
        return 0.0, 0.20
    return st.fmean(logs), max(st.pstdev(logs), 0.05)


def _fit_subfleet(train_rows):
    """Per sub-fleet: deseasonalized recent level + shrunk seasonal factors + per-flight conversion rates."""
    by_sf = defaultdict(list)
    for r in train_rows:
        by_sf[r["sf"]].append(r)
    fit = {}
    for sf, rs in by_sf.items():
        rs = sorted(rs, key=lambda x: x["mon"])
        series = [(_cal(r["mon"]), r["cycles"]) for r in rs if r["cycles"]]
        if not series:
            continue
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
        des = [f / seas[c] for c, f in series]                 # deseasonalized monthly level
        level = st.median(des[-LEVEL_L:])
        # recent slope (deseasonalized): last-3 median minus prior-3 median, per month
        slope = ((st.median(des[-3:]) - st.median(des[-6:-3])) / 3.0) if len(des) >= 6 else 0.0
        kmpf = _mean([(r["sum_km"] / r["cycles"]) for r in rs if r["sum_km"] and r["cycles"]])
        seats = _q([r["seats_med"] for r in rs if r["seats_med"]], 0.5)
        adays = _mean([r["active_days_per_tail"] for r in rs if r["active_days_per_tail"]])
        cyc = _mean([r["cyc_per_active_day"] for r in rs if r["cyc_per_active_day"]])
        fit[sf] = dict(level=level, slope=slope, seas=seas, kmpf=kmpf, seats=seats, adays=adays,
                       cyc=cyc, n=len(series), as_of=rs[-1]["mon"])
    return fit


def _hat(fit, sf, mon):
    """Forecast one sub-fleet's flights: (recent level + damped trend to the horizon) × seasonal factor."""
    f = fit.get(sf)
    if not f:
        return None
    proj = f["level"]
    if TREND_PHI > 0 and f.get("slope"):
        h = (mon.year - f["as_of"].year) * 12 + (mon.month - f["as_of"].month)
        if h > 0:
            proj = f["level"] + f["slope"] * (1 - TREND_PHI ** h) / (1 - TREND_PHI)
    return max(proj, 0.0) * f["seas"][_cal(mon)]


async def compute_backtest(carrier: str, cutoff: str = "2025-06") -> dict:
    """acys-only self-backtest; coverage-gates the held-out months, OOS lognormal band. Returns stats."""
    from datetime import date
    cut = date(int(cutoff[:4]), int(cutoff[5:7]), 1)
    rows = await subfleet_month_panel(carrier)
    train = [r for r in rows if r["mon"] <= cut]
    fit = _fit_subfleet(train)
    months = sorted({r["mon"] for r in rows})
    by_mon = defaultdict(list)
    for r in rows:
        by_mon[r["mon"]].append(r)

    # forward fleet (order book known at the cutoff): grow a sub-fleet's forecast as its future-delivery
    # aircraft arrive. Delivery Date > cut = aircraft that join after the anchor window (acys-only signal).
    from predictive.slice import forward_fleet
    deliv = defaultdict(list)
    for r in await forward_fleet(carrier, cut):
        if r["deliv"] and r["deliv"] > cut:
            deliv[r["sf"]].append((r["deliv"], r["n"]))
    for sf in deliv:
        deliv[sf].sort()
    base_fleet = {}
    for sf in {r["sf"] for r in train}:
        rec = [r["flown"] for r in train if r["sf"] == sf and r["flown"]][-3:]
        base_fleet[sf] = st.median(rec) if rec else 0

    def growth(sf, mon):
        if not base_fleet.get(sf) or sf not in deliv:
            return 1.0
        d = sum(n for dm, n in deliv[sf] if dm <= mon)
        return min((base_fleet[sf] + d) / base_fleet[sf], GROWTH_CAP)

    def carrier_hat(mon, f=fit):
        return sum(h * growth(r["sf"], mon) for r in by_mon[mon] if (h := _hat(f, r["sf"], mon)))

    def carrier_actual(mon):
        v = [r["cycles"] for r in by_mon[mon] if r["cycles"] is not None]
        return sum(v) if v else None

    def carrier_flown(mon):
        return sum(r["flown"] for r in by_mon[mon] if r["flown"])

    # coverage gate: valid month ⇒ flights ≥ COV_FRAC × training-median monthly flights (drops FR24-lag tail)
    cov_base = _q([a for m in months if m <= cut and (a := carrier_actual(m))], 0.5)
    cov_min = (cov_base * COV_FRAC) if cov_base else None

    ratios = [a / h for m in months if m <= cut and (a := carrier_actual(m)) and (h := carrier_hat(m))]
    loo = []
    for m in (mm for mm in months if mm <= cut):
        a = carrier_actual(m)
        if not a:
            continue
        h = carrier_hat(m, _fit_subfleet([r for r in train if r["mon"] != m]))
        if h:
            loo.append(a / h)
    mu, sd = _logstats(loo or ratios)   # OOS predictive spread; horizon-inflated per held-out month below

    detail, errs, covered, excluded = [], [], 0, 0
    for m in months:
        if m <= cut:
            continue
        a, h = carrier_actual(m), carrier_hat(m)
        if a is None or not h:
            continue
        if cov_min and a < cov_min:   # FR24-lag / collapsed-coverage month — not valid ground truth
            excluded += 1
            continue
        horizon = (m.year - cut.year) * 12 + (m.month - cut.month)   # months ahead (flat-level error grows ∝√h)
        sig = sd * math.sqrt(max(1, horizon))
        lo, hi = h * math.exp(mu - Z80 * sig), h * math.exp(mu + Z80 * sig)
        err = (h - a) / a * 100
        inside = lo <= a <= hi
        covered += inside
        errs.append(err)
        detail.append(dict(mon=m, actual=a, hat=h, err=err, lo=lo, hi=hi, inside=inside))

    train_ms = [m for m in months if m <= cut]
    ho_ms = [d["mon"] for d in detail]
    tr_f = _mean([carrier_flown(m) for m in train_ms[-12:]])
    ho_f = _mean([carrier_flown(m) for m in ho_ms]) if ho_ms else None
    n_growth = (ho_f / tr_f) if (tr_f and ho_f) else None

    return dict(
        carrier=carrier, n_subfleets=len(fit), band_mu_sd=(mu, sd), median_ratio=math.exp(mu),
        detail=detail, n_holdout=len(detail), n_excluded=excluded,
        cov_base=cov_base, validatable=(len(detail) >= MIN_VALID_HOLDOUT),
        mape=(_mean([abs(e) for e in errs]) if errs else None),
        mean_bias=(_mean(errs) if errs else None),
        coverage=(covered / len(detail) if detail else None),
        n_growth=n_growth, train=train, rows=rows, cut=cut,
    )


async def run_backtest(carrier: str, cutoff: str = "2025-06"):
    res = await compute_backtest(carrier, cutoff)
    mu, sd = res["band_mu_sd"]
    print(f"=== {carrier}: acys-only self-backtest (train ≤ {cutoff}) ===")
    print(f"  sub-fleets: {res['n_subfleets']} | median ratio={res['median_ratio']:.3f} "
          f"log-sd={sd:.3f} | flown growth held-out/train={res['n_growth']}")
    print(f"\n  {'month':8} {'actual':>8} {'hat':>8} {'err%':>7} {'band[lo..hi]':>20} {'in?':>4}")
    for d in res["detail"]:
        print(f"  {d['mon']:%Y-%m}  {d['actual']:8.0f} {d['hat']:8.0f} {d['err']:+6.1f}% "
              f"[{d['lo']:8.0f}..{d['hi']:8.0f}] {'yes' if d['inside'] else 'NO':>4}")
    if res["n_holdout"]:
        print(f"\n  READ: MAPE={res['mape']:.1f}%  bias={res['mean_bias']:+.1f}%  "
              f"80%-cover={sum(d['inside'] for d in res['detail'])}/{res['n_holdout']} "
              f"({res['coverage']*100:.0f}%)  [excluded {res['n_excluded']} low-coverage months]")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="acys-only anchor self-backtest for one carrier.")
    p.add_argument("--carrier", required=True)
    p.add_argument("--cutoff", default="2025-06")
    a = p.parse_args(argv)
    asyncio.run(run_backtest(a.carrier, a.cutoff))


if __name__ == "__main__":
    main()
