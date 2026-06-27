from datetime import datetime, timezone

from pydantic import BaseModel, field_validator
import inspect
import sys
from typing import List, Optional

from .Enums.Defaults import FlightStatusEnum


class FlightsTrackerResponseSchema(BaseModel):
    hex: Optional[str] = None
    reg_number: Optional[str] = None

    airline_icao: Optional[str] = None
    airline_iata: Optional[str] = None

    aircraft_icao: Optional[str] = None

    flight_icao: Optional[str] = None
    flight_iata: Optional[str] = None
    flight_number: Optional[str] = None

    dep_icao: Optional[str] = None
    dep_iata: Optional[str] = None
    arr_icao: Optional[str] = None
    arr_iata: Optional[str] = None

    lat: Optional[float] = None
    lng: Optional[float] = None
    alt: Optional[float] = None
    dir: Optional[float] = None
    speed: Optional[float] = None
    v_speed: Optional[float] = None

    squawk: Optional[str] = None
    flag: Optional[str] = None

    status: Optional[FlightStatusEnum] = None
    updated: Optional[datetime] = None
    type: Optional[str] = None

    @field_validator("updated", mode="before")
    @classmethod
    def parse_updated(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v





_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]