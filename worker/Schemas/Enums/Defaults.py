import inspect
import sys
from enum import Enum as __enum

class FlightStatusEnum(str, __enum):
    SCHEDULED = "scheduled"
    EN_ROUTE = "en-route"
    LANDED = "landed"


class UpsertdelStatusEnum(str, __enum):
    UPDATED = "Updated"
    CREATED = "Created"
    DELETED = "Deleted"


class EnginePositionEnum(int, __enum):
    CENTER = 0
    LEFT1 = 1
    LEFT2 = 2
    RIGHT1 = 3
    RIGHT2 = 4


class AircraftInsuredStatusEnum(str, __enum):
    INSURED = "Insured"
    NOT_INSURED = "Not Insured"


class AircraftDataSourceEnum(str, __enum):
    CIRIUM = "Cirium"
    LEASE_AGREEMENT = "Lease Agreement"
    MANUAL = "Manual"


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
