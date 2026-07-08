"""Thin-slice anchor forward-projection + layered self-backtest (handoff_v2 §5.2/§5.4/§6).

Forecast (per sub-fleet, per month):
    cycles_hat = N_insvc(month) × availability_plateau × active_days_typical[calendar_month] × cyc_plateau
  * N_insvc            — observed Cirium in-service fleet for that month (we always know the fleet).
  * availability_plateau, cyc_plateau — LAST-12-training-month means (post-ramp plateau, held FLAT).
  * active_days_typical[cal] — mean active-days-per-tail for that CALENDAR month over the training years
                               (the "typical year" seasonal shape). Fallback: sub-fleet overall mean.
Carrier flights_hat(month) = Σ_sub-fleet. This is the ANCHOR output (cycles = flights); the other three
metrics are deterministic conversions (added later; block-hours from distance, not raw Flight Time).

Backtest: train on months <= cutoff, forecast the held-out months (> cutoff, with actual flights),
compare per-component (did the plateau hold?) AND on the flights aggregate. The 80% band = the training
in-sample spread of (actual/hat); coverage = fraction of held-out months whose actual falls inside it.
This is the go/no-go read on the premise for ONE carrier (full cross-carrier LOCO reliability is later).

Run: python -m predictive.backtest --carrier "SCAT Airlines" --cutoff 2025-06
"""
from __future__ import annotations

import argparse
import asyncio
import math
import statistics as st
from collections import defaultdict

from predictive.slice import subfleet_month_panel


def _lognorm_band(ratios, p):
    """Central p-interval of a lognormal fit to `ratios` (multiplicative band around hat). Uses the full
    spread — stable where empirical tail quantiles are too few — and carries the median bias (exp(mu))."""
    logs = [math.log(x) for x in ratios if x and x > 0]
    if len(logs) < 2:
        return (0.85, 1.15)
    mu, sd = st.fmean(logs), st.pstdev(logs)
    z = {0.80: 1.2816, 0.90: 1.6449}.get(p, 1.2816)
    return (math.exp(mu - z * sd), math.exp(mu + z * sd))


def _q(xs, p):
    """p-quantile of xs (linear interpolation); xs need not be sorted."""
    if not xs:
        return float("nan")
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    i = p * (len(s) - 1)
    lo = int(i)
    frac = i - lo
    return s[lo] if lo + 1 >= len(s) else s[lo] * (1 - frac) + s[lo + 1] * frac


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return st.fmean(xs) if xs else None


def _cal(mon):
    return mon.month


def _fit_subfleet(train_rows):
    """Per sub-fleet: plateau (last-12 avail & cyc/day) + typical-year active-days by calendar month."""
    by_sf = defaultdict(list)
    for r in train_rows:
        by_sf[r["sf"]].append(r)
    fit = {}
    for sf, rs in by_sf.items():
        flown = [r for r in rs if r["cycles"] is not None]
        if not flown:
            continue
        last12 = flown[-12:]
        avail = _mean([r["availability"] for r in last12])
        cyc = _mean([r["cyc_per_active_day"] for r in last12])
        adays_by_cal = defaultdict(list)
        for r in flown:
            if r["active_days_per_tail"] is not None:
                adays_by_cal[_cal(r["mon"])].append(r["active_days_per_tail"])
        adays_cal = {m: _mean(v) for m, v in adays_by_cal.items()}
        adays_overall = _mean([r["active_days_per_tail"] for r in flown])
        kmpf = _mean([(r["sum_km"] / r["cycles"]) for r in flown if r["sum_km"] and r["cycles"]])
        fit[sf] = dict(avail=avail, cyc=cyc, adays_cal=adays_cal, adays_overall=adays_overall,
                       kmpf=kmpf, n=len(flown))
    return fit


def _hat_cycles(fit, sf, mon, n_insvc):
    """Forecast one sub-fleet's cycles for a month from its plateau + typical seasonal active-days."""
    f = fit.get(sf)
    if not f or not n_insvc or f["avail"] is None or f["cyc"] is None:
        return None
    adays = f["adays_cal"].get(_cal(mon), f["adays_overall"])
    if adays is None:
        return None
    return n_insvc * f["avail"] * adays * f["cyc"]


