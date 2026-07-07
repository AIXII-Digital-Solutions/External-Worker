"""Forecast data-prep panel — the production run (external-worker), with per-step status.

Two request modes: by **operator** (all its Cirium tails) or by an explicit **registrations** list.
Assembles forecast.history_1 (Cirium × FR24, date-respecting) then merges into forecast.final_1
(airport-enriched). Mirrors predictive/panel in Core-API (SQL vendored — worker has no Core-API dep).

Table lifecycle (per the spec):
  * history_1 — PERSISTS across requests; a request DELETEs only ITS scope (Operator= / Registration in)
    then re-inserts, so other operators/tails accumulate.
  * future_1  — TRUNCATEd every request (populated later by the forecast model).
  * final_1   — TRUNCATEd every request; rebuilt from history_1 (THIS request's flights only) + future_1.

Columns beyond the flight/aircraft fields:
  * Contract Year   — fiscal window (anchored at the REQUEST date's month/day) containing the flight
                      Date, labelled by its START year.  e.g. anchor 07-01: 2025-08 -> CY2025.
  * Circle Distance — FR24 great-circle origin->destination (flightsummary.circle_distance).
  * Flight Time     — Time Landed - Time Departed (interval).
"""
import random
from datetime import date

from sqlalchemy import text

from Config import setup_logger
from status import publish_status

logger = setup_logger("forecast_panel")

HISTORY_START = date(2023, 7, 1)
_DB = "cirium"   # any aviation logical name -> the physical aixii DB (cirium/flightradar/main/forecast)

# Contract Year for a flight date `d` vs the request anchor (:anchor_month/:anchor_day): the 12-month
# window [anchor, anchor+1y) containing d, labelled by its START year. Null when there's no flight.
_CONTRACT_YEAR = """CASE WHEN a6.flight_dt IS NULL THEN NULL ELSE
    'CY' || (extract(year from a6.flight_dt)::int - CASE
        WHEN extract(month from a6.flight_dt)::int < :anchor_month
          OR (extract(month from a6.flight_dt)::int = :anchor_month
              AND extract(day from a6.flight_dt)::int < :anchor_day)
        THEN 1 ELSE 0 END)::text
END"""


def _assemble_sql(a5_where: str) -> str:
    return f"""
INSERT INTO forecast.history_1
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time")
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
    WHERE {a5_where}
      AND to_date(r.period,'MM-YYYY') >= :start_date
    ORDER BY ca."Registration", to_date(r.period,'MM-YYYY'), ca.revision_id DESC
),
array6 AS (
    SELECT f.reg, f.datetime_takeoff, f.datetime_landed,
           f.orig_iata, f.dest_iata, f.dest_iata_actual, f.circle_distance,
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
    a5.operator, a5.master_series, a5.manufacturer, a5.sub_series, a5.primary_usage,
    {_CONTRACT_YEAR},
    a6.circle_distance,
    (a6.datetime_landed - a6.datetime_takeoff)
FROM array5 a5
LEFT JOIN array6 a6
       ON a6.reg = a5.registration
      AND date_trunc('month', a6.flight_dt) = a5.period_month
"""


def _merge_sql(final_scope: str) -> str:
    cols = """"Registration","Period","Date","Time Departed","Time Landed",
           "IATA Origin","IATA Destination","IATA Destination Actual",
           "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
           "Contract Year","Circle Distance","Flight Time\""""
    return f"""
INSERT INTO forecast.final_1
    ({cols},
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
    SELECT {cols} FROM forecast.history_1
    WHERE "Date" IS NOT NULL AND {final_scope}     -- flights only + THIS request's slice
    UNION ALL
    SELECT {cols} FROM forecast.future_1
)
SELECT p.*,
       o.country, o.city, o.airport_name,
       d.country, d.city, d.airport_name
FROM panel p
LEFT JOIN airports o ON o.iata = p."IATA Origin"
LEFT JOIN airports d ON d.iata = p."IATA Destination"
"""


_MISSING_TAILS_TMPL = """
SELECT DISTINCT ca."Registration" AS reg
FROM cirium.ciriumaircrafts ca
JOIN cirium.aircraftrevision r ON r.id = ca.revision_id
WHERE {a5_where}
  AND to_date(r.period,'MM-YYYY') >= :start_date
  AND NOT EXISTS (SELECT 1 FROM flightradar.flightsummary f WHERE f.reg = ca."Registration")
"""


