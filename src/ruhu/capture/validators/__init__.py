from __future__ import annotations

from .base import ValidatorRegistry
from .builtins import (
    AddressValidator,
    BooleanValidator,
    ConsentValidator,
    CycleValidator,
    DateTimeValidator,
    DurationValidator,
    EmailValidator,
    EnumValidator,
    IdValidator,
    MoneyValidator,
    NameValidator,
    PhoneValidator,
)


def build_default_validator_registry() -> ValidatorRegistry:
    registry = ValidatorRegistry()
    for validator in (
        EmailValidator(),
        PhoneValidator(),
        MoneyValidator(),
        DurationValidator(),
        CycleValidator(),
        BooleanValidator(),
        ConsentValidator(),
        DateTimeValidator(),
        NameValidator(),
        AddressValidator(),
        EnumValidator(),
        IdValidator(),
    ):
        registry.register(validator)
    return registry


__all__ = ["ValidatorRegistry", "build_default_validator_registry"]
