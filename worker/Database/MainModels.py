import inspect
import sys
from datetime import date

from sqlalchemy import String, Integer, Boolean, Date
from sqlalchemy.orm import Mapped, mapped_column
from .config import MainBase as Base


class Registrations(Base):
    reg: Mapped[str] = mapped_column(String, index=True)
    msn: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    aircraft_type: Mapped[str] = mapped_column(String, nullable=True)
    indashboard: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, nullable=False)


class Airlines(Base):
    airline_name: Mapped[str] = mapped_column(String, index=True)
    icao: Mapped[str] = mapped_column(String, index=True)


class Guests(Base):
    guest_email: Mapped[str] = mapped_column(String, unique=True)
    guest_upn: Mapped[str] = mapped_column(String)
    guest_name: Mapped[str] = mapped_column(String, default=None)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    inviter_email: Mapped[str] = mapped_column(String)
    expires_at: Mapped[date] = mapped_column(Date)
    invite_status: Mapped[int] = mapped_column(Integer, default=0)  # 0 = PendingAcceptance


class Lease_Output(Base):
    filename: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True, name="File name")
    aircraft_count: Mapped[int] = mapped_column(Integer, default=0, name="Aircraft Count")
    engines_count: Mapped[int] = mapped_column(Integer, default=0, name="Engines Count")
    aircraft_type: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Type")
    msn: Mapped[str] = mapped_column(String, nullable=True, name="MSN")
    engines_manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="Engines Manufacture")
    engines_models: Mapped[str] = mapped_column(String, nullable=True, name="Engines Models")
    engine1_msn: Mapped[str] = mapped_column(String, nullable=True, name="Engine1 MSN")
    engine2_msn: Mapped[str] = mapped_column(String, nullable=True, name="Engine2 MSN")
    aircraft_registration: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Registration")
    dated: Mapped[str] = mapped_column(String, nullable=True, name="Agreement dated as of")
    lessee: Mapped[str] = mapped_column(String, nullable=True, name="Lessee")
    lessor: Mapped[str] = mapped_column(String, nullable=True, name="Lessor")
    currency: Mapped[str] = mapped_column(String, nullable=True, name="Currency")
    damage_proceeds_threshold: Mapped[str] = mapped_column(String, nullable=True, name="Damage Proceeds Threshold")
    aircraft_agreed_value: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Agreed Value")
    aircraft_hull_all_risks: Mapped[str] = mapped_column(String, nullable=True, name="Hull All Risks")
    min_liability_coverages: Mapped[str] = mapped_column(String, nullable=True, name="Minimal Liability Coverages")
    all_risks_deductible: Mapped[str] = mapped_column(String, nullable=True, name="All Risks Deductible")


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
