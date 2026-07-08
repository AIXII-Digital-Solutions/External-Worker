"""Forecast data-prep panel — the production run (external-worker), with per-step status.

Request modes: by **operator** and/or an explicit **registrations** list (UNION scope). Assembles
forecast.acys_actuals (Cirium × FR24, date-respecting) then merges into forecast.acys_summary
(airport-enriched + route-grouped). Mirrors predictive/panel in Core-API (SQL vendored).

Tables:
  * acys_actuals  — one row per FLIGHT (Cirium aircraft × its flightsummary flight). A request
                    DELETEs only ITS scope then re-inserts. Flights only (no aircraft-without-flight).
  * acys_forecast — TRUNCATEd every request (populated later by the forecast model).
  * acys_summary  — TRUNCATEd every request; rebuilt from acys_actuals (THIS request's flights)
                    + acys_forecast, airport-enriched, then GROUPED by aircraft+month+route.

Assemble (acys_actuals) — per FLIGHT:
  * aircraft (2.2) from cirium.ciriumaircrafts: latest revision per (Registration, Period-month) for
    every month with Period >= 07-2022. Fields: Operator, Master Series, Manufacturer, Aircraft Sub
    Series, Primary Usage, Agreed Value (Indicative Market Value US$m), Total Seats (Number of Seats),
    Delivery Date, Lease Type, Lease Dry Wet (Lease Dry / Wet), Operational Lessor.
  * flight (2.3) from flightradar.flightsummary matched by reg AND first_seen's month == Period month.
    Date = first_seen; Time Departed/Landed = datetime_takeoff/landed; Actual Distance FR =
    actual_distance; Flight Time FR = flight_time (sec -> interval); IATA/ICAO Origin & Destination(+Actual).
  * derived (2.4): Contract Year (fiscal window anchored at the REQUEST date's month/day, labelled by
    its START year); Circle Distance = GREAT-CIRCLE (haversine, km) between origin & destination coords;
    Flight Time = Time Landed - Time Departed; Total PAX = Total Seats * FORECAST_PAX_LOAD_FACTOR (0.8).
  * DROP a flight whose ICAO Origin is empty OR whose ICAO Destination (actual or planned) is empty.

Airport coordinates / geography (2.4 distance + 2.6 enrichment) — by priority, first source that has
the code wins: main.virtual_airport_list by IATA -> flightradar.airports by IATA -> main.airports by
IATA -> main.airports by ICAO.

acys_summary (2.6) — merge acys_actuals + acys_forecast, add Age / geography / Origin&Dest lat-lon /
# Of Flights / Data Type, then GROUP identical (aircraft, month, route):
  * Agreed Value = 0    — when Lease Dry Wet = 'Wet'.
  * Age                 — (Date - Delivery Date) in decimal years ((Date - Delivery Date)/365.25).
  * Data Type           — 'Actuals' (from acys_actuals) / 'Forecast' (from acys_forecast).
  * # Of Flights        — count of grouped flights (same aircraft, month, route).
  * SUMMED across the group: Actual Distance FR, Circle Distance, Flight Time FR, Flight Time.
"""
import random
from datetime import date

from sqlalchemy import text

from Config import setup_logger
from settings import FORECAST_PAX_LOAD_FACTOR
from status import publish_status

logger = setup_logger("forecast_panel")

HISTORY_START = date(2022, 7, 1)   # Period >= 07-2022 (2.2) and flightsummary first_seen >= this
_DB = "cirium"   # any aviation logical name -> the physical aixii DB (cirium/flightradar/main/forecast)

# Contract Year for a flight's first_seen vs the request anchor (:anchor_month/:anchor_day): the
# 12-month window [anchor, anchor+1y) containing the date, labelled by its START year.
_CONTRACT_YEAR = """'CY' || (extract(year from a6.first_seen)::int - CASE
        WHEN extract(month from a6.first_seen)::int < :anchor_month
          OR (extract(month from a6.first_seen)::int = :anchor_month
              AND extract(day from a6.first_seen)::int < :anchor_day)
        THEN 1 ELSE 0 END)::text"""

# flightsummary.flight_time is integer seconds (with some bad negatives) -> interval, NULL if < 0/null.
_FLIGHT_TIME_FR = "CASE WHEN a6.flight_time >= 0 THEN a6.flight_time * interval '1 second' ELSE NULL END"


def _ne(expr: str) -> str:
    """nullif(expr, '') — treat an empty string code as absent so it never matches a lookup."""
    return f"nullif({expr}, '')"


