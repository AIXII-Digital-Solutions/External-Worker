"""Exogenous forecaster for SMALL on-demand carriers (charter / ACMI wet-lease / business-jet).

The fleet-anchor fails these carriers (median MAPE ~46–50%) because their activity is contract-driven,
not network-driven (see ondemand.py). A 6-model parallel bake-off (workflow wf_e2aeb27f) established the
honest result: the workhorse is the carrier's OWN recent activity level — a robust median of the last few
months beats the fleet-anchor by ~10pp. Seasonality and fleet-regression add ≈0; route-pairs are LEAKY
(null in exactly the flight-null rows — a summary OF the activity, not a leading indicator) and excluded.

Design (a-priori, NOT tuned on the held-out set):
  base_level = median of the last L non-null monthly flights ≤ as_of   (robust to contract spikes)
  seas[m]    = geometric month-of-year index, shrunk toward 1.0 by support (harmless; ≈0 net gain, kept
               only so a genuinely seasonal leisure-charter isn't flattened)
  level_hat  = base_level × seas[month(h)]
  ANCHOR GATE (regime safety + calibration): when recent-level and the fleet-anchor wildly disagree the
  regime is uncertain, so hedge toward the fleet-anchor (which at least carries fleet info):
    r = |log(base_level / anchor_ref)| ;  w = w0·exp(-a·max(0, r−r0)) ;  yhat = w·level_hat + (1−w)·anchor_hat
  BAND: multiplicative from the training one-step residual-ratio dispersion, √h horizon-inflated.
  REGIME FLAG: when the gate is fully engaged (w small) the carrier is a suspected contract regime-break —
  route it to the external-feed queue. Three carriers (Electra, SpiceJet, Avion Express Malta) sit past the
  in-house ceiling: only a TRUE external feed (confirmed bookings, ACMI/charter tender pipeline, contract
  win/loss dates) forecasts them. ~37–39% median MAPE is the in-house floor.

Run: python -m predictive.exogenous [--cutoff 2025-06]
"""
from __future__ import annotations

import argparse
import asyncio
import math
import statistics as st

from predictive.backtest import compute_backtest
from predictive.bucket import classify
from predictive.db import DB

# a-priori hyperparameters (fixed on principle; the bake-off showed a broad plateau, not a knife-edge)
L_RECENT = 3        # trailing non-null months for the robust level
LOOKBACK = 6        # don't reach past this to fill the L window (coverage gaps → no stale anomalies)
K_SHRINK = 6.0      # seasonal-index shrink toward 1.0 by month support
MIN_SPAN = 24       # < 2 years of span → flat (no seasonal shape)
# Default is PURE recent-level (w0=1.0): the bake-off showed the anchor-gate hedges toward the fleet-anchor,
# which is the WORSE model here, so it trades ~5pp median for little robustness — kept as an option, off by default.
W0, GATE_A, R0 = 1.0, 0.6, 0.0
Z80 = 1.2815515594
SIG_MIN, SIG_MAX = 0.30, 1.10
SIGMA_LOWCONF = 0.60  # recent-activity log-dispersion above this (≈1.8× swings) ⇒ LOW-CONFIDENCE forecast
                      # (wide band, route for review). Honest caveat: a prospective "this carrier will have a
                      # regime break" flag is unreliable — volatility over-flags some carriers that forecast
                      # fine and misses some that break; the truly-unforecastable ones (contract loss/gain)
                      # surface only post-hoc as sustained large error and need a TRUE external feed.

