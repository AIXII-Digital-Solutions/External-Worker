from typing import Optional
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel

from Schemas.decorators import exactly_one_of


@exactly_one_of('airline_name', 'airline_id', 'user_id')
class GetAirlineQuery(BaseModel):
    airline_name: Optional[str] = Query(default=None, description="Airline name")
    airline_id: Optional[int] = Query(default=None, description="Airline ID")
    user_id: Optional[UUID] = Query(default=None, description="User ID")