def _geo_lookup(iata_expr: str, icao_expr: str) -> str:
    """One airport's geography by PRIORITY, per-field: for each of city / country / airport_name /
    lat / lon, take the value from the lowest-priority source that has it non-empty. Priority:
    main.virtual_airport_list by IATA (1) -> flightradar.airports by IATA (2) -> main.airports by
    IATA (3) -> main.airports by ICAO (4). Per-field (not per-row) so a high-priority source that
    has coordinates but an empty city does not shadow a populated city from the next source."""
    return f"""(
        SELECT
            (array_agg(city         ORDER BY pri) FILTER (WHERE city         IS NOT NULL))[1] AS city,
            (array_agg(country      ORDER BY pri) FILTER (WHERE country      IS NOT NULL))[1] AS country,
            (array_agg(airport_name ORDER BY pri) FILTER (WHERE airport_name IS NOT NULL))[1] AS airport_name,
            (array_agg(lat          ORDER BY pri) FILTER (WHERE lat          IS NOT NULL))[1] AS lat,
            (array_agg(lon          ORDER BY pri) FILTER (WHERE lon          IS NOT NULL))[1] AS lon
        FROM (
            SELECT nullif("City",'') AS city, nullif("Country",'') AS country,
                   nullif("Airport Name",'') AS airport_name, "Latitude" AS lat, "Longitude" AS lon, 1 AS pri
              FROM main.virtual_airport_list WHERE "IATA Code" = {iata_expr}
            UNION ALL
            SELECT nullif(city,''), nullif(country_name,''), nullif(name,''), lat, lon, 2
              FROM flightradar.airports WHERE iata = {iata_expr}
            UNION ALL
            SELECT nullif(city,''), nullif(country,''), nullif(name,''), latitude, longitude, 3
              FROM main.airports WHERE iata = {iata_expr}
            UNION ALL
            SELECT nullif(city,''), nullif(country,''), nullif(name,''), latitude, longitude, 4
              FROM main.airports WHERE icao = {icao_expr}
        ) s
    )"""


def _great_circle(o: str, d: str) -> str:
    """Haversine great-circle distance in KM (matches flightsummary.circle_distance's unit) between
    aliases `o` and `d` (each exposing .lat/.lon); NULL if either coordinate pair is absent."""
    return (f"2 * 6371 * asin(sqrt( power(sin(radians(({d}.lat - {o}.lat) / 2)), 2) + "
            f"cos(radians({o}.lat)) * cos(radians({d}.lat)) * "
            f"power(sin(radians(({d}.lon - {o}.lon) / 2)), 2) ))")


def _assemble_sql(a5_where: str) -> str:
    # origin/destination coordinate lookups for the Circle Distance (2.4): IATA-first, ICAO fallback.
    o_geo = _geo_lookup(_ne("a6.orig_iata"), _ne("a6.orig_icao"))
    d_geo = _geo_lookup(f'coalesce({_ne("a6.dest_iata_actual")}, {_ne("a6.dest_iata")})',
                        f'coalesce({_ne("a6.dest_icao_actual")}, {_ne("a6.dest_icao")})')
    return f"""
INSERT INTO forecast.acys_actuals
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "ICAO Origin","ICAO Destination","ICAO Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time",
     "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR",
     "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor")
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
           ca."Number of Seats"                  AS total_seats,
           ca."Delivery Date"                    AS delivery_date,
           ca."Lease Type"                       AS lease_type,
           ca."Lease Dry / Wet"                  AS lease_dry_wet,
           ca."Operational Lessor"               AS operational_lessor
    FROM cirium.ciriumaircrafts ca
    JOIN cirium.aircraftrevision r ON r.id = ca.revision_id
    WHERE {a5_where}
      AND to_date(r.period,'MM-YYYY') >= :start_date
    ORDER BY ca."Registration", to_date(r.period,'MM-YYYY'), ca.revision_id DESC
),
array6 AS (
    SELECT f.reg, f.first_seen, f.datetime_takeoff, f.datetime_landed,
           f.orig_iata, f.dest_iata, f.dest_iata_actual,
           f.orig_icao, f.dest_icao, f.dest_icao_actual,
           f.actual_distance, f.flight_time
    FROM flightradar.flightsummary f
    WHERE f.reg IN (SELECT registration FROM array5)
      AND f.first_seen >= :start_date
      AND f.first_seen <  :as_of
      -- DROP a flight with NO ICAO origin, or NO ICAO destination (neither actual nor planned)
      AND nullif(f.orig_icao, '') IS NOT NULL
      AND coalesce(nullif(f.dest_icao_actual, ''), nullif(f.dest_icao, '')) IS NOT NULL
)
SELECT
    a5.registration, a5.period,
    CAST(a6.first_seen AS date),
    a6.datetime_takeoff, a6.datetime_landed,
    a6.orig_iata, a6.dest_iata, a6.dest_iata_actual,
    a6.orig_icao, a6.dest_icao, a6.dest_icao_actual,
    a5.operator, a5.master_series, a5.manufacturer, a5.sub_series, a5.primary_usage,
    {_CONTRACT_YEAR},
    {_great_circle('o', 'd')},
    (a6.datetime_landed - a6.datetime_takeoff),
    a5.agreed_value,
    a5.total_seats,
    a5.total_seats * CAST(:pax_factor AS double precision),
    a6.actual_distance,
    {_FLIGHT_TIME_FR},
    a5.delivery_date, a5.lease_type, a5.lease_dry_wet, a5.operational_lessor
FROM array5 a5
JOIN array6 a6
       ON a6.reg = a5.registration
      AND date_trunc('month', a6.first_seen) = a5.period_month
LEFT JOIN LATERAL {o_geo} o ON true
LEFT JOIN LATERAL {d_geo} d ON true
"""


