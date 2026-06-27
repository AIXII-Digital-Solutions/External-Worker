from datetime import date
from typing import Optional, List

from pydantic import Field, BaseModel

from Schemas.Enums import EnginePositionEnum, AircraftInsuredStatusEnum, AircraftDataSourceEnum


class AircraftTechnicalDataBody(BaseModel):
    av_fixed: bool = Field(default=False, description="Is agreed value fixed?")
    data_source: Optional[AircraftDataSourceEnum] = Field(default=AircraftDataSourceEnum.CIRIUM, description="Data source")
    in_dashboard: bool = Field(default=True, description="Use in Dashboard")
    status: AircraftInsuredStatusEnum = Field(default=AircraftInsuredStatusEnum.NOT_INSURED, description="Status")


class CreateUpdateEngineBody(BaseModel):
    engine_id: Optional[int] = Field(default=None, description="Engine ID")
    position: Optional[EnginePositionEnum] = Field(default=None, description="Engine Position")
    engine_msn: Optional[str] = Field(default=None, description="Engine MSN")


class CreateUpdateAircraftBody(BaseModel):
    # Aircraft Main data
    aircraft_id: int = Field(default=None, description="Aircraft ID")
    template_id: int = Field(..., description="Template ID")
    airline_id: int = Field(..., description="Airline ID")
    aircraft_registration: str = Field(..., description="Aircraft registration number")
    aircraft_msn: int = Field(..., description="Aircraft MSN")
    mtow: Optional[int] = Field(..., description="MTOW")

    # Policy data
    policy_from: Optional[date] = Field(default=None, description="Policy from")
    policy_to: Optional[date] = Field(default=None, description="Policy to")

    # Engines data
    engines: List[CreateUpdateEngineBody]

    # Lease main data
    agreed_value: Optional[float] = Field(default=None, description="Agreed value", gt=0)
    depreciation_rate: Optional[float] = Field(default=3.0, description="Depreciation rate", ge=0)
    depreciation_start_date: Optional[date] = Field(default=None, description="Depreciation start date")

    combined_single_limit: Optional[float] = Field(default=None, description="Combined single limit", le=1_000_000_000, ge=750_000_000)
    hsl_deductible: Optional[float] = Field(default=None, description="HSL deductible", le=1_000_000, ge=750_000)
    hd_deductible: Optional[float] = Field(default=None, description="HD deductible", le=750_000, ge=100_000)

    # Lease additional data
    lessee: Optional[str] = Field(default=None, description="Lessee")
    lessor: Optional[str] = Field(default=None, description="Lessor")

    # Additional data
    technical_data: AircraftTechnicalDataBody



class CreateAircraftTemplatesBody(BaseModel):
    file_data: Optional[str] = Field(None, description="File data")
    template_name: str = Field(..., description="Template name")


class AddSumPolicyBody(BaseModel):
    aircraft_id: Optional[int] = Field(None, description="Aircraft ID")
    is_hd: bool = Field(default=False, description="Is Hull Deductible?")
    is_hw: bool = Field(default=False, description="Is Hull War?")
    is_hsl: bool = Field(default=False, description="Is HSL?")
    indemnity_reserve: Optional[float] = Field(default=None, description="Indemnity reserve amount")
    paid_to_date_amount: Optional[float] = Field(default=None, description="Paid to date amount")
    currency_rate: Optional[float] = Field(None, description="Currency rate")


class GetAircraftsFromCiriumBody(BaseModel):
    airlines_name: List[str] = Field(..., description="Airlines name")


class CreateAircraftsFromCiriumBody(BaseModel):
    registrations: List[str] = Field(..., description="Registrations")
    msns: List[str] = Field(..., description="MSNs")


class CreateAircraftsFromExcelSchema(BaseModel):
    registration: Optional[str] = Field(default=None, description="Registration")
    msn: Optional[str] = Field(default=None, description="MSN")
    airline: Optional[str] = Field(default=None, description="Airline")
    mtow: Optional[int] = Field(default=None, description="MTOW")
    av_fixed: Optional[bool] = Field(default=None, description="Agreed value fixed")
    agreed_value: Optional[int] = Field(default=None, description="Agreed value")
    csl: Optional[int] = Field(default=None, description="Combined Single Limit")
    hsl_deductible: Optional[int] = Field(default=None, description="HSL deductible")
    hd_deductible: Optional[int] = Field(default=None, description="HD deductible")
    depreciation_rate: Optional[float] = Field(default=None, description="Depreciation rate")
    depreciation_start_date: Optional[date] = Field(default=None, description="Depreciation start date")
    policy_start: Optional[date] = Field(default=None, description="Policy start date")
    policy_end: Optional[date] = Field(default=None, description="Policy end date")
    lessee: Optional[str] = Field(default=None, description="Lessee")
    lessor: Optional[str] = Field(default=None, description="Lessor")
    engine_msn_1: Optional[str] = Field(default=None, description="Engine #1 MSN")
    engine_msn_2: Optional[str] = Field(default=None, description="Engine #2 MSN")
    engine_msn_3: Optional[str] = Field(default=None, description="Engine #3 MSN")
    engine_msn_4: Optional[str] = Field(default=None, description="Engine #4 MSN")

