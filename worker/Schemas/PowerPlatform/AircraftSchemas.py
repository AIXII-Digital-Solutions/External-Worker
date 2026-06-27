import inspect
import sys
from datetime import date
from typing import List, Optional

from pydantic import BaseModel

from .DefaultSchemas import AssetSchema
from .AirlineSchemas import AirlineSchemaFull, AirlineSchemaLight
from ..Enums import AircraftInsuredStatusEnum, EnginePositionEnum


class TemplateSchemaFull(BaseModel):
    template_id: int
    template_name: str
    asset: Optional[AssetSchema]


class TemplateSchemaLight(BaseModel):
    template_id: int
    template_name: Optional[str]


class PolicySchema(BaseModel):
    policy_id: Optional[int]
    policy_from: Optional[date | str]
    policy_to: Optional[date | str]


class EngineTypeSchema(BaseModel):
    engine_id: Optional[int]
    engine_manufacture: Optional[str]
    engine_model: Optional[str]


class EngineSchema(BaseModel):
    engine: EngineTypeSchema
    position: Optional[EnginePositionEnum]
    msn: Optional[str]


class AircraftTechnicalDataSchema(BaseModel):
    in_dashboard: bool
    status: AircraftInsuredStatusEnum
    data_source: Optional[str]
    av_fixed: bool


class AircraftSchemaFull(BaseModel):
    aircraft_id: Optional[int]
    registration: str
    msn: int
    mtow: Optional[int]

    airline: AirlineSchemaFull | AirlineSchemaLight
    template: TemplateSchemaFull | TemplateSchemaLight

    policy: Optional[List[PolicySchema]]

    engines: Optional[List[EngineSchema]]

    agreed_value: Optional[float]
    depreciation_rate: Optional[float]
    depreciation_start_date: Optional[date | str]
    combined_single_limit: Optional[float]
    hsl_deductible: Optional[float]
    hd_deductible: Optional[float]

    lessee: Optional[str]
    lessor: Optional[str]

    technical_data: Optional[AircraftTechnicalDataSchema]


class AircraftSchemaLight(BaseModel):
    aircraft_id: int
    registration: str
    msn: int

    status: str

    airline: AirlineSchemaFull | AirlineSchemaLight
    template: TemplateSchemaFull | TemplateSchemaLight


class AdditionalAircraftInfoValuationSchema(BaseModel):
    date: str
    market_value: Optional[float]


class AdditionalAircraftInfoSchema(BaseModel):
    aircraft: str
    msn: str
    age: Optional[float]
    num_of_engines: Optional[int]
    engines_type: Optional[str]
    apu_type: Optional[str]
    mtow: Optional[int]
    num_of_seats: Optional[int]
    lease_rate: Optional[float]
    market_values: Optional[List[AdditionalAircraftInfoValuationSchema]]


class SumPolicySchema(BaseModel):
    hd_reserve: float
    hw_reserve: float
    hsl_reserve: float
    hd_paid: float
    hw_paid: float
    hsl_paid: float



class CiriumAircraftSchema(BaseModel):
    registration: str
    msn: str
    master_series: str
    airline: str


class ExcelAircraftSchema(BaseModel):
    registration: Optional[str]
    msn: Optional[str]
    airline: Optional[str]
    mtow: Optional[int]
    av_fixed: Optional[bool]
    agreed_value: Optional[int]
    csl: Optional[int]
    hsl_deductible: Optional[int]
    hd_deductible: Optional[int]
    depreciation_rate: Optional[float]
    depreciation_start_date: Optional[date]
    policy_start: Optional[date]
    policy_end: Optional[date]
    lessee: Optional[str]
    lessor: Optional[str]
    engine_msn_1: Optional[str]
    engine_msn_2: Optional[str]
    engine_msn_3: Optional[str]
    engine_msn_4: Optional[str]


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]