# Columns carried verbatim from acys_actuals / acys_forecast into the merge panel.
_PANEL_COLS = """"Registration","Period","Date","Time Departed","Time Landed",
       "IATA Origin","IATA Destination","IATA Destination Actual",
       "ICAO Origin","ICAO Destination","ICAO Destination Actual",
       "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
       "Contract Year","Circle Distance","Flight Time",
       "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR",
       "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor\""""


def _merge_sql(final_scope: str) -> str:
    o_geo = _geo_lookup(_ne('p."IATA Origin"'), _ne('p."ICAO Origin"'))
    dia, di = _ne('p."IATA Destination Actual"'), _ne('p."IATA Destination"')
    dica, dic = _ne('p."ICAO Destination Actual"'), _ne('p."ICAO Destination"')
    d_geo = _geo_lookup(f"coalesce({dia}, {di})", f"coalesce({dica}, {dic})")
    return f"""
INSERT INTO forecast.acys_summary
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "ICAO Origin","ICAO Destination","ICAO Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time",
     "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR",
     "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor",
     "Age","Data Type","# Of Flights",
     "Origin Country","Origin City","Origin Airport Name",
     "Destination Country","Destination City","Destination Airport Name",
     origin_lat, origin_lon, dest_lat, dest_lon)
WITH panel AS (
    -- each branch tags its source so acys_summary rows carry Data Type = Actuals / Forecast
    SELECT {_PANEL_COLS}, 'Actuals' AS "Data Type" FROM forecast.acys_actuals
    WHERE "Date" IS NOT NULL AND {final_scope}     -- flights only + THIS request's slice
    UNION ALL
    SELECT {_PANEL_COLS}, 'Forecast' AS "Data Type" FROM forecast.acys_forecast
),
enriched AS (
    SELECT p.*,
           o.city AS o_city, o.country AS o_country, o.airport_name AS o_name, o.lat AS o_lat, o.lon AS o_lon,
           d.city AS d_city, d.country AS d_country, d.airport_name AS d_name, d.lat AS d_lat, d.lon AS d_lon
    FROM panel p
    LEFT JOIN LATERAL {o_geo} o ON true
    LEFT JOIN LATERAL {d_geo} d ON true
)
SELECT
    "Registration","Period",
    min("Date"), min("Time Departed"), max("Time Landed"),
    "IATA Origin","IATA Destination","IATA Destination Actual",
    "ICAO Origin","ICAO Destination","ICAO Destination Actual",
    "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
    "Contract Year",
    sum("Circle Distance"),
    sum("Flight Time"),
    CASE WHEN "Lease Dry Wet" = 'Wet' THEN 0 ELSE "Agreed Value" END,
    "Total Seats","Total PAX",
    sum("Actual Distance FR"),
    sum("Flight Time FR"),
    "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor",
    round((min("Date") - "Delivery Date")::numeric / 365.25, 2),
    "Data Type",
    count(*),
    o_country, o_city, o_name,
    d_country, d_city, d_name,
    o_lat, o_lon, d_lat, d_lon
FROM enriched
GROUP BY
    "Registration","Period",
    "IATA Origin","IATA Destination","IATA Destination Actual",
    "ICAO Origin","ICAO Destination","ICAO Destination Actual",
    "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
    "Contract Year",
    "Agreed Value","Total Seats","Total PAX",
    "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor",
    "Data Type",
    o_country, o_city, o_name, o_lat, o_lon,
    d_country, d_city, d_name, d_lat, d_lon
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

        # Step 2/4 — Fetching data: assemble acys_actuals (Cirium × FR24, date-respecting, flights only)
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
