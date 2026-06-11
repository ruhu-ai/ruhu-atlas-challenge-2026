from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


CaptureSource = Literal[
    "deterministic",
    "classifier",
    "extractor",
    "tool",
    "llm_proposed",
    "user_confirmed",
    "system",
]


@dataclass(slots=True)
class FactCandidate:
    fact_name: str
    raw_value: Any
    source: CaptureSource
    evidence: str | None
    confidence: float
    source_ref: str | None = None
    transcript_span: tuple[int, int] | None = None


@dataclass(slots=True)
class ValidationResult:
    status: Literal["passed", "failed"]
    normalized_value: Any | None
    reason: str | None = None


@dataclass(slots=True)
class PipelineDecision:
    decision: Literal[
        "accepted",
        "rejected_lower_priority_candidate",
        "rejected_policy",
        "rejected_safety",
        "rejected_validation",
        "rejected_conflict",
        "needs_confirmation_threshold",
        "needs_confirmation_conflict",
        "stored_audit_only",
        "stored_audit_only_redacted",
    ]
    reason: str | None = None


@dataclass(slots=True)
class SafetyVerdict:
    action: Literal["allow", "reject", "redact_audit_only"]
    reason: str | None = None
    emit_security_event: bool = False


@dataclass(slots=True)
class CaptureAuditRow:
    conversation_id: str
    turn_id: str
    step_id: str | None
    fact_name: str
    source: CaptureSource
    outcome: str
    reason: str | None = None
    raw_value: Any | None = None
    normalized_value: Any | None = None
    confidence: float | None = None
    evidence: str | None = None
    source_ref: str | None = None
    storage_scope: str = "conversation"
    retention_policy: str = "conversation"
    sensitivity: str = "personal"
    audit_raw_policy: str = "hash"
    replaced_previous: bool = False
    organization_id: str | None = None
    transcript_span: tuple[int, int] | None = None
