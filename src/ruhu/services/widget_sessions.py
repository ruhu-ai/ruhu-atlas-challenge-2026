"""Public-widget session token + access verification — extracted from api.py
(RP-3.1 step 13, blueprint groups 17–18 prerequisite).

Owns the widget session bearer-token lifecycle helpers (issue/hash/extract,
plus the metadata key the kernel conversation stores the hash under) and
``WidgetSessionAccessService.require_public_widget_session_access`` — the
session-token check every ``/public/widget/sessions/{conversation_id}/*``
route runs before touching a conversation. The access check hits the DB
(``WidgetSessionRecord``: origin binding + token expiry), so the service
takes ``auth_session_factory`` explicitly; everything else here is pure.

Origin helpers (``extract_request_origin`` / ``validate_widget_origin``)
live here too: origin binding is part of the same widget-session trust
model, and the access check shares ``extract_request_origin`` with the
session-create route's allowed-origins enforcement.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

    from ..schemas import ConversationState

__all__ = [
    "WIDGET_SESSION_TOKEN_HEADER",
    "WIDGET_SESSION_TOKEN_METADATA_KEY",
    "WIDGET_SESSION_TOKEN_QUERY_PARAM",
    "WidgetSessionAccessService",
    "extract_request_origin",
    "extract_widget_session_token_from_request",
    "hash_widget_session_token",
    "issue_widget_session_token",
    "validate_widget_origin",
]

WIDGET_SESSION_TOKEN_HEADER = "X-Ruhu-Widget-Session-Token"
WIDGET_SESSION_TOKEN_QUERY_PARAM = "session_token"
WIDGET_SESSION_TOKEN_METADATA_KEY = "public_widget_session_token_sha256"


def issue_widget_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_widget_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_widget_session_token_from_request(
    request: Request,
    *,
    explicit_token: str | None = None,
) -> str | None:
    if explicit_token is not None and explicit_token.strip():
        return explicit_token.strip()
    header_token = request.headers.get(WIDGET_SESSION_TOKEN_HEADER)
    if header_token is not None and header_token.strip():
        return header_token.strip()
    query_token = request.query_params.get(WIDGET_SESSION_TOKEN_QUERY_PARAM)
    if query_token is not None and query_token.strip():
        return query_token.strip()
    return None


def extract_request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if isinstance(origin, str) and origin.strip():
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if not isinstance(referer, str) or not referer.strip():
        return None
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def validate_widget_origin(request: Request, allowed_origins: list) -> None:
    """Enforce allowed-origins on a publishable-key-authenticated widget request.

    Rules:
    - empty list → all origins permitted (permissive / dev mode)
    - non-empty list → request origin must exactly match one entry
    - request origin is resolved from Origin first, then Referer
    - if no origin evidence is present, fail closed
    """
    if not allowed_origins:
        return
    normalized_allowed_origins = {str(item).rstrip("/") for item in allowed_origins if item}
    origin = extract_request_origin(request)
    if origin is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "origin_required",
                "message": "an allowed-origin widget request must include Origin or Referer",
            },
        )
    if origin not in normalized_allowed_origins:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "origin_not_allowed",
                "message": f"origin {origin!r} is not on the allowed-origins list for this publishable key",
            },
        )


@dataclass(frozen=True)
class WidgetSessionAccessService:
    """Verify widget session tokens against the conversation + session row."""

    auth_session_factory: "sessionmaker"

    def require_public_widget_session_access(
        self,
        request: Request,
        conversation: "ConversationState",
        *,
        explicit_token: str | None = None,
        allow_expired: bool = False,
    ) -> str:
        """Verify a widget session token and return it.

        ``allow_expired=True`` skips the ``token_expires_at`` check but
        still enforces the token-hash match.  Only the rotation endpoint
        (``/token/refresh``) should set this — otherwise an expired
        session is a dead end and clients can't recover without starting
        over.
        """
        if conversation.channel != "web_widget":
            raise HTTPException(status_code=404, detail="unknown conversation id")
        stored_hash = conversation.metadata.get(WIDGET_SESSION_TOKEN_METADATA_KEY)
        if not isinstance(stored_hash, str) or not stored_hash.strip():
            raise HTTPException(status_code=404, detail="unknown conversation id")
        presented_token = extract_widget_session_token_from_request(
            request,
            explicit_token=explicit_token,
        )
        if presented_token is None:
            raise HTTPException(status_code=401, detail="widget session token required")
        if not hmac.compare_digest(hash_widget_session_token(presented_token), stored_hash):
            raise HTTPException(status_code=404, detail="unknown conversation id")
        # ── Token expiry check (Phase 6) ──────────────────────────────────────
        # Check token_expires_at in WidgetSessionRecord when it exists.
        # NULL expiry means the row does not expire.
        # If expired, 401 so the client calls /token/refresh.  The refresh
        # endpoint itself passes allow_expired=True so rotation works even
        # after the old token crosses its 24h TTL.
        try:
            from sqlalchemy import select as _sa_select

            from ..db_models import WidgetSessionRecord
            with self.auth_session_factory() as _es:
                ws = _es.scalar(
                    _sa_select(WidgetSessionRecord).where(
                        WidgetSessionRecord.conversation_id == conversation.conversation_id
                    )
                )
                if ws is not None and isinstance(ws.origin, str) and ws.origin.strip():
                    request_origin = extract_request_origin(request)
                    expected_origin = ws.origin.rstrip("/")
                    if request_origin is None:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": "origin_required",
                                "message": "widget session is origin-bound and requires Origin or Referer",
                            },
                        )
                    if request_origin != expected_origin:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": "origin_mismatch",
                                "message": "widget session origin does not match the origin that created it",
                            },
                        )
                if not allow_expired and ws is not None and ws.token_expires_at is not None:
                    if ws.token_expires_at <= datetime.now(timezone.utc):
                        raise HTTPException(
                            status_code=401,
                            detail={"error": "token_expired", "message": "widget session token has expired; call /token/refresh"},
                        )
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            pass  # never break the auth check over a DB read failure
        return presented_token