def _scope(operator, registrations):
    """Return (a5_where, hist_delete_sql, final_scope, scope_params, label) for the request.

    operator and registrations are COMBINABLE (either or both): the scope is the UNION —
    the operator's tenure-scoped tails OR the explicit registrations. `ca."Operator"` uses the
    array5 alias; the bare form (no alias) is reused for the history DELETE and the final filter.
    """
    a5, bare, params, labels = [], [], {}, []
    if operator:
        a5.append('ca."Operator" = :scope_op')
        bare.append('"Operator" = :scope_op')
        params["scope_op"] = operator
        labels.append(f"operator '{operator}'")
    if registrations:
        a5.append('ca."Registration" = ANY(:scope_regs)')
        bare.append('"Registration" = ANY(:scope_regs)')
        params["scope_regs"] = list(registrations)
        labels.append(f"{len(registrations)} registration(s)")
    a5_where = "(" + " OR ".join(a5) + ")"
    bare_where = "(" + " OR ".join(bare) + ")"
    return (a5_where,
            f"DELETE FROM forecast.history_1 WHERE {bare_where}",
            bare_where,
            params,
            " + ".join(labels))


async def run_forecast_panel(*, db_client, redis, job_id: str, ref: str,
                             operator: str | None = None, registrations: list[str] | None = None,
                             as_of: date | None = None) -> dict:
    """Run the forecast panel for ONE request (operator XOR registrations), publishing a status per
    step. history_1 accumulates (this scope refreshed); future_1/final_1 are per-request."""
    if not operator and not registrations:
        raise ValueError("provide operator and/or registrations")
    as_of = as_of or date.today()
    a5_where, hist_delete, final_scope, scope_params, label = _scope(operator, registrations)

    base = {"start_date": HISTORY_START, "as_of": as_of,
            "anchor_month": as_of.month, "anchor_day": as_of.day, **scope_params}

    async def _pub(state, message, progress=None, payload=None):
        kwargs = {}
        if progress is not None:
            kwargs["progress"] = progress
        if payload is not None:
            kwargs["payload"] = payload
        await publish_status(db_client, redis, job_id=job_id, kind="external", ref=ref,
                             state=state, message=message, **kwargs)

    try:
        # Step 1/4 — Searching historical data: validate the scope, reset the per-request tables
        # (history_1 keeps other scopes — delete only THIS one; future_1/final_1 wiped), and probe
        # FR24 coverage.
        await _pub("running", "Searching historical data", progress=random.randint(10, 15))
        async with db_client.session(_DB) as s:
            ok = (await s.execute(
                text(f'SELECT 1 FROM cirium.ciriumaircrafts ca WHERE {a5_where} LIMIT 1'),
                scope_params)).first()
        if not ok:
            await _pub("error", f"No Cirium aircraft match {label}")
            raise ValueError(f"empty scope: {label}")
        async with db_client.session(_DB) as s:
            await s.execute(text(hist_delete), scope_params)
            await s.execute(text("TRUNCATE forecast.future_1"))
            await s.execute(text("TRUNCATE forecast.final_1"))
            await s.commit()
        async with db_client.session(_DB) as s:
            miss = (await s.execute(text(_MISSING_TAILS_TMPL.format(a5_where=a5_where)),
                                    base)).fetchall()
        tails_without_fr24 = [r[0] for r in miss]

        # Step 2/4 — Fetching data: assemble history_1 (Cirium × FR24, date-respecting) for this scope
        await _pub("running", "Fetching data", progress=random.randint(25, 49))
        async with db_client.session(_DB) as s:
            res = await s.execute(text(_assemble_sql(a5_where)), base)
            history_rows = res.rowcount
            await s.commit()

        # Step 3/4 — Creating predictive analysis
        await _pub("running", "Creating predictive analysis", progress=random.randint(55, 70),
                   payload={"history_rows": history_rows, "tails_without_fr24": len(tails_without_fr24)})

        # Step 4/4 — Building dataset: merge into final_1 (this request's flights only + future_1)
        await _pub("running", "Building dataset", progress=random.randint(80, 90))
        async with db_client.session(_DB) as s:
            res = await s.execute(text(_merge_sql(final_scope)), scope_params)
            final_rows = res.rowcount
            await s.commit()

        summary = {
            "mode": "+".join((["operator"] if operator else []) + (["registrations"] if registrations else [])),
            "operator": operator, "registrations": list(registrations) if registrations else None,
            "as_of": as_of.isoformat(),
            "history_rows": history_rows, "final_rows": final_rows,
            "tails_without_fr24": len(tails_without_fr24),
        }
        await _pub("success", f"Completed — history {history_rows}, final {final_rows}",
                   progress=100, payload=summary)
        logger.info("forecast_panel done: %s", summary)
        return summary

    except Exception as e:
        if not isinstance(e, ValueError):
            await _pub("error", f"Forecast panel failed: {e}")
        logger.exception("forecast_panel failed (%s)", label)
        raise
