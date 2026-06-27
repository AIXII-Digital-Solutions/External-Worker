import inspect
import sys
from datetime import datetime
from typing import Optional

from fastapi import Query
from pydantic import BaseModel, model_validator

from .decorators import at_most_one_of


@at_most_one_of("regs", "callsigns", "airlines")
class RequestFRFlightSummary(BaseModel):
    from_pbi: bool = Query(default=False)
    regs: Optional[str] = Query(default=None)
    callsigns: Optional[str] = Query(default=None)
    airlines: Optional[str] = Query(default=None)
    start_date: datetime = Query()
    end_date: datetime = Query()
    user: Optional[str] = Query(default=None)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date >= self.end_date:
            raise ValueError(
                "start_date must be earlier than end_date and cannot be equal"
            )
        return self


class RequestFRAirports(BaseModel):
    from_pbi: bool = Query(default=False)
    codes: str = Query(..., description="ICAO or IATA code of airports")




_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
