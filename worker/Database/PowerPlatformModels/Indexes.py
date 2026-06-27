from sqlalchemy import Index

from .AircraftModels import Engine
from .AirlineModels import Airline

Index(
    "engine_manufacture_trgm_idx",
    Engine.engine_manufacture,
    postgresql_using="gin",
    postgresql_ops={
        "engine_manufacture": "gin_trgm_ops"
    },
)

Index(
    "engine_model_trgm_idx",
    Engine.engine_model,
    postgresql_using="gin",
    postgresql_ops={
        "engine_model": "gin_trgm_ops"
    },
)

Index(
    "airline_name_trgm_idx",
    Airline.airline_name,
    postgresql_using="gin",
    postgresql_ops={
        "airline_name": "gin_trgm_ops"
    },
)

