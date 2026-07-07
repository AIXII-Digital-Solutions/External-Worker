"""Refresh main.airports from OurAirports open data — the scheduled worker side of the airport
reference. Mirrors Core-API's _admin/load_ourairports.py: download airports/countries/runways.csv,
parse, dedup by IATA, extract codes hidden in `keywords`, aggregate runways to a jsonb array, map
country/continent, TRUNCATE+reload main.airports, then apply the curated main.airport_city_overrides.

OurAirports is free open data (no API key). Wired as the `cron_refresh_airports` job (monthly). The
blocking download+parse runs in a worker thread so it never stalls the event loop.
"""
import asyncio
import csv
import json
import tempfile
import urllib.request
from pathlib import Path

import asyncpg

from Config import setup_logger, DBSettings

logger = setup_logger("airports_refresh")

BASE = "https://davidmegginson.github.io/ourairports-data"
FILES = {"airports": "airports.csv", "countries": "countries.csv", "runways": "runways.csv"}
CONTINENT = {"AF": "Africa", "AN": "Antarctica", "AS": "Asia", "EU": "Europe",
             "NA": "North America", "OC": "Oceania", "SA": "South America"}
TYPE_RANK = {"large_airport": 0, "medium_airport": 1, "small_airport": 2,
             "seaplane_base": 3, "heliport": 4, "balloonport": 5, "closed": 9}

csv.field_size_limit(10 * 1024 * 1024)

_INSERT = """
INSERT INTO main.airports
    (iata, ident, icao, name, city, country, country_code, longitude, latitude, type, elevation_ft,
     region, greater_region, continent_code, runways, ourairports_id, keywords)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16,$17)
"""

_APPLY_OVERRIDES = """
DO $do$ BEGIN
    IF to_regclass('main.airport_city_overrides') IS NOT NULL THEN
        UPDATE main.airports a
        SET city = coalesce(o.city, a.city), country = coalesce(o.country, a.country)
        FROM main.airport_city_overrides o WHERE a.iata = o.iata;
    END IF;
END $do$;
"""


def _conn_params() -> dict:
    s = DBSettings()
    if not s.DB_USER or not s.DB_PASSWORD:
        raise RuntimeError("DB credentials not provided")
    return dict(user=s.DB_USER, password=s.DB_PASSWORD, host=s.DB_HOST, port=int(s.DB_PORT),
                database=s.DB_AIXII_NAME)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _icao_like(s):
    s = (s or "").strip()
    return s if (len(s) == 4 and s.isascii() and s.isalpha() and s.isupper()) else None


def _keyword_codes(kw):
    iata3 = icao4 = None
    for tok in (kw or "").split(","):
        t = tok.strip()
        if not (t.isascii() and t.isalpha() and t.isupper()):
            continue
        if len(t) == 3 and iata3 is None:
            iata3 = t
        elif len(t) == 4 and icao4 is None:
            icao4 = t
    return iata3, icao4


def _download_and_build(data_dir: Path) -> list:
    """Blocking: download the three CSVs and build the row tuples (run in a worker thread)."""
    for key, fname in FILES.items():
        urllib.request.urlretrieve(f"{BASE}/{fname}", data_dir / f"oa_{key}.csv")

    with open(data_dir / "oa_countries.csv", encoding="utf-8", newline="") as f:
        countries = {r["code"]: r["name"] for r in csv.DictReader(f)}

    runways: dict[str, list] = {}
    with open(data_dir / "oa_runways.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("closed") == "1":
                continue
            runways.setdefault(r["airport_ident"], []).append({
                "le_ident": r["le_ident"] or None, "he_ident": r["he_ident"] or None,
                "length_ft": _int(r["length_ft"]), "width_ft": _int(r["width_ft"]),
                "surface": r["surface"] or None,
                "le_heading": _float(r["le_heading_degT"]), "he_heading": _float(r["he_heading_degT"]),
            })

    best: dict[str, tuple] = {}
    no_iata: list[tuple] = []
    with open(data_dir / "oa_airports.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            iata = (r["iata_code"] or "").strip() or None
            icao = (r["icao_code"] or "").strip() or None
            kw = r["keywords"] or None
            kw_iata, kw_icao = _keyword_codes(kw) if (iata is None or icao is None) else (None, None)
            iata_from_kw = False
            if iata is None and kw_iata:
                iata, iata_from_kw = kw_iata, True
            if icao is None:
                icao = _icao_like(r["ident"]) or kw_icao
            row = (
                iata, r["ident"] or None, icao, r["name"] or None, r["municipality"] or None,
                countries.get(r["iso_country"]), r["iso_country"] or None,
                _float(r["longitude_deg"]), _float(r["latitude_deg"]), r["type"] or None,
                _int(r["elevation_ft"]), r["iso_region"] or None, CONTINENT.get(r["continent"]),
                r["continent"] or None,
                json.dumps(runways[r["ident"]]) if r["ident"] in runways else None,
                _int(r["id"]), kw,
            )
            if iata:
                key = (iata_from_kw, r["type"] == "closed", TYPE_RANK.get(r["type"], 8),
                       r["scheduled_service"] != "yes")
                if iata in best and best[iata][0] <= key:
                    continue
                best[iata] = (key, row)
            else:
                no_iata.append(row)
    return [v[1] for v in best.values()] + no_iata


async def refresh_airports() -> int:
    """Download OurAirports, TRUNCATE+reload main.airports, apply curated overrides. Returns row count."""
    with tempfile.TemporaryDirectory(prefix="ourairports_") as td:
        logger.info("refresh_airports: downloading + parsing OurAirports ...")
        rows = await asyncio.to_thread(_download_and_build, Path(td))
    logger.info("refresh_airports: loading %d airports", len(rows))

    conn = await asyncpg.connect(**_conn_params(), timeout=60)
    try:
        await conn.execute("SET statement_timeout=0")
        await conn.execute("TRUNCATE main.airports RESTART IDENTITY")
        await conn.executemany(_INSERT, rows)
        await conn.execute(_APPLY_OVERRIDES)
        n = await conn.fetchval("SELECT count(*) FROM main.airports")
    finally:
        await conn.close()
    logger.info("refresh_airports: main.airports = %d rows", n)
    return n
