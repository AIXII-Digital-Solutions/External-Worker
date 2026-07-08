"""Formal LOCO gate (handoff_v2 §5.4/§6) — Leave-One-Carrier-Out with panel priors + reliability curve.

The self-backtest already passed (broad-read median MAPE 9.4%, 80%-band coverage 88%). LOCO is the FORMAL
judge: for each carrier C we build the fleet-intensity priors from the OTHER carriers only (pooled per
Master-Series sub-fleet), shrink C's own plateau toward that prior by sample size (thin cells → prior,
thick cells → own — empirical Bayes), and forecast C's held-out window. C's own history to the cutoff is
kept (that is what LOCO allows); what is hidden is C's contribution to the panel priors.

The single deliverable is the RELIABILITY CURVE: pool every held-out carrier-month, and at each nominal
band level (50/80/90%) measure the empirical coverage. On the diagonal ⇒ the premise's uncertainty is
honest; systematically below ⇒ over-confident (revisit the assumption, not the code).

Run: python -m predictive.loco [--cutoff 2025-06] [--n 24] [--dump <path.json>]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics as st
from collections import defaultdict

from predictive.backtest import (COV_FRAC, MIN_VALID_HOLDOUT, _fit_subfleet, _hat_cycles,
                                 _mean, _n_eff_map, _q)
from predictive.broadread import _candidates
from predictive.db import DB
from predictive.slice import subfleet_month_panel

SHRINK_K = 6.0          # own→prior shrinkage: own weight w = n / (n + K); n = months the sub-fleet flew
TYPE_K = 12.0           # Master-Series→Type shrinkage: MS-prior weight below which it leans on the family
NOMINAL = [0.50, 0.80, 0.90]
Z = {0.50: 0.6745, 0.80: 1.2816, 0.90: 1.6449}


async def _ms2type():
    """Master Series → dominant Type family map (A320neo/A320 → A320) for the hierarchical prior tier."""
    async with DB(statement_timeout_ms=0) as db:
        rows = await db.fetch("""SELECT "Master Series" ms, "Type" typ, count(*) n
            FROM cirium.ciriumaircrafts WHERE "Master Series" IS NOT NULL AND "Type" IS NOT NULL
            GROUP BY 1,2""")
    best = {}
    for r in rows:
        if r["ms"] not in best or r["n"] > best[r["ms"]][1]:
            best[r["ms"]] = (r["typ"], r["n"])
    return {ms: v[0] for ms, v in best.items()}


async def _load(carrier, cut):
    """Panel + own fit + coverage gate + valid held-out months (actuals) for one carrier."""
    rows = await subfleet_month_panel(carrier)
    train = [r for r in rows if r["mon"] <= cut]
    fit = _fit_subfleet(train)
    n_eff = _n_eff_map(rows)
    months = sorted({r["mon"] for r in rows})
    by_mon = defaultdict(list)
    for r in rows:
        by_mon[r["mon"]].append(r)

    def actual(mon):
        v = [r["cycles"] for r in by_mon[mon] if r["cycles"] is not None]
        return sum(v) if v else None

    def n_insvc(mon):
        return sum(r["n_insvc"] for r in by_mon[mon] if r["n_insvc"])

    cov_base = _q([a / n for m in months if m <= cut
                   and (a := actual(m)) and (n := n_insvc(m))], 0.5)
    cov_min = (cov_base * COV_FRAC) if cov_base else None
    ho_valid = []
    for m in months:
        if m <= cut:
            continue
        a, n = actual(m), n_insvc(m)
        if a is None:
            continue
        if cov_min and (not n or a / n < cov_min):
            continue
        ho_valid.append(m)
    return dict(carrier=carrier, rows=rows, train=train, fit=fit, n_eff=n_eff, by_mon=by_mon,
                months=months, actual=actual, ho_valid=ho_valid, cut=cut)


def _accum(fits, ms2type=None):
    """n-weighted avail/cyc accumulators at BOTH the sub-fleet (Master Series) and Type-family level.
    The family tier lets a thin sub-fleet borrow strength from its type (e.g. A320neo ← A320 family)."""
    ms2type = ms2type or {}
    msa = defaultdict(lambda: dict(w=0.0, avail=0.0, cyc=0.0))
    tya = defaultdict(lambda: dict(w=0.0, avail=0.0, cyc=0.0))
    for _, fit in fits:
        for sf, f in fit.items():
            if f["avail"] is None or f["cyc"] is None:
                continue
            w = f["n"]
            for acc, key in ((msa, sf), (tya, ms2type.get(sf, sf))):
                acc[key]["w"] += w
                acc[key]["avail"] += w * f["avail"]
                acc[key]["cyc"] += w * f["cyc"]
    return msa, tya


def _net(entry, own):
    """Accumulator entry minus this carrier's own contributions ⇒ true leave-one-carrier-out."""
    w, av, cy = entry["w"], entry["avail"], entry["cyc"]
    for f in own:
        w -= f["n"]; av -= f["n"] * f["avail"]; cy -= f["n"] * f["cyc"]
    return (w, av / w, cy / w) if w > 0 else None


