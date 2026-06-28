from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import (
    DateTime, String, Integer, Float, Boolean, Interval, text, ForeignKey, Index, UniqueConstraint,
)

from .config import FlightRadarBase as Base
from sqlalchemy.orm import Mapped, mapped_column, relationship


class FlightSummary(Base):
    __table_args__ = (
        # natural flight key (the app already de-dups on this before insert)
        UniqueConstraint("fr24_id", "flight", "reg", "callsign", name="uq_flightsummary_natural"),
        # "all flights of this aircraft over time"
        Index("ix_flightsummary_reg_takeoff", "reg", "datetime_takeoff"),
    )

    fr24_id: Mapped[str] = mapped_column(String, nullable=True)
    flight: Mapped[str] = mapped_column(String, nullable=True)

    callsign: Mapped[str] = mapped_column(String, nullable=True)
    operating_as: Mapped[str] = mapped_column(String, nullable=True, index=True)
    painted_as: Mapped[str] = mapped_column(String, nullable=True, index=True)

    type: Mapped[str] = mapped_column(String, nullable=True, index=True)
    reg: Mapped[str] = mapped_column(String, nullable=True, index=True)

    orig_icao: Mapped[str] = mapped_column(String, nullable=True, index=True)
    orig_iata: Mapped[str] = mapped_column(String, nullable=True, index=True)

    datetime_takeoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    runway_takeoff: Mapped[str] = mapped_column(String, nullable=True)

    dest_icao: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_iata: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_icao_actual: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_iata_actual: Mapped[str] = mapped_column(String, nullable=True, index=True)

    datetime_landed: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    runway_landed: Mapped[str] = mapped_column(String, nullable=True)

    flight_time: Mapped[int] = mapped_column(Integer, nullable=True)
    actual_distance: Mapped[float] = mapped_column(Float, nullable=True)
    circle_distance: Mapped[float] = mapped_column(Float, nullable=True)

    category: Mapped[str] = mapped_column(String, nullable=True)
    hex: Mapped[str] = mapped_column(String, nullable=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    flight_ended: Mapped[bool] = mapped_column(Boolean, nullable=True)


class LivePositions(Base):
    __table_args__ = (
        # one position per flight per source timestamp (append-only history; dedup re-polls)
        UniqueConstraint("fr24_id", "timestamp", name="uq_livepositions_fr24_timestamp"),
        # latest position for a flight: WHERE reg=? AND flight=? ORDER BY created_at DESC
        Index("ix_livepositions_reg_flight_created", "reg", "flight", "created_at"),
    )

    fr24_id: Mapped[str] = mapped_column(String, nullable=True)
    flight: Mapped[str] = mapped_column(String, nullable=True)
    hex: Mapped[str] = mapped_column(String, nullable=True)

    callsign: Mapped[str] = mapped_column(String, nullable=True)

    lat: Mapped[float] = mapped_column(Float, nullable=True)
    lon: Mapped[float] = mapped_column(Float, nullable=True)
    alt: Mapped[float] = mapped_column(Float, nullable=True)
    gspeed: Mapped[float] = mapped_column(Float, nullable=True)
    vspeed: Mapped[float] = mapped_column(Float, nullable=True)

    track: Mapped[int] = mapped_column(Integer, nullable=True)

    squawk: Mapped[str] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=True)

    orig_icao: Mapped[str] = mapped_column(String, nullable=True, index=True)
    orig_iata: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_icao: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_iata: Mapped[str] = mapped_column(String, nullable=True, index=True)

    type: Mapped[str] = mapped_column(String, nullable=True)
    reg: Mapped[str] = mapped_column(String, nullable=True, index=True)
    operating_as: Mapped[str] = mapped_column(String, nullable=True, index=True)
    painted_as: Mapped[str] = mapped_column(String, nullable=True, index=True)

    eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    actual_distance: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    time_delta: Mapped[timedelta] = mapped_column(Interval, nullable=False, server_default=text("INTERVAL '0'"))


class Airports(Base):
    __table_args__ = (
        # ICAO / IATA are globally-unique airport codes and the existence-check lookup key
        # (WHERE icao=? OR iata=?). UNIQUE gives both the constraint and the lookup index.
        UniqueConstraint("icao", name="uq_airports_icao"),
        UniqueConstraint("iata", name="uq_airports_iata"),
    )

    name: Mapped[str] = mapped_column(String, nullable=False)

    iata: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    icao: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)

    lon: Mapped[float] = mapped_column(Float, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)

    elevation: Mapped[int] = mapped_column(Integer, nullable=False)

    city: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    country_name: Mapped[str] = mapped_column(String, nullable=False)

    timezone_name: Mapped[str] = mapped_column(String, nullable=False)
    timezone_offset: Mapped[int] = mapped_column(Integer, nullable=False)

    runways: Mapped[List["AirportRunways"]] = relationship(
        back_populates="airport",
        cascade="all, delete-orphan"
    )


class AirportRunways(Base):
    __table_args__ = (
        # a runway is unique within its airport (designator e.g. "01L"/"27R")
        UniqueConstraint("airport_id", "designator", name="uq_airportrunways_airport_designator"),
    )

    airport_id: Mapped[int] = mapped_column(
        ForeignKey(Airports.id, ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    designator: Mapped[str] = mapped_column(String, nullable=False)
    heading: Mapped[int] = mapped_column(Integer, nullable=False)

    length: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)

    elevation: Mapped[int] = mapped_column(Integer, nullable=False)

    thr_lat: Mapped[float] = mapped_column(Float, nullable=False)
    thr_lon: Mapped[float] = mapped_column(Float, nullable=False)

    surface_type: Mapped[str] = mapped_column(String, nullable=False)
    surface_description: Mapped[str] = mapped_column(String, nullable=False)

    airport: Mapped["Airports"] = relationship(back_populates="runways")



