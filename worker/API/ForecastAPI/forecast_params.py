"""The forecast model's tunable parameters — the SPEC, and the resolver that turns a stored override
dict into a complete, validated parameter set.

WHY THIS FILE EXISTS, AND WHY IT IS COPIED
------------------------------------------
`service.forecast_profiles.params` is a JSONB of OVERRIDES ONLY — an absent key means "use the default".
That shape is what keeps a new parameter from needing a backfill of every existing profile, but it also
means the defaults and the validation rules have to live SOMEWHERE, and both core-api (which validates on
WRITE, and serves this spec to the portal so it can render the settings form) and external-worker (which
validates on READ and actually applies the values) need them.

So this module is the source of truth and is COPIED, exactly like the ORM models are (see CLAUDE.md — the
platform has no shared package; a schema change is a manual 3-step copy). Copies live at:
    * Core-API/app/Utils/forecast_params.py
    * External-Worker/worker/API/ForecastAPI/params.py
It is deliberately DEPENDENCY-FREE (stdlib only, no SQLAlchemy/pydantic) so copying it is mechanical and a
drift shows up as a plain diff.

VERSIONING
----------
`MODEL_VERSION` names the parameter set, and `forecast_profiles.model_version` records which set a row was
written against. A future model with a different set bumps this; `resolve()` refuses a profile written for a
version it does not know, so a stale profile can never be silently reinterpreted against new semantics.

ADDING A PARAMETER
------------------
Add an entry to SPEC with a default equal to the CURRENT hardcoded behaviour, and make the model read it.
Existing profiles keep behaving identically (absent key -> default). No migration, no portal change.
"""
from datetime import date

MODEL_VERSION = "acys-v1"

# type: "int" | "float" | "date". min/max are INCLUSIVE and enforced by resolve().
# label/description are what the portal shows; group orders the form into sections.
SPEC: dict[str, dict] = {
    # ── History ─────────────────────────────────────────────────────────────────────────────────────
    "history_start": {
        "type": "date", "default": "2022-07-01", "group": "History",
        "label": "History start",
        "description": "Facts before this date do not take part in the forecast at all. A hard cutoff: it "
                       "keeps the post-COVID 2021-2022 ramp-up from being pulled into the model.",
    },
    # ── Level (volume) ──────────────────────────────────────────────────────────────────────────────
    "level_window": {
        "type": "int", "default": 15, "min": 3, "max": 36, "group": "Level (volume)",
        "label": "Level window, months",
        "description": "How many recent months are considered when estimating the level. Only COMPLETE "
                       "months inside the window are kept (see 'month completeness threshold'). The window "
                       "must be clearly longer than the FR24 ingestion lag (~5 months now), otherwise it "
                       "holds no complete month, the completeness reference is taken from undercounted "
                       "months, the filter goes blind and the level is understated. Measured by a backtest "
                       "simulating the lag: 3->22.6%, 6->20.9%, 9->12.9%, 12->12.9%, 15->12.6%, 18->12.6%, "
                       "24->13.5% wMAPE. Cliff between 6 and 9; plateau 9-18; 24 starts pulling in the stale "
                       "regime. 15 sits at the centre of the plateau.",
    },
    "level_complete_frac": {
        "type": "float", "default": 0.85, "min": 0.1, "max": 1.0, "group": "Level (volume)",
        "label": "Month completeness threshold",
        "description": "A window month counts as COMPLETE if its deseasonalized volume >= this fraction of "
                       "the reference (the window's 0.75-quantile). Below it, the month is treated as "
                       "FR24-undercounted and excluded from the level. Lowering it = admit undercounted "
                       "months and understate the forecast.",
    },
    "level_l": {
        "type": "int", "default": 3, "min": 1, "max": 12, "group": "Level (volume)",
        "label": "Fallback level window, months",
        "description": "Used ONLY as an emergency fallback: when there are fewer than 3 months of history, "
                       "or no window month passed the completeness threshold. Then the level = median of the "
                       "last N months as-is, unfiltered.",
    },
    # ── Seasonality ─────────────────────────────────────────────────────────────────────────────────
    "seas_k": {
        "type": "float", "default": 3.0, "min": 0.0, "max": 24.0, "group": "Seasonality",
        "label": "Seasonality smoothing",
        "description": "Shrinks the seasonal factor toward 1.0 the more strongly the fewer months support "
                       "it: weight = n / (n + K). Larger K = flatter forecast. Smaller = more visible "
                       "seasonality, but a thin growing sub-fleet starts mistaking its early ramp-up for a "
                       "seasonal trough. 3 is the proven lower bound; below it this artefact appears.",
    },
    # ── Data frontier ───────────────────────────────────────────────────────────────────────────────
    "frontier_frac": {
        "type": "float", "default": 0.6, "min": 0.1, "max": 1.0, "group": "Data frontier",
        "label": "Coverage frontier threshold",
        "description": "A month counts as covered if its volume >= this fraction of the frontier-window "
                       "median. The last covered month is the frontier: history is fit up to it, and the "
                       "forecast starts at frontier+1.",
    },
    "frontier_window": {
        "type": "int", "default": 9, "min": 3, "max": 36, "group": "Data frontier",
        "label": "Coverage frontier window, months",
        "description": "How many recent months go into the median against which the coverage frontier "
                       "threshold is measured.",
    },
    # ── Route structure ─────────────────────────────────────────────────────────────────────────────
    "min_route_pool": {
        "type": "int", "default": 5, "min": 1, "max": 100, "group": "Route structure",
        "label": "Min. route-pool size",
        "description": "If a sub-fleet's template month holds fewer than this many distinct routes, the pool "
                       "is treated as degenerate and broadened by a cascade (sub-series all-history -> "
                       "master-series all-history). Guards against 'one aircraft flying one route the whole "
                       "month'.",
    },
    # ── Horizon ─────────────────────────────────────────────────────────────────────────────────────
    "horizon_years": {
        "type": "int", "default": 2, "min": 1, "max": 10, "group": "Horizon",
        "label": "Forecast horizon, years",
        "description": "The forecast runs from the coverage frontier to (request date + N years). The final "
                       "month is prorated by the request day — which also sets the end of the contract year.",
    },
    "pax_load_factor": {
        "type": "float", "default": 0.8, "min": 0.1, "max": 1.0, "group": "Horizon",
        "label": "PAX load factor",
        "description": "Total PAX = Total Seats * this factor. Applied to both actuals and forecast.",
    },
}

