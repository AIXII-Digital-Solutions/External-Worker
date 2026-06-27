from typing import Optional, List

from fastapi import Query
from pydantic import BaseModel

from Schemas.decorators import exactly_one_of, at_most_one_of


class GetAircraftIDQuery(BaseModel):
    aircraft_id: int = Query(..., description="Aircraft ID")


@at_most_one_of("template_name", "template_id")
class GetAircraftTemplateQuery(BaseModel):
    template_name: Optional[str] = Query(default=None, description="Template name")
    template_id: Optional[int] = Query(default=None, description="Template ID")


@at_most_one_of('template_id', 'template_name', 'aircraft_registration',
                'aircraft_msn', 'aircraft_id', 'airline_id', 'airline_name')
class GetAircraftQuery(BaseModel):
    aircraft_id: Optional[int] = Query(default=None, description="Aircraft ID")
    aircraft_registration: Optional[str] = Query(default=None, description="Aircraft registration number")
    aircraft_msn: Optional[int] = Query(default=None, description="Aircraft MSN")
    template_name: Optional[str] = Query(default=None, description="Template name")
    template_id: Optional[int] = Query(default=None, description="Template ID")
    airline_name: Optional[str] = Query(default=None, description="Airline name")
    airline_id: Optional[int] = Query(default=None, description="Airline ID")


@exactly_one_of('engine_id', 'engine_type', 'engine_manufacture')
class GetEngineTypeQuery(BaseModel):
    engine_id: Optional[int] = Query(default=None, description="Engine ID")
    engine_type: Optional[str] = Query(default=None, description="Engine type")
    engine_manufacture: Optional[str] = Query(default=None, description="Engine manufacture")

