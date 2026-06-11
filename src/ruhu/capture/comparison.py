from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def fact_value_equals(actual: Any, expected: Any, *, path: str | None = None) -> bool:
    """Compare normalized fact values while preserving plain equality semantics."""
    if path:
        actual = value_at_path(actual, path)
    if actual == expected:
        return True
    if isinstance(actual, dict) and not isinstance(expected, dict):
        for key in ("amount", "months", "value"):
            if key in actual and _scalar_equals(actual.get(key), expected):
                return True
    return _scalar_equals(actual, expected)


def value_at_path(value: Any, path: str) -> Any:
    current = value
    for raw_part in path.split("."):
        part = raw_part.strip()
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
            continue
        return None
    return current


def _scalar_equals(actual: Any, expected: Any) -> bool:
    actual_decimal = _to_decimal(actual)
    expected_decimal = _to_decimal(expected)
    if actual_decimal is not None and expected_decimal is not None:
        return actual_decimal == expected_decimal
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().casefold() == expected.strip().casefold()
    return False


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
