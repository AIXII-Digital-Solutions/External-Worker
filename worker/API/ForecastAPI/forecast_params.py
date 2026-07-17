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
    # ── История ───────────────────────────────────────────────────────────────────────────────────
    "history_start": {
        "type": "date", "default": "2022-07-01", "group": "История",
        "label": "Начало истории",
        "description": "Факты раньше этой даты не участвуют в прогнозе вообще. Жёсткая отсечка: "
                       "не позволяет затянуть в модель пост-ковидный разгон 2021-2022.",
    },
    # ── Уровень (объём) ───────────────────────────────────────────────────────────────────────────
    "level_window": {
        "type": "int", "default": 15, "min": 3, "max": 36, "group": "Уровень (объём)",
        "label": "Окно уровня, мес",
        "description": "Сколько последних месяцев рассматривается при оценке уровня. Внутри окна остаются "
                       "только ПОЛНЫЕ месяцы (см. «порог полноты»). Окно должно быть заметно длиннее "
                       "отставания загрузки FR24 (сейчас ~5 мес), иначе в нём не останется ни одного полного "
                       "месяца, эталон полноты будет взят из недогруженных, фильтр ослепнет и уровень "
                       "занизится. Замерено бэктестом с симуляцией отставания: 3->22.6%, 6->20.9%, "
                       "9->12.9%, 12->12.9%, 15->12.6%, 18->12.6%, 24->13.5% wMAPE. Обрыв между 6 и 9; "
                       "плато 9-18; 24 уже тянет устаревший режим. 15 стоит по центру плато.",
    },
    "level_complete_frac": {
        "type": "float", "default": 0.85, "min": 0.1, "max": 1.0, "group": "Уровень (объём)",
        "label": "Порог полноты месяца",
        "description": "Месяц окна считается ПОЛНЫМ, если его десезонализированный объём >= этой доли от "
                       "эталона (0.75-квантиль окна). Ниже — месяц считается недогруженным FR24 и в оценку "
                       "уровня не идёт. Понизить = впустить недогруженные месяцы и занизить прогноз.",
    },
    "level_l": {
        "type": "int", "default": 3, "min": 1, "max": 12, "group": "Уровень (объём)",
        "label": "Запасное окно уровня, мес",
        "description": "Используется ТОЛЬКО как аварийный откат: когда истории меньше 3 месяцев или ни один "
                       "месяц окна не прошёл порог полноты. Тогда уровень = медиана последних N месяцев как "
                       "есть, без фильтрации.",
    },
    # ── Сезонность ────────────────────────────────────────────────────────────────────────────────
    "seas_k": {
        "type": "float", "default": 3.0, "min": 0.0, "max": 24.0, "group": "Сезонность",
        "label": "Сглаживание сезонности",
        "description": "Стягивает сезонный коэффициент к 1.0 тем сильнее, чем меньше месяцев его "
                       "подтверждают: вес = n / (n + K). Больше K = ровнее прогноз. Меньше = виднее "
                       "сезонность, но тонкий растущий под-флот начинает принимать свой ранний разгон за "
                       "сезонный провал. 3 — проверенный нижний предел, ниже появляется этот артефакт.",
    },
    # ── Граница полноты данных ────────────────────────────────────────────────────────────────────
    "frontier_frac": {
        "type": "float", "default": 0.6, "min": 0.1, "max": 1.0, "group": "Граница данных",
        "label": "Порог границы покрытия",
        "description": "Месяц считается покрытым, если его объём >= этой доли от медианы окна границы. "
                       "Последний покрытый месяц — граница (frontier): по неё фитится история, с неё+1 "
                       "начинается прогноз.",
    },
    "frontier_window": {
        "type": "int", "default": 9, "min": 3, "max": 36, "group": "Граница данных",
        "label": "Окно границы покрытия, мес",
        "description": "Сколько последних месяцев берётся в медиану, относительно которой меряется порог "
                       "границы покрытия.",
    },
    # ── Структура рейсов ──────────────────────────────────────────────────────────────────────────
    "min_route_pool": {
        "type": "int", "default": 5, "min": 1, "max": 100, "group": "Структура рейсов",
        "label": "Мин. размер маршрутного пула",
        "description": "Если у под-флота в шаблонном месяце меньше стольких различных маршрутов, пул "
                       "считается вырожденным и расширяется каскадом (суб-серия за всю историю -> "
                       "мастер-серия за всю историю). Защищает от «весь месяц один борт летает одним "
                       "маршрутом».",
    },
    # ── Горизонт и метрики ────────────────────────────────────────────────────────────────────────
    "horizon_years": {
        "type": "int", "default": 2, "min": 1, "max": 10, "group": "Горизонт",
        "label": "Горизонт прогноза, лет",
        "description": "Прогноз строится от границы покрытия до (дата запроса + N лет). Последний месяц "
                       "пропорционально урезается по дню запроса — это же задаёт конец контрактного года.",
    },
    "pax_load_factor": {
        "type": "float", "default": 0.8, "min": 0.1, "max": 1.0, "group": "Горизонт",
        "label": "Коэффициент загрузки (PAX)",
        "description": "Total PAX = Total Seats * этот коэффициент. Применяется и к фактам, и к прогнозу.",
    },
}

GROUPS = ["История", "Уровень (объём)", "Сезонность", "Граница данных", "Структура рейсов", "Горизонт"]


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
                raise ValueError(f"{value} — не целое")
            if isinstance(value, bool):
                raise ValueError("ожидалось число")
            return int(value)
        if t == "float":
            if isinstance(value, bool):
                raise ValueError("ожидалось число")
            return float(value)
    except (TypeError, ValueError) as e:
        raise ForecastParamError(f"{name}: не удалось прочитать как {t} — {value!r} ({e})") from e
    raise ForecastParamError(f"{name}: неизвестный тип {t!r} в спецификации")


def resolve(overrides: dict | None, *, model_version: str | None = None) -> dict:
    """Overrides (the stored JSONB) -> the complete, validated, typed parameter set.

    Unknown keys and out-of-range values are hard errors, not warnings: a profile the portal saved wrong
    must fail loudly at save time rather than quietly produce a different forecast. `model_version`, when
    given, must match this module's — a profile written against another parameter set is refused rather
    than reinterpreted.
    """
    if model_version is not None and model_version != MODEL_VERSION:
        raise ForecastParamError(
            f"профиль записан для версии модели {model_version!r}, текущая — {MODEL_VERSION!r}")
    out = defaults()
    for name, raw in (overrides or {}).items():
        if name not in SPEC:
            raise ForecastParamError(f"неизвестный параметр {name!r}; допустимые: {sorted(SPEC)}")
        if raw is None:                      # explicit null == "use the default"
            continue
        val = _coerce(name, raw)
        spec = SPEC[name]
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and val < lo:
            raise ForecastParamError(f"{name}: {val} меньше минимума {lo}")
        if hi is not None and val > hi:
            raise ForecastParamError(f"{name}: {val} больше максимума {hi}")
        out[name] = val
    _cross_check(out)
    return out


def _cross_check(p: dict) -> None:
    """Constraints no single field's min/max can express."""
    if p["level_l"] > p["level_window"]:
        raise ForecastParamError(
            f"level_l ({p['level_l']}) не может быть больше level_window ({p['level_window']}): "
            "запасное окно не бывает шире основного")


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
