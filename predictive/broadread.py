"""Broad read (handoff_v2 spiral): run the anchor self-backtest across many schedule carriers to
answer ONE question — is the SCAT finding (linear N-scaling over-predicts when the fleet grows into a
low season) a GENERAL hole in the fleet-anchor, or SCAT-specific?

Lever = n_growth (held-out mean fleet / last-12 training mean fleet). Signal = forecast bias (mean
err% = (hat-actual)/actual). If carriers that GREW show systematic positive bias (over-predict) while
stable-fleet carriers sit near zero, the anchor's `cycles ∝ N` assumption is the culprit (GENERAL).
If bias is uncorrelated with growth, the SCAT miss was seasonal-calibration / noise, not the N-lever.

Run: python -m predictive.broadread [--cutoff 2025-06] [--n 15]
"""
from __future__ import annotations

import argparse
import asyncio

from predictive.backtest import compute_backtest, _mean, _q
from predictive.db import DB

# top schedule carriers by flight volume with enough train + held-out coverage since 2023-07
_CANDIDATES_SQL = """
SELECT "Operator" AS carrier,
       count(*)                                        AS flights,
       count(DISTINCT to_date("Period",'MM-YYYY'))     AS months,
       count(DISTINCT to_date("Period",'MM-YYYY'))
         FILTER (WHERE to_date("Period",'MM-YYYY') > date '{cut}') AS ho_months
FROM forecast.acys_actuals
WHERE "Operator" IS NOT NULL AND "Operator" <> ''
  AND to_date("Period",'MM-YYYY') >= date '2023-07-01'
GROUP BY 1
HAVING count(DISTINCT to_date("Period",'MM-YYYY')) >= 24
   AND count(DISTINCT to_date("Period",'MM-YYYY'))
         FILTER (WHERE to_date("Period",'MM-YYYY') > date '{cut}') >= 4
ORDER BY flights DESC
LIMIT {n}
"""


def _pearson(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    mx, my = _mean(xs), _mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else None


async def _candidates(cutoff: str, n: int):
    async with DB() as db:
        sql = _CANDIDATES_SQL.replace("{cut}", cutoff + "-01").replace("{n}", str(n))
        return await db.fetch(sql)


async def broad_read(cutoff: str = "2025-06", n: int = 15):
    cands = await _candidates(cutoff, n)
    print(f"=== broad read: {len(cands)} schedule carriers, self-backtest (train ≤ {cutoff}) ===")
    print(f"  (held-out months with collapsed FR24 coverage are excluded; carriers with <3 valid held-out "
          f"months are unvalidatable)\n")
    print(f"  {'carrier':28} {'cyc/tl':>6} {'val':>3} {'ex':>3} {'N_grow':>7} {'MAPE':>6} {'bias':>7} {'cover':>6}")
    results, unvalidatable = [], []
    for c in cands:
        try:
            r = await compute_backtest(c["carrier"], cutoff)
        except Exception as e:  # a bad carrier shouldn't kill the sweep
            print(f"  {c['carrier'][:28]:28}  ERROR {type(e).__name__}: {e}")
            continue
        cb = r["cov_base"] or 0
        if not r["validatable"] or r["n_growth"] is None:
            unvalidatable.append((c["carrier"], cb, r["n_holdout"], r["n_excluded"]))
            print(f"  {c['carrier'][:28]:28} {cb:>6.0f} {r['n_holdout']:>3} {r['n_excluded']:>3}   "
                  f"--- unvalidatable (coverage) ---")
            continue
        results.append(r)
        print(f"  {c['carrier'][:28]:28} {cb:>6.0f} {r['n_holdout']:>3} {r['n_excluded']:>3} "
              f"{r['n_growth']:>7.3f} {r['mape']:>5.1f}% {r['mean_bias']:>+6.1f}% {r['coverage']*100:>5.0f}%")

    if unvalidatable:
        print(f"\n  excluded {len(unvalidatable)} carrier(s) for coverage: "
              f"{', '.join(u[0] for u in unvalidatable)}")
    if len(results) < 3:
        print("\n  (too few validatable carriers to read a relationship)")
        return

    growth = [r["n_growth"] for r in results]
    bias = [r["mean_bias"] for r in results]
    rho = _pearson(growth, bias)

    grew = [r for r in results if r["n_growth"] > 1.05]
    stable = [r for r in results if 0.95 <= r["n_growth"] <= 1.05]
    shrank = [r for r in results if r["n_growth"] < 0.95]

    print(f"\n  === THE READ: is activity ∝ N (fleet-anchor core)? ===")
    print(f"  carriers={len(results)}  Pearson(N_growth, bias) = "
          f"{'n/a' if rho is None else f'{rho:+.2f}'}   [+strong ⇒ growth drives over-prediction ⇒ GENERAL hole]")
    for label, grp in [("grew  (N_grow>1.05)", grew), ("stable(0.95–1.05) ", stable),
                       ("shrank(N_grow<0.95)", shrank)]:
        if grp:
            print(f"    {label}: n={len(grp):>2}  mean bias={_mean([g['mean_bias'] for g in grp]):+6.1f}%  "
                  f"mean MAPE={_mean([g['mape'] for g in grp]):5.1f}%  "
                  f"mean cover={_mean([g['coverage'] for g in grp])*100:3.0f}%")
    print(f"\n  overall: median MAPE={_q([r['mape'] for r in results],0.5):.1f}%  "
          f"median bias={_q(bias,0.5):+.1f}%  "
          f"median coverage={_q([r['coverage'] for r in results],0.5)*100:.0f}%")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Cross-carrier broad read of the anchor self-backtest.")
    p.add_argument("--cutoff", default="2025-06", help="last training month YYYY-MM (default 2025-06)")
    p.add_argument("--n", type=int, default=15, help="how many top-volume carriers to sweep")
    a = p.parse_args(argv)
    asyncio.run(broad_read(a.cutoff, a.n))


if __name__ == "__main__":
    main()
