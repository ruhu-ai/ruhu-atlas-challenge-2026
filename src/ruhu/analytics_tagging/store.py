from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..db_models import ConversationRecord, TurnTraceRecord
from .models import (
    ClassificationReviewItem,
    ClassifierProfile,
    ConversationSemanticContext,
    ConversationSemanticSummary,
    IntentDefinition,
    SemanticSummaryWebhookTarget,
    TagAssignment,
    TagDefinition,
    TaxonomyVersion,
    TurnClassificationEvent,
)
from .sqlalchemy_models import (
    IntentDefinitionRecord,
    IntentTagAssignmentRecord,
    IntentTagClassificationEventRecord,
    IntentTagClassifierProfileRecord,
    IntentTagConversationSummaryRecord,
    IntentTagReviewItemRecord,
    IntentTagSemanticWebhookTargetRecord,
    IntentTagTaxonomyVersionRecord,
    TagDefinitionRecord,
)


class IntentTagsStore(Protocol):
    def save_taxonomy_version(self, version: TaxonomyVersion) -> TaxonomyVersion: ...

    def get_taxonomy_version(self, taxonomy_version_id: str) -> TaxonomyVersion | None: ...

    def list_taxonomy_versions(self, organization_id: str, *, status: str | None = None) -> list[TaxonomyVersion]: ...

    def save_intent_definition(self, intent: IntentDefinition) -> IntentDefinition: ...

    def get_intent_definition(self, intent_definition_id: str) -> IntentDefinition | None: ...

    def list_intent_definitions(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = True,
    ) -> list[IntentDefinition]: ...

    def save_tag_definition(self, tag: TagDefinition) -> TagDefinition: ...

    def get_tag_definition(self, tag_definition_id: str) -> TagDefinition | None: ...

    def list_tag_definitions(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = True,
    ) -> list[TagDefinition]: ...

    def save_classifier_profile(self, profile: ClassifierProfile) -> ClassifierProfile: ...

    def get_classifier_profile(self, classifier_profile_id: str) -> ClassifierProfile | None: ...

    def list_classifier_profiles(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        is_active: bool | None = None,
    ) -> list[ClassifierProfile]: ...

    def save_semantic_webhook_target(self, target: SemanticSummaryWebhookTarget) -> SemanticSummaryWebhookTarget: ...

    def get_semantic_webhook_target(self, webhook_target_id: str) -> SemanticSummaryWebhookTarget | None: ...

    def list_semantic_webhook_targets(
        self,
        organization_id: str,
        *,
        is_active: bool | None = None,
    ) -> list[SemanticSummaryWebhookTarget]: ...

    def delete_semantic_webhook_target(self, webhook_target_id: str) -> bool: ...

    def save_classification_event(self, event: TurnClassificationEvent) -> TurnClassificationEvent: ...

    def get_classification_event(self, classification_event_id: str) -> TurnClassificationEvent | None: ...

    def get_classification_event_by_turn_trace_id(
        self,
        turn_trace_id: str,
        *,
        organization_id: str | None = None,
    ) -> TurnClassificationEvent | None: ...

    def list_classification_events(
        self,
        organization_id: str,
        *,
        conversation_id: str | None = None,
        intent_name: str | None = None,
        limit: int = 100,
    ) -> list[TurnClassificationEvent]: ...

    def save_review_item(self, review_item: ClassificationReviewItem) -> ClassificationReviewItem: ...

    def get_review_item(self, review_item_id: str) -> ClassificationReviewItem | None: ...

    def list_review_items(
        self,
        organization_id: str,
        *,
        classification_event_id: str | None = None,
        conversation_summary_id: str | None = None,
        status: str | None = None,
        review_kind: str | None = None,
        claimed_by_user_id: str | None = None,
        limit: int = 100,
    ) -> list[ClassificationReviewItem]: ...

    def save_conversation_summary(
        self,
        summary: ConversationSemanticSummary,
    ) -> ConversationSemanticSummary: ...

    def get_conversation_summary(self, conversation_summary_id: str) -> ConversationSemanticSummary | None: ...

    def list_conversation_summaries(
        self,
        organization_id: str,
        *,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ConversationSemanticSummary]: ...

    def save_tag_assignment(self, assignment: TagAssignment) -> TagAssignment: ...

    def get_tag_assignment(self, tag_assignment_id: str) -> TagAssignment | None: ...

    def list_tag_assignments(
        self,
        organization_id: str,
        *,
        conversation_id: str | None = None,
        classification_event_id: str | None = None,
        conversation_summary_id: str | None = None,
        assignment_scope: str | None = None,
        limit: int = 200,
    ) -> list[TagAssignment]: ...

    def get_conversation_context(self, conversation_id: str) -> ConversationSemanticContext | None: ...

    def project_runtime_cache(self, event: TurnClassificationEvent) -> None: ...


