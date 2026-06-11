from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any

from .models import (
    ClassificationReviewItem,
    ClassifierProfile,
    ConversationSemanticContext,
    ConversationSemanticSummary,
    EffectiveConversationSummary,
    EffectiveTurnClassification,
    IntentDefinition,
    ResolvedClassifierProfile,
    ReviewDisposition,
    ReviewKind,
    TagAssignment,
    TagDefinition,
    TaxonomyVersion,
    TurnClassificationDecision,
    TurnClassificationEvent,
    utc_now,
)
from .store import IntentTagsStore


def _catalog_entry_from_intent(intent: IntentDefinition) -> dict[str, Any]:
    return {
        "id": intent.intent_definition_id,
        "name": intent.name,
        "display_name": intent.display_name,
        "description": intent.description,
        "category": intent.category,
        "confidence_threshold": intent.confidence_threshold,
        "priority": intent.priority,
        "example_phrases": list(intent.example_phrases),
    }


class TaxonomyService:
    def __init__(self, store: IntentTagsStore):
        self.store = store

    def save_taxonomy_version(self, version: TaxonomyVersion) -> TaxonomyVersion:
        return self.store.save_taxonomy_version(version.model_copy(update={"updated_at": utc_now()}))

    def publish_taxonomy_version(self, taxonomy_version_id: str) -> TaxonomyVersion:
        existing = self.store.get_taxonomy_version(taxonomy_version_id)
        if existing is None:
            raise ValueError(f"taxonomy version not found: {taxonomy_version_id}")
        now = utc_now()
        updated = existing.model_copy(update={"status": "published", "published_at": now, "updated_at": now})
        return self.store.save_taxonomy_version(updated)

    def save_intent_definition(self, intent: IntentDefinition) -> IntentDefinition:
        self._assert_unique_intent_name(intent)
        return self.store.save_intent_definition(intent.model_copy(update={"updated_at": utc_now()}))

    def save_tag_definition(self, tag: TagDefinition) -> TagDefinition:
        self._assert_unique_tag_name(tag)
        return self.store.save_tag_definition(tag.model_copy(update={"updated_at": utc_now()}))

    def list_effective_intents(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[IntentDefinition]:
        intents = self.store.list_intent_definitions(
            organization_id,
            taxonomy_version_id=taxonomy_version_id,
            include_inactive=include_inactive,
        )
        candidates = [
            item
            for item in intents
            if item.agent_id is None or (agent_id is not None and item.agent_id == agent_id)
        ]
        return self._dedupe_effective_intents(candidates)

    def list_effective_tags(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[TagDefinition]:
        tags = self.store.list_tag_definitions(
            organization_id,
            taxonomy_version_id=taxonomy_version_id,
            include_inactive=include_inactive,
        )
        candidates = [
            item
            for item in tags
            if item.agent_id is None or (agent_id is not None and item.agent_id == agent_id)
        ]
        return self._dedupe_effective_tags(candidates)

    def build_intent_catalog(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
    ) -> list[dict[str, Any]]:
        intents = self.list_effective_intents(
            organization_id,
            agent_id=agent_id,
            taxonomy_version_id=taxonomy_version_id,
            include_inactive=False,
        )
        return [_catalog_entry_from_intent(item) for item in intents]

    def _assert_unique_intent_name(self, intent: IntentDefinition) -> None:
        existing = self.store.list_intent_definitions(
            intent.organization_id,
            agent_id=intent.agent_id,
            taxonomy_version_id=intent.taxonomy_version_id,
            include_inactive=True,
        )
        for item in existing:
            if item.intent_definition_id != intent.intent_definition_id and item.name == intent.name:
                raise ValueError(
                    f"intent name '{intent.name}' already exists for organization={intent.organization_id}, "
                    f"agent={intent.agent_id}, taxonomy_version={intent.taxonomy_version_id}"
                )

    def _assert_unique_tag_name(self, tag: TagDefinition) -> None:
        existing = self.store.list_tag_definitions(
            tag.organization_id,
            agent_id=tag.agent_id,
            taxonomy_version_id=tag.taxonomy_version_id,
            include_inactive=True,
        )
        for item in existing:
            if item.tag_definition_id != tag.tag_definition_id and item.name == tag.name:
                raise ValueError(
                    f"tag name '{tag.name}' already exists for organization={tag.organization_id}, "
                    f"agent={tag.agent_id}, taxonomy_version={tag.taxonomy_version_id}"
                )

    @staticmethod
    def _dedupe_effective_intents(intents: list[IntentDefinition]) -> list[IntentDefinition]:
        ordered = sorted(
            intents,
            key=lambda item: (
                item.agent_id is not None,
                item.priority,
                item.updated_at,
                item.intent_definition_id,
            ),
            reverse=True,
        )
        by_name: dict[str, IntentDefinition] = {}
        for item in ordered:
            by_name.setdefault(item.name, item)
        return sorted(by_name.values(), key=lambda item: (-item.priority, item.display_name, item.name))

    @staticmethod
    def _dedupe_effective_tags(tags: list[TagDefinition]) -> list[TagDefinition]:
        ordered = sorted(
            tags,
            key=lambda item: (
                item.agent_id is not None,
                item.updated_at,
                item.tag_definition_id,
            ),
            reverse=True,
        )
        by_name: dict[str, TagDefinition] = {}
        for item in ordered:
            by_name.setdefault(item.name, item)
        return sorted(by_name.values(), key=lambda item: (item.display_name, item.name))


class ClassifierProfileService:
    def __init__(self, store: IntentTagsStore, taxonomy_service: TaxonomyService, *, default_adapter_name: str = "ruhu-general"):
        self.store = store
        self.taxonomy_service = taxonomy_service
        self.default_adapter_name = default_adapter_name

    def save_profile(self, profile: ClassifierProfile) -> ClassifierProfile:
        now = utc_now()
        stored = profile.model_copy(update={"updated_at": now})
        if stored.is_active:
            existing_profiles = self.store.list_classifier_profiles(
                stored.organization_id,
                agent_id=stored.agent_id,
                is_active=True,
            )
            for item in existing_profiles:
                if item.classifier_profile_id == stored.classifier_profile_id:
                    continue
                self.store.save_classifier_profile(item.model_copy(update={"is_active": False, "updated_at": now}))
        return self.store.save_classifier_profile(stored)

    def get_profile(self, classifier_profile_id: str) -> ClassifierProfile | None:
        return self.store.get_classifier_profile(classifier_profile_id)

    def list_profiles(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        is_active: bool | None = None,
    ) -> list[ClassifierProfile]:
        return self.store.list_classifier_profiles(organization_id, agent_id=agent_id, is_active=is_active)

    def mark_profiles_stale(self, organization_id: str, *, agent_id: str | None = None) -> list[ClassifierProfile]:
        now = utc_now()
        updated_profiles: list[ClassifierProfile] = []
        profiles = self.store.list_classifier_profiles(organization_id, is_active=True)
        for profile in profiles:
            if profile.taxonomy_mode != "cached_live":
                continue
            if agent_id is not None and profile.agent_id != agent_id:
                continue
            updated = profile.model_copy(
                update={
                    "intent_catalog": [],
                    "tool_catalog": list(profile.tool_catalog),
                    "catalog_cache_built_at": None,
                    "updated_at": now,
                }
            )
            updated_profiles.append(self.store.save_classifier_profile(updated))
        return updated_profiles

    def rebuild_profile_cache(
        self,
        classifier_profile_id: str,
        *,
        agent_id: str | None = None,
        live_tool_catalog: list[dict[str, Any]] | None = None,
    ) -> ClassifierProfile:
        profile = self.store.get_classifier_profile(classifier_profile_id)
        if profile is None:
            raise ValueError(f"classifier profile not found: {classifier_profile_id}")
        effective_agent_id = agent_id if agent_id is not None else profile.agent_id
        rebuilt = self._rebuild_cached_live_profile(profile, agent_id=effective_agent_id, live_tool_catalog=live_tool_catalog)
        return self.store.save_classifier_profile(rebuilt)

    def resolve_profile(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        live_tool_catalog: list[dict[str, Any]] | None = None,
    ) -> ResolvedClassifierProfile:
        selected = self._select_profile(organization_id, agent_id=agent_id)
        if selected is None:
            effective_intents = self.taxonomy_service.build_intent_catalog(organization_id, agent_id=agent_id)
            return ResolvedClassifierProfile(
                classifier_profile_id=None,
                organization_id=organization_id,
                agent_id=agent_id,
                adapter_name=self.default_adapter_name,
                taxonomy_mode="live",
                effective_intent_catalog=effective_intents,
                effective_tool_catalog=deepcopy(live_tool_catalog or []),
                source="default_live",
            )

        profile = selected
        if profile.taxonomy_mode == "cached_live":
            rebuilt = self._rebuild_cached_live_profile(profile, agent_id=agent_id, live_tool_catalog=live_tool_catalog)
            if rebuilt.model_dump() != profile.model_dump():
                profile = self.store.save_classifier_profile(rebuilt)

        effective_intents = self._resolve_effective_intent_catalog(profile, agent_id=agent_id)
        effective_tools = self._resolve_effective_tool_catalog(profile, live_tool_catalog=live_tool_catalog)
        return ResolvedClassifierProfile(
            classifier_profile_id=profile.classifier_profile_id,
            organization_id=profile.organization_id,
            agent_id=agent_id,
            adapter_name=profile.adapter_name,
            supported_languages=list(profile.supported_languages),
            taxonomy_mode=profile.taxonomy_mode,
            taxonomy_version_id=profile.taxonomy_version_id,
            effective_intent_catalog=effective_intents,
            effective_tool_catalog=effective_tools,
            policy_profile=deepcopy(profile.policy_profile),
            profile_metadata=deepcopy(profile.profile_metadata),
            catalog_cache_built_at=profile.catalog_cache_built_at,
            source="active_profile",
        )

    def _select_profile(self, organization_id: str, *, agent_id: str | None = None) -> ClassifierProfile | None:
        profiles = self.store.list_classifier_profiles(organization_id, is_active=True)
        exact_match: list[ClassifierProfile] = []
        org_default: list[ClassifierProfile] = []
        for profile in profiles:
            if agent_id is not None and profile.agent_id == agent_id:
                exact_match.append(profile)
            elif profile.agent_id is None:
                org_default.append(profile)
        if exact_match:
            return exact_match[0]
        if org_default:
            return org_default[0]
        return None

    def _resolve_effective_intent_catalog(self, profile: ClassifierProfile, *, agent_id: str | None) -> list[dict[str, Any]]:
        if profile.taxonomy_mode == "live":
            return self.taxonomy_service.build_intent_catalog(profile.organization_id, agent_id=agent_id)
        if profile.taxonomy_mode == "pinned":
            if not profile.taxonomy_version_id:
                raise ValueError("pinned profile missing taxonomy_version_id")
            return self.taxonomy_service.build_intent_catalog(
                profile.organization_id,
                agent_id=agent_id,
                taxonomy_version_id=profile.taxonomy_version_id,
            )
        return deepcopy(profile.intent_catalog)

    @staticmethod
    def _resolve_effective_tool_catalog(
        profile: ClassifierProfile,
        *,
        live_tool_catalog: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        if live_tool_catalog is not None:
            return deepcopy(live_tool_catalog)
        return deepcopy(profile.tool_catalog)

    def _rebuild_cached_live_profile(
        self,
        profile: ClassifierProfile,
        *,
        agent_id: str | None,
        live_tool_catalog: list[dict[str, Any]] | None,
    ) -> ClassifierProfile:
        expected_intents = self.taxonomy_service.build_intent_catalog(profile.organization_id, agent_id=agent_id)
        expected_tools = deepcopy(live_tool_catalog) if live_tool_catalog is not None else deepcopy(profile.tool_catalog)
        if profile.intent_catalog == expected_intents and profile.tool_catalog == expected_tools and profile.catalog_cache_built_at:
            return profile
        return profile.model_copy(
            update={
                "intent_catalog": expected_intents,
                "tool_catalog": expected_tools,
                "catalog_cache_built_at": utc_now(),
                "updated_at": utc_now(),
            }
        )


class TurnClassificationService:
    def __init__(self, store: IntentTagsStore, *, low_confidence_threshold: float = 0.6):
        self.store = store
        self.low_confidence_threshold = low_confidence_threshold

    def record_event(
        self,
        *,
        organization_id: str,
        conversation_id: str,
        channel: str,
        decision: TurnClassificationDecision,
        resolved_profile: ResolvedClassifierProfile | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
        turn_trace_id: str | None = None,
        realtime_event_id: str | None = None,
        provider: str | None = None,
        source_kind: str = "runtime",
        model_version: str = "classifier",
        request_payload: dict[str, Any] | None = None,
        context_payload: dict[str, Any] | None = None,
        apply_runtime_cache: bool = False,
        force_review_kind: ReviewKind | None = None,
        review_notes: str | None = None,
    ) -> tuple[TurnClassificationEvent, ClassificationReviewItem | None]:
        adapter_name = resolved_profile.adapter_name if resolved_profile else "unknown"
        event = TurnClassificationEvent(
            organization_id=organization_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            classifier_profile_id=resolved_profile.classifier_profile_id if resolved_profile else None,
            conversation_id=conversation_id,
            turn_trace_id=turn_trace_id,
            realtime_event_id=realtime_event_id,
            channel=channel,
            provider=provider,
            source_kind=source_kind,  # type: ignore[arg-type]
            adapter_name=adapter_name,
            model_version=model_version,
            taxonomy_mode=resolved_profile.taxonomy_mode if resolved_profile else "live",
            taxonomy_version_id=resolved_profile.taxonomy_version_id if resolved_profile else None,
            request_payload=deepcopy(request_payload or {}),
            context_payload=deepcopy(context_payload or {}),
            decision_payload=decision.model_dump(exclude_none=True),
            intent_name=decision.intent_name,
            confidence=decision.confidence,
            language=decision.language,
            response_language=decision.response_language,
            tool_route=decision.tool_route,
            slots=deepcopy(decision.slots),
            signals=deepcopy(decision.signals),
        )
        saved_event = self.store.save_classification_event(event)
        if apply_runtime_cache:
            self.store.project_runtime_cache(saved_event)

        review_item: ClassificationReviewItem | None = None
        if force_review_kind is not None or saved_event.confidence < self.low_confidence_threshold:
            review_item = self.store.save_review_item(
                ClassificationReviewItem(
                    organization_id=organization_id,
                    classification_event_id=saved_event.classification_event_id,
                    review_kind=force_review_kind or "low_confidence_turn",
                    review_notes=review_notes,
                )
            )
        return saved_event, review_item


def _safe_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "high", "required"}
    return bool(value)


def _ranked_counter_items(counter: Counter[str], *, limit: int = 10) -> list[dict[str, Any]]:
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [{"name": name, "count": count} for name, count in items[:limit]]


def _normalize_secondary_intents(intent_scores: dict[str, float], primary_intent_name: str | None) -> list[dict[str, Any]]:
    ranked = sorted(intent_scores.items(), key=lambda item: (-item[1], item[0]))
    secondary: list[dict[str, Any]] = []
    top_score = ranked[0][1] if ranked else 0.0
    for name, score in ranked:
        if name == primary_intent_name:
            continue
        if top_score > 0 and score < top_score * 0.3 and len(secondary) >= 1:
            continue
        secondary.append({"intent_name": name, "score": round(score, 4)})
    return secondary[:5]


def _resolve_summary_status(status: str | None, outcome: str | None) -> str | None:
    if outcome == "resolved":
        return "resolved"
    if outcome == "transferred":
        return "escalated"
    if outcome in {"callback_scheduled", "follow_up_required", "voicemail"}:
        return "follow_up_required"
    if outcome == "abandoned":
        return "abandoned"
    if outcome == "failed":
        return "failed"
    if status == "active":
        return "unresolved"
    if status == "ended":
        return "unknown"
    return None


def _latest_resolved_review(
    items: list[ClassificationReviewItem],
    *,
    corrected_only: bool = False,
) -> ClassificationReviewItem | None:
    candidates = [item for item in items if item.status == "resolved"]
    if corrected_only:
        candidates = [item for item in candidates if item.review_disposition == "corrected"]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.updated_at, item.review_item_id), reverse=True)
    return candidates[0]


