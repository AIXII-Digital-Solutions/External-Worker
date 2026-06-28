import inspect
import sys
from datetime import datetime

from sqlalchemy import func, BigInteger, MetaData, DateTime
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, declared_attr, Mapped, mapped_column


class BaseMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now()
    )

    @declared_attr.directive
    def __tablename__(cls) -> str:
        # Table name = the class name as-is (lowercased), NO pluralization:
        # `Airlines` -> `airlines`, `CiriumAircrafts` -> `ciriumaircrafts`. Models that need a
        # different physical name still override with an explicit `__tablename__`.
        return cls.__name__.lower()


class BaseMixinTz(BaseMixin):
    """BaseMixin with timezone-aware created_at/updated_at (timestamptz). Used by the service
    tables, whose writers pass tz-aware UTC datetimes (e.g. external-worker's publish_status) —
    consistent with finished_at / next_run_at / expires_at, which are already timezone=True. The
    aviation (aixii) tables keep plain BaseMixin: their timestamps are set by server defaults, so
    they never receive a tz-aware Python value and don't need the (huge) column rewrite."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# AIXII consolidation: the aviation domains now live as SCHEMAS inside ONE physical database
# (`aixii`); each Base carries a schema-scoped MetaData so its tables emit `<schema>.<table>`.
# `service` is a SEPARATE physical database (schema-less / public). `main` (core) is being
# rewritten — kept schema-less and intentionally NOT migrated yet (see migration/env.py).


# Base class for Main/core models (core rewrite pending — no schema, not migrated yet)
class MainBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    pass


# Base class for Service-DB models (separate `service` database, public schema)
class ServiceBase(AsyncAttrs, BaseMixinTz, DeclarativeBase):
    pass


# Base class for Cirium models -> schema `cirium` in the aixii database
class CiriumBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    metadata = MetaData(schema="cirium")


# Base class for Airlabs models -> schema `airlabs`
class AirlabsBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    metadata = MetaData(schema="airlabs")


# Base class for FlightRadar models -> schema `flightradar`
class FlightRadarBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    metadata = MetaData(schema="flightradar")


# Base class for Aviation Edge models -> schema `aviationedge`
class AviationEdgeBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    metadata = MetaData(schema="aviationedge")


# Base for cirium MATERIALIZED VIEWS (read-only): cirium.asg / cirium.delta. No BaseMixin (a view
# has no id/created_at/updated_at); each model declares its own logical primary key. Owned by
# core-api's Alembic — this worker only reads/refreshes them.
class CiriumViewBase(AsyncAttrs, DeclarativeBase):
    metadata = MetaData(schema="cirium")


# NOTE: the portal database is owned by the separate portal service (its own schema +
# alembic); it is intentionally NOT part of this db-contract.


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
