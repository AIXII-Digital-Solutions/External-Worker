"""Seat-density → primary-usage fallback (handoff §5.1 entry blocker).

8.2% of FLOWN tails carry a NULL Cirium `Primary Usage`, yet the conversion layer branches on it (pax
applies to passenger aircraft, not freighters). Seat count discriminates cleanly (measured on tails WITH
a label): Passenger seats p10/p50 = 65/174; Freight/Cargo is 99% zero-seat; business/VIP/utility sit low.
So we resolve a missing usage from the airframe:

    seats ≥ SEAT_PAX_MIN                         → Passenger
    seats < SEAT_CARGO_MAX and payload ≥ FREIGHTER_LBS  → Freight/Cargo   (a real freighter, not a bizjet)
    otherwise (flown commercial tail)            → Passenger   (default; bizjet/VIP are on-demand-bucket)

This module both VALIDATES the rule (accuracy vs the known labels) and reports how many flown NULL tails
it recovers. Higher = more of the fleet correctly routed through the pax-vs-freight conversion branch.

Run: python -m predictive.usage
"""
from __future__ import annotations

import argparse
import asyncio

from predictive.db import DB

SEAT_PAX_MIN = 40        # ≥ this many seats ⇒ commercial passenger aircraft
SEAT_CARGO_MAX = 10      # < this many seats ⇒ candidate freighter (needs payload confirmation)
FREIGHTER_LBS = 20000    # max payload above this on a low-seat airframe ⇒ true freighter (not a bizjet)


def usage_case_sql(seats="seats", payload="payload", usage="usage"):
    """SQL CASE that returns the effective usage: the label if present, else the seat/payload fallback.
    Reusable inside the panel/conversion so pax-vs-freight routing never silently drops NULL-usage tails."""
    return f"""CASE
        WHEN nullif({usage},'') IS NOT NULL THEN {usage}
        WHEN {seats} >= {SEAT_PAX_MIN} THEN 'Passenger'
        WHEN coalesce({seats},0) < {SEAT_CARGO_MAX} AND coalesce({payload},0) >= {FREIGHTER_LBS} THEN 'Freight / Cargo'
        ELSE 'Passenger'
      END"""


async def run():
    async with DB(statement_timeout_ms=0) as db:
        # latest Cirium revision per tail; restrict to FLOWN tails (present in acys_actuals)
        latest = """
        WITH flown AS (SELECT DISTINCT "Registration" reg FROM forecast.acys_actuals),
        cur AS (
          SELECT DISTINCT ON (ca."Registration") ca."Registration" reg,
                 nullif(ca."Primary Usage",'') usage, ca."Number of Seats" seats,
                 ca."Max Payload (lbs)" payload
          FROM cirium.ciriumaircrafts ca JOIN cirium.aircraftrevision r ON r.id=ca.revision_id
          JOIN flown f ON f.reg = ca."Registration"
          ORDER BY ca."Registration", ca.revision_id DESC)
        SELECT * FROM cur"""
        rows = await db.fetch(latest)

    def coarse(u):
        if not u:
            return None
        if u == "Passenger":
            return "Passenger"
        if "Cargo" in u or "Freight" in u:
            return "Freight / Cargo"
        return "Other"

    def infer(seats, payload):
        if seats is not None and seats >= SEAT_PAX_MIN:
            return "Passenger"
        if (seats or 0) < SEAT_CARGO_MAX and (payload or 0) >= FREIGHTER_LBS:
            return "Freight / Cargo"
        return "Passenger"

    n = len(rows)
    known = [r for r in rows if r["usage"]]
    nullu = [r for r in rows if not r["usage"]]

    # 1) validate the rule on tails WITH a label (accuracy on Passenger vs Freight)
    val = [r for r in known if coarse(r["usage"]) in ("Passenger", "Freight / Cargo")]
    correct = sum(1 for r in val if infer(r["seats"], r["payload"]) == coarse(r["usage"]))
    # freight recall specifically (the case pax-default would get wrong)
    frt = [r for r in val if coarse(r["usage"]) == "Freight / Cargo"]
    frt_ok = sum(1 for r in frt if infer(r["seats"], r["payload"]) == "Freight / Cargo")

    # 2) how the fallback resolves the NULL-usage flown tails
    res = {"Passenger": 0, "Freight / Cargo": 0}
    for r in nullu:
        res[infer(r["seats"], r["payload"])] += 1

    print(f"=== seat-density usage fallback — flown tails ===")
    print(f"  flown tails: {n}   labelled: {len(known)} ({len(known)/n*100:.1f}%)   "
          f"NULL usage: {len(nullu)} ({len(nullu)/n*100:.1f}%)")
    print(f"\n  RULE: seats≥{SEAT_PAX_MIN}→Passenger · seats<{SEAT_CARGO_MAX} & payload≥{FREIGHTER_LBS}lbs→Freight · else Passenger")
    print(f"\n  validation on {len(val)} labelled pax/freight tails:")
    print(f"    overall accuracy      = {correct/len(val)*100:.1f}%")
    print(f"    freighter recall      = {frt_ok}/{len(frt)} ({frt_ok/len(frt)*100:.0f}%)  "
          f"(the case a pax-default would miss)")
    print(f"\n  resolved {len(nullu)} NULL-usage flown tails → "
          f"Passenger {res['Passenger']} ({res['Passenger']/len(nullu)*100:.0f}%), "
          f"Freight {res['Freight / Cargo']} ({res['Freight / Cargo']/len(nullu)*100:.0f}%)")


def main(argv=None) -> None:
    argparse.ArgumentParser(description="Seat-density usage fallback: validate + resolve NULL usage.").parse_args(argv)
    asyncio.run(run())


if __name__ == "__main__":
    main()
