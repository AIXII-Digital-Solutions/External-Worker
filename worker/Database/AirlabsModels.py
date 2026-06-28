from datetime import datetime

from sqlalchemy import String, BigInteger, Float, DateTime, Index, ForeignKey
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .config import AirlabsBase as Base

try:
    from Schemas.Enums.Defaults import FlightStatusEnum
except ImportError:
    from ..Schemas.Enums.Defaults import FlightStatusEnum


# Shared native PG enum `airlabs.flight_status` — used by BOTH FlightSnapshot and AircraftState.
# create_type=False: the type is created exactly ONCE by the aixii schema-baseline migration
# (`flight_status_type.create(bind, checkfirst=True)`), so a single squashed initial migration
# that builds both tables does not emit two conflicting `CREATE TYPE flight_status` statements.
flight_status_type = PgEnum(
    FlightStatusEnum,
    name="flight_status",
    schema="airlabs",
    create_type=False,
    create_constraint=False,
)


class FlightSnapshot(Base):
    # identifiers
    hex: Mapped[str] = mapped_column(String(6), nullable=False)
    reg_number: Mapped[str] = mapped_column(String(16), nullable=False)

    airline_icao: Mapped[str] = mapped_column(String(4), nullable=True)
    airline_iata: Mapped[str] = mapped_column(String(4), nullable=True)

    aircraft_icao: Mapped[str] = mapped_column(String(4), nullable=True)

    flight_icao: Mapped[str] = mapped_column(String(10), nullable=True)
    flight_iata: Mapped[str] = mapped_column(String(10), nullable=True)
    flight_number: Mapped[str] = mapped_column(String(10), nullable=True)

    # airports
    dep_icao: Mapped[str] = mapped_column(String(4), nullable=True)
    dep_iata: Mapped[str] = mapped_column(String(4), nullable=True)
    arr_icao: Mapped[str] = mapped_column(String(4), nullable=True)
    arr_iata: Mapped[str] = mapped_column(String(4), nullable=True)

    # geo / motion
    lat: Mapped[float] = mapped_column(Float, nullable=True)
    lng: Mapped[float] = mapped_column(Float, nullable=True)
    alt: Mapped[float] = mapped_column(Float, nullable=True)  # meters
    dir: Mapped[float] = mapped_column(Float, nullable=True)  # degrees 0–360
    speed: Mapped[float] = mapped_column(Float, nullable=True)  # km/h
    v_speed: Mapped[float] = mapped_column(Float, nullable=True)  # km/h

    squawk: Mapped[str] = mapped_column(String(4), nullable=True)
    flag: Mapped[str] = mapped_column(String(4), nullable=True)

    status: Mapped[FlightStatusEnum] = mapped_column(
        flight_status_type,
        nullable=False
    )

    updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )

    current_state = relationship(
        "AircraftState",
        back_populates="snapshot",
        uselist=False,
        primaryjoin="FlightSnapshot.reg_number==foreign(AircraftState.reg_number)"
    )

    type: Mapped[str] = mapped_column(String, nullable=True)


class AircraftState(Base):
    reg_number: Mapped[str] = mapped_column(String(16), primary_key=True, index=True, unique=True)

    airline_icao: Mapped[str] = mapped_column(String(4), index=True, nullable=True)
    airline_iata: Mapped[str] = mapped_column(String(4), index=True, nullable=True)

    status: Mapped[FlightStatusEnum] = mapped_column(
        flight_status_type,
        nullable=False
    )

    last_update: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("flightsnapshot.id", ondelete="SET NULL"),
        nullable=True
    )

    snapshot: Mapped["FlightSnapshot"] = relationship(
        "FlightSnapshot",
        back_populates="current_state",
        lazy="joined"
    )


Index("ix_snapshots_reg_number", FlightSnapshot.reg_number)
Index("ix_snapshots_airline_icao", FlightSnapshot.airline_icao)
Index("ix_snapshots_airline_iata", FlightSnapshot.airline_iata)
Index("ix_snapshots_updated", FlightSnapshot.updated)

Index(
    "ix_snapshots_reg_updated",
    FlightSnapshot.reg_number,
    FlightSnapshot.updated.desc()
)

Index(
    "ix_snapshots_reg_status",
    FlightSnapshot.reg_number,
    FlightSnapshot.status,
)
