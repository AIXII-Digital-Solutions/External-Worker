"""
external_worker segment configuration.

Holds settings for all external-API interaction (Microsoft Graph, Airlabs,
FlightRadar, Aviation Edge) and the related polling/redis keys. Common settings
(DBSettings, logging, DEV_MODE, ROOT) come from the shared ``Config`` package.
"""
import os
from pathlib import Path

# --- Load THIS service's own .env before importing the shared Config ---------
# Each segment owns its environment file (repo-root .env[.dev]);
# they do NOT share a single root .env. We point the shared Config at our file
# via ENV_PATH / ENV_DEV_PATH (which Config already honours). In containers the
# vars are usually injected directly (compose env_file / --env-file).
_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_DEV = os.getenv("DEV_MODE", "false").lower() in ("1", "true", "yes", "on")
_ENV_VAR = "ENV_DEV_PATH" if _DEV else "ENV_PATH"
if not os.getenv(_ENV_VAR):
    _env_file = _SERVICE_ROOT / (".env.dev" if _DEV else ".env")
    if _env_file.exists():
        os.environ[_ENV_VAR] = str(_env_file)
# -----------------------------------------------------------------------------

from Config import require_env, ROOT

# This segment owns the webhook URLs, so it needs to know where the API is
# reachable. Kept in sync with api_server via the same env keys.
SELF_HOST: str = require_env("SELF_HOST", "api.aixii.com")
API_ROOT_URL: str = require_env("API_ROOT_URL", "/api/v1")


# PATHS

FLIGHT_RADAR_PATH: Path = ROOT / "flight_radar"
AVIATION_EDGE_PATH: Path = ROOT / "aviation_edge"
SUBSCRIPTION_FILE: Path = ROOT / "subscription_data.json"

for _p in (FLIGHT_RADAR_PATH, AVIATION_EDGE_PATH):
    _p.mkdir(parents=True, exist_ok=True)


# Microsoft Graph

MS_TENANT_ID: str = require_env("MS_TENANT_ID")
MS_CLIENT_ID: str = require_env("MS_CLIENT_ID")
MS_CLIENT_SECRET: str = require_env("MS_CLIENT_SECRET")
MS_GRAPHSCOPES: list = [scope.strip() for scope
                        in require_env("MS_GRAPHSCOPES", "https://graph.microsoft.com/.default").split(",")
                        if scope.strip()]
MS_WEBHOOK_URL: str = require_env("MS_WEBHOOK_URL", f"https://{SELF_HOST}{API_ROOT_URL}/webhooks/microsoft")
MS_WEBHOOK_LIFECYCLE_URL: str = require_env("MS_WEBHOOK_LIFECYCLE_URL",
                                            f"https://{SELF_HOST}{API_ROOT_URL}/webhooks/microsoft/lifecycle")
MS_WEBHOOK_SECRET: str = require_env("MS_WEBHOOK_SECRET")  # REQUIRED — must equal core-api


# AIRLABS

AIRLABS_API_KEY: str = require_env("AIRLABS_API_KEY")
AIRLABS_API_URL: str = "https://airlabs.co/api/v9/"


# Flight Radar

FLIGHT_RADAR_URL: str = require_env("FLIGHT_RADAR_URL", "https://fr24api.flightradar24.com/api")
FLIGHT_RADAR_API_KEY: str = require_env("FLIGHT_RADAR_API_KEY")
FLIGHT_RADAR_SECONDS_BETWEEN_REQUESTS: float = require_env("FLIGHT_RADAR_SECONDS_BETWEEN_REQUESTS", 60 / 90)
FLIGHT_RADAR_RANGE_DAYS: int = require_env("FLIGHT_RADAR_RANGE_DAYS", 14)
FLIGHT_RADAR_MAX_REG_PER_BATCH: int = require_env("FLIGHT_RADAR_MAX_REG_PER_BATCH", 15)
FLIGHT_RADAR_HEADERS: dict = {
    "Authorization": f"Bearer {FLIGHT_RADAR_API_KEY}",
    "Accept-Version": "v1",
    "Accept": "application/json"
}

FLIGHT_RADAR_REDIS_POLLING_KEY: str = "flights:polling"
FLIGHT_RADAR_REDIS_META_KEY: str = "flights:meta"
FLIGHT_RADAR_BOOTSTRAP_KEY: str = "fr:bootstrap_done"

# Adaptive re-check intervals are DERIVED from this job's scheduler cadence at runtime
# (live_flights_adaptive._resolve_intervals reads schedule_registry.interval_seconds for
# FLIGHT_RADAR_SCHEDULE_NAME): miss = the scheduler interval (re-poll a missed reg next tick),
# found = miss * FLIGHT_RADAR_FOUND_INTERVAL_MULTIPLIER (re-poll an active reg every Nth tick).
# So changing the interval via the /scheduler API instantly retunes the rotation — no redeploy.
FLIGHT_RADAR_SCHEDULE_NAME: str = require_env("FLIGHT_RADAR_SCHEDULE_NAME", "cron_live_flights")
FLIGHT_RADAR_FOUND_INTERVAL_MULTIPLIER: int = require_env("FLIGHT_RADAR_FOUND_INTERVAL_MULTIPLIER", 2)

# Fallbacks, used ONLY when the schedule has no interval_seconds (it's cron-driven) or isn't seeded.
FLIGHT_RADAR_CHECK_INTERVAL_MISS: int = require_env("FLIGHT_RADAR_CHECK_INTERVAL_MISS", 8 * 60)
FLIGHT_RADAR_CHECK_INTERVAL_FOUND: int = require_env("FLIGHT_RADAR_CHECK_INTERVAL_FOUND", 18 * 60)
FLIGHT_RADAR_FORCE_RECHECK_MISS: int = require_env("FLIGHT_RADAR_FORCE_RECHECK_MISS", 8 * 60)


# Forecast panel
# Total PAX = Total Seats * this load factor (acys_actuals). Tunable via env; default 0.8.
FORECAST_PAX_LOAD_FACTOR: float = float(require_env("FORECAST_PAX_LOAD_FACTOR", 0.8))


# Aviation Edge

AVIATION_EDGE_API_KEY: str = require_env("AVIATION_EDGE_API_KEY")
AVIATION_EDGE_EXTRA_API_KEY: str = require_env("AVIATION_EDGE_EXTRA_API_KEY")
AVIATION_EDGE_URL: str = require_env("AVIATION_EDGE_URL", "https://aviation-edge.com/v2/public")
AVIATION_EDGE_SECONDS_BETWEEN_REQUESTS: float = require_env("AVIATION_EDGE_SECONDS_BETWEEN_REQUESTS", 60 / 180)
AVIATION_EDGE_MAX_BATCH_SIZE: int = require_env("AVIATION_EDGE_MAX_BATCH_SIZE", 1)
AVIATION_EDGE_MAX_RANGE_DAYS: int = require_env("AVIATION_EDGE_MAX_RANGE_DAYS", 15)
