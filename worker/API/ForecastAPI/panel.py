"""Forecast data-prep panel — the production run (external-worker), with per-step status.

Assembles forecast.history_1 (Cirium x FR24, date-respecting) then merges history_1 + future_1 into
forecast.final_1 (airport-enriched). Mirrors predictive/panel in Core-API (the standalone harness);
the SQL is intentionally vendored here (like the ORM models are) so the worker has no Core-API dep.

Publishes a SEQUENTIAL status per step (job_statuses row + status:events) so the portal can render
progress live. `period` is 'MM-YYYY' text -> always parsed with to_date; the Cirium<->FR24 match is
DATE-RESPECTING (flight month = period month); final_1 keeps FLIGHTS ONLY.
"""
from datetime import date

from sqlalchemy import text

from Config import setup_logger
from status import publish_status

logger = setup_logger("forecast_panel")

# Inclusive lower bound for both the Cirium period and the flight date (task: "start 07-2023").
HISTORY_START = date(2023, 7, 1)

# All forecast tables + cirium/flightradar/main live in the ONE physical `aixii` DB, so any aviation
# logical name routes there.
_DB = "cirium"

_MISSING_TAILS_SQL = """
SELECT DISTINCT ca."Registration" AS reg
FROM cirium.ciriumaircrafts ca
JOIN cirium.aircraftrevision r ON r.id = ca.revision_id
WHERE ca."Operator" = :op
  AND to_date(r.period,'MM-YYYY') >= :start_date
  AND NOT EXISTS (SELECT 1 FROM flightradar.flightsummary f WHERE f.reg = ca."Registration")
"""

_ASSEMBLE_SQL = """
INSERT INTO forecast.history_1
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage")
WITH array5 AS (
    SELECT DISTINCT ON (ca."Registration", to_date(r.period,'MM-YYYY'))
           ca."Registration"          AS registration,
           r.period                    AS period,
           to_date(r.period,'MM-YYYY') AS period_month,
           ca."Operator"               AS operator,
           ca."Master Series"          AS master_series,
           ca."Manufacturer"           AS manufacturer,
           ca."Aircraft Sub Series"    AS sub_series,
           ca."Primary Usage"          AS primary_usage
    FROM cirium.ciriumaircrafts ca
    JOIN cirium.aircraftrevision r ON r.id = ca.revision_id
    WHERE ca."Operator" = :op
      AND to_date(r.period,'MM-YYYY') >= :start_date
    ORDER BY ca."Registration", to_date(r.period,'MM-YYYY'), ca.revision_id DESC
),
array6 AS (
    SELECT f.reg, f.datetime_takeoff, f.datetime_landed,
           f.orig_iata, f.dest_iata, f.dest_iata_actual,
           coalesce(f.datetime_takeoff, f.first_seen) AS flight_dt
    FROM flightradar.flightsummary f
    WHERE f.reg IN (SELECT registration FROM array5)
      AND coalesce(f.datetime_takeoff, f.first_seen) >= :start_date
      AND coalesce(f.datetime_takeoff, f.first_seen) <  :as_of
)
SELECT
    a5.registration, a5.period,
    CAST(a6.flight_dt AS date),
    a6.datetime_takeoff, a6.datetime_landed,
    a6.orig_iata, a6.dest_iata, a6.dest_iata_actual,
    a5.operator, a5.master_series, a5.manufacturer, a5.sub_series, a5.primary_usage
FROM array5 a5
LEFT JOIN array6 a6
       ON a6.reg = a5.registration
      AND date_trunc('month', a6.flight_dt) = a5.period_month
"""

_MERGE_SQL = """
INSERT INTO forecast.final_1
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Origin Country","Origin City","Origin Airport Name",
     "Destination Country","Destination City","Destination Airport Name")
WITH airports AS (
    SELECT DISTINCT ON ("IATA Code")
           "IATA Code" AS iata, "Country" AS country, "City" AS city, "Airport Name" AS airport_name
    FROM main.virtual_airport_list
    WHERE "IATA Code" IS NOT NULL AND "IATA Code" <> ''
    ORDER BY "IATA Code"
),
panel AS (
    SELECT "Registration","Period","Date","Time Departed","Time Landed",
           "IATA Origin","IATA Destination","IATA Destination Actual",
           "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage"
    FROM forecast.history_1
    WHERE "Date" IS NOT NULL
    UNION ALL
    SELECT "Registration","Period","Date","Time Departed","Time Landed",
           "IATA Origin","IATA Destination","IATA Destination Actual",
           "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage"
    FROM forecast.future_1
)
SELECT p.*,
       o.country, o.city, o.airport_name,
       d.country, d.city, d.airport_name
FROM panel p
LEFT JOIN airports o ON o.iata = p."IATA Origin"
LEFT JOIN airports d ON d.iata = p."IATA Destination"
"""


