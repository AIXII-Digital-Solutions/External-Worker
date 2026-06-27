import inspect
import sys
from datetime import date, timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, EmailStr, HttpUrl, Field

from ..Enums import UpsertdelStatusEnum
from ..Enums.PowerPlatformAPI import UserTypesEnum, AppearanceEnum


class InviteUserSchemaQuery(BaseModel):
    user_email: EmailStr = Query(description="Email of the user")
    user_displayName: Optional[str] = Query(None, description="Display name of the user", examples=["Marco Polo"])
    user_type: UserTypesEnum = Query(UserTypesEnum.GUEST, description="Type of the user",
                                     examples=[UserTypesEnum.GUEST, UserTypesEnum.MEMBER])
    inviter_email: EmailStr = Query(description="Email of the inviter")
    custom_message: Optional[str] = Query(None, description="Custom message in user invitation")
    expires_at: Optional[date] = Query(default_factory=lambda: date.today() + timedelta(days=30),
                                       description="Expiration date in YYYY-MM-DD format")
    reset_redemption: bool = Query(False, description="Reset redemption")
    redirect_url: Optional[HttpUrl] = Query("https://myaccount.microsoft.com/organizations",
                                            description="Redirect URL after accepting invitation. Only HTTPS allowed")


class InviteUserSchema(BaseModel):
    user_email: EmailStr = Field(description="Email of the user")
    user_displayName: Optional[str] = Field(None, description="Display name of the user", examples=["Marco Polo"])
    user_type: UserTypesEnum = Field(UserTypesEnum.GUEST, description="Type of the user",
                                     examples=[UserTypesEnum.GUEST, UserTypesEnum.MEMBER])
    inviter_email: EmailStr = Field(description="Email of the inviter")
    custom_message: Optional[str] = Field(None, description="Custom message in user invitation")
    expires_at: Optional[date] = Field(default_factory=lambda: date.today() + timedelta(days=30),
                                       description="Expiration date in YYYY-MM-DD format")
    reset_redemption: bool = Field(False, title="", description="Reset redemption")
    redirect_url: Optional[HttpUrl] = Field("https://myaccount.microsoft.com/organizations",
                                            description="Redirect URL after accepting invitation. Only HTTPS allowed")


class UpsertdelResponseSchema(BaseModel):
    status: UpsertdelStatusEnum


class AssetSchema(BaseModel):
    file_id: Optional[int]
    file_name: Optional[str]
    file_description: Optional[str]
    file_data: Optional[str]


class RuleSchema(BaseModel):
    rule_id: int
    rule_name: str
    rule_description: Optional[str]


class ApplicationRulesSchema(BaseModel):
    application_id: UUID
    rules: List[RuleSchema]


class ApplicationAccessSchema(BaseModel):
    application_id: UUID
    rules: List[RuleSchema]
    main_access: bool


class ApplicationSchema(BaseModel):
    application_id: UUID
    application_name: str
    application_description: Optional[str]
    application_status: str
    asset: Optional[AssetSchema]


class ApplicationAppearanceSchema(BaseModel):
    appearance: AppearanceEnum
    main_color: str
    secondary_color: str
    app_color: str
    background_color: str
    field_color: str
    button_color: str


class FontSchema(BaseModel):
    font_name: str
    font_size: int
    font_color: str
    font_weight: str
    screen_size: int
    usage_name: str


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
