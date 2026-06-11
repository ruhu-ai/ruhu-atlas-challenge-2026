from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RuntimeChannel = Literal["phone", "whatsapp", "web_chat", "web_widget", "browser"]
TagKind = Literal[
    "goal_attribute",
    "failure_reason",
    "blocker",
    "priority",
    "risk",
    "outcome_attribute",
]
TagApplyScope = Literal["turn", "conversation", "both"]
TaxonomyMode = Literal["live", "pinned", "cached_live"]
TaxonomyVersionStatus = Literal["draft", "published", "deprecated"]
ReviewStatus = Literal["pending", "in_review", "resolved", "dismissed"]
ReviewDisposition = Literal["confirmed", "corrected", "dismissed", "needs_followup"]
ReviewKind = Literal[
    "low_confidence_turn",
    "policy_violation_turn",
    "summary_correction",
    "tag_correction",
    "manual_flag",
]
ClassificationSourceKind = Literal["runtime", "turn_trace", "realtime_event", "manual_preview", "backfill"]
SummaryStatus = Literal["draft", "final", "corrected", "superseded"]
SummaryResolutionStatus = Literal[
    "resolved",
    "follow_up_required",
    "escalated",
    "abandoned",
    "failed",
    "unresolved",
    "unknown",
]
TagAssignmentScope = Literal["turn", "conversation"]
TagAssignmentSource = Literal[
    "deterministic_rule",
    "summary_rollup",
    "operator_manual",
    "review_correction",
    "backfill_model",
]

_RUNTIME_CHANNELS = {"phone", "whatsapp", "web_chat", "web_widget", "browser"}
_MACHINE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def _normalize_runtime_channel(value: str | None) -> RuntimeChannel | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized not in _RUNTIME_CHANNELS:
        raise ValueError(f"unsupported runtime channel: {value}")
    return normalized  # type: ignore[return-value]


