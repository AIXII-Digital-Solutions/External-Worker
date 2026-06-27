from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ApplicationFileLoadBody(BaseModel):
    file_name: str = Field(..., description="File name")
    file_description: Optional[str] = Field(default=None, description="File description")
    file_data: str = Field(..., description="File data")


class CreateClaimBody(BaseModel):
    claim_id: Optional[int] = Field(default=None, description="Claim ID")
    user_id: UUID = Field(..., description="User ID")
    aircraft_id: int = Field(..., description="Aircraft ID")
    date_of_loss: Optional[date | str] = Field(default=None, description="Date of loss")
    location_of_loss: Optional[str] = Field(default=None, description="Location of loss")
    status: str = Field(..., description="Status")
    damage: Optional[str] = Field(default=None, description="Damage")
    indemnity_reserve_amount: Optional[float] = Field(default=None, description="Indemnity reserve amount")
    paid_to_date_amount: Optional[float] = Field(default=None, description="Paid to date amount")
    paid_date: Optional[date | str] = Field(default=None, description="Paid date")
    is_hd: Optional[bool] = Field(default=False, description="Is Hull Deductible")
    is_hw: Optional[bool] = Field(default=False, description="Is Hull War")
    is_hsl: Optional[bool] = Field(default=False, description="Is HSL")
    leader: Optional[str] = Field(default=None, description="Leader")
    surveyor: Optional[str] = Field(default=None, description="Surveyor")
    currency: Optional[str] = Field(default=None, description="Currency")
    currency_rate: Optional[float] = Field(default=None, description="Currency rate")
    hd_reserve: Optional[float] = Field(default=None, description="HD reserve")
    hw_reserve: Optional[float] = Field(default=None, description="HW reserve")
    hsl_reserve: Optional[float] = Field(default=None, description="HSL reserve")
    hd_paid: Optional[float] = Field(default=None, description="HD paid")
    hw_paid: Optional[float] = Field(default=None, description="HW paid")
    hsl_paid: Optional[float] = Field(default=None, description="HSL paid")





