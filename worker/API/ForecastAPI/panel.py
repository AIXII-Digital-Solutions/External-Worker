"""Forecast data-prep panel — the production run (external-worker), with per-step status.

Request modes: by **operator** and/or an explicit **registrations** list (UNION scope). Assembles
forecast.acys_actuals (Cirium × FR24, date-respecting) then merges into forecast.acys_summary
(airport-enriched + route-grouped). Mirrors predictive/panel in Core-API (SQL vendored).

Tables:
  * acys_actuals         — one row per FLIGHT (Cirium aircraft × its flightsummary flight). A request
                    DELETEs only ITS scope then re-inserts. Flights only (no aircraft-without-flight).
  * acys_forecast        — TRUNCATEd every request (populated later by the forecast model).
  * acys_summary_by_day  — TRUNCATEd + rebuilt every request from acys_actuals (THIS request's flights)
                    + acys_forecast, airport-enriched. ONE ROW PER FLIGHT (no grouping); keeps Date /
                    Time Departed / Time Landed.
  * acys_summary_grouped — a DB VIEW over acys_summary_by_day (not written here): one row per
                    (aircraft, month, route) with "# Of Flights" = count and the four metric columns
                    summed, WITHOUT Date / Time Departed / Time Landed. For PBI Direct Query.

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
  * The flight LOWER BOUND is always the start of CY2022 (make_date(2022, anchor_month, anchor_day)) —
    flights that would land in CY2021 are excluded.

Airport coordinates / geography (2.4 distance + 2.6 enrichment) — by priority, first source that has
the code wins: main.virtual_airport_list by IATA -> flightradar.airports by IATA -> main.airports by
IATA -> main.airports by ICAO.

acys_summary_by_day (2.6) — merge acys_actuals + acys_forecast into ONE ROW PER FLIGHT, adding Age /
geography / Origin&Dest lat-lon / Data Type:
  * Agreed Value = 0    — when Lease Dry Wet = 'Wet'.
  * Age                 — (Date - Delivery Date) in decimal years ((Date - Delivery Date)/365.25).
  * Data Type           — 'Actuals' (from acys_actuals) / 'Forecast' (from acys_forecast).
acys_summary_grouped (VIEW, not written here) rolls by_day up to one row per (aircraft, month, route):
  * # Of Flights        — count of grouped flights; SUMS Actual Distance FR / Circle Distance /
                          Flight Time FR / Flight Time; drops Date / Time Departed / Time Landed.
"""
import asyncio
import json
from datetime import date, timedelta

from sqlalchemy import text

from Config import setup_logger
from settings import (FORECAST_PAX_LOAD_FACTOR, FORECAST_ASSEMBLE_ETA_SECONDS,
                      FORECAST_MERGE_ETA_SECONDS, FORECAST_FETCH_BUDGET_SECONDS,
                      FR24_SECONDS_PER_REQUEST_EST, FORECAST_PROGRESS_HEARTBEAT_SECONDS,
                      FORECAST_PROGRESS_MIN_INTERVAL_SECONDS, FORECAST_CALIB_WINDOW_DAYS,
                      FORECAST_BOOT_SEARCH_SECONDS, FORECAST_BOOT_FORECAST_PER_OP_SECONDS)
from status import publish_status

logger = setup_logger("forecast_panel")

HISTORY_START = date(2022, 7, 1)   # Period >= 07-2022 (2.2) and flightsummary first_seen >= this
_DB = "cirium"   # any aviation logical name -> the physical aixii DB (cirium/flightradar/main/forecast)
_REQUEST_TYPE = "ACYS"   # this Cirium×FR24 panel algorithm — stamped on the forecast_last_requests row