def _apply_turn_review_correction(
    event: TurnClassificationEvent,
    review_item: ClassificationReviewItem | None,
) -> TurnClassificationEvent:
    if review_item is None or review_item.review_disposition != "corrected":
        return event
    payload = dict(review_item.corrected_payload or {})
    allowed = {
        "intent_name",
        "confidence",
        "language",
        "response_language",
        "tool_route",
        "slots",
        "signals",
    }
    updates: dict[str, Any] = {}
    decision_updates: dict[str, Any] = {}
    for field in allowed:
        if field not in payload:
            continue
        value = deepcopy(payload[field])
        updates[field] = value
        decision_updates[field] = value
    if not updates:
        return event
    decision_payload = deepcopy(event.decision_payload)
    decision_payload.update(decision_updates)
    updates["decision_payload"] = decision_payload
    return event.model_copy(update=updates)


class ConversationSummaryService:
    def __init__(self, store: IntentTagsStore, *, low_confidence_threshold: float = 0.6):
        self.store = store
        self.low_confidence_threshold = low_confidence_threshold

    def rollup_conversation(
        self,
        *,
        organization_id: str,
        conversation_id: str,
        conversation_context: ConversationSemanticContext | None = None,
        summary_version: int = 1,
        target_status: str = "final",
    ) -> ConversationSemanticSummary:
        context = conversation_context or self.store.get_conversation_context(conversation_id)
        events = list(
            reversed(
                self.store.list_classification_events(
                    organization_id,
                    conversation_id=conversation_id,
                    limit=1000,
                )
            )
        )
        events = self._apply_turn_review_corrections(organization_id, events)
        if target_status == "final" and context is not None and context.status != "ended":
            raise ValueError("final summaries require ended conversations")
        if context is None and not events:
            raise ValueError("conversation summary requires either conversation context or classification events")

        existing_finals = self.store.list_conversation_summaries(
            organization_id,
            conversation_id=conversation_id,
            status="final",
            limit=20,
        )
        for existing in existing_finals:
            if existing.summary_version != summary_version:
                continue
            if existing.status != "superseded":
                self.store.save_conversation_summary(
                    existing.model_copy(update={"status": "superseded", "updated_at": utc_now()})
                )

        channel = (
            context.channel
            if context is not None and context.channel is not None
            else (events[-1].channel if events else None)
        )
        if channel is None:
            raise ValueError("channel is required to build a conversation summary")

        intent_scores: dict[str, float] = defaultdict(float)
        intent_counts: Counter[str] = Counter()
        signal_counts: Counter[str] = Counter()
        tool_route_counts: Counter[str] = Counter()
        language_counts: Counter[str] = Counter()
        response_language_counts: Counter[str] = Counter()
        low_confidence_count = 0

        for index, event in enumerate(events):
            recency_weight = 1.0 + ((index + 1) / max(len(events), 1)) * 0.15
            intent_scores[event.intent_name] += event.confidence * recency_weight
            intent_counts[event.intent_name] += 1
            language_counts[event.language] += 1
            response_language_counts[event.response_language] += 1
            if event.tool_route:
                tool_route_counts[event.tool_route] += 1
            if event.confidence < self.low_confidence_threshold:
                low_confidence_count += 1
            for signal_name, signal_value in (event.signals or {}).items():
                if _safe_truthy(signal_value):
                    signal_counts[signal_name] += 1

        primary_intent_name = None
        if intent_scores:
            primary_intent_name = max(
                intent_scores.items(),
                key=lambda item: (item[1], intent_counts[item[0]], item[0]),
            )[0]
        secondary_intents = _normalize_secondary_intents(intent_scores, primary_intent_name)

        outcome = context.outcome if context is not None else None
        conversation_status = context.status if context is not None else None
        resolution_status = _resolve_summary_status(conversation_status, outcome)
        requires_human_followup = outcome in {
            "transferred",
            "callback_scheduled",
            "follow_up_required",
            "voicemail",
        } or any(
            signal_counts.get(name, 0) > 0
            for name in ("human_followup_required", "requires_human_followup", "handoff_requested")
        )
        requires_review = low_confidence_count > 0 or primary_intent_name is None
        final_language = None
        response_language = None
        if language_counts:
            final_language = max(language_counts.items(), key=lambda item: (item[1], item[0]))[0]
        if response_language_counts:
            response_language = max(response_language_counts.items(), key=lambda item: (item[1], item[0]))[0]

        evidence_payload = {
            "conversation_status": conversation_status,
            "conversation_outcome": outcome,
            "classification_event_ids": [event.classification_event_id for event in events],
            "intent_scores": {name: round(score, 4) for name, score in intent_scores.items()},
            "intent_counts": dict(intent_counts),
            "signal_counts": dict(signal_counts),
            "tool_route_counts": dict(tool_route_counts),
            "low_confidence_event_count": low_confidence_count,
        }
        summary_payload = {
            "top_intents": [
                {"intent_name": name, "score": round(score, 4), "count": intent_counts[name]}
                for name, score in sorted(intent_scores.items(), key=lambda item: (-item[1], item[0]))[:5]
            ],
            "top_signals": _ranked_counter_items(signal_counts),
            "top_tool_routes": _ranked_counter_items(tool_route_counts),
        }
        now = utc_now()
        summary = ConversationSemanticSummary(
            organization_id=organization_id,
            agent_id=context.agent_id if context is not None else (events[-1].agent_id if events else None),
            agent_version_id=context.agent_version_id if context is not None else (events[-1].agent_version_id if events else None),
            conversation_id=conversation_id,
            summary_version=summary_version,
            status=target_status,  # type: ignore[arg-type]
            primary_intent_name=primary_intent_name,
            secondary_intents=secondary_intents,
            resolution_status=resolution_status,  # type: ignore[arg-type]
            outcome=outcome,
            final_language=final_language,
            response_language=response_language,
            channel=channel,
            requires_human_followup=requires_human_followup,
            requires_review=requires_review,
            summary_payload=summary_payload,
            evidence_payload=evidence_payload,
            generated_from_event_count=len(events),
            last_event_created_at=events[-1].created_at if events else None,
            created_at=now,
            updated_at=now,
        )
        return self.store.save_conversation_summary(summary)

    def _apply_turn_review_corrections(
        self,
        organization_id: str,
        events: list[TurnClassificationEvent],
    ) -> list[TurnClassificationEvent]:
        corrected: list[TurnClassificationEvent] = []
        for event in events:
            review_items = self.store.list_review_items(
                organization_id,
                classification_event_id=event.classification_event_id,
                status="resolved",
                limit=20,
            )
            corrected.append(_apply_turn_review_correction(event, _latest_resolved_review(review_items, corrected_only=True)))
        return corrected