# acys_actuals is an FR24-MATCHED subset: coverage is region-dependent and has a stale trailing edge.
# A held-out month is valid ground truth only if the fleet's observed cycles-per-in-service-tail is at
# least COV_FRAC of the carrier's own training plateau; a sharp sustained drop = coverage collapse
# (FR24 stopped seeing the fleet), NOT a real activity drop, so it can't be used to score the forecast.
COV_FRAC = 0.5
MIN_VALID_HOLDOUT = 3
# ramp-in: a tail's FIRST in-service month flies at ~0.46 of plateau cycles (measured, pooled 14 carriers:
# age0=0.46, age1=0.97, age≥2≈1.0 — a one-month induction effect, in cycles not availability). So the
# effective flying fleet discounts this month's net additions: N_eff(t)=N(t) − RAMP_DISCOUNT·max(0,ΔN).
RAMP_DISCOUNT = 0.54


def _n_eff_map(rows):
    """Per (sub-fleet, month) effective fleet = N minus the ramp discount on this month's net additions."""
    by_sf = defaultdict(list)
    for r in rows:
        by_sf[r["sf"]].append(r)
    n_eff = {}
    for sf, rs in by_sf.items():
        rs.sort(key=lambda x: x["mon"])
        prev = None
        for r in rs:
            n = r["n_insvc"] or 0
            add = max(0, n - prev) if prev is not None else 0
            n_eff[(sf, r["mon"])] = n - RAMP_DISCOUNT * add
            prev = n
    return n_eff


async def compute_backtest(carrier: str, cutoff: str = "2025-06") -> dict:
    """Run the anchor self-backtest and RETURN summary stats (no printing). Coverage-gates the held-out
    months (see COV_FRAC) so a collapsed-coverage tail can't corrupt the read; a carrier with < 3 valid
    held-out months is marked unvalidatable. n_growth = held-out mean fleet / last-12 training mean fleet
    — the lever for the 'activity ∝ N?' question."""
    from datetime import date
    cut = date(int(cutoff[:4]), int(cutoff[5:7]), 1)
    rows = await subfleet_month_panel(carrier)
    train = [r for r in rows if r["mon"] <= cut]
    fit = _fit_subfleet(train)
    months = sorted({r["mon"] for r in rows})
    by_mon = defaultdict(list)
    for r in rows:
        by_mon[r["mon"]].append(r)
    n_eff = _n_eff_map(rows)   # ramp-discounted effective fleet per (sub-fleet, month)

    def carrier_hat(mon, f=fit):
        return sum(h for r in by_mon[mon]
                   if (h := _hat_cycles(f, r["sf"], mon, n_eff.get((r["sf"], mon), r["n_insvc"]))))

    def carrier_actual(mon):
        vals = [r["cycles"] for r in by_mon[mon] if r["cycles"] is not None]
        return sum(vals) if vals else None

    def carrier_n(mon):
        return sum(r["n_insvc"] for r in by_mon[mon] if r["n_insvc"])

    def cyc_per_tail(mon):
        a, n = carrier_actual(mon), carrier_n(mon)
        return (a / n) if (a and n) else None

    # coverage baseline = median cycles-per-in-service-tail over training months that actually flew
    cov_base = _q([c for m in months if m <= cut and (c := cyc_per_tail(m))], 0.5)
    cov_min = (cov_base * COV_FRAC) if cov_base else None

    # OUT-OF-SAMPLE band: leave-one-month-out training ratios (refit the plateau without each month, so
    # the month's own noise is fully in the residual), then a lognormal 80% interval — uses the whole
    # spread (not 2 noisy tail points) and re-centres on the model's median bias.
    ratios = [a / h for m in months if m <= cut
              and (a := carrier_actual(m)) and (h := carrier_hat(m))]        # in-sample (for reporting)
    loo = []
    for m in (mm for mm in months if mm <= cut):
        a = carrier_actual(m)
        if not a:
            continue
        h = carrier_hat(m, _fit_subfleet([r for r in train if r["mon"] != m]))
        if h:
            loo.append(a / h)
    lo, hi = _lognorm_band(loo or ratios, 0.80)

    detail, errs, covered, excluded = [], [], 0, 0
    for m in months:
        if m <= cut:
            continue
        a, h = carrier_actual(m), carrier_hat(m)
        if a is None or not h:
            continue
        cpt = cyc_per_tail(m)
        if cov_min and (cpt is None or cpt < cov_min):   # coverage-collapsed month — not valid ground truth
            excluded += 1
            continue
        err = (h - a) / a * 100
        inside = (h * lo) <= a <= (h * hi)
        covered += inside
        errs.append(err)
        detail.append(dict(mon=m, actual=a, hat=h, err=err, lo=h * lo, hi=h * hi, inside=inside))

    train_ms = [m for m in months if m <= cut]
    ho_ms = [d["mon"] for d in detail]
    tr_N = _mean([carrier_n(m) for m in train_ms[-12:]])
    ho_N = _mean([carrier_n(m) for m in ho_ms]) if ho_ms else None
    n_growth = (ho_N / tr_N) if (tr_N and ho_N) else None

    return dict(
        carrier=carrier, n_subfleets=len(fit), band=(lo, hi), median_ratio=_q(ratios, 0.5),
        detail=detail, n_holdout=len(detail), n_excluded=excluded,
        cov_base=cov_base, validatable=(len(detail) >= MIN_VALID_HOLDOUT),
        mape=(_mean([abs(e) for e in errs]) if errs else None),
        mean_bias=(_mean(errs) if errs else None),
        coverage=(covered / len(detail) if detail else None),
        n_growth=n_growth, train=train, rows=rows, cut=cut,
    )


