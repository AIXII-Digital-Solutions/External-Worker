import inspect
import sys
from datetime import date
from typing import Optional, List

from pydantic import BaseModel

from .AircraftSchemas import AircraftSchemaLight
from .UserSchemas import UserSchemaLight


class ClaimSchemaFull(BaseModel):
    claim_id: int
    users: List[UserSchemaLight]
    aircraft: AircraftSchemaLight
    date_of_loss: Optional[date]
    location_of_loss: Optional[str]
    status: Optional[str]
    damage: Optional[str]
    indemnity_reserve_amount: Optional[float]
    paid_to_date_amount: Optional[float]
    paid_date: Optional[date]
    is_hd: Optional[bool]
    is_hw: Optional[bool]
    is_hsl: Optional[bool]
    leader: Optional[str]
    surveyor: Optional[str]
    currency: Optional[str]
    currency_rate: Optional[float]
    hd_reserve: Optional[float]
    hw_reserve: Optional[float]
    hsl_reserve: Optional[float]
    hd_paid: Optional[float]
    hw_paid: Optional[float]
    hsl_paid: Optional[float]


class ClaimSchemaLight(BaseModel):
    claim_id: int
    users: List[UserSchemaLight]
    aircraft: AircraftSchemaLight
    date_of_loss: Optional[date]
    location_of_loss: Optional[str]
    status: Optional[str]



_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]