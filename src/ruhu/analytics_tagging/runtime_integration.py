from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ruhu.agent_document import AgentDocument
from ruhu.provider_costs import SQLAlchemyProviderCostStore, build_provider_cost_records
from ruhu.realtime import RealtimeControlPlane
from ruhu.schemas import ConversationState, FactUpdate, RuntimeTurn, RuntimeTurnResult, SemanticEventRecord, ToolCallRecord

from .adapters import IntentTagsClassificationRequest, IntentTagsClassifierRegistry
from .models import ConversationSemanticContext, TurnClassificationDecision
from .runtime import IntentTagsRuntime

_CLASSIFIABLE_TURN_EVENTS = {"user_message", "user_final_transcript"}
_MACHINE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
CLASSIFIER_SEMANTIC_EVENTS_METADATA_KEY = "__ruhu_classifier_semantic_events"
CLASSIFIER_ADAPTER_NAME_METADATA_KEY = "__ruhu_classifier_adapter_name"
CLASSIFIER_MODEL_VERSION_METADATA_KEY = "__ruhu_classifier_model_version"
CLASSIFIER_METADATA_METADATA_KEY = "__ruhu_classifier_metadata"
CLASSIFIER_SLOTS_METADATA_KEY = "__ruhu_classifier_slots"
CLASSIFIER_SIGNALS_METADATA_KEY = "__ruhu_classifier_signals"
CLASSIFIER_LANGUAGE_METADATA_KEY = "__ruhu_classifier_language"
CLASSIFIER_RESPONSE_LANGUAGE_METADATA_KEY = "__ruhu_classifier_response_language"
CLASSIFIER_LANGUAGE_CONFIDENCE_METADATA_KEY = "__ruhu_classifier_language_confidence"


def _string_value(value: object | None) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _language_from_sources(
    turn: RuntimeTurn,
    conversation: ConversationState,
    *,
    kind: str,
    default: str,
) -> str:
    for key in (kind, f"detected_{kind}", "preferred_language"):
        value = _string_value(turn.metadata.get(key))
        if value is not None:
            return value.lower()
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    for key in (kind, f"detected_{kind}", "preferred_language"):
        value = _string_value(metadata.get(key))
        if value is not None:
            return value.lower()
    return default


def _select_intent_event(events: list[SemanticEventRecord]) -> SemanticEventRecord | None:
    ranked = [
        event
        for event in events
        if event.family == "intent_detected" and event.source == "classifier" and _MACHINE_NAME_RE.match(event.name)
    ]
    if not ranked:
        return None
    ranked.sort(key=lambda event: (event.confidence or 0.0, event.name), reverse=True)
    return ranked[0]


