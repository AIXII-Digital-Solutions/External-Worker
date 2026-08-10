"""IATA -> IANA timezone lookup + local-time conversion for the AviationEdge loader.

WHY. AviationEdge serves every timestamp as the naive LOCAL wall-clock time of the airport it
belongs to (``"2026-04-15t06:05:00.000"`` — no offset, lowercase separator). Departure fields are
local to the DEPARTURE airport, arrival fields to the ARRIVAL airport. The old code ran those
through ``parse_dt`` -> ``datetime.fromisoformat(...).astimezone(utc)``, and ``.astimezone()`` on a
NAIVE datetime interprets it in the HOST's zone — on a Europe/Moscow host every value landed as
``local - 3h`` labelled UTC. That corrupted ~1.46M rows, repaired by core-api's
``_admin/fix_aviationedge_timestamps.py``. This module exists so the loader never reintroduces it.

Zones come from ``scratch.ae_airport_tz`` in the ``aixii`` database (built and maintained by
core-api's ``_admin/aviationedge_tz.py`` from AE's own ``GET /airportDatabase``). If that table is
missing or empty the loader falls back to calling ``airportDatabase`` directly; if BOTH fail it
raises rather than silently writing mislabelled times again.

``_ZONE_ALIASES`` / ``_MANUAL`` are a COPY of the same tables in core-api's
``_admin/aviationedge_tz.py`` — keep the two in sync (same rule as ``forecast_params.py``). AE ships
retired tzdata names, and it has no zone at all for a handful of airports including **NQZ**
(Astana), which is one of the airports we load.

Conversion is historical, not a fixed offset: Kazakh airports were UTC+6 until 2024-03-01 and UTC+5
after, and ``ZoneInfo`` applies whichever was in force on the flight's own date. This needs a tzdata
source — ``tzdata`` is pinned in requirements.txt so it works on any host, slim images included.
"""
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import orjson
from sqlalchemy import text

from Config import setup_logger
from Database import DatabaseClient
from settings import AVIATION_EDGE_API_KEY, AVIATION_EDGE_URL

logger = setup_logger("aviationedge_timezones")

TZ_TABLE = "scratch.ae_airport_tz"

# AE's reference list still carries retired tzdata names.
_ZONE_ALIASES = {
    "Europe/Kiev": "Europe/Kyiv",
    "Europe/Uzhgorod": "Europe/Kyiv",
    "Europe/Zaporozhye": "Europe/Kyiv",
    "Asia/Rangoon": "Asia/Yangon",
    "America/Godthab": "America/Nuuk",
    "Pacific/Enderbury": "Pacific/Kanton",
    "Asia/Choibalsan": "Asia/Ulaanbaatar",
    "America/Indiana": "America/Indiana/Indianapolis",
    "+8": "Etc/GMT-8",
}

# Airports AE has no timezone for, resolved against OurAirports. Mostly airports that opened after
# AE's reference list was last refreshed.
_MANUAL = {
    "nqz": "Asia/Almaty",     # Astana / Nursultan Nazarbayev Intl (UACC)
    "dss": "Africa/Dakar",    # Blaise Diagne Intl (GOBD), Senegal
    "bpn": "Asia/Makassar",   # Balikpapan (WALL), Indonesia — WITA, UTC+8
    "nbj": "Africa/Luanda",   # Dr. Antonio Agostinho Neto Intl (FNBJ), Angola
    "tev": "Europe/Madrid",   # Teruel (LETL), Spain
    # surfaced by the ALA/NQZ/CIT back-fill (2022-2026)
    "hsa": "Asia/Almaty",     # Hazrat Sultan Intl, Turkistan, Kazakhstan
    "usj": "Asia/Almaty",     # Usharal, Kazakhstan
    "uzr": "Asia/Almaty",     # Urzhar, Kazakhstan
    "zia": "Europe/Moscow",   # Zhukovsky Intl, Moscow, Russia
    "ubn": "Asia/Ulaanbaatar",  # Chinggis Khaan Intl, Ulaanbaatar, Mongolia
    "ehu": "Asia/Shanghai",   # Ezhou Huahu Intl, China
    "iku": "Asia/Bishkek",    # Issyk-Kul Intl, Tamchy, Kyrgyzstan
    "ikg": "Asia/Bishkek",    # Karakol Intl, Kyrgyzstan
    "gox": "Asia/Kolkata",    # Manohar Intl, Mopa (Goa), India
    "dia": "Asia/Qatar",      # Doha Intl (old), Qatar
    "spx": "Africa/Cairo",    # Sphinx Intl, Al Jiza, Egypt
    "vip": "Europe/Zurich",   # Payerne, Switzerland
}