async def run_backtest(carrier: str, cutoff: str = "2025-06"):
    res = await compute_backtest(carrier, cutoff)
    lo, hi = res["band"]
    print(f"=== {carrier}: anchor self-backtest (train ≤ {cutoff}) ===")
    print(f"  sub-fleets fit: {res['n_subfleets']} | train ratio median={res['median_ratio']:.3f} "
          f"80%-band=[{lo:.3f}, {hi:.3f}] | fleet growth held-out/train={res['n_growth']:.3f}")
    print(f"\n  HELD-OUT forecast vs actual (flights = anchor output):")
    print(f"  {'month':8} {'actual':>7} {'hat':>7} {'err%':>7} {'band[lo..hi]':>18} {'in?':>4}")
    for d in res["detail"]:
        print(f"  {d['mon']:%Y-%m}  {d['actual']:7.0f} {d['hat']:7.0f} {d['err']:+6.1f}% "
              f"[{d['lo']:7.0f}..{d['hi']:7.0f}] {'yes' if d['inside'] else 'NO':>4}")
    if res["n_holdout"]:
        print(f"\n  READ: MAPE={res['mape']:.1f}%  bias={res['mean_bias']:+.1f}%  "
              f"80%-band coverage={sum(d['inside'] for d in res['detail'])}/{res['n_holdout']} "
              f"({res['coverage']*100:.0f}%)  [target ~80%]")
    print(f"\n  COMPONENT check (held-out actual vs training plateau, fleet-weighted):")
    for label, key in [("availability", "availability"), ("active_days/tail", "active_days_per_tail"),
                       ("cyc/active_day", "cyc_per_active_day")]:
        tr = _mean([r[key] for r in res["train"] if r["cycles"] is not None and r[key] is not None])
        ho = _mean([r[key] for r in res["rows"] if r["mon"] > res["cut"] and r["cycles"] is not None and r[key] is not None])
        if tr and ho:
            print(f"    {label:16} train={tr:.3f}  held-out={ho:.3f}  drift={(ho-tr)/tr*100:+.1f}%")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Anchor forward-projection + self-backtest for one carrier.")
    p.add_argument("--carrier", required=True, help='"Operator" value, e.g. "SCAT Airlines"')
    p.add_argument("--cutoff", default="2025-06", help="last training month YYYY-MM (default 2025-06)")
    a = p.parse_args(argv)
    asyncio.run(run_backtest(a.carrier, a.cutoff))


if __name__ == "__main__":
    main()
