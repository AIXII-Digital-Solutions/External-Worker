from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Integer, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from .config import AviationEdgeBase as Base


class HistoricalSchedule(Base):
    __table_args__ = (
        UniqueConstraint(
            "type",
            "departure_scheduled_time",
            "departure_iata_code",
            "arrival_iata_code",
            "flight_number",
            name="uq_historical_schedule_unique_flight"
        ),
        # canonical schedule lookups: departures/arrivals from an airport on a date,
        # and a specific flight on a date
        Index("ix_histsched_dep_iata_time", "departure_iata_code", "departure_scheduled_time"),
        Index("ix_histsched_arr_iata_time", "arrival_iata_code", "arrival_scheduled_time"),
        Index("ix_histsched_flight_iata_time", "flight_iata_number", "departure_scheduled_time"),
    )
    # Main
    type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Departure
    departure_iata_code: Mapped[Optional[str]] = mapped_column(String, index=True)
    departure_icao_code: Mapped[Optional[str]] = mapped_column(String, index=True)
    departure_terminal: Mapped[Optional[str]] = mapped_column(String)
    departure_gate: Mapped[Optional[str]] = mapped_column(String)
    departure_delay: Mapped[Optional[int]] = mapped_column(Integer)

    departure_scheduled_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    departure_estimated_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    departure_actual_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    departure_estimated_runway: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    departure_actual_runway: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Arrival
    arrival_iata_code: Mapped[Optional[str]] = mapped_column(String, index=True)
    arrival_icao_code: Mapped[Optional[str]] = mapped_column(String, index=True)
    arrival_terminal: Mapped[Optional[str]] = mapped_column(String)
    arrival_baggage: Mapped[Optional[str]] = mapped_column(String)
    arrival_gate: Mapped[Optional[str]] = mapped_column(String)
    arrival_delay: Mapped[Optional[int]] = mapped_column(Integer)

    arrival_scheduled_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    arrival_estimated_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    arrival_actual_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    arrival_estimated_runway: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    arrival_actual_runway: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Airline
    airline_name: Mapped[Optional[str]] = mapped_column(String)
    airline_iata_code: Mapped[Optional[str]] = mapped_column(String, index=True)
    airline_icao_code: Mapped[Optional[str]] = mapped_column(String, index=True)

    # Flight
    flight_number: Mapped[Optional[str]] = mapped_column(String)
    flight_iata_number: Mapped[Optional[str]] = mapped_column(String, index=True)
    flight_icao_number: Mapped[Optional[str]] = mapped_column(String, index=True)

    # Codeshared Airline
    codeshared_airline_name: Mapped[Optional[str]] = mapped_column(String)
    codeshared_airline_iata_code: Mapped[Optional[str]] = mapped_column(String, index=True)
    codeshared_airline_icao_code: Mapped[Optional[str]] = mapped_column(String, index=True)

    # Codeshared Flight
    codeshared_flight_number: Mapped[Optional[str]] = mapped_column(String)
    codeshared_flight_iata_number: Mapped[Optional[str]] = mapped_column(String, index=True)
    codeshared_flight_icao_number: Mapped[Optional[str]] = mapped_column(String, index=True)