# Contract Year for a flight's first_seen vs the request anchor DATE (:anchor_month/:anchor_day): a
# DAY-PRECISE 12-month window ending ON the anchor day. For a 10-Jul-2026 request, CY2025 =
# (10-Jul-2025 .. 10-Jul-2026], i.e. 11-Jul-2025 .. 10-Jul-2026 — the anchor day is the LAST day of the CY
# (hence `<= :anchor_day`). Labelled by its START year.
_CONTRACT_YEAR = """'CY' || (extract(year from a6.first_seen)::int - CASE
        WHEN extract(month from a6.first_seen)::int < :anchor_month
          OR (extract(month from a6.first_seen)::int = :anchor_month
              AND extract(day from a6.first_seen)::int <= :anchor_day)
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
      -- ONE authoritative operator per (Registration, month): Cirium lists a wet-leased/ACMI tail under
      -- MULTIPLE operators for the same month, and acys_actuals accumulates per-operator, so the same
      -- flight would be stored under each. Keep only the tail whose (revision, id) is the newest (closest
      -- to today) for that month; a non-authoritative operator's request assembles nothing for it.
      AND NOT EXISTS (
          SELECT 1 FROM cirium.ciriumaircrafts ca2
          JOIN cirium.aircraftrevision r2 ON r2.id = ca2.revision_id
          WHERE ca2."Registration" = ca."Registration"
            AND to_date(r2.period,'MM-YYYY') = to_date(r.period,'MM-YYYY')
            AND (ca2.revision_id, ca2.id) > (ca.revision_id, ca.id)
      )
    ORDER BY ca."Registration", to_date(r.period,'MM-YYYY'), ca.revision_id DESC, ca.id DESC
),
array6 AS (
    SELECT f.reg, f.first_seen, f.datetime_takeoff, f.datetime_landed,
           f.orig_iata, f.dest_iata, f.dest_iata_actual,
           f.orig_icao, f.dest_icao, f.dest_icao_actual,
           f.actual_distance, f.flight_time
    FROM flightradar.flightsummary f
    WHERE f.reg IN (SELECT registration FROM array5)
      -- lower bound is ALWAYS the start of CY2022: flights ON or before the anchor DAY in 2022 fall in
      -- CY2021 and are dropped. Compare the DATE (first_seen is a timestamp — a same-day flight at any
      -- clock time must still be excluded, so `first_seen > midnight` is not enough). Upper: request date.
      AND f.first_seen::date > make_date(2022, :anchor_month, :anchor_day)
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


def _future_aircraft_sql(a5_where: str) -> str:
    """Future-delivery aircraft (Delivery Date > the request date) inserted into acys_actuals as FLIGHTLESS
    stubs (Date + all flight fields NULL) so the forecast model can see the FORWARD fleet. They are NOT
    fetched from / checked against FlightRadar and NOT dropped for having no flights. The delivery date is
    read from the LATEST revision of EACH plan (Commercial AND Business&Helicopters — an aircraft sits in one
    plan; taking the latest of both covers both), deduped per Registration."""
    return f"""
INSERT INTO forecast.acys_actuals
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "ICAO Origin","ICAO Destination","ICAO Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time",
     "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR",
     "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor")
WITH latest_rev AS (   -- the latest revision of EACH plan_type (Commercial, Business&Helicopters)
    SELECT DISTINCT ON (plan_type) id
    FROM cirium.aircraftrevision
    ORDER BY plan_type, to_date(period,'MM-YYYY') DESC, id DESC
)
SELECT DISTINCT ON (ca."Registration")
    ca."Registration",
    to_char(ca."Delivery Date", 'MM-YYYY'),                 -- Period = arrival month
    NULL::date, NULL, NULL,                                 -- Date, Time Departed/Landed
    NULL, NULL, NULL, NULL, NULL, NULL,                     -- IATA/ICAO origin & destination(+actual)
    ca."Operator", ca."Master Series", ca."Manufacturer", ca."Aircraft Sub Series", ca."Primary Usage",
    NULL,                                                   -- Contract Year (no flight)
    NULL::double precision, NULL::interval,                 -- Circle Distance, Flight Time
    ca."Indicative Market Value (US$m)", ca."Number of Seats",
    NULL::double precision,                                 -- Total PAX (no flight)
    NULL::double precision, NULL::interval,                 -- Actual Distance FR, Flight Time FR
    ca."Delivery Date", ca."Lease Type", ca."Lease Dry / Wet", ca."Operational Lessor"
FROM cirium.ciriumaircrafts ca
JOIN latest_rev lr ON lr.id = ca.revision_id
WHERE {a5_where} AND ca."Delivery Date" > :as_of
  -- one authoritative operator per future tail (newest revision/id wins), same as the flight assemble
  AND NOT EXISTS (
      SELECT 1 FROM cirium.ciriumaircrafts ca2 JOIN latest_rev lr2 ON lr2.id = ca2.revision_id
      WHERE ca2."Registration" = ca."Registration" AND (ca2.revision_id, ca2.id) > (ca.revision_id, ca.id)
  )