GROUPS = ["History", "Level (volume)", "Seasonality", "Data frontier", "Route structure", "Horizon"]


class ForecastParamError(ValueError):
    """A stored/submitted override set that cannot be applied. The message is caller-facing."""


def defaults() -> dict:
    """The complete parameter set with no overrides applied."""
    return {k: _coerce(k, v["default"]) for k, v in SPEC.items()}


def _coerce(name: str, value):
    """One value -> its declared python type, or raise. Accepts the JSON forms (a date arrives as a str)."""
    spec = SPEC[name]
    t = spec["type"]
    try:
        if t == "date":
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        if t == "int":
            # reject a float that is not integral rather than silently truncating a portal typo
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"{value} is not an integer")
            if isinstance(value, bool):
                raise ValueError("expected a number")
            return int(value)
        if t == "float":
            if isinstance(value, bool):
                raise ValueError("expected a number")
            return float(value)
    except (TypeError, ValueError) as e:
        raise ForecastParamError(f"{name}: could not read as {t} — {value!r} ({e})") from e
    raise ForecastParamError(f"{name}: unknown type {t!r} in the spec")


def resolve(overrides: dict | None, *, model_version: str | None = None) -> dict:
    """Overrides (the stored JSONB) -> the complete, validated, typed parameter set.

    Unknown keys and out-of-range values are hard errors, not warnings: a profile the portal saved wrong
    must fail loudly at save time rather than quietly produce a different forecast. `model_version`, when
    given, must match this module's — a profile written against another parameter set is refused rather
    than reinterpreted.
    """
    if model_version is not None and model_version != MODEL_VERSION:
        raise ForecastParamError(
            f"profile was written for model version {model_version!r}, current is {MODEL_VERSION!r}")
    out = defaults()
    for name, raw in (overrides or {}).items():
        if name not in SPEC:
            raise ForecastParamError(f"unknown parameter {name!r}; allowed: {sorted(SPEC)}")
        if raw is None:                      # explicit null == "use the default"
            continue
        val = _coerce(name, raw)
        spec = SPEC[name]
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and val < lo:
            raise ForecastParamError(f"{name}: {val} is below the minimum {lo}")
        if hi is not None and val > hi:
            raise ForecastParamError(f"{name}: {val} is above the maximum {hi}")
        out[name] = val
    _cross_check(out)
    return out


def _cross_check(p: dict) -> None:
    """Constraints no single field's min/max can express."""
    if p["level_l"] > p["level_window"]:
        raise ForecastParamError(
            f"level_l ({p['level_l']}) cannot exceed level_window ({p['level_window']}): "
            "the fallback window is never wider than the main one")


def describe() -> dict:
    """The form descriptor the portal renders — spec + defaults, JSON-safe. Served by core-api."""
    return {
        "model_version": MODEL_VERSION,
        "groups": GROUPS,
        "params": [
            {"name": name, "type": s["type"], "default": s["default"], "group": s["group"],
             "label": s["label"], "description": s["description"],
             **({"min": s["min"]} if "min" in s else {}),
             **({"max": s["max"]} if "max" in s else {})}
            for name, s in SPEC.items()
        ],
    }
