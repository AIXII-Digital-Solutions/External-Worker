"""Deterministic conversion of the validated flights-anchor into the four business metrics + pax
(handoff §5.2 "coarse network shares + deterministic conversion").

Per sub-fleet, the frozen network fixes the sector-length mix (mean km/flight, from the plateau); the
four metrics then follow deterministically from the anchor's flights forecast:
    flights = cycles          = anchor output (validated: broad-read median MAPE 9.4%)
    km      = flights × mean_sector_km_sf
    block-h = flights × (BLOCK_OVERHEAD_H + mean_sector_km_sf / CRUISE_KMH)   # distance-based, NOT raw
                                                                             #   Flight Time (broken for
                                                                             #   old sub-fleets)
    pax     = flights × seats_sf × load_factor                               # LF exogenous (a projection)

Block-time model calibrated on 3.2M flights (10 well-covered carriers, R²=0.985):
    block_h = 0.411 + km / 868   (overhead 25 min ground/climb/descent, 868 km/h effective cruise).

km has an OBSERVED actual (Σ Circle Distance), so validating km-per-flight ALSO tests the frozen-network
assumption: if the carrier opened new long/short routes, mean_sector_km drifts and km errs even when
flights are right. pax has no observed actual (no passenger counts) — it is a labelled projection.

Run: python -m predictive.convert --carrier "SCAT Airlines" --cutoff 2025-06 [--lf 0.80]
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from predictive.backtest import (COV_FRAC, _fit_subfleet, _hat, _mean, _q)
from predictive.slice import subfleet_month_panel

BLOCK_OVERHEAD_H = 0.411   # measured: fixed ground+climb+descent overhead per flight
CRUISE_KMH = 868.0         # measured: effective cruise (great-circle km / airborne-ish hour)
DEFAULT_LF = 0.80          # exogenous load factor for the pax projection
SEASON_KM_K = 4.0          # seasonal-km shrinkage: months-support at which the mask is half-applied


def _block_h(km):
    return BLOCK_OVERHEAD_H + km / CRUISE_KMH


async def carrier_metric_forecast(carrier: str, cutoff: str = "2025-06", lf: float = DEFAULT_LF) -> dict:
    """Forecast the four metrics + pax per held-out month and compare flights & km to observed actuals."""
    from datetime import date
    cut = date(int(cutoff[:4]), int(cutoff[5:7]), 1)
    rows = await subfleet_month_panel(carrier)
    train = [r for r in rows if r["mon"] <= cut]
    fit = _fit_subfleet(train)
    months = sorted({r["mon"] for r in rows})
    by_mon = defaultdict(list)
    for r in rows:
        by_mon[r["mon"]].append(r)

    # representative seats + SEASONAL network mask (km/flight by calendar month) per sub-fleet
    seats, skm = {}, {}
    by_sf = defaultdict(list)
    for r in train:
        by_sf[r["sf"]].append(r)
    for sf, rs in by_sf.items():
        seats[sf] = _q([r["seats_med"] for r in rs if r["seats_med"]], 0.5)
        by_cal = defaultdict(list)
        for r in rs:
            if r["sum_km"] and r["cycles"]:
                by_cal[r["mon"].month].append(r["sum_km"] / r["cycles"])
        skm[sf] = {c: (_mean(v), len(v)) for c, v in by_cal.items()}   # (seasonal km/flight, n months)

    def kmpf_seasonal(sf, mon):
        """Seasonal km/flight, SHRUNK toward the flat mean by month support (a 2-yr calendar estimate is
        noisy → apply seasonality only as far as the evidence carries it, so stable networks aren't hurt)."""
        flat = fit[sf].get("kmpf") if sf in fit else None
        s = skm.get(sf, {}).get(mon.month)
        if not s or s[0] is None or flat is None:
            return flat
        w = s[1] / (s[1] + SEASON_KM_K)
        return w * s[0] + (1 - w) * flat

    def actual(mon, key):
        vals = [r[key] for r in by_mon[mon] if r[key] is not None]
        return sum(vals) if vals else None

    # coverage gate on flights (drops the FR24-lag tail), matching backtest.compute_backtest
    cov_base = _q([a for m in months if m <= cut and (a := actual(m, "cycles"))], 0.5)
    cov_min = (cov_base * COV_FRAC) if cov_base else None

    detail = []
    for m in months:
        if m <= cut:
            continue
        af = actual(m, "cycles")
        if cov_min and (af is None or af < cov_min):
            continue  # coverage-collapsed month — no valid actual to compare
        fh = kmh = kmh_flat = bhh = paxh = 0.0
        for r in by_mon[m]:
            f = _hat(fit, r["sf"], m)
            if not f:
                continue
            flat = (fit[r["sf"]].get("kmpf") if r["sf"] in fit else None) or 0.0
            seas = kmpf_seasonal(r["sf"], m) or flat
            fh += f
            kmh += f * seas          # seasonal network mask
            kmh_flat += f * flat     # flat mean, for the mask's delta
            bhh += f * _block_h(seas)
            paxh += f * (seats.get(r["sf"]) or 0.0) * lf
        if not fh:
            continue
        detail.append(dict(
            mon=m, flights_hat=fh, km_hat=kmh, km_hat_flat=kmh_flat, bh_hat=bhh, pax_hat=paxh,
            flights_act=actual(m, "cycles"), km_act=actual(m, "sum_km"),
            bh_act=actual(m, "block_hours"),   # raw — may be broken for old sub-fleets
        ))

    def mape(key_h, key_a):
        es = [abs(d[key_h] - d[key_a]) / d[key_a] * 100 for d in detail if d.get(key_a)]
        return _mean(es) if es else None

    return dict(carrier=carrier, cut=cut, lf=lf, detail=detail,
                flights_mape=mape("flights_hat", "flights_act"),
                km_mape=mape("km_hat", "km_act"),
                km_mape_flat=mape("km_hat_flat", "km_act"))


async def _main(carrier, cutoff, lf):
    r = await carrier_metric_forecast(carrier, cutoff, lf)
    print(f"=== {carrier}: four-metric forecast (train ≤ {cutoff}, LF={lf:.0%}) ===")
    print(f"  block-time model: {BLOCK_OVERHEAD_H:.3f}h + km/{CRUISE_KMH:.0f}")
    print(f"\n  {'month':8} {'flights':>18} {'km (000)':>18} {'block-h':>10} {'pax (000)':>10}")
    print(f"  {'':8} {'hat / act  err%':>18} {'hat / act  err%':>18} {'hat':>10} {'proj':>10}")
    for d in r["detail"]:
        fe = (d["flights_hat"] - d["flights_act"]) / d["flights_act"] * 100 if d["flights_act"] else None
        ke = (d["km_hat"] - d["km_act"]) / d["km_act"] * 100 if d["km_act"] else None
        print(f"  {d['mon']:%Y-%m}  {d['flights_hat']:6.0f}/{d['flights_act'] or 0:6.0f} "
              f"{('%+.0f' % fe) if fe is not None else '  .':>5}%  "
              f"{d['km_hat']/1000:6.0f}/{(d['km_act'] or 0)/1000:6.0f} "
              f"{('%+.0f' % ke) if ke is not None else '  .':>5}%  "
              f"{d['bh_hat']:9.0f} {d['pax_hat']/1000:9.0f}")
    print(f"\n  READ: flights MAPE={r['flights_mape']:.1f}%   km MAPE={r['km_mape']:.1f}%   "
          f"(km MAPE also tests the frozen-network assumption)")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Deterministic four-metric conversion for one carrier.")
    p.add_argument("--carrier", required=True)
    p.add_argument("--cutoff", default="2025-06")
    p.add_argument("--lf", type=float, default=DEFAULT_LF, help="exogenous load factor (pax projection)")
    a = p.parse_args(argv)
    asyncio.run(_main(a.carrier, a.cutoff, a.lf))


if __name__ == "__main__":
    main()
