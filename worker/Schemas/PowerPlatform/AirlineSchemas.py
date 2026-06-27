import inspect
import sys
from typing import List, Optional

from pydantic import BaseModel

from .DefaultSchemas import AssetSchema
from .UserSchemas import UserSchemaLight


class AirlineSchemaFull(BaseModel):
    airline_id: int
    airline_name: str
    airline_icao: Optional[str]
    airline_iata: Optional[str]
    asset: AssetSchema


class AirlineUsersSchemaFull(AirlineSchemaFull):
    users: List[UserSchemaLight]


class AirlineSchemaLight(BaseModel):
    airline_id: int
    airline_name: str
    airline_icao: Optional[str]
    airline_iata: Optional[str]


class AirlineUsersSchemaLight(AirlineSchemaLight):
    users: List[UserSchemaLight]



_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]

