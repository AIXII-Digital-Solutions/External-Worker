"""Loading the forecast model's parameters at RUNTIME — the profile in `service.forecast_profiles`, resolved
against the spec, frozen into the object the model reads.

Split from `forecast_params.py` on purpose: that module is the dependency-free spec, byte-identical across
core-api / external-worker / db-contract, so drift is a plain diff. Everything that needs SQLAlchemy or this
worker's config lives HERE and is not copied anywhere.
"""
from dataclasses import dataclass, fields
from datetime import date

from sqlalchemy import text

from Config import setup_logger

from .forecast_params import MODEL_VERSION, SPEC, ForecastParamError, defaults, resolve

logger = setup_logger("forecast_params")


@dataclass(frozen=True)
class ForecastParams:
    """One resolved, validated parameter set. Frozen: a run must not retune itself halfway through.

    Field names mirror SPEC's keys exactly (asserted at import), so `ForecastParams(**resolve(...))` needs no
    mapping table and a knob added to the spec fails loudly here instead of being silently ignored.
    """
    history_start: date
    level_window: int
    level_complete_frac: float
    level_l: int
    seas_k: float
    frontier_frac: float
    frontier_window: int
    min_route_pool: int
    horizon_years: int
    pax_load_factor: float

    @classmethod
    def from_overrides(cls, overrides: dict | None, *, model_version: str | None = None) -> "ForecastParams":
        return cls(**resolve(overrides, model_version=model_version))

    @classmethod
    def defaults(cls) -> "ForecastParams":
        return cls(**defaults())

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# The spec and the dataclass must not drift apart — a knob present in one and not the other is a silent
# behaviour bug (a portal setting that changes nothing, or a crash at first load). Fail at import instead.
_SPEC_KEYS, _FIELD_NAMES = set(SPEC), {f.name for f in fields(ForecastParams)}
if _SPEC_KEYS != _FIELD_NAMES:
    raise RuntimeError(
        f"ForecastParams расходится с SPEC: только в спеке {_SPEC_KEYS - _FIELD_NAMES}, "
        f"только в датаклассе {_FIELD_NAMES - _SPEC_KEYS}")

_SELECT = ("SELECT name, model_version, params FROM forecast_profiles "
           "WHERE enabled AND {where} LIMIT 1")


async def load_params(db_client, *, profile: str | None = None) -> tuple[ForecastParams, str]:
    """Read the forecast profile from the SERVICE database and resolve it. Returns (params, source label).

    Failure handling is deliberately asymmetric, because the two failures mean different things:

    * The table is MISSING, or holds no usable row -> fall back to the defaults with a warning. This is the
      deployment window: the worker may well start before core-api's migration has landed, and the defaults
      are exactly the behaviour it had before profiles existed, so nothing changes.
    * A row EXISTS but does not resolve (bad value, unknown key, wrong model_version), or a NAMED profile was
      requested and is not there -> RAISE. Someone configured something specific; quietly forecasting with
      different numbers than the portal shows would be worse than a failed run.
    """
    where = "name = :n" if profile else "is_default"
    try:
        async with db_client.session("service") as s:
            row = (await s.execute(text(_SELECT.format(where=where)),
                                   {"n": profile} if profile else {})).first()
    except Exception as e:
        if profile:
            raise
        logger.warning("forecast_profiles недоступна (%s) — беру параметры по умолчанию", e)
        return ForecastParams.defaults(), "defaults (профиль недоступен)"

    if row is None:
        if profile:
            raise ForecastParamError(f"профиль прогноза {profile!r} не найден или выключен")
        logger.warning("нет профиля по умолчанию в forecast_profiles — беру параметры по умолчанию")
        return ForecastParams.defaults(), "defaults (профиль по умолчанию отсутствует)"

    name, version, overrides = row[0], row[1], row[2] or {}
    p = ForecastParams.from_overrides(overrides, model_version=version)
    if overrides:
        logger.info("профиль прогноза %r (%s): переопределения %s", name, version, overrides)
    return p, name


__all__ = ["ForecastParams", "load_params", "MODEL_VERSION", "ForecastParamError"]
