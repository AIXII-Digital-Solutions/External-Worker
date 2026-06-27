from datetime import datetime, date
from typing import Optional, List
from uuid import UUID as UUID_Python

from sqlalchemy import DateTime, String, Boolean, ForeignKey, LargeBinary, Date, Float, Enum, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from src.Schemas.Enums import OSTypeEnum
except ModuleNotFoundError:
    from Schemas.Enums import OSTypeEnum

from .AssociationModels import _claim_users
from ..config import PowerPlatformBase as Base


class User(Base):
    user_id: Mapped[UUID_Python] = mapped_column(UUID, unique=True, index=True)

    display_name: Mapped[str] = mapped_column(String, nullable=True)
    given_name: Mapped[str] = mapped_column(String, nullable=True)
    surname: Mapped[str] = mapped_column(String, nullable=True)
    user_principal_name: Mapped[str] = mapped_column(String, nullable=True)
    account_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=True)
    mail: Mapped[str] = mapped_column(String, nullable=True)
    mobile_phone: Mapped[str] = mapped_column(String, nullable=True)
    business_phones: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=True)
    city: Mapped[str] = mapped_column(String, nullable=True)
    country: Mapped[str] = mapped_column(String, nullable=True)
    department: Mapped[str] = mapped_column(String, nullable=True)
    job_title: Mapped[str] = mapped_column(String, nullable=True)
    employee_id: Mapped[str] = mapped_column(String, nullable=True)
    employee_hire_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_date_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    super_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


    manager_id: Mapped[UUID_Python | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    manager: Mapped["User"] = relationship(
        remote_side="User.id",
        back_populates="direct_reports"
    )

    direct_reports: Mapped[List["User"]] = relationship(back_populates="manager")

    application_accesses: Mapped[List["Access"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )

    airlines: Mapped[List["Airline"]] = relationship(
        secondary="userairlineaccesses",
        back_populates="users"
    )

    claims: Mapped[List["Claim"]] = relationship(
        secondary=_claim_users,
        back_populates="users"
    )

    device_settings: Mapped[list["UserDeviceSetting"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )


class UserDeviceSetting(Base):
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "os_type",
            name="uq_user_device_os_type"
        ),
    )
    os_type: Mapped[OSTypeEnum] = mapped_column(Enum(OSTypeEnum), nullable=False)


    user_id: Mapped[UUID_Python] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    appearance_id: Mapped[int] = mapped_column(
        ForeignKey("applicationappearances.id", ondelete="CASCADE"),
        nullable=False
    )
    user: Mapped["User"] = relationship(
        back_populates="device_settings"
    )
    appearance: Mapped["ApplicationAppearance"] = relationship(
        back_populates="user_device_settings"
    )



class Asset(Base):
    asset_name: Mapped[str] = mapped_column(String, nullable=False)
    asset_description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    base64: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    airline: Mapped["Airline"] = relationship(back_populates="asset")
    aircraft_template: Mapped["AircraftTemplate"] = relationship(
        back_populates="asset",
        uselist=False
    )

    application_id: Mapped[UUID_Python] = mapped_column(
        UUID, ForeignKey("applications.application_id"), nullable=True
    )

    application: Mapped["Application"] = relationship(
        "Application",
        back_populates="asset",
        uselist=False
    )


class Claim(Base):
    aircraft_id: Mapped[int] = mapped_column(
        ForeignKey("aircraft.id", ondelete="RESTRICT"),
        nullable=False
    )

    policy_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("aircraftpolicies.id", ondelete="SET NULL"),
        nullable=True
    )

    aircraft: Mapped["Aircraft"] = relationship(back_populates="claims")

    users: Mapped[List["User"]] = relationship(
        secondary=_claim_users,
        back_populates="claims"
    )

    policy: Mapped["AircraftPolicy"] = relationship(
        back_populates="claims"
    )

    date_of_loss: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    location_of_loss: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    status: Mapped[str] = mapped_column(String, nullable=False, default="Opened")

    damage: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    indemnity_reserve_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    paid_to_date_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    paid_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    is_hd: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hw: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hsl: Mapped[bool] = mapped_column(Boolean, default=False)

    leader: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    surveyor: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    currency: Mapped[Optional[str]] = mapped_column(String, nullable=True, default="USD")
    currency_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=1)

    hd_reserve: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hw_reserve: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hsl_reserve: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hd_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hw_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hsl_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)