class DeterministicTaggingService:
    def __init__(self, store: IntentTagsStore, taxonomy_service: TaxonomyService):
        self.store = store
        self.taxonomy_service = taxonomy_service

    def assign_turn_tags(self, event: TurnClassificationEvent) -> list[TagAssignment]:
        tags = self.taxonomy_service.list_effective_tags(
            event.organization_id,
            agent_id=event.agent_id,
            taxonomy_version_id=event.taxonomy_version_id if event.taxonomy_mode == "pinned" else None,
            include_inactive=False,
        )
        existing = self.store.list_tag_assignments(
            event.organization_id,
            classification_event_id=event.classification_event_id,
            assignment_scope="turn",
            limit=500,
        )
        existing_tag_ids = {item.tag_definition_id for item in existing}
        assignments: list[TagAssignment] = []
        for tag in tags:
            if tag.apply_scope not in {"turn", "both"}:
                continue
            if tag.tag_definition_id in existing_tag_ids:
                continue
            evidence = self._match_turn_tag(tag, event)
            if evidence is None:
                continue
            assignment = self.store.save_tag_assignment(
                TagAssignment(
                    organization_id=event.organization_id,
                    conversation_id=event.conversation_id,
                    classification_event_id=event.classification_event_id,
                    tag_definition_id=tag.tag_definition_id,
                    assignment_scope="turn",
                    assignment_source="deterministic_rule",
                    confidence=event.confidence,
                    reason_text=evidence.get("reason_text"),
                    evidence_payload=evidence,
                )
            )
            assignments.append(assignment)
        return assignments

    def assign_summary_tags(self, summary: ConversationSemanticSummary) -> list[TagAssignment]:
        tags = self.taxonomy_service.list_effective_tags(
            summary.organization_id,
            agent_id=summary.agent_id,
            include_inactive=False,
        )
        existing = self.store.list_tag_assignments(
            summary.organization_id,
            conversation_summary_id=summary.conversation_summary_id,
            assignment_scope="conversation",
            limit=500,
        )
        existing_tag_ids = {item.tag_definition_id for item in existing}
        assignments: list[TagAssignment] = []
        for tag in tags:
            if tag.apply_scope not in {"conversation", "both"}:
                continue
            if tag.tag_definition_id in existing_tag_ids:
                continue
            evidence = self._match_summary_tag(tag, summary)
            if evidence is None:
                continue
            assignment = self.store.save_tag_assignment(
                TagAssignment(
                    organization_id=summary.organization_id,
                    conversation_id=summary.conversation_id,
                    conversation_summary_id=summary.conversation_summary_id,
                    tag_definition_id=tag.tag_definition_id,
                    assignment_scope="conversation",
                    assignment_source="summary_rollup",
                    confidence=evidence.get("confidence"),
                    reason_text=evidence.get("reason_text"),
                    evidence_payload=evidence,
                )
            )
            assignments.append(assignment)
        return assignments

    def _match_turn_tag(self, tag: TagDefinition, event: TurnClassificationEvent) -> dict[str, Any] | None:
        rule_config = tag.rule_config or {}
        if rule_config and not self._rule_matches_turn(rule_config, event):
            return None
        if rule_config:
            return {
                "matched_via": "rule_config",
                "rule_config": deepcopy(rule_config),
                "classification_event_id": event.classification_event_id,
                "reason_text": f"Matched deterministic turn rule for {tag.name}",
            }
        signal_value = (event.signals or {}).get(tag.name)
        if _safe_truthy(signal_value):
            return {
                "matched_via": "signal_name",
                "signal_name": tag.name,
                "classification_event_id": event.classification_event_id,
                "reason_text": f"Classifier signal {tag.name} was asserted",
            }
        return None

    def _match_summary_tag(
        self,
        tag: TagDefinition,
        summary: ConversationSemanticSummary,
    ) -> dict[str, Any] | None:
        rule_config = tag.rule_config or {}
        if rule_config and self._rule_matches_summary(rule_config, summary):
            return {
                "matched_via": "rule_config",
                "rule_config": deepcopy(rule_config),
                "conversation_summary_id": summary.conversation_summary_id,
                "reason_text": f"Matched summary rule for {tag.name}",
                "confidence": 0.9,
            }

        signal_counts = dict(summary.evidence_payload.get("signal_counts") or {})
        tool_route_counts = dict(summary.evidence_payload.get("tool_route_counts") or {})
        if tag.tag_kind in {"blocker", "failure_reason", "risk", "outcome_attribute"}:
            if signal_counts.get(tag.name, 0) > 0:
                return {
                    "matched_via": "summary_signal_rollup",
                    "signal_name": tag.name,
                    "count": signal_counts[tag.name],
                    "conversation_summary_id": summary.conversation_summary_id,
                    "reason_text": f"Summary evidence contained signal {tag.name}",
                    "confidence": 0.85,
                }
            if tool_route_counts.get(tag.name, 0) > 0:
                return {
                    "matched_via": "tool_route_rollup",
                    "tool_route": tag.name,
                    "count": tool_route_counts[tag.name],
                    "conversation_summary_id": summary.conversation_summary_id,
                    "reason_text": f"Summary evidence contained tool route {tag.name}",
                    "confidence": 0.8,
                }
        if tag.tag_kind == "outcome_attribute" and summary.outcome == tag.name:
            return {
                "matched_via": "summary_outcome",
                "outcome": summary.outcome,
                "conversation_summary_id": summary.conversation_summary_id,
                "reason_text": f"Conversation outcome matched {tag.name}",
                "confidence": 0.95,
            }
        if tag.related_intent_id and summary.primary_intent_name:
            tag_intent = self.store.get_intent_definition(tag.related_intent_id)
            if tag_intent is not None and tag_intent.name == summary.primary_intent_name:
                return {
                    "matched_via": "related_intent",
                    "intent_name": summary.primary_intent_name,
                    "conversation_summary_id": summary.conversation_summary_id,
                    "reason_text": f"Summary primary intent matched related intent for {tag.name}",
                    "confidence": 0.7,
                }
        return None

    @staticmethod
    def _rule_matches_turn(rule_config: dict[str, Any], event: TurnClassificationEvent) -> bool:
        signals = dict(event.signals or {})
        if rule_config.get("intent_names") and event.intent_name not in set(rule_config["intent_names"]):
            return False
        if rule_config.get("tool_routes") and event.tool_route not in set(rule_config["tool_routes"]):
            return False
        any_signals = list(rule_config.get("any_signals") or [])
        if any_signals and not any(_safe_truthy(signals.get(name)) for name in any_signals):
            return False
        all_signals = list(rule_config.get("all_signals") or [])
        if all_signals and not all(_safe_truthy(signals.get(name)) for name in all_signals):
            return False
        return True

    @staticmethod
    def _rule_matches_summary(rule_config: dict[str, Any], summary: ConversationSemanticSummary) -> bool:
        evidence = dict(summary.evidence_payload or {})
        signal_counts = dict(evidence.get("signal_counts") or {})
        tool_route_counts = dict(evidence.get("tool_route_counts") or {})
        if rule_config.get("primary_intents") and summary.primary_intent_name not in set(rule_config["primary_intents"]):
            return False
        if rule_config.get("outcomes") and summary.outcome not in set(rule_config["outcomes"]):
            return False
        if rule_config.get("resolution_statuses") and summary.resolution_status not in set(rule_config["resolution_statuses"]):
            return False
        if "requires_human_followup" in rule_config and summary.requires_human_followup is not bool(
            rule_config["requires_human_followup"]
        ):
            return False
        any_signals = list(rule_config.get("any_signals") or [])
        if any_signals and not any(signal_counts.get(name, 0) > 0 for name in any_signals):
            return False
        any_tool_routes = list(rule_config.get("any_tool_routes") or [])
        if any_tool_routes and not any(tool_route_counts.get(name, 0) > 0 for name in any_tool_routes):
            return False
        return True


