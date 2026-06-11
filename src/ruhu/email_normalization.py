from __future__ import annotations


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_optional_email(value: str | None) -> str | None:
    if value is None:
        return None
    return normalize_email(value)
