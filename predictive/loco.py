"""Formal LOCO gate (acys-only) — Leave-One-Carrier-Out with a panel prior + reliability curve.

Under the acys-only anchor (flights = own recent LEVEL × SEASONAL factor), the level is carrier-specific
(its own recent activity — not poolable), but the seasonal SHAPE per sub-fleet can be borrowed across
carriers. So the panel prior pools the seasonal factors per (sub-fleet, calendar-month) over the OTHER
carriers, and each hidden carrier's own seasonal factors are shrunk toward that prior by sample size
(thin carriers lean on the panel; thick carriers keep their own). The level always stays the carrier's own.

The deliverable is the RELIABILITY CURVE: pool every held-out carrier-month and, at each nominal band
level (50/80/90%), measure empirical coverage. On the diagonal ⇒ the premise's uncertainty is honest.
The band is the out-of-sample (leave-one-month-out) lognormal spread, √h horizon-inflated per month.

Run: python -m predictive.loco [--cutoff 2025-06] [--n 24] [--dump <path.json>]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics as st
from collections import defaultdict

from predictive.backtest import (COV_FRAC, MIN_VALID_HOLDOUT, Z80, _fit_subfleet, _hat, _logstats,
                                 _mean, _q)
from predictive.broadread import _candidates
from predictive.slice import subfleet_month_panel

SHRINK_K = 6.0          # own→prior seasonal shrinkage: own weight w = n / (n + K)
NOMINAL = [0.50, 0.80, 0.90]
Z = {0.50: 0.6745, 0.80: 1.2816, 0.90: 1.6449}


async def _load(carrier, cut):
    """Panel + own fit + coverage gate + valid held-out months for one carrier (acys-only)."""
    rows = await subfleet_month_panel(carrier)
    train = [r for r in rows if r["mon"] <= cut]
    fit = _fit_subfleet(train)
    months = sorted({r["mon"] for r in rows})
    by_mon = defaultdict(list)
    for r in rows:
        by_mon[r["mon"]].append(r)

    def actual(mon):
        v = [r["cycles"] for r in by_mon[mon] if r["cycles"] is not None]
        return sum(v) if v else None

    def flown(mon):
        return sum(r["flown"] for r in by_mon[mon] if r["flown"])

    cov_base = _q([a for m in months if m <= cut and (a := actual(m))], 0.5)
    cov_min = (cov_base * COV_FRAC) if cov_base else None
    ho_valid = [m for m in months if m > cut and actual(m) is not None
                and not (cov_min and actual(m) < cov_min)]
    return dict(carrier=carrier, rows=rows, train=train, fit=fit, by_mon=by_mon,
                months=months, actual=actual, flown=flown, ho_valid=ho_valid, cut=cut)


def _accum(fits):
    """Per (sub-fleet, calendar-month) n-weighted seasonal-factor accumulators (supports leave-one-out)."""
    acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))   # sf -> cal -> [w, w*factor]
    for _, fit in fits:
        for sf, f in fit.items():
            for cal, s in f["seas"].items():
                acc[sf][cal][0] += f["n"]
                acc[sf][cal][1] += f["n"] * s
    return acc


def _prior_excl(acc, own_fit):
    """Seasonal prior per (sub-fleet, calendar-month) with THIS carrier removed (true LOCO)."""
    prior = defaultdict(dict)
    for sf, cals in acc.items():
        f = own_fit.get(sf)
        for cal, (w, ws) in cals.items():
            if f and cal in f["seas"]:
                w -= f["n"]; ws -= f["n"] * f["seas"][cal]
            if w > 0:
                prior[sf][cal] = ws / w
    return prior


def _shrink(own_fit, prior):
    """Blend own seasonal factors toward the panel prior by sample size; keep the (own) level."""
    out = {}
    for sf, f in own_fit.items():
        g = dict(f)
        p = prior.get(sf)
        if p:
            w = f["n"] / (f["n"] + SHRINK_K)
            g["seas"] = {cal: (w * s + (1 - w) * p[cal]) if cal in p else s
                         for cal, s in f["seas"].items()}
        out[sf] = g
    return out


def _carrier_hat(fit, cd, mon):
    return sum(h for r in cd["by_mon"][mon] if (h := _hat(fit, r["sf"], mon)))


def _loco_one(cd, prior):
    """LOCO forecast for one carrier: shrunk point forecast + OOS lognormal predictive (mu, sd), √h band."""
    cut = cd["cut"]
    shrunk = _shrink(cd["fit"], prior)
    train_ms = [m for m in cd["months"] if m <= cut]

    ratios = []
    for m in train_ms:
        a = cd["actual"](m)
        if not a:
            continue
        h = _carrier_hat(_shrink(_fit_subfleet([r for r in cd["train"] if r["mon"] != m]), prior), cd, m)
        if h > 0:
            ratios.append(a / h)
    mu, sd = _logstats(ratios) if ratios else (0.0, 0.20)

    detail = []
    for m in cd["ho_valid"]:
        a, h = cd["actual"](m), _carrier_hat(shrunk, cd, m)
        if not h:
            continue
        horizon = (m.year - cut.year) * 12 + (m.month - cut.month)
        sig = sd * math.sqrt(max(1, horizon))
        # point = raw hat (recentering by mu wrongly pulls a trending series toward its in-sample bias);
        # mu shifts only the BAND to reflect the systematic in-sample offset.
        covers = {lv: (h * math.exp(mu - Z[lv] * sig) <= a <= h * math.exp(mu + Z[lv] * sig)) for lv in NOMINAL}
        detail.append(dict(mon=f"{m:%Y-%m}", actual=a, hat=h, err=(h - a) / a * 100, covers=covers))
    mape = _mean([abs(d["err"]) for d in detail]) if detail else None
    bias = _mean([d["err"] for d in detail]) if detail else None
    return dict(carrier=cd["carrier"], detail=detail, mape=mape, bias=bias,
                cov80=_mean([d["covers"][0.80] for d in detail]) if detail else None)


async def run_loco(cutoff="2025-06", n=24, dump=None, carriers=None, label="LOCO"):
    from datetime import date
    cut = date(int(cutoff[:4]), int(cutoff[5:7]), 1)
    cands = [{"carrier": c} for c in carriers] if carriers else await _candidates(cutoff, n)
    print(f"loading {len(cands)} carriers… [{label}]")
    loaded = []
    for c in cands:
        cd = await _load(c["carrier"], cut)
        if len(cd["ho_valid"]) >= MIN_VALID_HOLDOUT and cd["fit"]:
            loaded.append(cd)
    print(f"{len(loaded)} validatable carriers.\n")

    acc = _accum([(cd["carrier"], cd["fit"]) for cd in loaded])
    results = [r for r in (_loco_one(cd, _prior_excl(acc, cd["fit"])) for cd in loaded) if r["detail"]]
    results.sort(key=lambda r: r["mape"])

    print(f"=== {label} (leave-one-carrier-out, seasonal panel prior) ===")
    print(f"  {'carrier':28} {'mo':>3} {'MAPE':>6} {'bias':>7} {'80%cov':>7}")
    for r in results:
        print(f"  {r['carrier'][:28]:28} {len(r['detail']):>3} {r['mape']:>5.1f}% "
              f"{r['bias']:>+6.1f}% {r['cov80']*100:>6.0f}%")

    curve = {}
    allmonths = [d for r in results for d in r["detail"]]
    for lv in NOMINAL:
        curve[lv] = _mean([d["covers"][lv] for d in allmonths])
    print(f"\n  === RELIABILITY CURVE (n={len(allmonths)} carrier-months, {len(results)} carriers) ===")
    print(f"  {'nominal':>8} {'empirical':>10}   calibration")
    for lv in NOMINAL:
        print(f"  {lv*100:>6.0f}% {curve[lv]*100:>9.0f}%   {'█' * round(curve[lv] * 30)}")
    print(f"\n  overall LOCO: median MAPE={_q([r['mape'] for r in results],0.5):.1f}%  "
          f"median bias={_q([r['bias'] for r in results],0.5):+.1f}%")

    if dump:
        with open(dump, "w", encoding="utf-8") as fh:
            json.dump(dict(cutoff=cutoff, n_carriers=len(results), n_months=len(allmonths),
                           curve={str(k): v for k, v in curve.items()},
                           median_mape=_q([r["mape"] for r in results], 0.5),
                           median_bias=_q([r["bias"] for r in results], 0.5),
                           carriers=[dict(carrier=r["carrier"], months=len(r["detail"]),
                                          mape=r["mape"], bias=r["bias"], cov80=r["cov80"],
                                          detail=r["detail"]) for r in results]), fh, indent=1)
        print(f"\n  dumped -> {dump}")
    return results, curve


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="acys-only formal LOCO gate + reliability curve.")
    p.add_argument("--cutoff", default="2025-06")
    p.add_argument("--n", type=int, default=24)
    p.add_argument("--dump", default=None)
    a = p.parse_args(argv)
    asyncio.run(run_loco(a.cutoff, a.n, a.dump))


if __name__ == "__main__":
    main()
