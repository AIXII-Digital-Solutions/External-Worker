"""Forecast data-prep panel — the production run (external-worker), with per-step status.

Request modes: by **operator** and/or an explicit **registrations** list (UNION scope). Assembles
forecast.acys_actuals (Cirium × FR24, date-respecting) then merges into forecast.acys_summary
(airport-enriched). Mirrors predictive/panel in Core-API (SQL vendored — worker has no Core-API dep).

Tables:
  * acys_actuals  — PERSISTS across requests; a request DELETEs only ITS scope then re-inserts.
  * acys_forecast — TRUNCATEd every request (populated later by the forecast model).
  * acys_summary  — TRUNCATEd every request; rebuilt from acys_actuals (THIS request's flights only)
                    + acys_forecast, enriched with origin/destination airport geography + lat/lon.

Columns beyond the flight/aircraft fields (populated in acys_actuals, carried into acys_summary):
  * Contract Year       — fiscal window (anchored at the REQUEST date's month/day) of the flight Date.
  * Circle Distance     — flightsummary.circle_distance.
  * Flight Time         — Time Landed - Time Departed (interval).
  * Agreed Value        — Cirium "Indicative Market Value (US$m)".
  * Total Seats         — Cirium "Number of Seats".
  * Total PAX           — Total Seats * FORECAST_PAX_LOAD_FACTOR (config, default 0.8).
  * Actual Distance FR  — flightsummary.circle_distance (same source as Circle Distance).
  * Flight Time FR      — flightsummary.flight_time (seconds) -> interval.
"""
import random
from datetime import date

from sqlalchemy import text

from Config import setup_logger
from settings import FORECAST_PAX_LOAD_FACTOR
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

# flightsummary.flight_time is integer seconds (with some bad negatives) -> interval, NULL if < 0/null.
_FLIGHT_TIME_FR = "CASE WHEN a6.flight_time >= 0 THEN a6.flight_time * interval '1 second' ELSE NULL END"


def _assemble_sql(a5_where: str) -> str:
    return f"""
INSERT INTO forecast.acys_actuals
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "ICAO Origin","ICAO Destination","ICAO Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time",
     "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR")
WITH array5 AS (
    SELECT DISTINCT ON (ca."Registration", to_date(r.period,'MM-YYYY'))
           ca."Registration"                    AS registration,
           r.period                              AS period,
           to_date(r.period,'MM-YYYY')           AS period_month,
           ca."Operator"                         AS operator,
           ca."Master Series"                    AS master_series,
           ca."Manufacturer"                     AS manufacturer,
           ca."Aircraft Sub Series"              AS sub_series,
           ca."Primary Usage"                    AS primary_usage,
           ca."Indicative Market Value (US$m)"   AS agreed_value,
           ca."Number of Seats"                  AS total_seats
    FROM cirium.ciriumaircrafts ca
    JOIN cirium.aircraftrevision r ON r.id = ca.revision_id
    WHERE {a5_where}
      AND to_date(r.period,'MM-YYYY') >= :start_date
    ORDER BY ca."Registration", to_date(r.period,'MM-YYYY'), ca.revision_id DESC
),
array6 AS (
    SELECT f.reg, f.datetime_takeoff, f.datetime_landed,
           f.orig_iata, f.dest_iata, f.dest_iata_actual,
           f.orig_icao, f.dest_icao, f.dest_icao_actual,
           f.circle_distance, f.flight_time,
           coalesce(f.datetime_takeoff, f.first_seen) AS flight_dt
    FROM flightradar.flightsummary f
    WHERE f.reg IN (SELECT registration FROM array5)
      AND coalesce(f.datetime_takeoff, f.first_seen) >= :start_date
      AND coalesce(f.datetime_takeoff, f.first_seen) <  :as_of
      -- drop a flight with NO origin, or NO destination (neither actual nor planned)
      AND nullif(f.orig_iata, '') IS NOT NULL
      AND coalesce(nullif(f.dest_iata_actual, ''), nullif(f.dest_iata, '')) IS NOT NULL
)
SELECT
    a5.registration, a5.period,
    CAST(a6.flight_dt AS date),
    a6.datetime_takeoff, a6.datetime_landed,
    a6.orig_iata, a6.dest_iata, a6.dest_iata_actual,
    a6.orig_icao, a6.dest_icao, a6.dest_icao_actual,
    a5.operator, a5.master_series, a5.manufacturer, a5.sub_series, a5.primary_usage,
    {_CONTRACT_YEAR},
    a6.circle_distance,
    (a6.datetime_landed - a6.datetime_takeoff),
    a5.agreed_value,
    a5.total_seats,
    a5.total_seats * CAST(:pax_factor AS double precision),
    a6.circle_distance,
    {_FLIGHT_TIME_FR}
FROM array5 a5
LEFT JOIN array6 a6
       ON a6.reg = a5.registration
      AND date_trunc('month', a6.flight_dt) = a5.period_month
"""


# Airport lookup CHAIN for one airport: main.airports by IATA -> main.airports by ICAO ->
# flightradar.airports by IATA. `pri` orders the sources; LIMIT 1 takes the first that matched.
def _airport_lookup(iata_expr: str, icao_expr: str) -> str:
    return f"""(
        SELECT city, country, airport_name, lat, lon FROM (
            SELECT city, country, name AS airport_name, latitude AS lat, longitude AS lon, 1 AS pri
              FROM main.airports WHERE iata = {iata_expr}
            UNION ALL
            SELECT city, country, name, latitude, longitude, 2
              FROM main.airports WHERE icao = {icao_expr}
            UNION ALL
            SELECT city, country_name, name, lat, lon, 3
              FROM flightradar.airports WHERE iata = {iata_expr}
        ) s ORDER BY pri LIMIT 1
    )"""


