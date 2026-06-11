"""Core audit event model, event types, and hash chain.

An AuditEvent is a statement of fact: "Actor X performed operation Y on
resource Z at time T, with outcome O."  Everything else is metadata.

Two write-guarantee tiers:
  - Security / admin events → synchronous Postgres write (never lossy)
  - Operational events      → async queue → flusher → Postgres (tolerable loss)

Hash chain: each event's ``content_hash`` covers all semantic fields.
``prev_hash`` links to the preceding event in the same org's chain,
making deletion, insertion, and modification detectable.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

# ── Event type constants ─────────────────────────────────────────────────────

# Operational (async write path)
RESOURCE_CREATED = "resource.created"
RESOURCE_UPDATED = "resource.updated"
RESOURCE_DELETED = "resource.deleted"
RESOURCE_ACCESSED = "resource.accessed"

# Security (sync write path — never lossy)
AUTH_LOGIN = "auth.login"
AUTH_LOGIN_FAILED = "auth.login_failed"
AUTH_LOGOUT = "auth.logout"
AUTH_TOKEN_REFRESHED = "auth.token_refreshed"
AUTH_TOKEN_REUSE = "auth.token_reuse_detected"
AUTH_SESSION_REVOKED = "auth.session_revoked"
# Third-party OAuth connection lifecycle. The ``auth.*`` prefix routes
# these through the sync write path (never lossy) — they're the audit
# trail for who authorized which integration when, and when consent was
# withdrawn or invalidated. Routine machine refreshes are NOT audited
# here (would generate one event per active connection per ~50 minutes,
# drowning out the user-meaningful actions); they appear in stdlib logs.
AUTH_OAUTH_CONNECTION_AUTHORIZED = "auth.oauth.connection_authorized"
AUTH_OAUTH_CONNECTION_REQUIRES_REAUTH = "auth.oauth.connection_requires_reauth"
AUTH_OAUTH_CONNECTION_REVOKED = "auth.oauth.connection_revoked"
SECURITY_PERMISSION_DENIED = "security.permission_denied"
SECURITY_RATE_LIMITED = "security.rate_limited"
SECURITY_SUSPICIOUS = "security.suspicious"
SECURITY_PII_DETECTED = "security.pii_detected"

# Admin (sync write path)
ADMIN_USER_INVITED = "admin.user_invited"
ADMIN_INVITATION_ACCEPTED = "admin.invitation_accepted"
ADMIN_USER_REMOVED = "admin.user_removed"
ADMIN_ROLE_CHANGED = "admin.role_changed"
ADMIN_SETTINGS_CHANGED = "admin.settings_changed"
ADMIN_API_KEY_CREATED = "admin.api_key_created"
ADMIN_API_KEY_REVOKED = "admin.api_key_revoked"

_SYNC_EVENT_PREFIXES = frozenset({"auth.", "security.", "admin."})

Operation = Literal["create", "update", "delete", "access", "auth", "security"]
Outcome = Literal["success", "failure", "denied"]


def requires_sync_write(event_type: str) -> bool:
    """Return True if this event type must be written synchronously."""
    return any(event_type.startswith(p) for p in _SYNC_EVENT_PREFIXES)


def operation_from_event_type(event_type: str) -> Operation:
    """Derive the operation category from event_type."""
    if event_type.startswith("resource."):
        suffix = event_type.split(".", 1)[1]
        return {"created": "create", "updated": "update", "deleted": "delete", "accessed": "access"}.get(suffix, "access")
    if event_type.startswith("auth."):
        return "auth"
    if event_type.startswith("security."):
        return "security"
    if event_type.startswith("admin."):
        return "update"
    return "access"


# ── Sensitive field filtering ────────────────────────────────────────────────

_SENSITIVE_KEYS = frozenset({
    "password", "password_hash", "token", "access_token", "refresh_token",
    "api_key", "secret", "client_secret", "private_key", "credit_card",
    "ssn", "transcript", "audio_transcript",
})


def redact_sensitive(obj: object, *, _depth: int = 0) -> object:
    """Recursively redact values of known-sensitive keys."""
    if _depth > 4:
        return obj
    if isinstance(obj, dict):
        return {
            k: "***" if k.lower() in _SENSITIVE_KEYS else redact_sensitive(v, _depth=_depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_sensitive(v, _depth=_depth + 1) for v in obj]
    return obj


# ── Core event dataclass ─────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    event_type: str
    organization_id: str
    outcome: Outcome = "success"
    actor_id: str | None = None
    actor_ip: str | None = None
    actor_session_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    http_method: str | None = None
    http_path: str | None = None
    http_status: int | None = None
    duration_ms: int | None = None
    request_id: str | None = None
    trace_id: str | None = None

    # Set automatically
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    operation: str = ""
    content_hash: str = ""
    prev_hash: str | None = None
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def __post_init__(self) -> None:
        if not self.operation:
            self.operation = operation_from_event_type(self.event_type)

    def compute_hash(self) -> str:
        """Compute SHA-256 covering all semantic fields."""
        detail_canonical = json.dumps(self.detail, sort_keys=True, default=str)
        raw = "|".join([
            self.event_id,
            self.organization_id or "",
            self.actor_id or "",
            self.event_type,
            self.operation,
            self.resource_type or "",
            self.resource_id or "",
            self.trace_id or "",
            detail_canonical,
            self.outcome,
            self.created_at,
        ])
        return hashlib.sha256(raw.encode()).hexdigest()

    def finalize(self, prev_hash: str | None = None) -> None:
        """Compute content_hash and set the chain link."""
        self.prev_hash = prev_hash
        self.content_hash = self.compute_hash()

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "organization_id": self.organization_id,
            "actor_id": self.actor_id,
            "actor_ip": self.actor_ip,
            "actor_session_id": self.actor_session_id,
            "event_type": self.event_type,
            "operation": self.operation,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "detail": self.detail,
            "outcome": self.outcome,
            "http_method": self.http_method,
            "http_path": self.http_path,
            "http_status": self.http_status,
            "duration_ms": self.duration_ms,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "content_hash": self.content_hash,
            "prev_hash": self.prev_hash,
            "created_at": self.created_at,
        }


# ── HTTP path helpers ────────────────────────────────────────────────────────

def _looks_like_id(part: str) -> bool:
    return len(part) > 8 and part.replace("-", "").isalnum() and any(c.isdigit() for c in part)


def path_to_resource_type(path: str) -> str:
    """Extract the primary resource type from an HTTP path."""
    parts = [p for p in path.split("/") if p and not _looks_like_id(p)]
    return parts[0] if parts else "unknown"


def method_to_event_type(method: str) -> str:
    """Map an HTTP method to an event type."""
    return {
        "POST": RESOURCE_CREATED,
        "PUT": RESOURCE_UPDATED,
        "PATCH": RESOURCE_UPDATED,
        "DELETE": RESOURCE_DELETED,
    }.get(method, RESOURCE_ACCESSED)
