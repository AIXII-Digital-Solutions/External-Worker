from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class CreateAirlinesBody(BaseModel):
    file_data: Optional[str] = Field(None, description="File data")
    airline_name: str = Field(..., description="Airline name")
    airline_icao: str = Field(..., description="Airline ICAO")
    user_id: Optional[UUID] = Field(default=None, description="User ID")