ORDER BY ca."Registration", ca.revision_id DESC, ca.id DESC
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
    # acys_summary_by_day: ONE ROW PER FLIGHT — panel + per-field geo enrichment + Wet rule + Age.
    # NO grouping / NO "# Of Flights" (that lives in the acys_summary_grouped VIEW). Keeps Date /
    # Time Departed / Time Landed.
    o_geo = _geo_lookup(_ne('p."IATA Origin"'), _ne('p."ICAO Origin"'))
    dia, di = _ne('p."IATA Destination Actual"'), _ne('p."IATA Destination"')
    dica, dic = _ne('p."ICAO Destination Actual"'), _ne('p."ICAO Destination"')
    d_geo = _geo_lookup(f"coalesce({dia}, {di})", f"coalesce({dica}, {dic})")
    # month ordinal (year*12+month) helper for the per-aircraft Agreed-Value model
    ord_of = 'extract(year from {c})::int * 12 + extract(month from {c})::int'
    p_ord = ord_of.format(c='p."Date"')
    return f"""
INSERT INTO forecast.acys_summary_by_day
    ("Registration","Period","Date","Time Departed","Time Landed",
     "IATA Origin","IATA Destination","IATA Destination Actual",
     "ICAO Origin","ICAO Destination","ICAO Destination Actual",
     "Operator","Master Series","Manufacturer","Aircraft Sub Series","Primary Usage",
     "Contract Year","Circle Distance","Flight Time",
     "Agreed Value","Total Seats","Total PAX","Actual Distance FR","Flight Time FR",
     "Delivery Date","Lease Type","Lease Dry Wet","Operational Lessor",
     "Age","Data Type",
     "Origin Country","Origin City","Origin Airport Name",
     "Destination Country","Destination City","Destination Airport Name",
     origin_lat, origin_lon, dest_lat, dest_lon)
WITH aav AS (   -- per (reg, month-ordinal): the REAL actual Agreed Value (non-Wet, > 0) — source of truth
    SELECT "Registration" reg, {ord_of.format(c='"Date"')} ord, max("Agreed Value") av
    FROM forecast.acys_actuals
    WHERE "Date" IS NOT NULL AND "Lease Dry Wet" IS DISTINCT FROM 'Wet' AND "Agreed Value" > 0
    GROUP BY 1, 2
),
astat AS (      -- per reg: last known AV, its month-ordinal, and the monthly depreciation slope (<= 0,
                -- clamped so a projection can only decline — an aircraft never baselessly gets pricier)
    SELECT reg, (array_agg(av ORDER BY ord DESC))[1] last_av, max(ord) last_ord,
           LEAST(0, coalesce(regr_slope(av, ord), 0)) slope
    FROM aav GROUP BY 1
),
panel AS (
    -- each branch tags its source so rows carry Data Type = Actuals / Forecast
    SELECT {_PANEL_COLS}, 'Actuals' AS "Data Type" FROM forecast.acys_actuals
    WHERE "Date" IS NOT NULL AND {final_scope}     -- flights only + THIS request's slice
    UNION ALL
    SELECT {_PANEL_COLS}, 'Forecast' AS "Data Type" FROM forecast.acys_forecast
),
avfill AS (     -- fixed Agreed Value per (reg, month, data type):
    --  * Actuals  -> the real month value, else CARRY FORWARD the last known one (fills Cirium gaps),
    --  * Forecast -> project the aircraft's own depreciation forward from the last actual, floored at 0.
    SELECT om.reg, om.ord, om.dt,
           CASE WHEN om.dt = 'Forecast'
                THEN GREATEST(0, st.last_av + st.slope * (om.ord - st.last_ord))
                ELSE coalesce(
                     (SELECT av FROM aav WHERE aav.reg = om.reg AND aav.ord <= om.ord
                      ORDER BY aav.ord DESC LIMIT 1),   -- carry FORWARD the last known value (fills gaps/tail)
                     (SELECT av FROM aav WHERE aav.reg = om.reg
                      ORDER BY aav.ord ASC LIMIT 1))     -- else the earliest known value (fills LEADING nulls)
           END AS av
    FROM (SELECT DISTINCT "Registration" reg, {ord_of.format(c='"Date"')} ord, "Data Type" dt
          FROM panel WHERE "Date" IS NOT NULL) om
    LEFT JOIN astat st ON st.reg = om.reg
)
SELECT
    p."Registration", p."Period", p."Date", p."Time Departed", p."Time Landed",
    p."IATA Origin", p."IATA Destination", p."IATA Destination Actual",
    p."ICAO Origin", p."ICAO Destination", p."ICAO Destination Actual",
    p."Operator", p."Master Series", p."Manufacturer", p."Aircraft Sub Series", p."Primary Usage",
    p."Contract Year", p."Circle Distance", p."Flight Time",
    CASE WHEN p."Lease Dry Wet" = 'Wet' THEN 0 ELSE coalesce(av.av, p."Agreed Value") END,
    p."Total Seats", p."Total PAX", p."Actual Distance FR", p."Flight Time FR",
    p."Delivery Date", p."Lease Type", p."Lease Dry Wet", p."Operational Lessor",
    round((p."Date" - p."Delivery Date")::numeric / 365.25, 2),
    p."Data Type",
    o.country, o.city, o.airport_name,
    d.country, d.city, d.airport_name,
    o.lat, o.lon, d.lat, d.lon
FROM panel p
LEFT JOIN avfill av ON av.reg = p."Registration" AND av.dt = p."Data Type" AND av.ord = ({p_ord})
LEFT JOIN LATERAL {o_geo} o ON true
LEFT JOIN LATERAL {d_geo} d ON true
"""


