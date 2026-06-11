from __future__ import annotations

from typing import Any, Protocol

from ruhu.capture.types import ValidationResult
from ruhu.schemas import FactDef


class Validator(Protocol):
    fact_type: str
    is_exact: bool

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult: ...


class PassthroughValidator:
    fact_type = "__passthrough__"
    is_exact = False

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        return ValidationResult(status="passed", normalized_value=raw)


class ValidatorRegistry:
    def __init__(self) -> None:
        self._validators: dict[str, Validator] = {}
        self._passthrough = PassthroughValidator()

    def register(self, validator: Validator, *, replace: bool = False) -> None:
        key = validator.fact_type.lower()
        if key in self._validators and not replace:
            raise ValueError(f"validator already registered for fact_type={key!r}")
        self._validators[key] = validator

    def get(self, fact_type: str) -> Validator:
        return self._validators.get((fact_type or "").lower(), self._passthrough)


def failed(reason: str) -> ValidationResult:
    return ValidationResult(status="failed", normalized_value=None, reason=reason)


def passed(value: Any) -> ValidationResult:
    return ValidationResult(status="passed", normalized_value=value)
