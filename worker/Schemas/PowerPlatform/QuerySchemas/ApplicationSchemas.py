from typing import Optional
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel

from Schemas import AppearanceEnum
from Schemas.decorators import exactly_one_of, at_least_one_of
from Schemas.Enums import OSTypeEnum


class GetApplicationIdQuery(BaseModel):
    application_id: Optional[UUID] = Query(default=None, description="Application ID")


class GetApplicationSizeQuery(BaseModel):
    screen_size: Optional[int] = Query(default=None, description="Screen size")


@at_least_one_of("user_id", "screen_size")
class DeviceInfo(BaseModel):
    user_id: Optional[UUID] = Query(default=None, description="User ID")
    os_type: OSTypeEnum
    screen_size: Optional[int] = Query(default=None, description="Screen size")


@exactly_one_of('file_name', 'file_id')
class GetApplicationFileQuery(BaseModel):
    file_name: Optional[str] = Query(None, description="File name")
    file_id: Optional[int] = Query(None, description="File ID")


class UpsertAppearanceQuery(BaseModel):
    appearance: AppearanceEnum
    main_color: str = Query(..., description="Main color of the theme")
    secondary_color: str = Query(..., description="Secondary color of the theme")
    app_color: str = Query(..., description="Color of main app layer")
    background_color: str = Query(..., description="Background color")
    field_color: str = Query(..., description="Fields color")
    button_color: str = Query(..., description="Buttons color")

