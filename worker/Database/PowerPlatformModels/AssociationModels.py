from sqlalchemy import Table, Column, ForeignKey, UUID, ForeignKeyConstraint

from ..config import PowerPlatformBase as Base


_claim_users = Table(
    "_claim_users",
    Base.metadata,
    Column("claim_id", ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
)


application_access_rules = Table(
    "application_access_rules",
    Base.metadata,

    Column("user_id", UUID, primary_key=True),
    Column("application_id", UUID, primary_key=True),
    Column(
        "rule_id",
        ForeignKey("rules.id", ondelete="CASCADE"),
        primary_key=True
    ),

    ForeignKeyConstraint(
        ["user_id", "application_id"],
        ["accesses.user_id", "accesses.application_id"],
        ondelete="CASCADE"
    ),
)



