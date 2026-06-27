"""
Shared aircraft-domain helpers used by both the API Server (read queries that
build responses) and external_worker (sync jobs). They only depend on shared
models/schemas, so they live in the shared package to avoid cross-segment imports.
"""
from typing import Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from Database import Airline, Engine, AircraftTemplate, AircraftLesseeLessor
from Schemas import EnginePositionEnum


async def load_references(session: AsyncSession) -> dict:
    return {
        "airlines": {
            a.airline_name: a
            for a in (
                await session.execute(select(Airline))
            ).scalars()
        },
        "engines": {
            e.engine_model: e
            for e in (
                await session.execute(select(Engine))
            ).scalars()
        },
        "templates": {
            t.template_name: t
            for t in (
                await session.execute(select(AircraftTemplate))
            ).scalars()
        },
    }


async def load_lessee_lessors(session: AsyncSession, aircraft_id: Optional[int] = None) -> dict:
    return {
        "lessee_lessors": {
            (l.lessee, l.lessor): l
            for l in (
                await session.execute(
                    select(AircraftLesseeLessor).where(AircraftLesseeLessor.aircraft_id == aircraft_id))
            ).scalars()
        }
    }


def get_engine_positions(number: int) -> Set[EnginePositionEnum]:
    if number == 1:
        return {EnginePositionEnum.NOSE}
    if number == 2:
        return {EnginePositionEnum.LEFT1, EnginePositionEnum.RIGHT1}
    if number == 3:
        return {EnginePositionEnum.LEFT1, EnginePositionEnum.RIGHT1, EnginePositionEnum.TAIL}
    if number == 4:
        return {EnginePositionEnum.LEFT1, EnginePositionEnum.RIGHT1, EnginePositionEnum.LEFT2,
                EnginePositionEnum.RIGHT2}
    raise ValueError(f"Invalid engine position number: {number}")


__all__ = ["load_references", "load_lessee_lessors", "get_engine_positions"]