def _prior_excl(msa, tya, own_fit, ms2type=None, hier=False):
    """Per sub-fleet prior with THIS carrier removed. With hier=True, a thin Master-Series prior is
    itself shrunk toward its Type-family prior (nested empirical Bayes) before the own-plateau blend."""
    ms2type = ms2type or {}
    own_by_type = defaultdict(list)
    for sf, f in own_fit.items():
        if f["avail"] is not None and f["cyc"] is not None:
            own_by_type[ms2type.get(sf, sf)].append(f)
    prior = {}
    for sf, a in msa.items():
        f = own_fit.get(sf)
        own = [f] if (f and f["avail"] is not None and f["cyc"] is not None) else []
        ms = _net(a, own)
        if hier:
            t = ms2type.get(sf, sf)
            ty = _net(tya[t], own_by_type.get(t, [])) if t in tya else None
            if ms and ty:
                sw = ms[0] / (ms[0] + TYPE_K)     # rich MS prior → keep it; thin → lean on the family
                prior[sf] = dict(avail=sw * ms[1] + (1 - sw) * ty[1], cyc=sw * ms[2] + (1 - sw) * ty[2])
            elif ms:
                prior[sf] = dict(avail=ms[1], cyc=ms[2])
            elif ty:
                prior[sf] = dict(avail=ty[1], cyc=ty[2])
        elif ms:
            prior[sf] = dict(avail=ms[1], cyc=ms[2])
    return prior


def _shrink(own_fit, prior):
    """Empirical-Bayes blend of own plateau toward the panel prior, by sample size (avail & cyc)."""
    out = {}
    for sf, f in own_fit.items():
        p = prior.get(sf)
        g = dict(f)
        if p and f["avail"] is not None and f["cyc"] is not None:
            w = f["n"] / (f["n"] + SHRINK_K)
            g["avail"] = w * f["avail"] + (1 - w) * p["avail"]
            g["cyc"] = w * f["cyc"] + (1 - w) * p["cyc"]
        out[sf] = g
    return out


def _carrier_hat(fit, cd, mon):
    return sum(h for r in cd["by_mon"][mon]
               if (h := _hat_cycles(fit, r["sf"], mon, cd["n_eff"].get((r["sf"], mon), r["n_insvc"]))))