class TaxonomyVersion(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    taxonomy_version_id: str = Field(default_factory=new_id)
    organization_id: str
    name: str
    status: TaxonomyVersionStatus = "draft"
    notes: str | None = None
    published_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class IntentDefinition(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    intent_definition_id: str = Field(default_factory=new_id)
    organization_id: str
    agent_id: str | None = None
    taxonomy_version_id: str | None = None
    name: str
    display_name: str
    description: str | None = None
    category: str | None = None
    example_phrases: list[str] = Field(default_factory=list)
    confidence_threshold: float = 0.7
    priority: int = 0
    is_active: bool = True
    is_deprecated: bool = False
    color: str | None = None
    icon: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _MACHINE_NAME_RE.match(value):
            raise ValueError("intent name must be lowercase snake_case")
        return value

    @field_validator("confidence_threshold")
    @classmethod
    def _validate_threshold(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        return value

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: int) -> int:
        if value < 0:
            raise ValueError("priority must be non-negative")
        return value


class TagDefinition(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tag_definition_id: str = Field(default_factory=new_id)
    organization_id: str
    agent_id: str | None = None
    taxonomy_version_id: str | None = None
    name: str
    display_name: str
    description: str | None = None
    tag_kind: TagKind
    category: str | None = None
    confidence_threshold: float = 0.6
    apply_scope: TagApplyScope = "conversation"
    related_intent_id: str | None = None
    is_active: bool = True
    is_deprecated: bool = False
    color: str | None = None
    icon: str | None = None
    rule_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _MACHINE_NAME_RE.match(value):
            raise ValueError("tag name must be lowercase snake_case")
        return value

    @field_validator("confidence_threshold")
    @classmethod
    def _validate_threshold(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        return value


class ClassifierProfile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    classifier_profile_id: str = Field(default_factory=new_id)
    organization_id: str
    agent_id: str | None = None
    adapter_name: str = "ruhu-general"
    supported_languages: list[str] = Field(default_factory=list)
    taxonomy_mode: TaxonomyMode = "live"
    taxonomy_version_id: str | None = None
    intent_catalog: list[dict[str, Any]] = Field(default_factory=list)
    tool_catalog: list[dict[str, Any]] = Field(default_factory=list)
    catalog_cache_built_at: datetime | None = None
    policy_profile: dict[str, Any] = Field(default_factory=dict)
    profile_metadata: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("supported_languages")
    @classmethod
    def _normalize_languages(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            language = str(item).strip().lower()
            if not language or language in seen:
                continue
            seen.add(language)
            normalized.append(language)
        return normalized

    @model_validator(mode="after")
    def _validate_taxonomy_mode(self) -> "ClassifierProfile":
        if self.taxonomy_mode == "pinned" and not self.taxonomy_version_id:
            raise ValueError("taxonomy_version_id is required when taxonomy_mode is 'pinned'")
        return self


class SemanticSummaryWebhookTarget(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    webhook_target_id: str = Field(default_factory=new_id)
    organization_id: str
    name: str
    url: str
    event_name: str = "semantic_summary.finalized"
    agent_ids: list[str] = Field(default_factory=list)
    channels: list[RuntimeChannel] = Field(default_factory=list)
    signing_secret_ref: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 5.0
    max_retries: int = 5
    retry_backoff_seconds: float = 5.0
    is_active: bool = True
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failure_count: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("webhook target name is required")
        return value

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("webhook target url must be a valid http or https URL")
        return value

    @field_validator("event_name")
    @classmethod
    def _validate_event_name(cls, value: str) -> str:
        normalized = value.strip()
        # Canonical shape: ``family.name`` — must be non-empty, lower_snake on
        # both sides, no whitespace, no double dots. The dispatcher matches
        # by exact equality against ``f"{event.family}.{event.name}"``, so we
        # enforce the same shape here to catch typos at registration time.
        if not normalized:
            raise ValueError("webhook target event_name is required")
        if "." not in normalized:
            raise ValueError(
                "webhook target event_name must use 'family.name' shape "
                f"(got {value!r})"
            )
        family, _, name = normalized.partition(".")
        if not family or not name:
            raise ValueError(
                "webhook target event_name must have non-empty family and name "
                f"(got {value!r})"
            )
        if any(ch.isspace() for ch in normalized):
            raise ValueError(f"webhook target event_name must not contain whitespace (got {value!r})")
        return normalized

    @field_validator("agent_ids")
    @classmethod
    def _normalize_agent_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = str(item).strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @field_validator("channels")
    @classmethod
    def _normalize_channels(cls, value: list[str]) -> list[RuntimeChannel]:
        normalized: list[RuntimeChannel] = []
        seen: set[str] = set()
        for item in value:
            channel = _normalize_runtime_channel(item)
            if channel is None or channel in seen:
                continue
            seen.add(channel)
            normalized.append(channel)
        return normalized

    @field_validator("extra_headers")
    @classmethod
    def _normalize_headers(cls, value: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, item in dict(value or {}).items():
            header_name = str(key).strip()
            if not header_name:
                continue
            header_value = str(item).strip()
            if not header_value:
                continue
            normalized[header_name] = header_value
        return normalized

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout_seconds must be positive")
        if value > 120:
            raise ValueError("timeout_seconds must be <= 120")
        return value

    @field_validator("max_retries")
    @classmethod
    def _validate_max_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_retries must be >= 0")
        if value > 25:
            raise ValueError("max_retries must be <= 25")
        return value

    @field_validator("retry_backoff_seconds")
    @classmethod
    def _validate_retry_backoff_seconds(cls, value: float) -> float:
        if value < 0:
            raise ValueError("retry_backoff_seconds must be >= 0")
        if value > 3600:
            raise ValueError("retry_backoff_seconds must be <= 3600")
        return value


class ResolvedClassifierProfile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    classifier_profile_id: str | None = None
    organization_id: str
    agent_id: str | None = None
    adapter_name: str
    supported_languages: list[str] = Field(default_factory=list)
    taxonomy_mode: TaxonomyMode = "live"
    taxonomy_version_id: str | None = None
    effective_intent_catalog: list[dict[str, Any]] = Field(default_factory=list)
    effective_tool_catalog: list[dict[str, Any]] = Field(default_factory=list)
    policy_profile: dict[str, Any] = Field(default_factory=dict)
    profile_metadata: dict[str, Any] = Field(default_factory=dict)
    catalog_cache_built_at: datetime | None = None
    source: str = "active_profile"


class TurnClassificationDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    intent_name: str
    confidence: float
    language: str
    response_language: str
    tool_route: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    signals: dict[str, Any] = Field(default_factory=dict)

    @field_validator("intent_name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _MACHINE_NAME_RE.match(value):
            raise ValueError("intent_name must be lowercase snake_case")
        return value

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class TurnClassificationEvent(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    classification_event_id: str = Field(default_factory=new_id)
    organization_id: str
    agent_id: str | None = None
    agent_version_id: str | None = None
    classifier_profile_id: str | None = None
    conversation_id: str
    turn_trace_id: str | None = None
    realtime_event_id: str | None = None
    channel: RuntimeChannel
    provider: str | None = None
    source_kind: ClassificationSourceKind = "runtime"
    adapter_name: str
    model_version: str
    taxonomy_mode: TaxonomyMode = "live"
    taxonomy_version_id: str | None = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    context_payload: dict[str, Any] = Field(default_factory=dict)
    decision_payload: dict[str, Any] = Field(default_factory=dict)
    intent_name: str
    confidence: float
    language: str
    response_language: str
    tool_route: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    signals: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("channel", mode="before")
    @classmethod
    def _validate_channel(cls, value: str | None) -> RuntimeChannel:
        channel = _normalize_runtime_channel(value)
        if channel is None:
            raise ValueError("channel is required")
        return channel

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @field_validator("intent_name")
    @classmethod
    def _validate_intent_name(cls, value: str) -> str:
        if not _MACHINE_NAME_RE.match(value):
            raise ValueError("intent_name must be lowercase snake_case")
        return value


class ClassificationReviewItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    review_item_id: str = Field(default_factory=new_id)
    organization_id: str
    classification_event_id: str | None = None
    conversation_summary_id: str | None = None
    status: ReviewStatus = "pending"
    review_kind: ReviewKind
    review_disposition: ReviewDisposition | None = None
    review_notes: str | None = None
    corrected_payload: dict[str, Any] = Field(default_factory=dict)
    claimed_by_user_id: str | None = None
    claimed_at: datetime | None = None
    reviewed_by_user_id: str | None = None
    reviewed_at: datetime | None = None
    corrected_conversation_summary_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_review_target(self) -> "ClassificationReviewItem":
        has_event = self.classification_event_id is not None
        has_summary = self.conversation_summary_id is not None
        if has_event == has_summary:
            raise ValueError(
                "exactly one of classification_event_id or conversation_summary_id must be present"
            )
        if self.status == "resolved" and self.review_disposition is None:
            raise ValueError("resolved review items require review_disposition")
        return self


class ConversationSemanticContext(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    organization_id: str
    conversation_id: str
    agent_id: str | None = None
    agent_version_id: str | None = None
    channel: RuntimeChannel | None = None
    status: str | None = None
    outcome: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @field_validator("channel", mode="before")
    @classmethod
    def _normalize_channel(cls, value: str | None) -> RuntimeChannel | None:
        return _normalize_runtime_channel(value)


class ConversationSemanticSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    conversation_summary_id: str = Field(default_factory=new_id)
    organization_id: str
    agent_id: str | None = None
    agent_version_id: str | None = None
    conversation_id: str
    summary_version: int = 1
    status: SummaryStatus = "draft"
    primary_intent_name: str | None = None
    secondary_intents: list[dict[str, Any]] = Field(default_factory=list)
    resolution_status: SummaryResolutionStatus | None = None
    outcome: str | None = None
    final_language: str | None = None
    response_language: str | None = None
    channel: RuntimeChannel
    requires_human_followup: bool = False
    requires_review: bool = False
    summary_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    generated_from_event_count: int = 0
    last_event_created_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("channel", mode="before")
    @classmethod
    def _validate_summary_channel(cls, value: str | None) -> RuntimeChannel:
        channel = _normalize_runtime_channel(value)
        if channel is None:
            raise ValueError("channel is required")
        return channel

    @field_validator("summary_version")
    @classmethod
    def _validate_summary_version(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("summary_version must be positive")
        return value

    @field_validator("primary_intent_name")
    @classmethod
    def _validate_primary_intent_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _MACHINE_NAME_RE.match(value):
            raise ValueError("primary_intent_name must be lowercase snake_case")
        return value


class TagAssignment(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tag_assignment_id: str = Field(default_factory=new_id)
    organization_id: str
    conversation_id: str
    classification_event_id: str | None = None
    conversation_summary_id: str | None = None
    tag_definition_id: str
    assignment_scope: TagAssignmentScope
    assignment_source: TagAssignmentSource
    confidence: float | None = None
    reason_text: str | None = None
    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    is_validated: bool = False
    validated_by_user_id: str | None = None
    validated_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("confidence")
    @classmethod
    def _validate_assignment_confidence(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0 or value > 1:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def _validate_assignment_linkage(self) -> "TagAssignment":
        has_event = self.classification_event_id is not None
        has_summary = self.conversation_summary_id is not None
        if has_event == has_summary:
            raise ValueError(
                "exactly one of classification_event_id or conversation_summary_id must be present"
            )
        if self.assignment_scope == "turn" and not has_event:
            raise ValueError("turn assignments require classification_event_id")
        if self.assignment_scope == "conversation" and not has_summary:
            raise ValueError("conversation assignments require conversation_summary_id")
        return self


class EffectiveTurnClassification(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event: TurnClassificationEvent
    effective_event: TurnClassificationEvent
    review_item: ClassificationReviewItem | None = None
    is_corrected: bool = False


class EffectiveConversationSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    summary: ConversationSemanticSummary
    effective_summary: ConversationSemanticSummary
    tag_assignments: list[TagAssignment] = Field(default_factory=list)
    review_item: ClassificationReviewItem | None = None
    is_corrected: bool = False
