"""Public API for emitting audit events from application code.

Usage in route handlers or services::

    from ruhu.audit.emitter import emit_audit_event
    from ruhu.audit.events import RESOURCE_UPDATED, AUTH_LOGIN

    # Operational event (async write)
    emit_audit_event(
        router,
        event_type=RESOURCE_UPDATED,
        organization_id=org_id,
        actor_id=user_id,
        resource_type="agent",
        resource_id=agent_id,
        detail={"changes": {"name": {"old": "v1", "new": "v2"}}},
    )

    # Security event (sync write — never lossy)
    emit_audit_event(
        router,
        event_type=AUTH_LOGIN,
        organization_id=org_id,
        actor_id=user_id,
        actor_ip=client_ip,
    )
"""
from __future__ import annotations

from typing import Any

import structlog

from .events import AuditEvent
from .router import AuditEventRouter

log = structlog.get_logger(__name__)


def emit_audit_event(
    router: AuditEventRouter,
    *,
    event_type: str,
    organization_id: str,
    outcome: str = "success",
    actor_id: str | None = None,
    actor_ip: str | None = None,
    actor_session_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    """Build and route an audit event from application code.

    This is the primary entry point for semantic audit events that carry
    richer context than what the HTTP middleware can infer (e.g., before/after
    field changes on an agent update).
    """
    event = AuditEvent(
        event_type=event_type,
        organization_id=organization_id,
        outcome=outcome,
        actor_id=actor_id,
        actor_ip=actor_ip,
        actor_session_id=actor_session_id,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail or {},
        request_id=request_id,
    )

    try:
        router.route(event)
    except Exception:
        log.warning(
            "emit_audit_event_failed",
            event_type=event_type,
            org_id=organization_id,
            exc_info=True,
        )
