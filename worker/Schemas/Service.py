import inspect
import sys
from typing import List, Optional

from pydantic import BaseModel, EmailStr


class JsonFileSchema(BaseModel):
    user_email: Optional[EmailStr] = None
    filename: str
    type: str


class ProgressFileSchema(BaseModel):
    user_email: EmailStr
    filename: str
    type: str
    queue_position: int
    status: str
    status_description: Optional[str] = None
    progress: float


class StatusResponseSchema(BaseModel):
    user_email: str
    total: int
    processing_file: str
    processing_status: str
    processing_status_description: str
    progress: float
    data: List[ProgressFileSchema]


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
