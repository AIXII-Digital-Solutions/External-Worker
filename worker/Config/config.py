import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from dotenv import load_dotenv, find_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# This module holds ONLY the configuration shared by every segment
# (api_server, file_processor, external_worker):
#   - environment loading (DEV_MODE / .env discovery)
#   - the base data ROOT and logs directory
#   - database + redis settings (DBSettings)
#   - require_env helper
# Segment-specific settings live in each segment's own ``settings.py``.

DEV_MODE: bool = os.getenv("DEV_MODE", "false").lower() in ("1", "true", "yes", "on", True)


# PATH

def get_project_root() -> Path:
    current_file = Path(__file__).absolute()

    for parent in current_file.parents:
        if (parent / '.git').exists() or (parent / 'requirements.txt').exists():
            return parent

    return current_file.parents[2]


# ENVIRONMENT

if DEV_MODE:
    ENV_PATH: Path | str = os.getenv("ENV_DEV_PATH") or get_project_root() / ".env.dev"
else:
    ENV_PATH: Path | str = os.getenv("ENV_PATH") or get_project_root() / ".env"

# Base data directory. Segment-specific sub-folders are derived from ROOT in
# each segment's settings.py.
ROOT: Path = get_project_root() / "api_data"
ROOT.mkdir(parents=True, exist_ok=True)

PATH = find_dotenv(filename=str(Path(ENV_PATH).absolute()))

load_dotenv(dotenv_path=PATH)


_ENV_MISSING = object()


def require_env(name: str, additional=_ENV_MISSING) -> Optional[bool | str | int]:
    value = os.getenv(name)
    if value is not None and value != "":
        return value
    if additional is not _ENV_MISSING:
        return additional
    raise RuntimeError(f"{name} is required")


ENABLE_PERFORMANCE_LOGGER: bool = require_env("ENABLE_PERFORMANCE_LOGGER", False)


# DATABASE

class DBSettings(BaseSettings):
    """
    ENVIRONMENT AUTO, NO PARAMS NEED
    """
    DB_USER: str = Field(default="")
    DB_PASSWORD: str = Field(default="")
    DB_HOST: str = Field(default="localhost")
    DB_PORT: int = Field(default=5432)
    DB_NAME: str = Field(default="")

    REDIS_USER: str = Field(default="")
    REDIS_USER_PASSWORD: str = Field(default="")
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)

    model_config = SettingsConfigDict(
        env_file=PATH, extra='ignore'
    )

    @property
    def db_list(self) -> list[str]:
        """Splits a string into a DB list"""
        return [db.strip() for db in self.DB_NAME.split(",") if db.strip()]

    def get_db_url(self, db_name: str) -> str:
        """Returns DSN for the best matching database (substring match)."""
        if self.DB_USER == "" or self.DB_PASSWORD == "":
            raise ValueError("Database credentials not provided")

        matches = [db for db in self.db_list if db_name.lower() in db.lower()]
        if not matches:
            raise ValueError(f"No database similar to '{db_name}' found in {self.db_list}")
        if len(matches) > 1:
            raise ValueError(f"Ambiguous name '{db_name}', matches: {matches}")
        return (f"postgresql+asyncpg://{self.DB_USER}:{quote_plus(self.DB_PASSWORD)}@"
                f"{self.DB_HOST}:{self.DB_PORT}/{matches[0]}")

    def get_reddis_credentials(self):
        return self.REDIS_USER, self.REDIS_USER_PASSWORD, self.REDIS_HOST, self.REDIS_PORT


#  LOGS

LOGS_DIR = get_project_root() / 'Logs'
LOGS_DIR.mkdir(exist_ok=True, parents=True)


__all__ = [
    "DEV_MODE",
    "get_project_root",
    "require_env",
    "ENV_PATH",
    "PATH",
    "ROOT",
    "ENABLE_PERFORMANCE_LOGGER",
    "DBSettings",
    "LOGS_DIR",
]
