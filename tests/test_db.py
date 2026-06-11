from __future__ import annotations

import pytest

from ruhu.db import resolve_database_url


@pytest.mark.parametrize(
    ("raw_url", "expected_url"),
    [
        (
            "postgres://postgres:postgres@localhost:5432/ruhu_runtime_dev",
            "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev",
        ),
        (
            "postgresql://postgres:postgres@localhost:5432/ruhu_runtime_dev",
            "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev",
        ),
        (
            "postgresql+psycopg2://postgres:postgres@localhost:5432/ruhu_runtime_dev",
            "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev",
        ),
        (
            "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev",
            "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev",
        ),
    ],
)
def test_resolve_database_url_normalizes_legacy_postgres_driver_variants(
    raw_url: str,
    expected_url: str,
) -> None:
    assert resolve_database_url(database_url=raw_url) == expected_url


def test_resolve_database_url_rejects_blank_values() -> None:
    with pytest.raises(ValueError, match="database_url is required"):
        resolve_database_url(database_url="   ")