async def run_forecast_panel(*, db_client, redis, job_id: str, ref: str,
                             operator: str, as_of: date | None = None) -> dict:
    """Run the full forecast panel for one operator, publishing a status per step. Returns a summary
    dict. Raises on failure (after publishing an `error` status)."""
    as_of = as_of or date.today()

    async def _pub(state: str, message: str, progress: int | None = None, payload=None) -> None:
        kwargs = {}
        if progress is not None:
            kwargs["progress"] = progress
        if payload is not None:
            kwargs["payload"] = payload
        await publish_status(db_client, redis, job_id=job_id, kind="external", ref=ref,
                             state=state, message=message, **kwargs)

    try:
        # 1/6 — validate the operator exists in the Cirium fleet
        await _pub("running", f"Validating operator '{operator}'", progress=5)
        async with db_client.session(_DB) as s:
            found = (await s.execute(
                text('SELECT 1 FROM cirium.ciriumaircrafts WHERE "Operator" = :op LIMIT 1'),
                {"op": operator},
            )).first()
        if not found:
            await _pub("error", f"Operator '{operator}' not found in the Cirium fleet")
            raise ValueError(f"operator not found: {operator}")

        # 2/6 — prepare (truncate the working tables for this per-request rebuild)
        await _pub("running", "Preparing tables (history_1 / final_1)", progress=10)
        async with db_client.session(_DB) as s:
            await s.execute(text("TRUNCATE forecast.history_1"))
            await s.execute(text("TRUNCATE forecast.final_1"))
            await s.commit()

        # 3/6 — FR24 coverage check (backfill is a future step; here we just report the gap)
        await _pub("running", "Checking FR24 flight coverage for the fleet", progress=20)
        async with db_client.session(_DB) as s:
            miss_rows = (await s.execute(text(_MISSING_TAILS_SQL),
                                         {"op": operator, "start_date": HISTORY_START})).fetchall()
        tails_without_fr24 = [r[0] for r in miss_rows]
        if tails_without_fr24:
            await _pub("running",
                       f"{len(tails_without_fr24)} tail(s) have no FR24 data "
                       f"(FR24 backfill not yet wired) — they contribute Cirium-only rows",
                       progress=25, payload={"tails_without_fr24": tails_without_fr24[:50]})

        # 4/6 — assemble history_1 (Cirium x FR24, date-respecting)
        await _pub("running", f"Assembling history (Cirium × FR24, as of {as_of})", progress=30)
        async with db_client.session(_DB) as s:
            res = await s.execute(text(_ASSEMBLE_SQL),
                                  {"op": operator, "start_date": HISTORY_START, "as_of": as_of})
            history_rows = res.rowcount
            await s.commit()
        await _pub("running", f"History assembled: {history_rows} row(s)", progress=70,
                   payload={"history_rows": history_rows, "tails_without_fr24": len(tails_without_fr24)})

        # 5/6 — merge into final_1 (flights only + airport geography)
        await _pub("running", "Merging into final (airport geography)", progress=80)
        async with db_client.session(_DB) as s:
            res = await s.execute(text(_MERGE_SQL))
            final_rows = res.rowcount
            await s.commit()

        # 6/6 — done
        summary = {
            "operator": operator, "as_of": as_of.isoformat(),
            "history_rows": history_rows, "final_rows": final_rows,
            "tails_without_fr24": len(tails_without_fr24),
        }
        await _pub("success", f"Completed — history {history_rows}, final {final_rows}",
                   progress=100, payload=summary)
        logger.info("forecast_panel done: %s", summary)
        return summary

    except Exception as e:
        # ValueError (bad operator) already published its own error above.
        if not isinstance(e, ValueError):
            await _pub("error", f"Forecast panel failed: {e}")
        logger.exception("forecast_panel failed for operator=%s", operator)
        raise
