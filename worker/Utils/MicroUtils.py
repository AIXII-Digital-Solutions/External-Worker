import csv
import inspect
import os
import re
import sys
from base64 import b64encode
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Any, Iterable


def cache_key_first_non_null(name: str, data: dict[str, Any], keys: Iterable[str], fallback: str = "all") -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return f"{name}:{value}"
    return f"{name}:{fallback}"


def str_to_list(text: str | List[str]) -> List[str]:
    if isinstance(text, str):
        values = re.split(r"[,\n]+", text)
        values = [v.strip() for v in values if v.strip()]
        return sorted(set(values))
    return sorted(text)


def to_bool(value: str) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y", "t")


def remove_file(path: str | Path):
    try:
        os.remove(path)
    except FileNotFoundError:
        raise FileNotFoundError(f"File {path} not found")
    except Exception:
        os.remove(str(path))


def next_quarter(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    minutes = ((dt.minute // 15) + 1) * 15
    if minutes == 60:
        return dt.replace(minute=0) + timedelta(hours=1)
    return dt.replace(minute=minutes)


def next_ten_minutes(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)

    minutes = ((dt.minute // 10) + 1) * 10

    if minutes == 60:
        return dt.replace(minute=0) + timedelta(hours=1)

    return dt.replace(minute=minutes)


def write_csv(rows: List[dict], path: str):
    file_exists = Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def normalize_dt(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


def parse_date_or_datetime(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None

    return dt.astimezone(timezone.utc)


def ensure_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if (inspect.isfunction(obj) or inspect.isasyncgenfunction(obj)) and obj.__module__ == __name__
]