class ReviewQueueService:
    def __init__(self, store: IntentTagsStore):
        self.store = store

    def create_review_item(
        self,
        *,
        organization_id: str,
        review_kind: ReviewKind,
        classification_event_id: str | None = None,
        conversation_summary_id: str | None = None,
        review_notes: str | None = None,
    ) -> ClassificationReviewItem:
        item = ClassificationReviewItem(
            organization_id=organization_id,
            classification_event_id=classification_event_id,
            conversation_summary_id=conversation_summary_id,
            review_kind=review_kind,
            review_notes=review_notes,
        )
        return self.store.save_review_item(item)

    def list_queue(
        self,
        organization_id: str,
        *,
        status: str | None = None,
        review_kind: str | None = None,
        claimed_by_user_id: str | None = None,
        limit: int = 100,
    ) -> list[ClassificationReviewItem]:
        return self.store.list_review_items(
            organization_id,
            status=status,
            review_kind=review_kind,
            claimed_by_user_id=claimed_by_user_id,
            limit=limit,
        )

    def claim_review_item(self, review_item_id: str, *, user_id: str) -> ClassificationReviewItem:
        existing = self.store.get_review_item(review_item_id)
        if existing is None:
            raise ValueError(f"review item not found: {review_item_id}")
        if existing.status == "resolved" or existing.status == "dismissed":
            raise ValueError("resolved review items cannot be claimed")
        if existing.claimed_by_user_id and existing.claimed_by_user_id != user_id:
            raise ValueError(f"review item already claimed by {existing.claimed_by_user_id}")
        now = utc_now()
        updated = existing.model_copy(
            update={
                "status": "in_review",
                "claimed_by_user_id": user_id,
                "claimed_at": existing.claimed_at or now,
                "updated_at": now,
            }
        )
        return self.store.save_review_item(updated)

    def resolve_turn_review(
        self,
        review_item_id: str,
        *,
        user_id: str,
        disposition: ReviewDisposition,
        corrected_decision: TurnClassificationDecision | None = None,
        review_notes: str | None = None,
    ) -> ClassificationReviewItem:
        item = self._require_review_item(review_item_id)
        if item.classification_event_id is None:
            raise ValueError("turn correction requires classification_event_id")
        if disposition == "corrected" and corrected_decision is None:
            raise ValueError("corrected turn review requires corrected_decision")
        corrected_payload = (
            corrected_decision.model_dump(exclude_none=True)
            if corrected_decision is not None
            else deepcopy(item.corrected_payload)
        )
        return self._finalize_review_item(
            item,
            user_id=user_id,
            disposition=disposition,
            corrected_payload=corrected_payload,
            review_notes=review_notes,
        )

    def resolve_summary_review(
        self,
        review_item_id: str,
        *,
        user_id: str,
        disposition: ReviewDisposition,
        corrected_fields: dict[str, Any] | None = None,
        corrected_tag_definition_ids: list[str] | None = None,
        review_notes: str | None = None,
    ) -> ClassificationReviewItem:
        item = self._require_review_item(review_item_id)
        if item.conversation_summary_id is None:
            raise ValueError("summary correction requires conversation_summary_id")
        if disposition == "corrected" and corrected_fields is None and corrected_tag_definition_ids is None:
            raise ValueError("corrected summary review requires corrected_fields or corrected_tag_definition_ids")

        original = self.store.get_conversation_summary(item.conversation_summary_id)
        if original is None:
            raise ValueError(f"conversation summary not found: {item.conversation_summary_id}")

        corrected_summary_id: str | None = None
        corrected_payload = deepcopy(item.corrected_payload)
        if disposition == "corrected":
            now = utc_now()
            self.store.save_conversation_summary(
                original.model_copy(update={"status": "superseded", "updated_at": now})
            )
            summary_updates = deepcopy(corrected_fields or {})
            # Rebuild with a new identity so the original summary remains preserved.
            new_summary = ConversationSemanticSummary(
                organization_id=original.organization_id,
                agent_id=summary_updates.get("agent_id", original.agent_id),
                agent_version_id=summary_updates.get("agent_version_id", original.agent_version_id),
                conversation_id=original.conversation_id,
                summary_version=summary_updates.get("summary_version", original.summary_version),
                status="corrected",
                primary_intent_name=summary_updates.get("primary_intent_name", original.primary_intent_name),
                secondary_intents=deepcopy(summary_updates.get("secondary_intents", original.secondary_intents)),
                resolution_status=summary_updates.get("resolution_status", original.resolution_status),
                outcome=summary_updates.get("outcome", original.outcome),
                final_language=summary_updates.get("final_language", original.final_language),
                response_language=summary_updates.get("response_language", original.response_language),
                channel=summary_updates.get("channel", original.channel),
                requires_human_followup=summary_updates.get(
                    "requires_human_followup",
                    original.requires_human_followup,
                ),
                requires_review=summary_updates.get("requires_review", False),
                summary_payload={
                    **deepcopy(original.summary_payload),
                    **deepcopy(summary_updates.get("summary_payload", {})),
                    "review_correction_of": original.conversation_summary_id,
                },
                evidence_payload={
                    **deepcopy(original.evidence_payload),
                    "review_correction_of": original.conversation_summary_id,
                    "review_item_id": item.review_item_id,
                },
                generated_from_event_count=summary_updates.get(
                    "generated_from_event_count",
                    original.generated_from_event_count,
                ),
                last_event_created_at=summary_updates.get(
                    "last_event_created_at",
                    original.last_event_created_at,
                ),
                created_at=now,
                updated_at=now,
            )
            saved_summary = self.store.save_conversation_summary(new_summary)
            corrected_summary_id = saved_summary.conversation_summary_id
            self._clone_or_replace_summary_assignments(
                original_summary_id=original.conversation_summary_id,
                corrected_summary_id=saved_summary.conversation_summary_id,
                organization_id=original.organization_id,
                conversation_id=original.conversation_id,
                corrected_tag_definition_ids=corrected_tag_definition_ids,
            )
            corrected_payload = {
                **deepcopy(corrected_payload),
                "summary_fields": deepcopy(corrected_fields or {}),
                "corrected_tag_definition_ids": list(corrected_tag_definition_ids or []),
            }

        return self._finalize_review_item(
            item,
            user_id=user_id,
            disposition=disposition,
            corrected_payload=corrected_payload,
            review_notes=review_notes,
            corrected_conversation_summary_id=corrected_summary_id,
        )

    def get_effective_turn_classification(self, classification_event_id: str) -> EffectiveTurnClassification:
        event = self.store.get_classification_event(classification_event_id)
        if event is None:
            raise ValueError(f"classification event not found: {classification_event_id}")
        review_item = _latest_resolved_review(
            self.store.list_review_items(
                event.organization_id,
                classification_event_id=classification_event_id,
                status="resolved",
                limit=20,
            )
        )
        effective_event = _apply_turn_review_correction(event, review_item)
        return EffectiveTurnClassification(
            event=event,
            effective_event=effective_event,
            review_item=review_item,
            is_corrected=review_item is not None and review_item.review_disposition == "corrected",
        )

    def get_effective_summary(
        self,
        *,
        conversation_id: str | None = None,
        conversation_summary_id: str | None = None,
    ) -> EffectiveConversationSummary:
        if conversation_summary_id is None and conversation_id is None:
            raise ValueError("conversation_id or conversation_summary_id is required")

        if conversation_summary_id is not None:
            base_summary = self.store.get_conversation_summary(conversation_summary_id)
        else:
            summaries = self.store.list_conversation_summaries(
                self._require_conversation_organization(conversation_id),
                conversation_id=conversation_id,
                limit=50,
            )
            base_summary = self._pick_effective_summary_candidate(summaries)
        if base_summary is None:
            raise ValueError("effective summary target not found")

        review_items = self.store.list_review_items(
            base_summary.organization_id,
            conversation_summary_id=base_summary.conversation_summary_id,
            status="resolved",
            limit=20,
        )
        review_item = _latest_resolved_review(review_items)
        effective_summary = base_summary
        is_corrected = base_summary.status == "corrected"
        if review_item and review_item.corrected_conversation_summary_id:
            corrected_summary = self.store.get_conversation_summary(review_item.corrected_conversation_summary_id)
            if corrected_summary is not None:
                effective_summary = corrected_summary
                is_corrected = True
        tag_assignments = self.store.list_tag_assignments(
            effective_summary.organization_id,
            conversation_summary_id=effective_summary.conversation_summary_id,
            assignment_scope="conversation",
            limit=500,
        )
        return EffectiveConversationSummary(
            summary=base_summary,
            effective_summary=effective_summary,
            tag_assignments=tag_assignments,
            review_item=review_item,
            is_corrected=is_corrected,
        )

    def _clone_or_replace_summary_assignments(
        self,
        *,
        original_summary_id: str,
        corrected_summary_id: str,
        organization_id: str,
        conversation_id: str,
        corrected_tag_definition_ids: list[str] | None,
    ) -> None:
        original_assignments = self.store.list_tag_assignments(
            organization_id,
            conversation_summary_id=original_summary_id,
            assignment_scope="conversation",
            limit=500,
        )
        target_tag_ids = (
            list(corrected_tag_definition_ids)
            if corrected_tag_definition_ids is not None
            else [item.tag_definition_id for item in original_assignments]
        )
        original_by_tag = {item.tag_definition_id: item for item in original_assignments}
        for tag_definition_id in target_tag_ids:
            source_assignment = original_by_tag.get(tag_definition_id)
            self.store.save_tag_assignment(
                TagAssignment(
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    conversation_summary_id=corrected_summary_id,
                    tag_definition_id=tag_definition_id,
                    assignment_scope="conversation",
                    assignment_source="review_correction" if corrected_tag_definition_ids is not None else (
                        source_assignment.assignment_source if source_assignment is not None else "review_correction"
                    ),
                    confidence=source_assignment.confidence if source_assignment is not None else None,
                    reason_text=(
                        source_assignment.reason_text
                        if source_assignment is not None and corrected_tag_definition_ids is None
                        else "Operator summary correction"
                    ),
                    evidence_payload=(
                        deepcopy(source_assignment.evidence_payload)
                        if source_assignment is not None and corrected_tag_definition_ids is None
                        else {"corrected_from_summary_id": original_summary_id}
                    ),
                    is_validated=True,
                )
            )

    @staticmethod
    def _pick_effective_summary_candidate(
        summaries: list[ConversationSemanticSummary],
    ) -> ConversationSemanticSummary | None:
        if not summaries:
            return None
        ranked = sorted(
            summaries,
            key=lambda item: (
                item.status == "corrected",
                item.status == "final",
                item.updated_at,
                item.conversation_summary_id,
            ),
            reverse=True,
        )
        return ranked[0]

    def _finalize_review_item(
        self,
        item: ClassificationReviewItem,
        *,
        user_id: str,
        disposition: ReviewDisposition,
        corrected_payload: dict[str, Any],
        review_notes: str | None,
        corrected_conversation_summary_id: str | None = None,
    ) -> ClassificationReviewItem:
        now = utc_now()
        updated = item.model_copy(
            update={
                "status": "dismissed" if disposition == "dismissed" else "resolved",
                "review_disposition": disposition,
                "corrected_payload": deepcopy(corrected_payload),
                "review_notes": review_notes if review_notes is not None else item.review_notes,
                "reviewed_by_user_id": user_id,
                "reviewed_at": now,
                "claimed_by_user_id": item.claimed_by_user_id or user_id,
                "claimed_at": item.claimed_at or now,
                "corrected_conversation_summary_id": corrected_conversation_summary_id,
                "updated_at": now,
            }
        )
        return self.store.save_review_item(updated)

    def _require_review_item(self, review_item_id: str) -> ClassificationReviewItem:
        item = self.store.get_review_item(review_item_id)
        if item is None:
            raise ValueError(f"review item not found: {review_item_id}")
        return item

    def _require_conversation_organization(self, conversation_id: str | None) -> str:
        if conversation_id is None:
            raise ValueError("conversation_id is required")
        context = self.store.get_conversation_context(conversation_id)
        if context is None:
            raise ValueError(f"conversation not found: {conversation_id}")
        return context.organization_id
