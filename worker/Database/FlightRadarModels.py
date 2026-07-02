from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import (
    DateTime, String, Integer, Float, Boolean, Interval, BigInteger, text, ForeignKey, Index,
    UniqueConstraint,
)

from .config import FlightRadarBase as Base, FlightRadarViewBase
from sqlalchemy.orm import Mapped, mapped_column, relationship


class FlightSummary(Base):
    __table_args__ = (
        # natural flight key (the app already de-dups on this before insert)
        UniqueConstraint("fr24_id", "flight", "reg", "callsign", name="uq_flightsummary_natural"),
        # "all flights of this aircraft over time"
        Index("ix_flightsummary_reg_takeoff", "reg", "datetime_takeoff"),
        # created_at is inherited from BaseMixin -> index via __table_args__ (time-range scans)
        Index("ix_flightradar_flightsummary_created_at", "created_at"),
    )

    fr24_id: Mapped[str] = mapped_column(String, nullable=True)
    flight: Mapped[str] = mapped_column(String, nullable=True)

    callsign: Mapped[str] = mapped_column(String, nullable=True, index=True)
    operating_as: Mapped[str] = mapped_column(String, nullable=True, index=True)
    painted_as: Mapped[str] = mapped_column(String, nullable=True, index=True)

    type: Mapped[str] = mapped_column(String, nullable=True, index=True)
    reg: Mapped[str] = mapped_column(String, nullable=True, index=True)

    orig_icao: Mapped[str] = mapped_column(String, nullable=True, index=True)
    orig_iata: Mapped[str] = mapped_column(String, nullable=True, index=True)

    datetime_takeoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    runway_takeoff: Mapped[str] = mapped_column(String, nullable=True)

    dest_icao: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_iata: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_icao_actual: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_iata_actual: Mapped[str] = mapped_column(String, nullable=True, index=True)

    datetime_landed: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    runway_landed: Mapped[str] = mapped_column(String, nullable=True)

    flight_time: Mapped[int] = mapped_column(Integer, nullable=True)
    actual_distance: Mapped[float] = mapped_column(Float, nullable=True)
    circle_distance: Mapped[float] = mapped_column(Float, nullable=True)

    category: Mapped[str] = mapped_column(String, nullable=True)
    hex: Mapped[str] = mapped_column(String, nullable=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    flight_ended: Mapped[bool] = mapped_column(Boolean, nullable=True)


class LivePositions(Base):
    """RANGE-partitioned by timestamp (monthly), composite PK (id, timestamp) — see migration
    a4b5c6d7e8f9. The table is partitioned at the DB level (managed by that migration); this stays a
    plain table definition (columns/PK/indexes match the DB), which Alembic compares fine as it
    ignores the partition scheme. New months are created ahead of time by external-worker
    (cron_ensure_livepositions_partition); a DEFAULT partition catches anything out of range."""
    __table_args__ = (
        # one position per flight per source timestamp (append-only history; dedup re-polls)
        UniqueConstraint("fr24_id", "timestamp", name="uq_livepositions_fr24_timestamp"),
        # latest position for a flight: WHERE reg=? AND flight=? ORDER BY created_at DESC
        Index("ix_livepositions_reg_flight_created", "reg", "flight", "created_at"),
        # latest position per aircraft (the flightradar.current_positions view: skip-scan over reg +
        # backward index scan per reg for ORDER BY timestamp DESC LIMIT 1)
        Index("ix_livepositions_reg_timestamp", "reg", "timestamp"),
        # append-only + time-ordered -> BRIN on created_at is ~KB (vs MB btree) and great for
        # historical "over a period" range scans. (btree here was unused: scans ~= 0.)
        Index("ix_livepositions_created_at_brin", "created_at", postgresql_using="brin"),
        # timestamp is the partition key (range pruned at partition level) -> BRIN, not btree.
        Index("ix_livepositions_timestamp_brin", "timestamp", postgresql_using="brin"),
        Index("ix_livepositions_eta_brin", "eta", postgresql_using="brin"),
    )

    fr24_id: Mapped[str] = mapped_column(String, nullable=True)
    flight: Mapped[str] = mapped_column(String, nullable=True)
    hex: Mapped[str] = mapped_column(String, nullable=True)

    callsign: Mapped[str] = mapped_column(String, nullable=True, index=True)

    lat: Mapped[float] = mapped_column(Float, nullable=True)
    lon: Mapped[float] = mapped_column(Float, nullable=True)
    alt: Mapped[float] = mapped_column(Float, nullable=True)
    gspeed: Mapped[float] = mapped_column(Float, nullable=True)
    vspeed: Mapped[float] = mapped_column(Float, nullable=True)

    track: Mapped[int] = mapped_column(Integer, nullable=True)

    squawk: Mapped[str] = mapped_column(String, nullable=True)
    # partition key + part of the composite PK (id, timestamp) -> NOT NULL, primary_key
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=True)

    orig_icao: Mapped[str] = mapped_column(String, nullable=True, index=True)
    orig_iata: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_icao: Mapped[str] = mapped_column(String, nullable=True, index=True)
    dest_iata: Mapped[str] = mapped_column(String, nullable=True, index=True)

    type: Mapped[str] = mapped_column(String, nullable=True, index=True)
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
        # NOTE: airports (~1k rows) and airportrunways (~3k rows) are tiny — extra secondary indexes
        # (name/city/country) are deliberately NOT added; a seq scan of a 1k-row table is instant and
        # the planner would ignore them.
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


# ---------------------------------------------------------------------------
# View (read-only). flightradar.current_positions = the latest livepositions row per aircraft
# (reg), created by a hand-written op.execute migration and NOT managed by Alembic (lives on
# FlightRadarViewBase, whose MetaData is out of the aixii target). "Current flight" for aircraft in
# the air; the last known row for ones long out of coverage. is_grounded = the aircraft is NOT
# currently airborne (stale telemetry OR low ground speed).
# ---------------------------------------------------------------------------
class CurrentPositions(FlightRadarViewBase):
    __tablename__ = "current_positions"

    # every livepositions column + is_grounded. Logical PK = id (= livepositions.id, unique per row).
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_grounded: Mapped[bool] = mapped_column(Boolean, nullable=True)

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

    orig_icao: Mapped[str] = mapped_column(String, nullable=True)
    orig_iata: Mapped[str] = mapped_column(String, nullable=True)
    dest_icao: Mapped[str] = mapped_column(String, nullable=True)
    dest_iata: Mapped[str] = mapped_column(String, nullable=True)

    type: Mapped[str] = mapped_column(String, nullable=True)
    reg: Mapped[str] = mapped_column(String, nullable=True)
    operating_as: Mapped[str] = mapped_column(String, nullable=True)
    painted_as: Mapped[str] = mapped_column(String, nullable=True)

    eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_distance: Mapped[float] = mapped_column(Float, nullable=True)
    time_delta: Mapped[timedelta] = mapped_column(Interval, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
