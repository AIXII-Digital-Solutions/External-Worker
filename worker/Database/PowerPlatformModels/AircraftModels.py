from datetime import date
from typing import Optional, List

from sqlalchemy import String, Boolean, ForeignKey, Integer, Date, Float, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from src.Schemas.Enums import EnginePositionEnum, AircraftInsuredStatusEnum, AircraftDataSourceEnum
except ModuleNotFoundError:
    from Schemas.Enums import EnginePositionEnum, AircraftInsuredStatusEnum, AircraftDataSourceEnum

from ..config import PowerPlatformBase as Base


class AircraftEngineManual(Base):
    __tablename__ = 'aircraftenginesmanual'
    aircraft_manual_id: Mapped[int] = mapped_column(
        ForeignKey("aircraftmanual.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    engine_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    position: Mapped[Optional[EnginePositionEnum]] = mapped_column(Enum(EnginePositionEnum), nullable=False)
    engine_msn: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    manual: Mapped["AircraftManual"] = relationship(
        back_populates="engines"
    )


class AircraftManual(Base):
    __tablename__ = "aircraftmanual"
    aircraft_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    registration: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    msn: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    airline_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    template_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mtow: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    agreed_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    agreed_value_result: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    combined_single_limit: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.0)
    hsl_deductible: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.0)
    hd_deductible: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.0)
    depreciation_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=3.0)
    depreciation_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    policy_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    policy_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    lessee: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lessor: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    data_source: Mapped[AircraftDataSourceEnum] = mapped_column(
        Enum(AircraftDataSourceEnum),
        nullable=True,
        default=AircraftDataSourceEnum.CIRIUM
    )

    status: Mapped[AircraftInsuredStatusEnum] = mapped_column(
        Enum(AircraftInsuredStatusEnum),
        nullable=False,
        default=AircraftInsuredStatusEnum.NOT_INSURED
    )

    av_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    in_dashboard: Mapped[bool] = mapped_column(Boolean, default=False)


    engines: Mapped[list["AircraftEngineManual"]] = relationship(
        back_populates="manual",
        cascade="all, delete-orphan",
        lazy="selectin"
    )


class Aircraft(Base):
    registration: Mapped[str] = mapped_column(String, nullable=False, index=True)
    msn: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    airline_id: Mapped[int] = mapped_column(
        ForeignKey("airlines.id", ondelete="CASCADE"),
        nullable=True
    )

    template_id: Mapped[int] = mapped_column(
        ForeignKey("aircrafttemplates.id", ondelete="RESTRICT"),
        nullable=True
    )

    mtow: Mapped[int] = mapped_column(Integer, nullable=True)
    agreed_value: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    agreed_value_result: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    combined_single_limit: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    hsl_deductible: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    hd_deductible: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    depreciation_rate: Mapped[float] = mapped_column(Float, nullable=True, default=3.0)
    depreciation_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    policy: Mapped[List["AircraftPolicy"]] = relationship(
        back_populates="aircraft",
        cascade="all, delete-orphan"
    )

    engines: Mapped[List["AircraftEngine"]] = relationship(
        back_populates="aircraft",
        cascade="all, delete-orphan"
    )

    airline: Mapped["Airline"] = relationship(
        back_populates="aircrafts"
    )
    template: Mapped["AircraftTemplate"] = relationship(
        back_populates="aircrafts"
    )

    claims: Mapped[List["Claim"]] = relationship(
        back_populates="aircraft"
    )

    technical_data: Mapped["AircraftTechnicalData"] = relationship(
        back_populates="aircraft",
        uselist=False,
        cascade="all, delete-orphan"
    )

    lessee_lessors: Mapped[List["AircraftLesseeLessor"]] = relationship(
        back_populates="aircraft",
        cascade="all, delete-orphan"
    )


class AircraftTemplate(Base):
    template_name: Mapped[str] = mapped_column(String, nullable=False, index=True)

    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
        unique=True, index=True
    )

    asset: Mapped["Asset"] = relationship(
        back_populates="aircraft_template",
        uselist=False
    )

    aircrafts: Mapped[List["Aircraft"]] = relationship(
        back_populates="template"
    )


class AircraftPolicy(Base):
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False, index=True)

    policy_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    policy_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)

    aircraft: Mapped["Aircraft"] = relationship(back_populates="policy")

    claims: Mapped[List["Claim"]] = relationship(back_populates="policy", cascade="all, delete-orphan")


class AircraftTechnicalData(Base):
    aircraft_id: Mapped[int] = mapped_column(
        ForeignKey("aircraft.id", ondelete="CASCADE"),
        unique=True,
        nullable=False, index=True
    )

    data_source: Mapped[AircraftDataSourceEnum] = mapped_column(
        Enum(AircraftDataSourceEnum),
        nullable=True,
        default=AircraftDataSourceEnum.CIRIUM
    )

    data_source_row_id: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[AircraftInsuredStatusEnum] = mapped_column(
        Enum(AircraftInsuredStatusEnum),
        nullable=False,
        default=AircraftInsuredStatusEnum.NOT_INSURED
    )

    av_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    in_dashboard: Mapped[bool] = mapped_column(Boolean, default=False)

    aircraft: Mapped["Aircraft"] = relationship(
        back_populates="technical_data"
    )


class AircraftLesseeLessor(Base):
    aircraft_id: Mapped[int] = mapped_column(
        ForeignKey("aircraft.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    lessee: Mapped[str] = mapped_column(String, nullable=True)
    lessor: Mapped[str] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)

    aircraft: Mapped["Aircraft"] = relationship(
        back_populates="lessee_lessors"
    )


class AircraftEngine(Base):
    aircraft_id: Mapped[int] = mapped_column(
        ForeignKey("aircraft.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    engine_id: Mapped[int] = mapped_column(
        ForeignKey("engines.id", ondelete="RESTRICT"),
        nullable=False
    )

    engine_msn: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    position: Mapped[Optional[EnginePositionEnum]] = mapped_column(Enum(EnginePositionEnum), nullable=True)

    aircraft: Mapped["Aircraft"] = relationship(back_populates="engines")
    engine: Mapped["Engine"] = relationship(back_populates="aircraft_engines")


class Engine(Base):
    engine_manufacture: Mapped[str] = mapped_column(String, nullable=False)
    engine_model: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    aircraft_engines: Mapped[List["AircraftEngine"]] = relationship(
        back_populates="engine"
    )

