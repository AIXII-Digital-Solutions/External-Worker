import inspect
import sys
from typing import TypeVar, Generic


from pydantic import BaseModel
from uuid import UUID


T = TypeVar("T")

class DetailField(BaseModel):
    msg: str
    correlationId: UUID


class DefaultResponse(BaseModel, Generic[T]):
    status_code: int
    details: DetailField
    data: T


class ErrorResponse(BaseModel):
    status_code: int
    details: DetailField
    data: list[None] = [None]


class ErrorValidObject(DetailField):
    field: str




_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]

