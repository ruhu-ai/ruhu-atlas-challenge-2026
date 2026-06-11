"""Unit tests for ruhu.pagination — cursor keyset pagination (RP-3.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from ruhu.pagination import (
    DEFAULT_MAX_LIMIT,
    InvalidCursor,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    paginate,
)


class _Base(DeclarativeBase):
    pass


class _Item(_Base):
    __tablename__ = "pagination_test_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    rank: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _seed(session: Session, count: int = 10) -> list[_Item]:
    base = datetime(2026, 6, 11, 12, 0, 0)
    items = [
        _Item(
            id=f"item-{index:03d}",
            # Duplicate ranks (pairs) so tie-breaking by id is exercised.
            rank=index // 2,
            created_at=base + timedelta(minutes=index),
        )
        for index in range(count)
    ]
    session.add_all(items)
    session.commit()
    return items


# ── cursor encoding ────────────────────────────────────────────────────────────


def test_cursor_roundtrip_plain_values() -> None:
    cursor = encode_cursor(42, "item-001")
    assert decode_cursor(cursor) == (42, "item-001")


def test_cursor_roundtrip_datetime() -> None:
    moment = datetime(2026, 6, 11, 9, 30, 15, tzinfo=timezone.utc)
    cursor = encode_cursor(moment, "item-002")
    assert decode_cursor(cursor) == (moment, "item-002")


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "not-base64!!!",
        "aGVsbG8=",  # base64("hello") — not JSON
        encode_cursor(1, "x")[:-4],  # truncated token
    ],
)
def test_decode_cursor_rejects_garbage(garbage: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(garbage)


def test_decode_cursor_rejects_wrong_shape() -> None:
    import base64
    import json

    token = base64.urlsafe_b64encode(json.dumps({"a": 1}).encode()).decode()
    with pytest.raises(InvalidCursor):
        decode_cursor(token)
    token = base64.urlsafe_b64encode(json.dumps(["??", 1, "x"]).encode()).decode()
    with pytest.raises(InvalidCursor):
        decode_cursor(token)


# ── limit clamping ─────────────────────────────────────────────────────────────


def test_clamp_limit_bounds() -> None:
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(50) == 50
    assert clamp_limit(10_000) == DEFAULT_MAX_LIMIT
    assert clamp_limit(10_000, max_limit=25) == 25


def test_paginate_clamps_oversized_limit(session: Session) -> None:
    _seed(session, count=6)
    page = paginate(
        session,
        select(_Item),
        sort_key=_Item.created_at,
        id_key=_Item.id,
        limit=9_999,
        max_limit=4,
    )
    assert len(page.items) == 4
    assert page.has_more is True


# ── forward iteration ──────────────────────────────────────────────────────────


def test_paginate_walks_all_rows_exactly_once(session: Session) -> None:
    seeded = _seed(session, count=10)
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        page = paginate(
            session,
            select(_Item),
            sort_key=_Item.created_at,
            id_key=_Item.id,
            cursor=cursor,
            limit=3,
        )
        seen.extend(item.id for item in page.items)
        pages += 1
        if page.next_cursor is None:
            assert page.has_more is False
            break
        cursor = page.next_cursor
    assert pages == 4  # 3 + 3 + 3 + 1
    assert seen == [item.id for item in seeded]
    assert len(seen) == len(set(seen))


def test_paginate_duplicate_sort_keys_never_skip_or_repeat(session: Session) -> None:
    # rank is duplicated in pairs; page size 1 forces a tie-break on every page.
    seeded = _seed(session, count=8)
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = paginate(
            session,
            select(_Item),
            sort_key=_Item.rank,
            id_key=_Item.id,
            cursor=cursor,
            limit=1,
        )
        seen.extend(item.id for item in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert seen == sorted(item.id for item in seeded)


def test_paginate_descending_order(session: Session) -> None:
    seeded = _seed(session, count=5)
    first = paginate(
        session,
        select(_Item),
        sort_key=_Item.created_at,
        id_key=_Item.id,
        limit=2,
        descending=True,
    )
    assert [item.id for item in first.items] == [seeded[-1].id, seeded[-2].id]
    second = paginate(
        session,
        select(_Item),
        sort_key=_Item.created_at,
        id_key=_Item.id,
        cursor=first.next_cursor,
        limit=2,
        descending=True,
    )
    assert [item.id for item in second.items] == [seeded[-3].id, seeded[-4].id]


def test_paginate_respects_statement_filters(session: Session) -> None:
    _seed(session, count=10)
    page = paginate(
        session,
        select(_Item).where(_Item.rank >= 3),
        sort_key=_Item.created_at,
        id_key=_Item.id,
        limit=50,
    )
    assert [item.rank >= 3 for item in page.items] == [True] * len(page.items)
    assert len(page.items) == 4  # ranks 3 and 4, two items each
    assert page.next_cursor is None
    assert page.has_more is False


def test_paginate_empty_result(session: Session) -> None:
    page = paginate(
        session,
        select(_Item),
        sort_key=_Item.created_at,
        id_key=_Item.id,
        limit=10,
    )
    assert page.items == []
    assert page.next_cursor is None
    assert page.has_more is False


def test_paginate_exact_page_boundary_has_no_next_cursor(session: Session) -> None:
    _seed(session, count=4)
    page = paginate(
        session,
        select(_Item),
        sort_key=_Item.created_at,
        id_key=_Item.id,
        limit=4,
    )
    assert len(page.items) == 4
    assert page.next_cursor is None
    assert page.has_more is False


def test_paginate_invalid_cursor_raises(session: Session) -> None:
    _seed(session, count=2)
    with pytest.raises(InvalidCursor):
        paginate(
            session,
            select(_Item),
            sort_key=_Item.created_at,
            id_key=_Item.id,
            cursor="garbage-token",
            limit=2,
        )