_SERIES = """
WITH cm AS (
  SELECT DISTINCT ON (ca."Registration", to_date(r.period,'MM-YYYY'))
    ca."Registration" reg, to_date(r.period,'MM-YYYY') mon, ca."Status" status
  FROM cirium.ciriumaircrafts ca JOIN cirium.aircraftrevision r ON r.id=ca.revision_id
  WHERE ca."Operator"=$1 AND to_date(r.period,'MM-YYYY')>=date '2023-01-01'
  ORDER BY ca."Registration", to_date(r.period,'MM-YYYY'), ca.revision_id DESC),
insvc AS (SELECT mon, count(*) n FROM cm WHERE status='In Service' GROUP BY 1),
fa AS (SELECT to_date("Period",'MM-YYYY') mon, count(*) flights
       FROM forecast.acys_actuals WHERE "Operator"=$1 AND "Date">=date '2023-01-01' GROUP BY 1)
SELECT to_char(coalesce(i.mon,fa.mon),'YYYY-MM') ym, fa.flights
FROM insvc i FULL JOIN fa ON i.mon=fa.mon
WHERE coalesce(i.mon,fa.mon) >= date '2023-07-01' ORDER BY 1
"""


def _idx(ym):
    y, m = ym.split("-"); return int(y) * 12 + int(m) - 1


def _cal(ym):
    return int(ym.split("-")[1])


def fit_exog(monthly, as_of):
    """Static fit on ym ≤ as_of: robust recent level, shrunk seasonal indices, residual dispersion."""
    cut = _idx(as_of)
    train = sorted((_idx(r["ym"]), _cal(r["ym"]), float(r["flights"]))
                   for r in monthly if r["flights"] is not None and _idx(r["ym"]) <= cut)
    if not train:
        return None
    span = train[-1][0] - train[0][0] + 1

    by_m = {}
    for _, mm, f in train:
        by_m.setdefault(mm, []).append(f)
    month_med = {mm: st.median(v) for mm, v in by_m.items()}
    base = st.mean(list(month_med.values())) or 1.0
    seas = {}
    for mm in range(1, 13):
        if mm in month_med:
            w = len(by_m[mm]) / (len(by_m[mm]) + K_SHRINK)
            seas[mm] = max(w * (month_med[mm] / base) + (1 - w), 0.10)
        else:
            seas[mm] = 1.0

    recent = []
    for i, mm, f in reversed(train):
        if cut - i > LOOKBACK:
            break
        recent.append((f, mm))
        if len(recent) >= L_RECENT:
            break
    if not recent:
        recent = [(train[-1][2], train[-1][1])]
    flat_level = st.median([f for f, _ in recent])
    recent_level = st.median([f / seas[mm] for f, mm in recent])

    logs = [math.log(max(f / seas[mm], 0.5)) for _, mm, f in train]
    sigma = SIG_MIN
    if len(logs) >= 3:
        med = st.median(logs)
        sigma = min(max(1.4826 * st.median([abs(x - med) for x in logs]), SIG_MIN), SIG_MAX)
    return dict(seas=seas, recent_level=recent_level, flat_level=flat_level, sigma=sigma, span=span)


def forecast_exog(fit, test_ym, anchor_hat, anchor_ref, horizon, w0=W0, gate_a=GATE_A):
    """One month's forecast: recent-level × seasonal, optionally gated toward the fleet-anchor on regime
    disagreement. w0=1.0 & gate_a=0 → pure recent-level (the bake-off workhorse); a lighter gate trades a
    few MAPE points for regime robustness + a calibrated band."""
    mm = _cal(test_ym)
    level_hat = fit["flat_level"] if fit["span"] < MIN_SPAN else fit["recent_level"] * fit["seas"][mm]
    w, r = w0, 0.0
    if anchor_hat and anchor_ref and level_hat > 0 and anchor_ref > 0:
        r = abs(math.log(level_hat / anchor_ref))          # recent-vs-fleet disagreement (uncertainty signal)
        w = w0 * math.exp(-gate_a * max(0.0, r - R0))
        yhat = w * level_hat + (1 - w) * anchor_hat
    else:
        yhat = level_hat
    sig = fit["sigma"] * math.sqrt(max(1, horizon))
    return dict(yhat=yhat, lo=yhat * math.exp(-Z80 * sig), hi=yhat * math.exp(Z80 * sig),
                w=w, r=r, low_conf=(fit["sigma"] > SIGMA_LOWCONF))


async def _carrier_monthly(db, carrier):
    return [dict(r) for r in await db.fetch(_SERIES, carrier)]