_SCOPE_REGS_TMPL = """
SELECT DISTINCT ca."Registration" AS reg
FROM cirium.ciriumaircrafts ca
JOIN cirium.aircraftrevision r ON r.id = ca.revision_id
WHERE {a5_where}
  AND to_date(r.period,'MM-YYYY') >= :start_date
  AND ca."Registration" IS NOT NULL AND ca."Registration" <> ''
"""


def _cy2022_floor(as_of: date) -> date:
    """Start of CY2022 relative to the request anchor DATE (make_date(2022, month, day)); the FR24 fetch
    lower bound. Guards the Feb-29 case (2022 is not a leap year)."""
    try:
        return date(2022, as_of.month, as_of.day)
    except ValueError:
        return date(2022, as_of.month, 28)


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

    # ── Progress + ETA: an honest, self-calibrating FIVE-step model (no hardcoded step weights) ──────
    # Pending steps are estimated from a moving average of past runs (forecast_step_timings) scaled by
    # this run's unit count; completed steps use their measured wall time; a background heartbeat keeps
    # the bar and the countdown live even during a blocking SQL. Step titles + `detail` NEVER name a data
    # source. The frontend reads `message` + payload.{eta, detail, step, step_total, step_key}.
    from API.ForecastAPI.progress import Calibrator, ProgressReporter, Step
    from API.FlightRadarAPI.coverage import (plan_missing_ranges, fetch_planned_ranges,
                                             JobCancelled)   # lazy: avoid import cycle

    # `weight` = each step's FIXED share of the visual bar so the two long steps (Fetching / Generating
    # forecast) can't push the bar near 100% while earlier steps run; brief steps keep a small share.
    # ETA stays time-based. Titles + detail NEVER name a data source.
    steps = [
        Step("validating",    "Validating request",
             "Checking the request and matching the requested aircraft.", unit_based=False, weight=2),
        Step("aircraft_list", "Building Aircraft List",
             "Preparing the working tables and the list of aircraft in scope.", unit_based=False, weight=3),
        Step("coverage",      "Checking data coverage",
             "Determining which history is already available and what is missing.", unit_based=True, weight=8),
        Step("fetching",      "Fetching historical data",
             "Retrieving the missing flight history for the requested aircraft.", unit_based=True,
             max_s=FORECAST_FETCH_BUDGET_SECONDS, weight=33),
        Step("snapshot",      "Saving historical coverage snapshot",
             "Recording which history has been retrieved so it is not fetched again.", unit_based=False, weight=2),
        Step("transform",     "Transforming historical data",
             "Compiling the historical activity into the working dataset.", unit_based=False, weight=12),
        Step("measures",      "Calculating measures",
             "Adding incoming aircraft (forward fleet) and their derived measures.", unit_based=False, weight=5),
        Step("forecast",      "Generating forecast",
             "Projecting future activity from the historical patterns.", unit_based=True, weight=20),
        Step("merging",       "Merging all data in the dataset",
             "Combining the actual and forecast data into the final dataset.", unit_based=False, weight=12),
        Step("rendering",     "Rendering report",
             "Finalising the dataset for reporting.", unit_based=False, weight=3),
    ]

    async def _pub(state, message, progress=None, payload=None):
        # progress=None => omit (so a terminal publish never wipes the stored bar).
        kwargs = {}
        if progress is not None:
            kwargs["progress"] = progress
        if payload is not None:
            kwargs["payload"] = payload
        await publish_status(db_client, redis, job_id=job_id, kind="external", ref=ref,
                             state=state, message=message, **kwargs)

    # Cooperative cancellation: core-api's POST /status/{job_id}/cancel sets this Redis flag; checked
    # between steps AND per fetch request (should_cancel) so the run stops promptly and cleanly.
    _cancel_key = f"job:cancel:{job_id}"

    async def _cancelled() -> bool:
        try:
            return bool(redis is not None and await redis.exists(_cancel_key))
        except Exception:
            return False

    async def _ck():
        if await _cancelled():
            raise JobCancelled()

    cal = Calibrator(db_client, window_days=FORECAST_CALIB_WINDOW_DAYS)
    await cal.load()
    estimates = [
        cal.estimate("validating",    boot_flat=1),
        cal.estimate("aircraft_list", boot_flat=2),
        cal.estimate("coverage",      boot_flat=FORECAST_BOOT_SEARCH_SECONDS),
        cal.estimate("fetching",      boot_flat=FR24_SECONDS_PER_REQUEST_EST * 4),
        cal.estimate("snapshot",      boot_flat=1),
        cal.estimate("transform",     boot_flat=FORECAST_ASSEMBLE_ETA_SECONDS),
        cal.estimate("measures",      boot_flat=3),
        cal.estimate("forecast",      boot_flat=FORECAST_BOOT_FORECAST_PER_OP_SECONDS * 4),
        cal.estimate("merging",       boot_flat=FORECAST_MERGE_ETA_SECONDS),
        cal.estimate("rendering",     boot_flat=1),
    ]
    reporter = ProgressReporter(publish=_pub, steps=steps, estimates=estimates,
                                heartbeat_s=FORECAST_PROGRESS_HEARTBEAT_SECONDS,
                                min_interval=FORECAST_PROGRESS_MIN_INTERVAL_SECONDS)

    try:
        await reporter.start()

        # ── 1/10 Validating request — the scope has matching aircraft (else fail fast). ─────────────
        await reporter.enter("validating")
        async with db_client.session(_DB) as s:
            ok = (await s.execute(
                text(f'SELECT 1 FROM cirium.ciriumaircrafts ca WHERE {a5_where} LIMIT 1'),
                scope_params)).first()
        if not ok:
            await reporter.terminal("error", "No aircraft match the request")
            raise ValueError(f"empty scope: {label}")
        d = await reporter.complete()
        await cal.record("validating", d, 1, {"label": label})

        # ── 2/10 Building Aircraft List — reset THIS request's tables + collect the aircraft list. ───
        await _ck()
        await reporter.enter("aircraft_list")
        async with db_client.session(_DB) as s:
            await s.execute(text(hist_delete), scope_params)   # acys_actuals keeps other scopes
            await s.execute(text("TRUNCATE forecast.acys_forecast"))
            await s.execute(text("TRUNCATE forecast.acys_summary_by_day"))
            await s.commit()
        async with db_client.session(_DB) as s:
            scope_regs = (await s.execute(text(_SCOPE_REGS_TMPL.format(a5_where=a5_where)),
                                          base)).scalars().all()
        n_regs = max(1, len(scope_regs))
        # refine the estimates now that the fleet size is known (unit-scaled where it helps)
        reporter.set_estimate("aircraft_list", cal.estimate("aircraft_list", n_regs, boot_per_unit=0.005, boot_flat=2))
        reporter.set_estimate("coverage",  cal.estimate("coverage", n_regs, boot_per_unit=0.03, boot_flat=FORECAST_BOOT_SEARCH_SECONDS))
        reporter.set_estimate("transform", cal.estimate("transform", n_regs, boot_per_unit=0.02, boot_flat=FORECAST_ASSEMBLE_ETA_SECONDS))
        reporter.set_estimate("measures",  cal.estimate("measures", n_regs, boot_per_unit=0.005, boot_flat=3))
        reporter.set_estimate("merging",   cal.estimate("merging", n_regs, boot_per_unit=0.02, boot_flat=FORECAST_MERGE_ETA_SECONDS))
        d = await reporter.complete()
        await cal.record("aircraft_list", d, n_regs, {"regs": n_regs})

        # ── 3/10 Checking data coverage — what history exists vs is missing (no external calls). ─────
        await _ck()
        await reporter.enter("coverage", unit_total=n_regs)

        async def _plan_progress(done, total):
            await reporter.tick(done, total)

        plan_info = await plan_missing_ranges(
            db_client, list(scope_regs),
            w_start=_cy2022_floor(as_of), w_end=date.today() - timedelta(days=1),
            on_progress=_plan_progress, should_cancel=_cancelled)
        d = await reporter.complete()
        await cal.record("coverage", d, n_regs, {"regs": n_regs, "groups": plan_info["groups"]})

        # ── 4/10 Fetching historical data — fetch ONLY the missing ranges per tail (spends budget). ──
        await _ck()
        total_requests = max(1, plan_info["total_requests"])
        reporter.set_estimate("fetching", cal.estimate("fetching", total_requests,
                                                       boot_per_unit=FR24_SECONDS_PER_REQUEST_EST,
                                                       boot_flat=FR24_SECONDS_PER_REQUEST_EST))
        await reporter.enter("fetching", unit_total=total_requests)

        async def _fetch_progress(done, total):
            await reporter.tick(done, total)

        coverage = await fetch_planned_ranges(
            db_client, plan_info["plan"], total_requests,
            time_budget_s=FORECAST_FETCH_BUDGET_SECONDS,
            on_progress=_fetch_progress, should_cancel=_cancelled)
        coverage["regs"] = plan_info["regs"]
        d = await reporter.complete()
        # calibrate by planned requests; skip a budget-capped fetch (its wall time reflects the budget).
        if not coverage["incomplete"]:
            await cal.record("fetching", d, total_requests,
                             {"groups": coverage["groups"], "ranges_fetched": coverage["ranges_fetched"],
                              "requests_est": total_requests, "incomplete": coverage["incomplete"]})

        # ── 5/10 Saving historical coverage snapshot — the coverage ledger was persisted during fetch. ─
        await _ck()
        await reporter.enter("snapshot")
        d = await reporter.complete()
        await cal.record("snapshot", d, 1, {"ranges_fetched": coverage["ranges_fetched"]})

        # ── 6/10 Transforming historical data — assemble acys_actuals (one row per flight). ──────────
        await _ck()
        await reporter.enter("transform")
        async with db_client.session(_DB) as s:
            res = await s.execute(text(_assemble_sql(a5_where)), base)
            history_rows = res.rowcount
            await s.commit()
        d = await reporter.complete()
        await cal.record("transform", d, n_regs, {"regs": n_regs, "history_rows": history_rows})

        # ── 7/10 Calculating measures — forward-fleet aircraft (future deliveries) as flightless stubs
        # (no external data, never dropped) + their derived measures. ────────────────────────────────
        await _ck()
        await reporter.enter("measures")
        async with db_client.session(_DB) as s:
            fut = await s.execute(text(_future_aircraft_sql(a5_where)), base)
            future_aircraft = fut.rowcount
            await s.commit()
        d = await reporter.complete()
        await cal.record("measures", d, n_regs, {"regs": n_regs, "future": future_aircraft})

        # ── 8/10 Generating forecast — project forward per operator. ─────────────────────────────────
        await _ck()
        from API.ForecastAPI.model import run_forecast_model   # lazy: model imports panel helpers
        # forecast per operator in scope: the explicit operator, else the operators of the scoped tails
        async with db_client.session(_DB) as s:
            if operator:
                fc_ops = [operator]
            else:
                fc_ops = (await s.execute(
                    text(f'SELECT DISTINCT "Operator" FROM forecast.acys_actuals WHERE {final_scope} '
                         'AND "Operator" IS NOT NULL'), scope_params)).scalars().all()
        n_ops = max(1, len(fc_ops))
        reporter.set_estimate("forecast", cal.estimate("forecast", n_ops,
                                                       boot_per_unit=FORECAST_BOOT_FORECAST_PER_OP_SECONDS,
                                                       boot_flat=FORECAST_BOOT_FORECAST_PER_OP_SECONDS))
        await reporter.enter("forecast", unit_total=n_ops)
        forecast_rows = 0
        for idx, op in enumerate(fc_ops):
            await _ck()

            async def _fc_prog(frac, _i=idx):
                # move WITHIN this operator's share of the step so a single-operator run isn't frozen
                await reporter.tick(_i + frac, n_ops)

            async with db_client.session(_DB) as s:
                # scope the forecast source to THIS request's tails (acys_actuals accumulates across
                # requests) so a registrations-scoped run does not forecast sibling tails
                fr = await run_forecast_model(session=s, operator=op, as_of=as_of,
                                              scope_where=final_scope, scope_params=scope_params,
                                              on_progress=_fc_prog)
            forecast_rows += fr.get("forecast_rows", 0)
            await reporter.tick(idx + 1, n_ops)
        d = await reporter.complete()
        await cal.record("forecast", d, n_ops,
                         {"operators": len(fc_ops), "forecast_rows": forecast_rows})

        # ── 9/10 Merging all data in the dataset — actuals + forecast → acys_summary_by_day. ─────────
        await _ck()
        await reporter.enter("merging")
        async with db_client.session(_DB) as s:
            res = await s.execute(text(_merge_sql(final_scope)), scope_params)
            final_rows = res.rowcount
            await s.commit()
        d = await reporter.complete()
        await cal.record("merging", d, max(1, history_rows + forecast_rows),
                         {"history_rows": history_rows, "forecast_rows": forecast_rows,
                          "final_rows": final_rows})

        # ── 10/10 Rendering report — record the run for GET /forecast/last, finalise the dataset. ────
        await reporter.enter("rendering")
        # best-effort: never fail a good run
        try:
            async with db_client.session("service") as s:
                await s.execute(
                    text("INSERT INTO forecast_last_requests (request_type, request_params) "
                         "VALUES (:rt, CAST(:params AS jsonb))"),
                    {"rt": _REQUEST_TYPE, "params": json.dumps({
                        "operator": operator,
                        "registrations": list(registrations) if registrations else None,
                        "date": as_of.isoformat(),
                    })})
                await s.commit()
        except Exception as e:
            logger.warning("failed to record forecast_last_requests: %s", e)
        d = await reporter.complete()
        await cal.record("rendering", d, 1, {"final_rows": final_rows})

        summary = {
            "mode": "+".join((["operator"] if operator else []) + (["registrations"] if registrations else [])),
            "operator": operator, "registrations": list(registrations) if registrations else None,
            "as_of": as_of.isoformat(),
            "history_rows": history_rows, "future_aircraft": future_aircraft,
            "forecast_rows": forecast_rows, "final_rows": final_rows,
            "coverage": coverage,
        }
        await reporter.success(
            f"Completed — actuals {history_rows}, future {future_aircraft}, "
            f"forecast {forecast_rows}, summary {final_rows}", summary)
        logger.info("forecast_panel done: %s", summary)
        return summary

    except JobCancelled:
        # User cancelled (cooperative flag): publish the terminal 'cancelled' status LAST (heartbeat
        # already stopped by terminal()), clear the flag, and finish normally.
        await reporter.terminal("cancelled", "Cancelled by user")
        try:
            await redis.delete(_cancel_key)
        except Exception:
            pass
        logger.info("forecast_panel cancelled (%s)", label)
        return {"cancelled": True, "operator": operator,
                "registrations": list(registrations) if registrations else None}
    except asyncio.CancelledError:
        # ARQ abort / worker shutdown: stop the heartbeat synchronously, publish 'cancelled' on a
        # DETACHED task (survives this task's cancellation), then re-raise so ARQ records the abort.
        reporter.request_stop()
        asyncio.ensure_future(_pub("cancelled", "Cancelled by user"))
        logger.info("forecast_panel aborted (%s)", label)
        raise
    except Exception as e:
        if not isinstance(e, ValueError):
            await reporter.terminal("error", f"Forecast panel failed: {e}")
        logger.exception("forecast_panel failed (%s)", label)
        raise
    finally:
        reporter.request_stop()
