from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class Permission(str, Enum):
    # Agent lifecycle
    AGENT_READ    = "agent:read"
    AGENT_EDIT    = "agent:edit"
    AGENT_PUBLISH = "agent:publish"
    AGENT_DELETE  = "agent:delete"
    AGENT_AUDIT   = "agent:audit"
    # Conversations
    CONVERSATION_READ   = "conversation:read"
    CONVERSATION_REPLAY = "conversation:replay"
    CONVERSATION_DELETE = "conversation:delete"
    # Tools
    TOOL_INVOKE = "tool:invoke"
    TOOL_MANAGE = "tool:manage"
    # Knowledge
    KNOWLEDGE_READ   = "knowledge:read"
    KNOWLEDGE_MANAGE = "knowledge:manage"
    # Members & org
    MEMBER_INVITE = "member:invite"
    MEMBER_REMOVE = "member:remove"
    ORG_READ      = "org:read"
    ORG_UPDATE    = "org:update"
    # Billing
    BILLING_READ   = "billing:read"
    BILLING_MANAGE = "billing:manage"
    # KPI / Rules / Audit
    KPI_READ    = "kpi:read"
    KPI_MANAGE  = "kpi:manage"
    RULE_READ   = "rule:read"
    RULE_MANAGE = "rule:manage"
    AUDIT_READ  = "audit:read"


# Seed mapping — preserves the existing role hierarchy exactly.
# Orgs cannot customize roles yet (Phase 5b adds the DB table for that).

_ANALYST: FrozenSet[Permission] = frozenset({
    Permission.AGENT_READ,
    Permission.CONVERSATION_READ,
    Permission.KNOWLEDGE_READ,
    Permission.KPI_READ,
    Permission.RULE_READ,
    Permission.ORG_READ,
})

_DEVELOPER: FrozenSet[Permission] = _ANALYST | frozenset({
    Permission.AGENT_EDIT,
    Permission.AGENT_PUBLISH,
    Permission.CONVERSATION_REPLAY,
    Permission.TOOL_INVOKE,
    Permission.KNOWLEDGE_MANAGE,
    Permission.KPI_MANAGE,
    Permission.RULE_MANAGE,
})

_ADMIN: FrozenSet[Permission] = _DEVELOPER | frozenset({
    Permission.AGENT_DELETE,
    Permission.AGENT_AUDIT,
    Permission.CONVERSATION_DELETE,
    Permission.TOOL_MANAGE,
    Permission.MEMBER_INVITE,
    Permission.MEMBER_REMOVE,
    Permission.ORG_UPDATE,
    Permission.BILLING_READ,
    Permission.AUDIT_READ,
})

ROLE_PERMISSIONS: dict[str, FrozenSet[Permission]] = {
    "analyst":   _ANALYST,
    "developer": _DEVELOPER,
    "admin":     _ADMIN,
}