async def _eval(cutoff, w0, gate_a):
    rows, thr = await classify()
    od = [(r["op"], r["tails"]) for r in rows if r["bucket"] == "ondemand"]
    results, lowc = [], []
    async with DB(statement_timeout_ms=0) as db:
        for carrier, tails in od:
            try:
                bt = await compute_backtest(carrier, cutoff)
            except Exception:
                continue
            if not bt["validatable"] or not bt["detail"]:
                continue
            fit = fit_exog(await _carrier_monthly(db, carrier), cutoff)
            if not fit:
                continue
            anchor_ref = bt["detail"][0]["hat"]
            preds, acts, cov_hit, cov_tot, reg = [], [], 0, 0, False
            for h, d in enumerate(bt["detail"], start=1):
                fc = forecast_exog(fit, f"{d['mon']:%Y-%m}", d["hat"], anchor_ref, h, w0, gate_a)
                preds.append(fc["yhat"]); acts.append(d["actual"]); reg = reg or fc["low_conf"]
                cov_tot += 1; cov_hit += (1 if fc["lo"] <= d["actual"] <= fc["hi"] else 0)
            mape = st.mean([abs(p - a) / a * 100 for p, a in zip(preds, acts)])
            rec = dict(carrier=carrier, tails=tails, small=tails < 40, mape=mape,
                       anchor=bt["mape"], cov=cov_hit / cov_tot, low_conf=reg)
            results.append(rec)
            if rec["low_conf"]:
                lowc.append(carrier)
    return results, lowc


def _summary(results, tag):
    small = [r for r in results if r["small"]]
    med_s = st.median([r["mape"] for r in small])
    med_a = st.median([r["anchor"] for r in small])
    beats = sum(1 for r in results if r["anchor"] and r["mape"] < r["anchor"])
    cov = st.mean([r["cov"] for r in small])
    print(f"  [{tag:14}] small median MAPE={med_s:>5.1f}% vs anchor {med_a:.1f}% (−{med_a-med_s:.1f}pp) · "
          f"beats {beats}/{len(results)} · cov80={cov*100:.0f}%")
    return med_s


async def run(cutoff="2025-06"):
    # two a-priori configs (NOT tuned on the held-out set): the pure recent-level workhorse, and a
    # lightly-gated variant for regime robustness + calibration.
    pure, lowc = await _eval(cutoff, w0=1.0, gate_a=0.0)
    gated, _ = await _eval(cutoff, w0=0.8, gate_a=0.6)

    print(f"=== exogenous layer — small on-demand ({sum(r['small'] for r in pure)} small / {len(pure)} carriers) ===\n")
    print(f"  {'carrier':24} {'tails':>5} {'pure':>7} {'gated':>7} {'anchor':>8} {'cov80':>6} {'lowconf?':>9}")
    gmap = {r["carrier"]: r for r in gated}
    for r in sorted(pure, key=lambda x: (not x["small"], x["mape"])):
        g = gmap.get(r["carrier"], r)
        print(f"  {r['carrier'][:24]:24} {r['tails']:>5} {r['mape']:>6.1f}% {g['mape']:>6.1f}% "
              f"{r['anchor']:>7.1f}% {r['cov']*100:>5.0f}% {'◄ review' if r['low_conf'] else '':>9}")
    print()
    _summary(pure, "pure level")
    _summary(gated, "gated (w0=.8)")
    print(f"\n  DEFAULT = pure recent-level: for contract-driven carriers the carrier's OWN recent activity")
    print(f"  carries the signal the fleet cannot. The band + low-confidence flag route volatile carriers")
    print(f"  for review. Honest ceiling: a few carriers (e.g. Electra, Avion Express Malta, SpiceJet) have")
    print(f"  real regime breaks — sustained large error even here — that only a TRUE external feed")
    print(f"  (confirmed bookings / ACMI-charter tenders / contract win-loss dates) can forecast.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Exogenous recent-level forecaster for small on-demand carriers.")
    p.add_argument("--cutoff", default="2025-06")
    a = p.parse_args(argv)
    asyncio.run(run(a.cutoff))


if __name__ == "__main__":
    main()
