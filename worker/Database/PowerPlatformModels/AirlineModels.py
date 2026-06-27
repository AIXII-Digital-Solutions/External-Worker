from typing import Optional, List
from uuid import UUID as UUID_Python

from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..config import PowerPlatformBase as Base


class UserAirlineAccess(Base):
    user_id: Mapped[UUID_Python] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True
    )

    airline_id: Mapped[int] = mapped_column(
        ForeignKey("airlines.id", ondelete="CASCADE"),
        primary_key=True
    )


class Airline(Base):
    airline_name: Mapped[str] = mapped_column(String, nullable=False)
    icao: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    iata: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    asset_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
        unique=True  # 1-2-1
    )

    asset: Mapped[Optional["Asset"]] = relationship(
        back_populates="airline",
        uselist=False
    )

    users: Mapped[List["User"]] = relationship(
        secondary="userairlineaccesses",
        back_populates="airlines"
    )

    aircrafts: Mapped[List["Aircraft"]] = relationship(
        back_populates="airline",
        cascade="all, delete-orphan"
    )

