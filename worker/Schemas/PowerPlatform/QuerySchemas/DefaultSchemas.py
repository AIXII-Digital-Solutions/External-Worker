from typing import Optional
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, EmailStr

from Schemas import AppearanceEnum, OSTypeEnum


class GetUserSchemaQuery(BaseModel):
    user_email: EmailStr = Query(..., description="Email of the user")


class GetSumPolicyQuery(BaseModel):
    aircraft_id: int = Query(..., description="Aircraft ID")
    is_hd: Optional[bool] = Query(default=False, description="Is HD?")
    is_hw: Optional[bool] = Query(default=False, description="Is HW?")
    is_hsl: Optional[bool] = Query(default=False, description="Is HSL?")


class SwitchUserAppearanceQuery(BaseModel):
    user_id: UUID = Query(..., description="User ID")
    os_type: OSTypeEnum
    appearance: AppearanceEnum

