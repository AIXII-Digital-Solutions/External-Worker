from typing import Optional
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel

from Schemas.decorators import at_least_one_of


@at_least_one_of("claim_id", "user_id")
class GetClaimQuery(BaseModel):
    claim_id: Optional[int] = Query(default=None, description="Claim ID")
    user_id: Optional[UUID] = Query(default=None, description="User ID")


class DeleteClaimQuery(BaseModel):
    claim_id: int = Query(..., description="Claim ID")