def _merge_sql(final_scope: str) -> str:
    cols = """"Registration","Period","Date","Time Departed","Time Landed",
           "IATA Origin","IATA Destination","IATA Destination Actual",
           "ICAO Origin","ICAO Destination","ICAO Destination Actual",
           "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
           "Contract Year","Circle Distance","Flight Time",
           "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR\""""
    origin = _airport_lookup('p."IATA Origin"', 'p."ICAO Origin"')
    dest = _airport_lookup('coalesce(p."IATA Destination Actual", p."IATA Destination")',
                           'coalesce(p."ICAO Destination Actual", p."ICAO Destination")')
    return f"""
INSERT INTO forecast.acys_summary
    ({cols},
     "Origin Country","Origin City","Origin Airport Name",
     "Destination Country","Destination City","Destination Airport Name",
     origin_lat, origin_lon, dest_lat, dest_lon)
WITH panel AS (
    SELECT {cols} FROM forecast.acys_actuals
    WHERE "Date" IS NOT NULL AND {final_scope}     -- flights only + THIS request's slice
    UNION ALL
    SELECT {cols} FROM forecast.acys_forecast
)
SELECT p.*,
       o.country, o.city, o.airport_name,
       d.country, d.city, d.airport_name,
       o.lat, o.lon, d.lat, d.lon
FROM panel p
LEFT JOIN LATERAL {origin} o ON true
LEFT JOIN LATERAL {dest} d ON true
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
    array5 alias; the bare form (no alias) is reused for the acys_actuals DELETE and the final filter.
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
            f"DELETE FROM forecast.acys_actuals WHERE {bare_where}",
            bare_where,
            params,
            " + ".join(labels))


async def _fr24_backfill(tails: list[str], as_of: date, pub) -> None:
    """Fetch FR24 flight-summary for tails with NO flightsummary rows yet and insert them into
    flightradar.flightsummary (so the assemble step then picks them up). Best-effort: a FlightRadar
    failure (bad key / rate limit / network) is logged and the panel proceeds with existing data."""
    await pub("running", f"Fetching {len(tails)} tail(s) from FlightRadar", progress=random.randint(16, 24))
    try:
        from API.FlightRadarAPI.FlightSummary import fetch_all_ranges   # lazy: avoid import cycle
        await fetch_all_ranges(
            start_date=HISTORY_START.isoformat(),
            end_date=as_of.isoformat(),
            registrations=list(tails),
            storage_mode="db",
        )
    except Exception as e:
        logger.warning("FR24 backfill failed for %d tail(s); continuing with existing data: %s",
                       len(tails), e)


async def run_forecast_panel(*, db_client, redis, job_id: str, ref: str,
                             operator: str | None = None, registrations: list[str] | None = None,
                             as_of: date | None = None) -> dict:
    """Run the forecast panel for ONE request (operator and/or registrations), publishing a status
    per step. acys_actuals accumulates (this scope refreshed); acys_forecast/acys_summary per-request."""
    if not operator and not registrations:
        raise ValueError("provide operator and/or registrations")
    as_of = as_of or date.today()
    a5_where, hist_delete, final_scope, scope_params, label = _scope(operator, registrations)

    base = {"start_date": HISTORY_START, "as_of": as_of,
            "anchor_month": as_of.month, "anchor_day": as_of.day,
            "pax_factor": FORECAST_PAX_LOAD_FACTOR, **scope_params}

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
        # (acys_actuals keeps other scopes — delete only THIS one; acys_forecast/acys_summary wiped),
        # and probe FR24 coverage.
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
            await s.execute(text("TRUNCATE forecast.acys_forecast"))
            await s.execute(text("TRUNCATE forecast.acys_summary"))
            await s.commit()
        async with db_client.session(_DB) as s:
            miss = (await s.execute(text(_MISSING_TAILS_TMPL.format(a5_where=a5_where)),
                                    base)).fetchall()
        tails_without_fr24 = [r[0] for r in miss]

        # Backfill missing tails from FlightRadar, then re-check what's still absent (best-effort).
        if tails_without_fr24:
            await _fr24_backfill(tails_without_fr24, as_of, _pub)
            async with db_client.session(_DB) as s:
                miss = (await s.execute(text(_MISSING_TAILS_TMPL.format(a5_where=a5_where)),
                                        base)).fetchall()
            tails_without_fr24 = [r[0] for r in miss]

        # Step 2/4 — Fetching data: assemble acys_actuals (Cirium × FR24, date-respecting)
        await _pub("running", "Fetching data", progress=random.randint(25, 49))
        async with db_client.session(_DB) as s:
            res = await s.execute(text(_assemble_sql(a5_where)), base)
            history_rows = res.rowcount
            await s.commit()

        # Step 3/4 — Creating predictive analysis
        await _pub("running", "Creating predictive analysis", progress=random.randint(55, 70),
                   payload={"history_rows": history_rows, "tails_without_fr24": len(tails_without_fr24)})

        # Step 4/4 — Building dataset: merge into acys_summary (this request's flights only + forecast)
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
        await _pub("success", f"Completed — actuals {history_rows}, summary {final_rows}",
                   progress=100, payload=summary)
        logger.info("forecast_panel done: %s", summary)
        return summary

    except Exception as e:
        if not isinstance(e, ValueError):
            await _pub("error", f"Forecast panel failed: {e}")
        logger.exception("forecast_panel failed (%s)", label)
        raise
