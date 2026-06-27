import inspect
import sys
from datetime import date, datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, EmailStr

from .DefaultSchemas import ApplicationAccessSchema


class UserSchemaFull(BaseModel):
    user_id: UUID
    user_displayname: Optional[str]
    given_name: Optional[str]
    surname: Optional[str]
    user_principal_name: Optional[str]
    account_enabled: Optional[bool]
    user_mail: Optional[str]
    mobile_phone: Optional[str]
    city: Optional[str]
    country: Optional[str]
    department: Optional[str]
    job_title: Optional[str]
    employee_id: Optional[str]
    employee_hire_date: Optional[date]
    created_date_time: Optional[datetime]
    manager_id: Optional[UUID]
    application_accesses: List[ApplicationAccessSchema]


class UserSchemaLight(BaseModel):
    user_id: UUID
    user_displayname: Optional[str]
    user_mail: Optional[EmailStr] | Optional[str]


class UserAccessSchema(BaseModel):
    user: UserSchemaLight
    super_admin: bool
    applications: List[ApplicationAccessSchema]

    class Config:
        from_attributes = True


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]

