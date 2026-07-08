"""On-demand fork (handoff step-1 read: "the on-demand bucket needs its own low-recurrence intensity
model"; archetype: "pool within bucket").

Empirical premise test for the OTHER bucket. The schedule fleet-anchor assumes a frozen network and a
stable plateau; on-demand carriers (charter / ACMI wet-lease / business-jet / cargo) violate both —
their activity is contract-driven, not network-driven. Measured (backtest.compute_backtest, schedule
priors): the same anchor scores median MAPE ~46% on-demand vs 9.4% on schedule.

What this fork establishes, honestly:
  1. classify the bucket (bucket.classify) and route;
  2. pool priors WITHIN the on-demand bucket and read its OWN reliability curve — can the (naturally
     wider, volatility-driven) band still be CALIBRATED even when point accuracy is poor? An honest
     "we don't know precisely, and here is how wide that is" beats a confident wrong number;
  3. surface the fleet-SIZE dependence: a large on-demand fleet (VistaJet, 102 tails) is aggregate-
     stable; small ACMI (10–25 tails) swing ±100% on a single contract and are not forecastable from
     fleet+network alone — they need exogenous booking/contract data (out of scope for this engine).

Run: python -m predictive.ondemand [--cutoff 2025-06]
"""
from __future__ import annotations

import argparse
import asyncio
import statistics as st

from predictive.bucket import classify
from predictive.loco import run_loco

SMALL_FLEET = 40   # tails; below this an on-demand carrier's monthly volume is contract-dominated


async def run(cutoff="2025-06"):
    rows, thr = await classify()
    od = [r for r in rows if r["bucket"] == "ondemand"]
    tails = {r["op"]: r["tails"] for r in rows}
    od_names = [r["op"] for r in od]
    print(f"on-demand bucket: {len(od_names)} carriers (valley reg_index={thr:+.2f})\n")

    results, curve = await run_loco(cutoff, carriers=od_names, label="ON-DEMAND LOCO")
    if not results:
        print("\n  no validatable on-demand carriers.")
        return

    big = [r for r in results if tails.get(r["carrier"], 0) >= SMALL_FLEET]
    small = [r for r in results if tails.get(r["carrier"], 0) < SMALL_FLEET]
    print(f"\n  === FORECASTABILITY BY FLEET SIZE ===")
    for lab, grp in [(f"large  (≥{SMALL_FLEET} tails)", big), (f"small  (<{SMALL_FLEET} tails)", small)]:
        if grp:
            print(f"    {lab}: n={len(grp):>2}  median MAPE={st.median([r['mape'] for r in grp]):5.1f}%  "
                  f"median 80%-cover={st.median([r['cov80'] for r in grp])*100:3.0f}%  "
                  f"({', '.join(r['carrier'] for r in grp)})")
    print(f"\n  READ: on-demand point accuracy is intrinsically poor (contract-driven), but the honest "
          f"question is whether the BAND is calibrated — see the reliability curve above. Small on-demand "
          f"fleets need exogenous booking data; the fleet-anchor premise does not extend to them.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="On-demand fork: bucket reliability + fleet-size forecastability.")
    p.add_argument("--cutoff", default="2025-06")
    a = p.parse_args(argv)
    asyncio.run(run(a.cutoff))


if __name__ == "__main__":
    main()
