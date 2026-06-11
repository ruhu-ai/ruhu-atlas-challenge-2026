from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from .models import NotificationCreate, _utc_now
from .store import NotificationStore

if TYPE_CHECKING:
    from .fanout import NotificationFanoutDispatcher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default expiry policy
# ---------------------------------------------------------------------------

_EXPIRY_HOURS_BY_CATEGORY_PREFIX: list[tuple[str, int | None]] = [
    # Permanent failures / audit — longest retention or never
    ("billing.", None),
    ("rules.enforcement_triggered", None),
    ("provider.whatsapp_delivery_exhausted", 168),
    ("provider.livekit_session_error", 168),
    ("knowledge.document_index_failed", 168),
    # Auth — long audit trail
    ("auth.", 720),
    # Completion signals
    ("eval.", 72),
    ("agent.", 72),
    ("knowledge.document_indexed", 24),
    ("knowledge.document_index_retrying", 48),
    ("provider.whatsapp_delivery_failed", 48),
    # Default
]

_EXPIRY_HOURS_DEFAULT = 72
_EXPIRY_HOURS_FYI_OVERRIDE = 24  # fyi urgency caps to 24h if category default is higher


def _resolve_expiry_hours(
    category: str,
    urgency: str,
    expires_after_hours: int | None,
) -> int | None:
    """Determine expiry hours from spec, with category and urgency defaults."""
    if expires_after_hours is not None:
        return expires_after_hours

    category_hours: int | None = _EXPIRY_HOURS_DEFAULT
    for prefix, hours in _EXPIRY_HOURS_BY_CATEGORY_PREFIX:
        if category.startswith(prefix) or category == prefix.rstrip("."):
            category_hours = hours
            break

    if category_hours is None:
        return None  # never expires

    # fyi urgency: cap to 24h if the category default is higher
    if urgency == "fyi":
        return min(category_hours, _EXPIRY_HOURS_FYI_OVERRIDE)

    return category_hours


# ---------------------------------------------------------------------------
# Emission helper — the only creation path for notifications
# ---------------------------------------------------------------------------

def emit_notification(
    store: NotificationStore,
    *,
    organization_id: str,
    category: str,
    title: str,
    level: str = "info",
    urgency: str = "fyi",
    user_id: str | None = None,
    message: str | None = None,
    url: str | None = None,
    url_label: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    payload: dict[str, object] | None = None,
    expires_after_hours: int | None = None,
    fanout: "NotificationFanoutDispatcher | None" = None,
) -> None:
    """
    Emit a notification. This is the only creation path.

    Does not raise on failure — logs the error and continues. Notifications
    are a best-effort side effect; they must not cause the primary operation
    to fail.

    When ``fanout`` is provided, the notification additionally fans out to
    email and/or webhook subscribers per the dispatcher's policy. Fan-out
    failures are logged but never raise.
    """
    resolved_hours = _resolve_expiry_hours(category, urgency, expires_after_hours)

    spec = NotificationCreate(
        organization_id=organization_id,
        user_id=user_id,
        category=category,
        level=level,
        urgency=urgency,
        title=title,
        message=message,
        url=url,
        url_label=url_label,
        source_type=source_type,
        source_id=source_id,
        payload=payload or {},
        expires_after_hours=resolved_hours,
    )

    try:
        record = store.create(spec)
    except Exception:
        logger.exception(
            "emit_notification failed — category=%s org=%s",
            category,
            organization_id,
        )
        return

    if fanout is None:
        return

    try:
        fanout.dispatch(record)
    except Exception:
        logger.exception(
            "emit_notification_fanout failed — category=%s notification_id=%s",
            category,
            record.notification_id,
        )
