"""Cursor-based pagination for SQLAlchemy selects (RP-3.3 shared infra).

This module is the standard pagination primitive for NEW list endpoints.
It implements forward-only keyset pagination over a ``(sort_key, id)``
column pair with an opaque base64 cursor:

* **Stable** — ties on ``sort_key`` are broken by the (unique) ``id``
  column, so rows are never skipped or duplicated between pages even when
  many rows share the same sort value.
* **O(page) not O(table)** — the cursor becomes a SQL keyset predicate;
  no load-everything-and-slice-in-memory.
* **Opaque** — clients treat the cursor as a token. The encoding
  (base64 JSON) is an implementation detail and not a contract.

Usage::

    from ruhu.pagination import paginate

    page = paginate(
        session,
        select(ConversationRecord).where(...),
        sort_key=ConversationRecord.updated_at,
        id_key=ConversationRecord.conversation_id,
        cursor=request_cursor,          # None for the first page
        limit=requested_limit,          # clamped to [1, max_limit]
    )
    return SomePageResponse(items=page.items, next_cursor=page.next_cursor)

Existing endpoints that expose ``limit``/``offset`` query parameters and a
bare ``list[...]`` response model cannot adopt cursors without an OpenAPI
schema change; those stay on offset pagination until their schemas are
deliberately revved (tracked as RP-future work in the remediation plan).
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import Session

__all__ = [
    "DEFAULT_MAX_LIMIT",
    "InvalidCursor",
    "Page",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
    "paginate",
]

DEFAULT_MAX_LIMIT = 200

# Cursor payload type tags — how the sort value survives JSON.
_TYPE_DATETIME = "dt"
_TYPE_PLAIN = "raw"


class InvalidCursor(ValueError):
    """Raised when a cursor token is malformed or was not produced here.

    Routes should translate this to a 400 response.
    """


@dataclass(frozen=True)
class Page:
    """One page of results plus the continuation token."""

    items: list[Any]
    next_cursor: str | None
    has_more: bool


def clamp_limit(limit: int, *, max_limit: int = DEFAULT_MAX_LIMIT) -> int:
    """Clamp a requested page size into ``[1, max_limit]``."""
    return max(1, min(int(limit), int(max_limit)))


def encode_cursor(sort_value: Any, id_value: Any) -> str:
    """Encode a ``(sort_value, id_value)`` keyset position as an opaque token."""
    if isinstance(sort_value, datetime):
        payload = [_TYPE_DATETIME, sort_value.isoformat(), id_value]
    else:
        payload = [_TYPE_PLAIN, sort_value, id_value]
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[Any, Any]:
    """Decode an opaque cursor back into ``(sort_value, id_value)``.

    Raises :class:`InvalidCursor` for anything that did not come out of
    :func:`encode_cursor`.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor("malformed pagination cursor") from exc
    if not isinstance(payload, list) or len(payload) != 3:
        raise InvalidCursor("malformed pagination cursor")
    type_tag, sort_value, id_value = payload
    if type_tag == _TYPE_DATETIME:
        if not isinstance(sort_value, str):
            raise InvalidCursor("malformed pagination cursor")
        try:
            sort_value = datetime.fromisoformat(sort_value)
        except ValueError as exc:
            raise InvalidCursor("malformed pagination cursor") from exc
    elif type_tag != _TYPE_PLAIN:
        raise InvalidCursor("malformed pagination cursor")
    return sort_value, id_value


def paginate(
    session: Session,
    statement: Select,
    *,
    sort_key,
    id_key,
    cursor: str | None = None,
    limit: int,
    max_limit: int = DEFAULT_MAX_LIMIT,
    descending: bool = False,
) -> Page:
    """Execute *statement* as one keyset-paginated page.

    ``statement`` must select a single ORM entity (it is executed via
    ``session.scalars``). ``sort_key`` and ``id_key`` are mapped columns of
    that entity; ``id_key`` must be unique so the ``(sort_key, id_key)``
    pair totally orders the result set.

    Forward-only: each returned :class:`Page` carries ``next_cursor`` for
    the following page (or ``None`` at the end of the result set).
    """
    limit = clamp_limit(limit, max_limit=max_limit)

    if cursor is not None:
        sort_value, id_value = decode_cursor(cursor)
        if descending:
            keyset = or_(
                sort_key < sort_value,
                and_(sort_key == sort_value, id_key < id_value),
            )
        else:
            keyset = or_(
                sort_key > sort_value,
                and_(sort_key == sort_value, id_key > id_value),
            )
        statement = statement.where(keyset)

    if descending:
        statement = statement.order_by(sort_key.desc(), id_key.desc())
    else:
        statement = statement.order_by(sort_key.asc(), id_key.asc())

    rows: Sequence[Any] = session.scalars(statement.limit(limit + 1)).all()
    has_more = len(rows) > limit
    items = list(rows[:limit])

    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            getattr(last, sort_key.key),
            getattr(last, id_key.key),
        )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)