def _loco_one(cd, prior):
    """LOCO forecast for one carrier: shrunk point forecast + OOS lognormal predictive (mu, sd)."""
    cut = cd["cut"]
    shrunk = _shrink(cd["fit"], prior)
    train_ms = [m for m in cd["months"] if m <= cut]

    # OOS band: leave-one-month-out, refit + reshrink, collect log(actual/hat)
    logr = []
    for m in train_ms:
        a = cd["actual"](m)
        if not a:
            continue
        fit_m = _fit_subfleet([r for r in cd["train"] if r["mon"] != m])
        h = _carrier_hat(_shrink(fit_m, prior), cd, m)
        if h > 0:
            logr.append(math.log(a / h))
    mu = st.fmean(logr) if logr else 0.0
    sd = st.pstdev(logr) if len(logr) > 1 else 0.15

    detail = []
    for m in cd["ho_valid"]:
        a, h = cd["actual"](m), _carrier_hat(shrunk, cd, m)
        if not h:
            continue
        # predictive median = hat * exp(mu) (carries the model's systematic bias)
        med = h * math.exp(mu)
        covers = {lv: (h * math.exp(mu - Z[lv] * sd) <= a <= h * math.exp(mu + Z[lv] * sd)) for lv in NOMINAL}
        detail.append(dict(mon=f"{m:%Y-%m}", actual=a, hat=med, err=(med - a) / a * 100, covers=covers))
    mape = _mean([abs(d["err"]) for d in detail]) if detail else None
    bias = _mean([d["err"] for d in detail]) if detail else None
    return dict(carrier=cd["carrier"], detail=detail, mape=mape, bias=bias,
                cov80=_mean([d["covers"][0.80] for d in detail]) if detail else None)


async def run_loco(cutoff="2025-06", n=24, dump=None, carriers=None, label="LOCO", hier=True):
    from datetime import date
    cut = date(int(cutoff[:4]), int(cutoff[5:7]), 1)
    cands = [{"carrier": c} for c in carriers] if carriers else await _candidates(cutoff, n)
    print(f"loading {len(cands)} carriers… [{label}{' +hier' if hier else ''}]")
    ms2type = await _ms2type() if hier else {}
    loaded = []
    for c in cands:
        cd = await _load(c["carrier"], cut)
        if len(cd["ho_valid"]) >= MIN_VALID_HOLDOUT and cd["fit"]:
            loaded.append(cd)
    print(f"{len(loaded)} validatable carriers.\n")

    msa, tya = _accum([(cd["carrier"], cd["fit"]) for cd in loaded], ms2type)
    results = [_loco_one(cd, _prior_excl(msa, tya, cd["fit"], ms2type, hier)) for cd in loaded]
    results = [r for r in results if r["detail"]]
    results.sort(key=lambda r: r["mape"])

    print(f"=== {label} (leave-one-carrier-out, panel-prior shrinkage) ===")
    print(f"  {'carrier':28} {'mo':>3} {'MAPE':>6} {'bias':>7} {'80%cov':>7}")
    for r in results:
        print(f"  {r['carrier'][:28]:28} {len(r['detail']):>3} {r['mape']:>5.1f}% "
              f"{r['bias']:>+6.1f}% {r['cov80']*100:>6.0f}%")

    # reliability curve: pool every held-out carrier-month
    curve = {}
    allmonths = [d for r in results for d in r["detail"]]
    for lv in NOMINAL:
        curve[lv] = _mean([d["covers"][lv] for d in allmonths])
    print(f"\n  === RELIABILITY CURVE (n={len(allmonths)} carrier-months, {len(results)} carriers) ===")
    print(f"  {'nominal':>8} {'empirical':>10}   calibration")
    for lv in NOMINAL:
        emp = curve[lv]
        bar = "█" * round(emp * 30)
        print(f"  {lv*100:>6.0f}% {emp*100:>9.0f}%   {bar}")
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
    p = argparse.ArgumentParser(description="Formal LOCO gate with panel priors + reliability curve.")
    p.add_argument("--cutoff", default="2025-06")
    p.add_argument("--n", type=int, default=24)
    p.add_argument("--dump", default=None, help="write results JSON here (for the report)")
    a = p.parse_args(argv)
    asyncio.run(run_loco(a.cutoff, a.n, a.dump))


if __name__ == "__main__":
    main()