def _preclassified_semantic_events(turn: RuntimeTurn) -> list[SemanticEventRecord]:
    payload = turn.metadata.get(CLASSIFIER_SEMANTIC_EVENTS_METADATA_KEY)
    if not isinstance(payload, list):
        return []
    events: list[SemanticEventRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            events.append(SemanticEventRecord.model_validate(item))
        except Exception:
            continue
    return events


def _classifier_metadata_dict(turn: RuntimeTurn, key: str) -> dict[str, Any]:
    payload = turn.metadata.get(key)
    if isinstance(payload, dict):
        return {str(name): value for name, value in payload.items()}
    return {}


def _tool_route(result: RuntimeTurnResult) -> str | None:
    chosen = result.chosen_action.payload if isinstance(result.chosen_action.payload, dict) else {}
    tool = _string_value(chosen.get("tool"))
    if tool is not None:
        return tool
    for call in result.tool_calls:
        tool_ref = _string_value(call.tool_ref)
        if tool_ref is not None:
            return tool_ref
    return None


def _slots(fact_updates: list[FactUpdate]) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    for update in fact_updates:
        if _MACHINE_NAME_RE.match(update.name):
            slots[update.name] = deepcopy(update.value)
    return slots


def _signals(
    *,
    semantic_events: list[SemanticEventRecord],
    tool_calls: list[ToolCallRecord],
    conversation: ConversationState,
) -> dict[str, Any]:
    signals: dict[str, Any] = {}
    if any(event.family == "uncertain_understanding" for event in semantic_events):
        signals["uncertain_understanding"] = True
    if any(event.family == "terminal_requested" for event in semantic_events):
        signals["terminal_requested"] = True
    for call in tool_calls:
        if call.status in {"blocked", "timeout", "error", "confirmation_required"}:
            signals[f"tool_{call.status}"] = True
    if conversation.outcome == "transferred":
        signals["requires_human_followup"] = True
    return signals


def _merge_signals(
    *,
    semantic_events: list[SemanticEventRecord],
    tool_calls: list[ToolCallRecord],
    conversation: ConversationState,
    classifier_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = _signals(
        semantic_events=semantic_events,
        tool_calls=tool_calls,
        conversation=conversation,
    )
    for key, value in (classifier_signals or {}).items():
        if value is None or value is False or value == "" or value == 0:
            continue
        merged[str(key)] = value
    return merged


def _merge_slots(
    *,
    fact_updates: list[FactUpdate],
    classifier_slots: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = deepcopy(classifier_slots or {})
    merged.update(_slots(fact_updates))
    return merged


def _isoformat(value: object | None) -> str | None:
    if value is None or not hasattr(value, "isoformat"):
        return None
    return value.isoformat()


def _conversation_context(conversation: ConversationState, *, organization_id: str) -> ConversationSemanticContext:
    return ConversationSemanticContext(
        organization_id=organization_id,
        conversation_id=conversation.conversation_id,
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        channel=conversation.channel,
        status=conversation.status,
        outcome=conversation.outcome,
        metadata=deepcopy(conversation.metadata),
        started_at=conversation.started_at,
        ended_at=conversation.ended_at,
    )


@dataclass(slots=True)
class IntentTagsRuntimeIntegrator:
    runtime: IntentTagsRuntime
    classifier_registry: IntentTagsClassifierRegistry
    realtime_control_plane: RealtimeControlPlane | None = None
    provider_cost_store: SQLAlchemyProviderCostStore | None = None
    default_language: str = "und"

    def handle_result(
        self,
        *,
        conversation: ConversationState,
        result: RuntimeTurnResult,
        agent_document: AgentDocument | None = None,
        agent_name: str | None = None,
        turn: RuntimeTurn | None = None,
    ) -> None:
        if conversation.mode != "live":
            return
        organization_id = conversation.organization_id
        if organization_id is None:
            # Enterprise posture: every live conversation must be tenant-scoped.
            # Skip intent-tag recording rather than attributing to a sentinel.
            return
        if turn is not None and turn.event_type in _CLASSIFIABLE_TURN_EVENTS:
            self._record_turn_classification(
                organization_id=organization_id,
                conversation=conversation,
                agent_document=agent_document,
                agent_name=agent_name,
                turn=turn,
                result=result,
            )
        if conversation.status == "ended":
            self._finalize_conversation_summary(
                organization_id=organization_id,
                conversation=conversation,
            )

    def _record_turn_classification(
        self,
        *,
        organization_id: str,
        conversation: ConversationState,
        agent_document: AgentDocument | None,
        agent_name: str | None,
        turn: RuntimeTurn,
        result: RuntimeTurnResult,
    ) -> None:
        if result.chosen_action.reason == "duplicate_dedupe_key":
            return
        if self.runtime.store.get_classification_event_by_turn_trace_id(
            result.trace_id,
            organization_id=organization_id,
        ) is not None:
            return

        resolved_profile = self.runtime.profile_service.resolve_profile(
            organization_id,
            agent_id=conversation.agent_id,
        )
        classifier_result = None
        classifier_semantic_events = _preclassified_semantic_events(turn)
        adapter_name = _string_value(turn.metadata.get(CLASSIFIER_ADAPTER_NAME_METADATA_KEY)) or "classifier"
        model_version = _string_value(turn.metadata.get(CLASSIFIER_MODEL_VERSION_METADATA_KEY)) or f"{adapter_name}-runtime-v1"
        classifier_metadata = _classifier_metadata_dict(turn, CLASSIFIER_METADATA_METADATA_KEY)

        if not classifier_semantic_events and agent_document is not None:
            classifier_result = self.classifier_registry.classify(
                IntentTagsClassificationRequest(
                    agent_id=conversation.agent_id,
                    agent_name=agent_name or conversation.agent_id,
                    schema_version=agent_document.version,
                    agent_document=agent_document,
                    step=agent_document.step_by_id(result.step_before),
                    conversation=conversation,
                    turn=turn,
                    result=result,
                    resolved_profile=resolved_profile,
                )
            )
            classifier_semantic_events = list(classifier_result.semantic_events)
            adapter_name = classifier_result.adapter_name
            model_version = classifier_result.model_version
            classifier_metadata = dict(classifier_result.metadata)

        intent_event = _select_intent_event(classifier_semantic_events)
        if intent_event is None:
            return

        resolved_profile = resolved_profile.model_copy(update={"adapter_name": adapter_name})
        inferred_language = _string_value(intent_event.payload.get("language"))
        inferred_response_language = _string_value(intent_event.payload.get("response_language"))
        classifier_slots = _classifier_metadata_dict(turn, CLASSIFIER_SLOTS_METADATA_KEY)
        classifier_signals = _classifier_metadata_dict(turn, CLASSIFIER_SIGNALS_METADATA_KEY)
        classifier_language_confidence = turn.metadata.get(CLASSIFIER_LANGUAGE_CONFIDENCE_METADATA_KEY)
        if not isinstance(classifier_language_confidence, (int, float)):
            classifier_language_confidence = None
        language = (
            (None if classifier_result is None else _string_value(classifier_result.language))
            or _string_value(turn.metadata.get(CLASSIFIER_LANGUAGE_METADATA_KEY))
            or inferred_language
        ) or _language_from_sources(turn, conversation, kind="language", default=self.default_language)
        response_language = (
            (None if classifier_result is None else _string_value(classifier_result.response_language))
            or _string_value(turn.metadata.get(CLASSIFIER_RESPONSE_LANGUAGE_METADATA_KEY))
            or inferred_response_language
        ) or _language_from_sources(turn, conversation, kind="response_language", default=language)
        decision = TurnClassificationDecision(
            intent_name=intent_event.name,
            confidence=intent_event.confidence or 0.5,
            language=language,
            response_language=response_language,
            tool_route=(None if classifier_result is None else _string_value(classifier_result.tool_route))
            or _tool_route(result),
            slots=_merge_slots(
                fact_updates=result.fact_updates,
                classifier_slots=classifier_slots if classifier_slots else (
                    None if classifier_result is None else classifier_result.slots
                ),
            ),
            signals=_merge_signals(
                semantic_events=classifier_semantic_events,
                tool_calls=result.tool_calls,
                conversation=conversation,
                classifier_signals=classifier_signals if classifier_signals else (
                    None if classifier_result is None else classifier_result.signals
                ),
            ),
        )
        event, _ = self.runtime.turn_service.record_event(
            organization_id=organization_id,
            conversation_id=conversation.conversation_id,
            channel=turn.channel,
            decision=decision,
            resolved_profile=resolved_profile,
            agent_id=conversation.agent_id,
            agent_version_id=conversation.agent_version_id,
            turn_trace_id=result.trace_id,
            provider=_string_value(turn.metadata.get("provider")),
            source_kind="runtime",
            model_version=model_version,
            request_payload={
                "text": turn.text,
                "event_type": turn.event_type,
                "modality": turn.modality,
                "metadata": deepcopy(turn.metadata),
            },
            context_payload={
                "step_before": result.step_before,
                "step_after": result.step_after,
                "conversation_facts": deepcopy(conversation.facts),
                "resolved_profile": resolved_profile.model_dump(mode="json"),
                "classifier_adapter_name": adapter_name,
                "classifier_metadata": classifier_metadata,
                "classifier_language_confidence": (
                    classifier_language_confidence
                    if classifier_language_confidence is not None
                    else (None if classifier_result is None else classifier_result.language_confidence)
                ),
                "classifier_semantic_event_keys": [event.key for event in classifier_semantic_events],
                "effective_intent_catalog": deepcopy(resolved_profile.effective_intent_catalog),
                "effective_tool_catalog": deepcopy(resolved_profile.effective_tool_catalog),
                "semantic_event_keys": [event.key for event in result.semantic_events],
                "tool_calls": [call.model_dump(mode="json") for call in result.tool_calls],
                "fact_updates": [update.model_dump(mode="json") for update in result.fact_updates],
                "chosen_action": result.chosen_action.model_dump(mode="json"),
            },
            apply_runtime_cache=True,
        )
        self._record_classifier_provider_costs(
            organization_id=organization_id,
            conversation=conversation,
            result=result,
            classifier_result=classifier_result,
        )
        self.runtime.tagging_service.assign_turn_tags(event)

    def _finalize_conversation_summary(
        self,
        *,
        organization_id: str,
        conversation: ConversationState,
    ) -> None:
        existing = self.runtime.store.list_conversation_summaries(
            organization_id,
            conversation_id=conversation.conversation_id,
            limit=20,
        )
        if any(item.status in {"final", "corrected"} for item in existing):
            return

        summary = self.runtime.summary_service.rollup_conversation(
            organization_id=organization_id,
            conversation_id=conversation.conversation_id,
            conversation_context=_conversation_context(conversation, organization_id=organization_id),
            target_status="final",
        )
        assignments = self.runtime.tagging_service.assign_summary_tags(summary)
        review_item_id: str | None = None
        if not summary.requires_review:
            self._emit_summary_finalized_event(
                organization_id=organization_id,
                conversation=conversation,
                summary=summary,
                assignments=assignments,
                review_item_id=review_item_id,
            )
            return
        existing_reviews = self.runtime.store.list_review_items(
            organization_id,
            conversation_summary_id=summary.conversation_summary_id,
            limit=1,
        )
        if existing_reviews:
            review_item_id = existing_reviews[0].review_item_id
            self._emit_summary_finalized_event(
                organization_id=organization_id,
                conversation=conversation,
                summary=summary,
                assignments=assignments,
                review_item_id=review_item_id,
            )
            return
        review_item = self.runtime.review_service.create_review_item(
            organization_id=organization_id,
            review_kind="summary_correction",
            conversation_summary_id=summary.conversation_summary_id,
            review_notes="Auto-created during runtime summary finalization.",
        )
        review_item_id = review_item.review_item_id
        self._emit_summary_finalized_event(
            organization_id=organization_id,
            conversation=conversation,
            summary=summary,
            assignments=assignments,
            review_item_id=review_item_id,
        )

    def _emit_summary_finalized_event(
        self,
        *,
        organization_id: str,
        conversation: ConversationState,
        summary: Any,
        assignments: list[Any],
        review_item_id: str | None,
    ) -> None:
        if self.realtime_control_plane is None:
            return
        tags_by_id = {
            tag.tag_definition_id: tag.name
            for tag in self.runtime.taxonomy_service.list_effective_tags(
                organization_id,
                agent_id=summary.agent_id,
                include_inactive=False,
            )
        }
        tag_names = sorted(
            {
                tags_by_id.get(item.tag_definition_id)
                for item in assignments
                if tags_by_id.get(item.tag_definition_id)
            }
        )
        self.realtime_control_plane.events.append(
            conversation_id=conversation.conversation_id,
            organization_id=organization_id,
            family="semantic_summary",
            name="finalized",
            payload={
                "conversation_summary_id": summary.conversation_summary_id,
                "agent_id": summary.agent_id,
                "agent_version_id": summary.agent_version_id,
                "summary_status": summary.status,
                "primary_intent_name": summary.primary_intent_name,
                "secondary_intents": deepcopy(summary.secondary_intents),
                "resolution_status": summary.resolution_status,
                "outcome": summary.outcome,
                "channel": summary.channel,
                "requires_human_followup": summary.requires_human_followup,
                "requires_review": summary.requires_review,
                "generated_from_event_count": summary.generated_from_event_count,
                "last_event_created_at": _isoformat(summary.last_event_created_at),
                "tag_names": tag_names,
                "review_item_id": review_item_id,
            },
            actor_type="system",
            visibility="internal",
            causation_id=summary.conversation_summary_id,
            correlation_id=summary.conversation_summary_id,
            outbox_topic="outbound_webhooks.publication",
        )

    def _record_classifier_provider_costs(
        self,
        *,
        organization_id: str,
        conversation: ConversationState,
        result: RuntimeTurnResult,
        classifier_result: Any,
    ) -> None:
        if self.provider_cost_store is None or classifier_result is None:
            return
        records = build_provider_cost_records(
            provider="intent_tags_classifier",
            payload=getattr(classifier_result, "provider_cost_payload", None),
            organization_id=organization_id,
            conversation_id=conversation.conversation_id,
            turn_trace_id=result.trace_id,
            default_cost_type="classifier_inference",
        )
        if not records:
            return
        self.provider_cost_store.save_all(records)
        if self.realtime_control_plane is None:
            return
        for record in records:
            self.realtime_control_plane.events.append(
                conversation_id=record.conversation_id or conversation.conversation_id,
                organization_id=record.organization_id,
                family="provider",
                name="cost_recorded",
                payload={
                    "provider": record.provider,
                    "cost_type": record.cost_type,
                    "amount_usd": record.amount_usd,
                    "reference_key": record.reference_key,
                    "turn_trace_id": record.turn_trace_id,
                },
                actor_type="system",
                visibility="internal",
                causation_id=result.trace_id,
                correlation_id=result.trace_id,
                outbox_topic="conversation_projection",
            )