class InMemoryIntentTagsStore:
    def __init__(self) -> None:
        self._taxonomy_versions: dict[str, TaxonomyVersion] = {}
        self._intents: dict[str, IntentDefinition] = {}
        self._tags: dict[str, TagDefinition] = {}
        self._profiles: dict[str, ClassifierProfile] = {}
        self._webhook_targets: dict[str, SemanticSummaryWebhookTarget] = {}
        self._events: dict[str, TurnClassificationEvent] = {}
        self._review_items: dict[str, ClassificationReviewItem] = {}
        self._summaries: dict[str, ConversationSemanticSummary] = {}
        self._assignments: dict[str, TagAssignment] = {}

    def save_taxonomy_version(self, version: TaxonomyVersion) -> TaxonomyVersion:
        stored = version.model_copy(deep=True)
        self._taxonomy_versions[stored.taxonomy_version_id] = stored
        return stored.model_copy(deep=True)

    def get_taxonomy_version(self, taxonomy_version_id: str) -> TaxonomyVersion | None:
        item = self._taxonomy_versions.get(taxonomy_version_id)
        return None if item is None else item.model_copy(deep=True)

    def list_taxonomy_versions(self, organization_id: str, *, status: str | None = None) -> list[TaxonomyVersion]:
        items = [item for item in self._taxonomy_versions.values() if item.organization_id == organization_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.created_at, item.taxonomy_version_id))
        return [item.model_copy(deep=True) for item in items]

    def save_intent_definition(self, intent: IntentDefinition) -> IntentDefinition:
        stored = intent.model_copy(deep=True)
        self._intents[stored.intent_definition_id] = stored
        return stored.model_copy(deep=True)

    def get_intent_definition(self, intent_definition_id: str) -> IntentDefinition | None:
        item = self._intents.get(intent_definition_id)
        return None if item is None else item.model_copy(deep=True)

    def list_intent_definitions(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = True,
    ) -> list[IntentDefinition]:
        items = [item for item in self._intents.values() if item.organization_id == organization_id]
        if agent_id is not None:
            items = [item for item in items if item.agent_id == agent_id]
        if taxonomy_version_id is not None:
            items = [item for item in items if item.taxonomy_version_id == taxonomy_version_id]
        if not include_inactive:
            items = [item for item in items if item.is_active and not item.is_deprecated]
        items.sort(key=lambda item: (item.priority, item.display_name, item.intent_definition_id), reverse=True)
        return [item.model_copy(deep=True) for item in items]

    def save_tag_definition(self, tag: TagDefinition) -> TagDefinition:
        stored = tag.model_copy(deep=True)
        self._tags[stored.tag_definition_id] = stored
        return stored.model_copy(deep=True)

    def get_tag_definition(self, tag_definition_id: str) -> TagDefinition | None:
        item = self._tags.get(tag_definition_id)
        return None if item is None else item.model_copy(deep=True)

    def list_tag_definitions(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = True,
    ) -> list[TagDefinition]:
        items = [item for item in self._tags.values() if item.organization_id == organization_id]
        if agent_id is not None:
            items = [item for item in items if item.agent_id == agent_id]
        if taxonomy_version_id is not None:
            items = [item for item in items if item.taxonomy_version_id == taxonomy_version_id]
        if not include_inactive:
            items = [item for item in items if item.is_active and not item.is_deprecated]
        items.sort(key=lambda item: (item.display_name, item.tag_definition_id))
        return [item.model_copy(deep=True) for item in items]

    def save_classifier_profile(self, profile: ClassifierProfile) -> ClassifierProfile:
        stored = profile.model_copy(deep=True)
        self._profiles[stored.classifier_profile_id] = stored
        return stored.model_copy(deep=True)

    def get_classifier_profile(self, classifier_profile_id: str) -> ClassifierProfile | None:
        item = self._profiles.get(classifier_profile_id)
        return None if item is None else item.model_copy(deep=True)

    def list_classifier_profiles(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        is_active: bool | None = None,
    ) -> list[ClassifierProfile]:
        items = [item for item in self._profiles.values() if item.organization_id == organization_id]
        if agent_id is not None:
            items = [item for item in items if item.agent_id == agent_id]
        if is_active is not None:
            items = [item for item in items if item.is_active is is_active]
        items.sort(key=lambda item: (item.updated_at, item.classifier_profile_id), reverse=True)
        return [item.model_copy(deep=True) for item in items]

    def save_semantic_webhook_target(self, target: SemanticSummaryWebhookTarget) -> SemanticSummaryWebhookTarget:
        stored = target.model_copy(deep=True)
        self._webhook_targets[stored.webhook_target_id] = stored
        return stored.model_copy(deep=True)

    def get_semantic_webhook_target(self, webhook_target_id: str) -> SemanticSummaryWebhookTarget | None:
        item = self._webhook_targets.get(webhook_target_id)
        return None if item is None else item.model_copy(deep=True)

    def list_semantic_webhook_targets(
        self,
        organization_id: str,
        *,
        is_active: bool | None = None,
    ) -> list[SemanticSummaryWebhookTarget]:
        items = [item for item in self._webhook_targets.values() if item.organization_id == organization_id]
        if is_active is not None:
            items = [item for item in items if item.is_active is is_active]
        items.sort(key=lambda item: (item.updated_at, item.webhook_target_id), reverse=True)
        return [item.model_copy(deep=True) for item in items]

    def delete_semantic_webhook_target(self, webhook_target_id: str) -> bool:
        return self._webhook_targets.pop(webhook_target_id, None) is not None

    def save_classification_event(self, event: TurnClassificationEvent) -> TurnClassificationEvent:
        stored = event.model_copy(deep=True)
        self._events[stored.classification_event_id] = stored
        return stored.model_copy(deep=True)

    def get_classification_event(self, classification_event_id: str) -> TurnClassificationEvent | None:
        item = self._events.get(classification_event_id)
        return None if item is None else item.model_copy(deep=True)

    def get_classification_event_by_turn_trace_id(
        self,
        turn_trace_id: str,
        *,
        organization_id: str | None = None,
    ) -> TurnClassificationEvent | None:
        for item in self._events.values():
            if item.turn_trace_id != turn_trace_id:
                continue
            if organization_id is not None and item.organization_id != organization_id:
                continue
            return item.model_copy(deep=True)
        return None

    def list_classification_events(
        self,
        organization_id: str,
        *,
        conversation_id: str | None = None,
        intent_name: str | None = None,
        limit: int = 100,
    ) -> list[TurnClassificationEvent]:
        items = [item for item in self._events.values() if item.organization_id == organization_id]
        if conversation_id is not None:
            items = [item for item in items if item.conversation_id == conversation_id]
        if intent_name is not None:
            items = [item for item in items if item.intent_name == intent_name]
        items.sort(key=lambda item: (item.created_at, item.classification_event_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def save_review_item(self, review_item: ClassificationReviewItem) -> ClassificationReviewItem:
        stored = review_item.model_copy(deep=True)
        self._review_items[stored.review_item_id] = stored
        return stored.model_copy(deep=True)

    def get_review_item(self, review_item_id: str) -> ClassificationReviewItem | None:
        item = self._review_items.get(review_item_id)
        return None if item is None else item.model_copy(deep=True)

    def list_review_items(
        self,
        organization_id: str,
        *,
        classification_event_id: str | None = None,
        conversation_summary_id: str | None = None,
        status: str | None = None,
        review_kind: str | None = None,
        claimed_by_user_id: str | None = None,
        limit: int = 100,
    ) -> list[ClassificationReviewItem]:
        items = [item for item in self._review_items.values() if item.organization_id == organization_id]
        if classification_event_id is not None:
            items = [item for item in items if item.classification_event_id == classification_event_id]
        if conversation_summary_id is not None:
            items = [item for item in items if item.conversation_summary_id == conversation_summary_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        if review_kind is not None:
            items = [item for item in items if item.review_kind == review_kind]
        if claimed_by_user_id is not None:
            items = [item for item in items if item.claimed_by_user_id == claimed_by_user_id]
        items.sort(key=lambda item: (item.created_at, item.review_item_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def save_conversation_summary(self, summary: ConversationSemanticSummary) -> ConversationSemanticSummary:
        stored = summary.model_copy(deep=True)
        self._summaries[stored.conversation_summary_id] = stored
        return stored.model_copy(deep=True)

    def get_conversation_summary(self, conversation_summary_id: str) -> ConversationSemanticSummary | None:
        item = self._summaries.get(conversation_summary_id)
        return None if item is None else item.model_copy(deep=True)

    def list_conversation_summaries(
        self,
        organization_id: str,
        *,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ConversationSemanticSummary]:
        items = [item for item in self._summaries.values() if item.organization_id == organization_id]
        if conversation_id is not None:
            items = [item for item in items if item.conversation_id == conversation_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.updated_at, item.conversation_summary_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def save_tag_assignment(self, assignment: TagAssignment) -> TagAssignment:
        stored = assignment.model_copy(deep=True)
        self._assignments[stored.tag_assignment_id] = stored
        return stored.model_copy(deep=True)

    def get_tag_assignment(self, tag_assignment_id: str) -> TagAssignment | None:
        item = self._assignments.get(tag_assignment_id)
        return None if item is None else item.model_copy(deep=True)

    def list_tag_assignments(
        self,
        organization_id: str,
        *,
        conversation_id: str | None = None,
        classification_event_id: str | None = None,
        conversation_summary_id: str | None = None,
        assignment_scope: str | None = None,
        limit: int = 200,
    ) -> list[TagAssignment]:
        items = [item for item in self._assignments.values() if item.organization_id == organization_id]
        if conversation_id is not None:
            items = [item for item in items if item.conversation_id == conversation_id]
        if classification_event_id is not None:
            items = [item for item in items if item.classification_event_id == classification_event_id]
        if conversation_summary_id is not None:
            items = [item for item in items if item.conversation_summary_id == conversation_summary_id]
        if assignment_scope is not None:
            items = [item for item in items if item.assignment_scope == assignment_scope]
        items.sort(key=lambda item: (item.created_at, item.tag_assignment_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def get_conversation_context(self, conversation_id: str) -> ConversationSemanticContext | None:
        return None

    def project_runtime_cache(self, event: TurnClassificationEvent) -> None:
        return None


def _version_from_record(record: IntentTagTaxonomyVersionRecord) -> TaxonomyVersion:
    return TaxonomyVersion(
        taxonomy_version_id=record.taxonomy_version_id,
        organization_id=record.organization_id,
        name=record.name,
        status=record.status,  # type: ignore[arg-type]
        notes=record.notes,
        published_at=record.published_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _intent_from_record(record: IntentDefinitionRecord) -> IntentDefinition:
    return IntentDefinition(
        intent_definition_id=record.intent_definition_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        taxonomy_version_id=record.taxonomy_version_id,
        name=record.name,
        display_name=record.display_name,
        description=record.description,
        category=record.category,
        example_phrases=deepcopy(record.example_phrases_json or []),
        confidence_threshold=record.confidence_threshold,
        priority=record.priority,
        is_active=record.is_active,
        is_deprecated=record.is_deprecated,
        color=record.color,
        icon=record.icon,
        metadata=deepcopy(record.metadata_json or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _tag_from_record(record: TagDefinitionRecord) -> TagDefinition:
    return TagDefinition(
        tag_definition_id=record.tag_definition_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        taxonomy_version_id=record.taxonomy_version_id,
        name=record.name,
        display_name=record.display_name,
        description=record.description,
        tag_kind=record.tag_kind,  # type: ignore[arg-type]
        category=record.category,
        confidence_threshold=record.confidence_threshold,
        apply_scope=record.apply_scope,  # type: ignore[arg-type]
        related_intent_id=record.related_intent_id,
        is_active=record.is_active,
        is_deprecated=record.is_deprecated,
        color=record.color,
        icon=record.icon,
        rule_config=deepcopy(record.rule_config_json or {}),
        metadata=deepcopy(record.metadata_json or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _profile_from_record(record: IntentTagClassifierProfileRecord) -> ClassifierProfile:
    return ClassifierProfile(
        classifier_profile_id=record.classifier_profile_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        adapter_name=record.adapter_name,
        supported_languages=deepcopy(record.supported_languages_json or []),
        taxonomy_mode=record.taxonomy_mode,  # type: ignore[arg-type]
        taxonomy_version_id=record.taxonomy_version_id,
        intent_catalog=deepcopy(record.intent_catalog_json or []),
        tool_catalog=deepcopy(record.tool_catalog_json or []),
        catalog_cache_built_at=record.catalog_cache_built_at,
        policy_profile=deepcopy(record.policy_profile_json or {}),
        profile_metadata=deepcopy(record.profile_metadata_json or {}),
        is_active=record.is_active,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _semantic_webhook_target_from_record(
    record: IntentTagSemanticWebhookTargetRecord,
) -> SemanticSummaryWebhookTarget:
    return SemanticSummaryWebhookTarget(
        webhook_target_id=record.webhook_target_id,
        organization_id=record.organization_id,
        name=record.name,
        url=record.url,
        event_name=record.event_name,
        agent_ids=deepcopy(record.agent_ids_json or []),
        channels=deepcopy(record.channels_json or []),
        signing_secret_ref=record.signing_secret_ref,
        extra_headers=deepcopy(record.extra_headers_json or {}),
        timeout_seconds=record.timeout_seconds,
        max_retries=record.max_retries,
        retry_backoff_seconds=record.retry_backoff_seconds,
        is_active=record.is_active,
        last_attempt_at=record.last_attempt_at,
        last_success_at=record.last_success_at,
        last_failure_at=record.last_failure_at,
        consecutive_failure_count=record.consecutive_failure_count,
        last_error=record.last_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _event_from_record(record: IntentTagClassificationEventRecord) -> TurnClassificationEvent:
    return TurnClassificationEvent(
        classification_event_id=record.classification_event_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        agent_version_id=record.agent_version_id,
        classifier_profile_id=record.classifier_profile_id,
        conversation_id=record.conversation_id,
        turn_trace_id=record.turn_trace_id,
        realtime_event_id=record.realtime_event_id,
        channel=record.channel,  # type: ignore[arg-type]
        provider=record.provider,
        source_kind=record.source_kind,  # type: ignore[arg-type]
        adapter_name=record.adapter_name,
        model_version=record.model_version,
        taxonomy_mode=record.taxonomy_mode,  # type: ignore[arg-type]
        taxonomy_version_id=record.taxonomy_version_id,
        request_payload=deepcopy(record.request_payload_json or {}),
        context_payload=deepcopy(record.context_payload_json or {}),
        decision_payload=deepcopy(record.decision_payload_json or {}),
        intent_name=record.intent_name,
        confidence=record.confidence,
        language=record.language,
        response_language=record.response_language,
        tool_route=record.tool_route,
        slots=deepcopy(record.slots_json or {}),
        signals=deepcopy(record.signals_json or {}),
        created_at=record.created_at,
    )


def _review_item_from_record(record: IntentTagReviewItemRecord) -> ClassificationReviewItem:
    return ClassificationReviewItem(
        review_item_id=record.review_item_id,
        organization_id=record.organization_id,
        classification_event_id=record.classification_event_id,
        conversation_summary_id=record.conversation_summary_id,
        status=record.status,  # type: ignore[arg-type]
        review_kind=record.review_kind,  # type: ignore[arg-type]
        review_disposition=record.review_disposition,  # type: ignore[arg-type]
        review_notes=record.review_notes,
        corrected_payload=deepcopy(record.corrected_payload_json or {}),
        claimed_by_user_id=record.claimed_by_user_id,
        claimed_at=record.claimed_at,
        reviewed_by_user_id=record.reviewed_by_user_id,
        reviewed_at=record.reviewed_at,
        corrected_conversation_summary_id=record.corrected_conversation_summary_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _conversation_context_from_record(record: ConversationRecord) -> ConversationSemanticContext:
    return ConversationSemanticContext(
        organization_id=record.organization_id,
        conversation_id=record.conversation_id,
        agent_id=record.agent_id,
        agent_version_id=record.agent_version_id,
        channel=record.channel,
        status=record.status,
        outcome=record.outcome,
        metadata=deepcopy(record.metadata_json or {}),
        started_at=record.started_at,
        ended_at=record.ended_at,
    )


def _summary_from_record(record: IntentTagConversationSummaryRecord) -> ConversationSemanticSummary:
    return ConversationSemanticSummary(
        conversation_summary_id=record.conversation_summary_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        agent_version_id=record.agent_version_id,
        conversation_id=record.conversation_id,
        summary_version=record.summary_version,
        status=record.status,  # type: ignore[arg-type]
        primary_intent_name=record.primary_intent_name,
        secondary_intents=deepcopy(record.secondary_intents_json or []),
        resolution_status=record.resolution_status,  # type: ignore[arg-type]
        outcome=record.outcome,
        final_language=record.final_language,
        response_language=record.response_language,
        channel=record.channel,  # type: ignore[arg-type]
        requires_human_followup=record.requires_human_followup,
        requires_review=record.requires_review,
        summary_payload=deepcopy(record.summary_payload_json or {}),
        evidence_payload=deepcopy(record.evidence_payload_json or {}),
        generated_from_event_count=record.generated_from_event_count,
        last_event_created_at=record.last_event_created_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _tag_assignment_from_record(record: IntentTagAssignmentRecord) -> TagAssignment:
    return TagAssignment(
        tag_assignment_id=record.tag_assignment_id,
        organization_id=record.organization_id,
        conversation_id=record.conversation_id,
        classification_event_id=record.classification_event_id,
        conversation_summary_id=record.conversation_summary_id,
        tag_definition_id=record.tag_definition_id,
        assignment_scope=record.assignment_scope,  # type: ignore[arg-type]
        assignment_source=record.assignment_source,  # type: ignore[arg-type]
        confidence=record.confidence,
        reason_text=record.reason_text,
        evidence_payload=deepcopy(record.evidence_payload_json or {}),
        is_validated=record.is_validated,
        validated_by_user_id=record.validated_by_user_id,
        validated_at=record.validated_at,
        created_at=record.created_at,
    )


class SQLAlchemyIntentTagsStore:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def save_taxonomy_version(self, version: TaxonomyVersion) -> TaxonomyVersion:
        with self._session_factory.begin() as session:
            record = session.get(IntentTagTaxonomyVersionRecord, version.taxonomy_version_id)
            if record is None:
                record = IntentTagTaxonomyVersionRecord(taxonomy_version_id=version.taxonomy_version_id)
                session.add(record)
            record.organization_id = version.organization_id
            record.name = version.name
            record.status = version.status
            record.notes = version.notes
            record.published_at = version.published_at
            record.created_at = version.created_at
            record.updated_at = version.updated_at
        return version.model_copy(deep=True)

    def get_taxonomy_version(self, taxonomy_version_id: str) -> TaxonomyVersion | None:
        with self._session_factory() as session:
            record = session.get(IntentTagTaxonomyVersionRecord, taxonomy_version_id)
            return None if record is None else _version_from_record(record)

    def list_taxonomy_versions(self, organization_id: str, *, status: str | None = None) -> list[TaxonomyVersion]:
        with self._session_factory() as session:
            query = select(IntentTagTaxonomyVersionRecord).where(
                IntentTagTaxonomyVersionRecord.organization_id == organization_id
            )
            if status is not None:
                query = query.where(IntentTagTaxonomyVersionRecord.status == status)
            query = query.order_by(IntentTagTaxonomyVersionRecord.created_at)
            return [_version_from_record(item) for item in session.execute(query).scalars().all()]

    def save_intent_definition(self, intent: IntentDefinition) -> IntentDefinition:
        with self._session_factory.begin() as session:
            record = session.get(IntentDefinitionRecord, intent.intent_definition_id)
            if record is None:
                record = IntentDefinitionRecord(intent_definition_id=intent.intent_definition_id)
                session.add(record)
            record.organization_id = intent.organization_id
            record.agent_id = intent.agent_id
            record.taxonomy_version_id = intent.taxonomy_version_id
            record.name = intent.name
            record.display_name = intent.display_name
            record.description = intent.description
            record.category = intent.category
            record.example_phrases_json = deepcopy(intent.example_phrases)
            record.confidence_threshold = intent.confidence_threshold
            record.priority = intent.priority
            record.is_active = intent.is_active
            record.is_deprecated = intent.is_deprecated
            record.color = intent.color
            record.icon = intent.icon
            record.metadata_json = deepcopy(intent.metadata)
            record.created_at = intent.created_at
            record.updated_at = intent.updated_at
        return intent.model_copy(deep=True)

    def get_intent_definition(self, intent_definition_id: str) -> IntentDefinition | None:
        with self._session_factory() as session:
            record = session.get(IntentDefinitionRecord, intent_definition_id)
            return None if record is None else _intent_from_record(record)

    def list_intent_definitions(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = True,
    ) -> list[IntentDefinition]:
        with self._session_factory() as session:
            query = select(IntentDefinitionRecord).where(IntentDefinitionRecord.organization_id == organization_id)
            if agent_id is not None:
                query = query.where(IntentDefinitionRecord.agent_id == agent_id)
            if taxonomy_version_id is not None:
                query = query.where(IntentDefinitionRecord.taxonomy_version_id == taxonomy_version_id)
            if not include_inactive:
                query = query.where(IntentDefinitionRecord.is_active.is_(True))
                query = query.where(IntentDefinitionRecord.is_deprecated.is_(False))
            query = query.order_by(IntentDefinitionRecord.priority.desc(), IntentDefinitionRecord.display_name)
            return [_intent_from_record(item) for item in session.execute(query).scalars().all()]

    def save_tag_definition(self, tag: TagDefinition) -> TagDefinition:
        with self._session_factory.begin() as session:
            record = session.get(TagDefinitionRecord, tag.tag_definition_id)
            if record is None:
                record = TagDefinitionRecord(tag_definition_id=tag.tag_definition_id)
                session.add(record)
            record.organization_id = tag.organization_id
            record.agent_id = tag.agent_id
            record.taxonomy_version_id = tag.taxonomy_version_id
            record.name = tag.name
            record.display_name = tag.display_name
            record.description = tag.description
            record.tag_kind = tag.tag_kind
            record.category = tag.category
            record.confidence_threshold = tag.confidence_threshold
            record.apply_scope = tag.apply_scope
            record.related_intent_id = tag.related_intent_id
            record.is_active = tag.is_active
            record.is_deprecated = tag.is_deprecated
            record.color = tag.color
            record.icon = tag.icon
            record.rule_config_json = deepcopy(tag.rule_config)
            record.metadata_json = deepcopy(tag.metadata)
            record.created_at = tag.created_at
            record.updated_at = tag.updated_at
        return tag.model_copy(deep=True)

    def get_tag_definition(self, tag_definition_id: str) -> TagDefinition | None:
        with self._session_factory() as session:
            record = session.get(TagDefinitionRecord, tag_definition_id)
            return None if record is None else _tag_from_record(record)

    def list_tag_definitions(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = True,
    ) -> list[TagDefinition]:
        with self._session_factory() as session:
            query = select(TagDefinitionRecord).where(TagDefinitionRecord.organization_id == organization_id)
            if agent_id is not None:
                query = query.where(TagDefinitionRecord.agent_id == agent_id)
            if taxonomy_version_id is not None:
                query = query.where(TagDefinitionRecord.taxonomy_version_id == taxonomy_version_id)
            if not include_inactive:
                query = query.where(TagDefinitionRecord.is_active.is_(True))
                query = query.where(TagDefinitionRecord.is_deprecated.is_(False))
            query = query.order_by(TagDefinitionRecord.display_name)
            return [_tag_from_record(item) for item in session.execute(query).scalars().all()]

    def save_classifier_profile(self, profile: ClassifierProfile) -> ClassifierProfile:
        with self._session_factory.begin() as session:
            record = session.get(IntentTagClassifierProfileRecord, profile.classifier_profile_id)
            if record is None:
                record = IntentTagClassifierProfileRecord(classifier_profile_id=profile.classifier_profile_id)
                session.add(record)
            record.organization_id = profile.organization_id
            record.agent_id = profile.agent_id
            record.adapter_name = profile.adapter_name
            record.supported_languages_json = deepcopy(profile.supported_languages)
            record.taxonomy_mode = profile.taxonomy_mode
            record.taxonomy_version_id = profile.taxonomy_version_id
            record.intent_catalog_json = deepcopy(profile.intent_catalog)
            record.tool_catalog_json = deepcopy(profile.tool_catalog)
            record.catalog_cache_built_at = profile.catalog_cache_built_at
            record.policy_profile_json = deepcopy(profile.policy_profile)
            record.profile_metadata_json = deepcopy(profile.profile_metadata)
            record.is_active = profile.is_active
            record.created_at = profile.created_at
            record.updated_at = profile.updated_at
        return profile.model_copy(deep=True)

    def get_classifier_profile(self, classifier_profile_id: str) -> ClassifierProfile | None:
        with self._session_factory() as session:
            record = session.get(IntentTagClassifierProfileRecord, classifier_profile_id)
            return None if record is None else _profile_from_record(record)

    def list_classifier_profiles(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        is_active: bool | None = None,
    ) -> list[ClassifierProfile]:
        with self._session_factory() as session:
            query = select(IntentTagClassifierProfileRecord).where(
                IntentTagClassifierProfileRecord.organization_id == organization_id
            )
            if agent_id is not None:
                query = query.where(IntentTagClassifierProfileRecord.agent_id == agent_id)
            if is_active is not None:
                query = query.where(IntentTagClassifierProfileRecord.is_active.is_(is_active))
            query = query.order_by(IntentTagClassifierProfileRecord.updated_at.desc())
            return [_profile_from_record(item) for item in session.execute(query).scalars().all()]

    def save_semantic_webhook_target(self, target: SemanticSummaryWebhookTarget) -> SemanticSummaryWebhookTarget:
        with self._session_factory.begin() as session:
            record = session.get(IntentTagSemanticWebhookTargetRecord, target.webhook_target_id)
            if record is None:
                record = IntentTagSemanticWebhookTargetRecord(webhook_target_id=target.webhook_target_id)
                session.add(record)
            record.organization_id = target.organization_id
            record.name = target.name
            record.url = target.url
            record.event_name = target.event_name
            record.agent_ids_json = deepcopy(target.agent_ids)
            record.channels_json = deepcopy(target.channels)
            record.signing_secret_ref = target.signing_secret_ref
            record.extra_headers_json = deepcopy(target.extra_headers)
            record.timeout_seconds = target.timeout_seconds
            record.max_retries = target.max_retries
            record.retry_backoff_seconds = target.retry_backoff_seconds
            record.is_active = target.is_active
            record.last_attempt_at = target.last_attempt_at
            record.last_success_at = target.last_success_at
            record.last_failure_at = target.last_failure_at
            record.consecutive_failure_count = target.consecutive_failure_count
            record.last_error = target.last_error
            record.created_at = target.created_at
            record.updated_at = target.updated_at
        return target.model_copy(deep=True)

    def get_semantic_webhook_target(self, webhook_target_id: str) -> SemanticSummaryWebhookTarget | None:
        with self._session_factory() as session:
            record = session.get(IntentTagSemanticWebhookTargetRecord, webhook_target_id)
            return None if record is None else _semantic_webhook_target_from_record(record)

    def list_semantic_webhook_targets(
        self,
        organization_id: str,
        *,
        is_active: bool | None = None,
    ) -> list[SemanticSummaryWebhookTarget]:
        with self._session_factory() as session:
            query = select(IntentTagSemanticWebhookTargetRecord).where(
                IntentTagSemanticWebhookTargetRecord.organization_id == organization_id
            )
            if is_active is not None:
                query = query.where(IntentTagSemanticWebhookTargetRecord.is_active.is_(is_active))
            query = query.order_by(IntentTagSemanticWebhookTargetRecord.updated_at.desc())
            return [
                _semantic_webhook_target_from_record(item)
                for item in session.execute(query).scalars().all()
            ]

    def delete_semantic_webhook_target(self, webhook_target_id: str) -> bool:
        with self._session_factory.begin() as session:
            record = session.get(IntentTagSemanticWebhookTargetRecord, webhook_target_id)
            if record is None:
                return False
            session.delete(record)
            return True

    def save_classification_event(self, event: TurnClassificationEvent) -> TurnClassificationEvent:
        with self._session_factory.begin() as session:
            record = session.get(IntentTagClassificationEventRecord, event.classification_event_id)
            if record is None:
                record = IntentTagClassificationEventRecord(classification_event_id=event.classification_event_id)
                session.add(record)
            record.organization_id = event.organization_id
            record.agent_id = event.agent_id
            record.agent_version_id = event.agent_version_id
            record.classifier_profile_id = event.classifier_profile_id
            record.conversation_id = event.conversation_id
            record.turn_trace_id = event.turn_trace_id
            record.realtime_event_id = event.realtime_event_id
            record.channel = event.channel
            record.provider = event.provider
            record.source_kind = event.source_kind
            record.adapter_name = event.adapter_name
            record.model_version = event.model_version
            record.taxonomy_mode = event.taxonomy_mode
            record.taxonomy_version_id = event.taxonomy_version_id
            record.request_payload_json = deepcopy(event.request_payload)
            record.context_payload_json = deepcopy(event.context_payload)
            record.decision_payload_json = deepcopy(event.decision_payload)
            record.intent_name = event.intent_name
            record.confidence = event.confidence
            record.language = event.language
            record.response_language = event.response_language
            record.tool_route = event.tool_route
            record.slots_json = deepcopy(event.slots)
            record.signals_json = deepcopy(event.signals)
            record.created_at = event.created_at
        return event.model_copy(deep=True)

    def get_classification_event(self, classification_event_id: str) -> TurnClassificationEvent | None:
        with self._session_factory() as session:
            record = session.get(IntentTagClassificationEventRecord, classification_event_id)
            return None if record is None else _event_from_record(record)

    def get_classification_event_by_turn_trace_id(
        self,
        turn_trace_id: str,
        *,
        organization_id: str | None = None,
    ) -> TurnClassificationEvent | None:
        with self._session_factory() as session:
            query = select(IntentTagClassificationEventRecord).where(
                IntentTagClassificationEventRecord.turn_trace_id == turn_trace_id
            )
            if organization_id is not None:
                query = query.where(IntentTagClassificationEventRecord.organization_id == organization_id)
            query = query.order_by(IntentTagClassificationEventRecord.created_at.desc()).limit(1)
            record = session.execute(query).scalar_one_or_none()
            return None if record is None else _event_from_record(record)

    def list_classification_events(
        self,
        organization_id: str,
        *,
        conversation_id: str | None = None,
        intent_name: str | None = None,
        limit: int = 100,
    ) -> list[TurnClassificationEvent]:
        with self._session_factory() as session:
            query = select(IntentTagClassificationEventRecord).where(
                IntentTagClassificationEventRecord.organization_id == organization_id
            )
            if conversation_id is not None:
                query = query.where(IntentTagClassificationEventRecord.conversation_id == conversation_id)
            if intent_name is not None:
                query = query.where(IntentTagClassificationEventRecord.intent_name == intent_name)
            query = query.order_by(IntentTagClassificationEventRecord.created_at.desc()).limit(limit)
            return [_event_from_record(item) for item in session.execute(query).scalars().all()]

    def save_review_item(self, review_item: ClassificationReviewItem) -> ClassificationReviewItem:
        with self._session_factory.begin() as session:
            record = session.get(IntentTagReviewItemRecord, review_item.review_item_id)
            if record is None:
                record = IntentTagReviewItemRecord(review_item_id=review_item.review_item_id)
                session.add(record)
            record.organization_id = review_item.organization_id
            record.classification_event_id = review_item.classification_event_id
            record.conversation_summary_id = review_item.conversation_summary_id
            record.status = review_item.status
            record.review_kind = review_item.review_kind
            record.review_disposition = review_item.review_disposition
            record.review_notes = review_item.review_notes
            record.corrected_payload_json = deepcopy(review_item.corrected_payload)
            record.claimed_by_user_id = review_item.claimed_by_user_id
            record.claimed_at = review_item.claimed_at
            record.reviewed_by_user_id = review_item.reviewed_by_user_id
            record.reviewed_at = review_item.reviewed_at
            record.corrected_conversation_summary_id = review_item.corrected_conversation_summary_id
            record.created_at = review_item.created_at
            record.updated_at = review_item.updated_at
        return review_item.model_copy(deep=True)

    def get_review_item(self, review_item_id: str) -> ClassificationReviewItem | None:
        with self._session_factory() as session:
            record = session.get(IntentTagReviewItemRecord, review_item_id)
            return None if record is None else _review_item_from_record(record)

    def list_review_items(
        self,
        organization_id: str,
        *,
        classification_event_id: str | None = None,
        conversation_summary_id: str | None = None,
        status: str | None = None,
        review_kind: str | None = None,
        claimed_by_user_id: str | None = None,
        limit: int = 100,
    ) -> list[ClassificationReviewItem]:
        with self._session_factory() as session:
            query = select(IntentTagReviewItemRecord).where(IntentTagReviewItemRecord.organization_id == organization_id)
            if classification_event_id is not None:
                query = query.where(IntentTagReviewItemRecord.classification_event_id == classification_event_id)
            if conversation_summary_id is not None:
                query = query.where(IntentTagReviewItemRecord.conversation_summary_id == conversation_summary_id)
            if status is not None:
                query = query.where(IntentTagReviewItemRecord.status == status)
            if review_kind is not None:
                query = query.where(IntentTagReviewItemRecord.review_kind == review_kind)
            if claimed_by_user_id is not None:
                query = query.where(IntentTagReviewItemRecord.claimed_by_user_id == claimed_by_user_id)
            query = query.order_by(IntentTagReviewItemRecord.created_at.desc()).limit(limit)
            return [_review_item_from_record(item) for item in session.execute(query).scalars().all()]

    def save_conversation_summary(self, summary: ConversationSemanticSummary) -> ConversationSemanticSummary:
        with self._session_factory.begin() as session:
            record = session.get(IntentTagConversationSummaryRecord, summary.conversation_summary_id)
            if record is None:
                record = IntentTagConversationSummaryRecord(
                    conversation_summary_id=summary.conversation_summary_id
                )
                session.add(record)
            record.organization_id = summary.organization_id
            record.agent_id = summary.agent_id
            record.agent_version_id = summary.agent_version_id
            record.conversation_id = summary.conversation_id
            record.summary_version = summary.summary_version
            record.status = summary.status
            record.primary_intent_name = summary.primary_intent_name
            record.secondary_intents_json = deepcopy(summary.secondary_intents)
            record.resolution_status = summary.resolution_status
            record.outcome = summary.outcome
            record.final_language = summary.final_language
            record.response_language = summary.response_language
            record.channel = summary.channel
            record.requires_human_followup = summary.requires_human_followup
            record.requires_review = summary.requires_review
            record.summary_payload_json = deepcopy(summary.summary_payload)
            record.evidence_payload_json = deepcopy(summary.evidence_payload)
            record.generated_from_event_count = summary.generated_from_event_count
            record.last_event_created_at = summary.last_event_created_at
            record.created_at = summary.created_at
            record.updated_at = summary.updated_at
        return summary.model_copy(deep=True)

    def get_conversation_summary(self, conversation_summary_id: str) -> ConversationSemanticSummary | None:
        with self._session_factory() as session:
            record = session.get(IntentTagConversationSummaryRecord, conversation_summary_id)
            return None if record is None else _summary_from_record(record)

    def list_conversation_summaries(
        self,
        organization_id: str,
        *,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ConversationSemanticSummary]:
        with self._session_factory() as session:
            query = select(IntentTagConversationSummaryRecord).where(
                IntentTagConversationSummaryRecord.organization_id == organization_id
            )
            if conversation_id is not None:
                query = query.where(IntentTagConversationSummaryRecord.conversation_id == conversation_id)
            if status is not None:
                query = query.where(IntentTagConversationSummaryRecord.status == status)
            query = query.order_by(IntentTagConversationSummaryRecord.updated_at.desc()).limit(limit)
            return [_summary_from_record(item) for item in session.execute(query).scalars().all()]

    def save_tag_assignment(self, assignment: TagAssignment) -> TagAssignment:
        with self._session_factory.begin() as session:
            record = session.get(IntentTagAssignmentRecord, assignment.tag_assignment_id)
            if record is None:
                record = IntentTagAssignmentRecord(tag_assignment_id=assignment.tag_assignment_id)
                session.add(record)
            record.organization_id = assignment.organization_id
            record.conversation_id = assignment.conversation_id
            record.classification_event_id = assignment.classification_event_id
            record.conversation_summary_id = assignment.conversation_summary_id
            record.tag_definition_id = assignment.tag_definition_id
            record.assignment_scope = assignment.assignment_scope
            record.assignment_source = assignment.assignment_source
            record.confidence = assignment.confidence
            record.reason_text = assignment.reason_text
            record.evidence_payload_json = deepcopy(assignment.evidence_payload)
            record.is_validated = assignment.is_validated
            record.validated_by_user_id = assignment.validated_by_user_id
            record.validated_at = assignment.validated_at
            record.created_at = assignment.created_at
        return assignment.model_copy(deep=True)

    def get_tag_assignment(self, tag_assignment_id: str) -> TagAssignment | None:
        with self._session_factory() as session:
            record = session.get(IntentTagAssignmentRecord, tag_assignment_id)
            return None if record is None else _tag_assignment_from_record(record)

    def list_tag_assignments(
        self,
        organization_id: str,
        *,
        conversation_id: str | None = None,
        classification_event_id: str | None = None,
        conversation_summary_id: str | None = None,
        assignment_scope: str | None = None,
        limit: int = 200,
    ) -> list[TagAssignment]:
        with self._session_factory() as session:
            query = select(IntentTagAssignmentRecord).where(IntentTagAssignmentRecord.organization_id == organization_id)
            if conversation_id is not None:
                query = query.where(IntentTagAssignmentRecord.conversation_id == conversation_id)
            if classification_event_id is not None:
                query = query.where(IntentTagAssignmentRecord.classification_event_id == classification_event_id)
            if conversation_summary_id is not None:
                query = query.where(IntentTagAssignmentRecord.conversation_summary_id == conversation_summary_id)
            if assignment_scope is not None:
                query = query.where(IntentTagAssignmentRecord.assignment_scope == assignment_scope)
            query = query.order_by(IntentTagAssignmentRecord.created_at.desc()).limit(limit)
            return [_tag_assignment_from_record(item) for item in session.execute(query).scalars().all()]

    def get_conversation_context(self, conversation_id: str) -> ConversationSemanticContext | None:
        with self._session_factory() as session:
            record = session.get(ConversationRecord, conversation_id)
            return None if record is None else _conversation_context_from_record(record)

    def project_runtime_cache(self, event: TurnClassificationEvent) -> None:
        with self._session_factory.begin() as session:
            conversation = session.get(ConversationRecord, event.conversation_id)
            if conversation is not None:
                metadata = deepcopy(conversation.metadata_json or {})
                cache = deepcopy(metadata.get("intent_tags") or {})
                cache.update(
                    {
                        "last_classification_event_id": event.classification_event_id,
                        "last_intent": event.intent_name,
                        "intent_confidence": event.confidence,
                        "language": event.language,
                        "response_language": event.response_language,
                        "tool_route": event.tool_route,
                        "taxonomy_mode": event.taxonomy_mode,
                        "taxonomy_version_id": event.taxonomy_version_id,
                    }
                )
                metadata["intent_tags"] = cache
                conversation.metadata_json = metadata

            if event.turn_trace_id:
                turn_trace = session.get(TurnTraceRecord, event.turn_trace_id)
                if turn_trace is not None:
                    semantic_events = list(turn_trace.semantic_events_json or [])
                    if not any(
                        isinstance(item, dict) and item.get("classification_event_id") == event.classification_event_id
                        for item in semantic_events
                    ):
                        semantic_events.append(
                            {
                                "family": "intent_tags",
                                "name": "turn_classified",
                                "source": "system",
                                "confidence": event.confidence,
                                "classification_event_id": event.classification_event_id,
                                "payload": {
                                    "classification_event_id": event.classification_event_id,
                                    "intent": event.intent_name,
                                    "language": event.language,
                                    "response_language": event.response_language,
                                    "tool_route": event.tool_route,
                                },
                            }
                        )
                        turn_trace.semantic_events_json = semantic_events