_zones: dict[str, ZoneInfo] | None = None
_lock = asyncio.Lock()


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(_ZONE_ALIASES.get(name, name))
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


async def _from_db() -> dict[str, ZoneInfo]:
    client = DatabaseClient()
    async with client.session("aviationedge") as session:
        rows = (await session.execute(text(f"SELECT iata, tz FROM {TZ_TABLE}"))).all()
    zones, bad = {}, 0
    for iata, tz_name in rows:
        zone = _zone(tz_name)
        if zone is None:
            bad += 1
            continue
        zones[(iata or "").strip().lower()] = zone
    if bad:
        logger.warning(f"[AE Timezones] {bad} rows in {TZ_TABLE} have an unusable zone")
    return zones


async def _from_api() -> dict[str, ZoneInfo]:
    params = {"key": AVIATION_EDGE_API_KEY}
    async with aiohttp.ClientSession() as http:
        async with http.get(f"{AVIATION_EDGE_URL}/airportDatabase", params=params,
                            timeout=aiohttp.ClientTimeout(total=300)) as resp:
            raw = await resp.read()
    data = orjson.loads(raw)
    if not isinstance(data, list):
        raise RuntimeError(f"airportDatabase returned {str(data)[:200]}")
    zones = {}
    for a in data:
        iata = (a.get("codeIataAirport") or "").strip().lower()
        zone = _zone((a.get("timezone") or "").strip())
        if iata and zone is not None:
            zones.setdefault(iata, zone)
    return zones


async def load_zones(force: bool = False) -> dict[str, ZoneInfo]:
    """IATA (lowercase) -> ZoneInfo, cached for the process.

    Raises if no source yields anything — writing local times as if they were UTC is exactly the bug
    this module exists to prevent, so failing loudly beats corrupting the table again."""
    global _zones
    async with _lock:
        if _zones is not None and not force:
            return _zones
        zones: dict[str, ZoneInfo] = {}
        try:
            zones = await _from_db()
            logger.info(f"[AE Timezones] loaded {len(zones)} zones from {TZ_TABLE}")
        except Exception as e:                                             # noqa: BLE001
            logger.warning(f"[AE Timezones] {TZ_TABLE} unavailable ({e}); "
                           f"falling back to airportDatabase")
        if not zones:
            zones = await _from_api()
            logger.info(f"[AE Timezones] loaded {len(zones)} zones from airportDatabase")
        for iata, tz_name in _MANUAL.items():
            zone = _zone(tz_name)
            if zone is not None:
                zones[iata] = zone
        if not zones:
            raise RuntimeError(
                "no airport timezones available (neither the DB table nor the vendor API) — "
                "refusing to load, since local times would be stored as UTC")
        _zones = zones
        return _zones


def to_utc(value: str | None, zone: ZoneInfo | None) -> tuple[datetime | None, bool]:
    """Vendor timestamp -> tz-aware UTC. Returns ``(value, zone_was_missing)``.

    With no zone the wall-clock is kept verbatim and merely labelled UTC — the caller counts those
    so the gap shows up in the logs instead of masquerading as a converted value."""
    if not value:
        return None, False
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        return None, False
    if dt.tzinfo is not None:                       # already offset-aware — trust it
        return dt.astimezone(timezone.utc), False
    if zone is None:
        return dt.replace(tzinfo=timezone.utc), True
    return dt.replace(tzinfo=zone).astimezone(timezone.utc), False
