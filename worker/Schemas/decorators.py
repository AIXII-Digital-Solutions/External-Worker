from typing import Callable
from pydantic import model_validator


# Only one field of many
def exactly_one_of(*field_names: str) -> Callable:
    def decorator(cls):
        @model_validator(mode="after")
        def _validate_exactly_one(self):
            values = [getattr(self, f) for f in field_names]
            provided = sum(v is not None for v in values)

            if provided != 1:
                fields_str = ", ".join(f"'{f}'" for f in field_names)
                raise ValueError(f"Exactly one of {fields_str} must be provided")

            return self

        setattr(cls, f"_validate_exactly_one_{'_'.join(field_names)}", _validate_exactly_one)
        return cls

    return decorator


# > 0 field(-s) of many
def at_least_one_of(*field_names: str):
    def decorator(cls):
        @model_validator(mode="after")
        def _validate(self):
            if not any(getattr(self, f) is not None for f in field_names):
                raise ValueError(f"At least one of {field_names} must be provided")
            return self

        setattr(cls, f"_validate_at_least_one_{'_'.join(field_names)}", _validate)
        return cls
    return decorator


# 0 or 1 of field(-s) of many
def at_most_one_of(*field_names: str) -> Callable:
    def decorator(cls):
        @model_validator(mode="after")
        def _validate_at_most_one(self):
            values = [getattr(self, f) for f in field_names]
            provided = sum(v is not None for v in values)

            if provided > 1:
                fields_str = ", ".join(f"'{f}'" for f in field_names)
                raise ValueError(f"At most one of {fields_str} can be provided")

            return self

        setattr(
            cls,
            f"_validate_at_most_one_{'_'.join(field_names)}",
            _validate_at_most_one,
        )
        return cls

    return decorator


def require_fields(*field_names: str, allow_empty: bool = False) -> Callable:
    def decorator(cls):
        @model_validator(mode="after")
        def _validate_required_fields(self):
            for field in field_names:
                value = getattr(self, field, None)

                if value is None:
                    raise ValueError(f"Field '{field}' is required")

                if not allow_empty:
                    if isinstance(value, (str, list, dict, set, tuple)) and not value:
                        raise ValueError(f"Field '{field}' must not be empty")

            return self

        setattr(
            cls,
            f"_validate_required_{'_'.join(field_names)}",
            _validate_required_fields,
        )
        return cls

    return decorator
