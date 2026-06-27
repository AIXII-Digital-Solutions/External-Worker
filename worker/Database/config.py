import inspect
import sys
from datetime import datetime

import inflect
from sqlalchemy import func, BigInteger
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
        return inflect.engine().plural(cls.__name__.lower())


# Base class for others models in Main DB
class MainBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    pass


# Base class for others models in Service DB
class ServiceBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    pass


# Base class for others models in Cirium DB
class CiriumBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    pass


# Base class for others models in Airlabs DB
class AirlabsBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    pass


# Base class for others models in FlightRadar DB
class FlightRadarBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    pass


class AviationEdgeBase(AsyncAttrs, BaseMixin, DeclarativeBase):
    pass


# NOTE: the portal database is owned by the separate portal service (its own schema +
# alembic); it is intentionally NOT part of this db-contract.


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
