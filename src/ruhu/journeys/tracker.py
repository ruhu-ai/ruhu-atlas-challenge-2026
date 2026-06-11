from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ruhu.capture.comparison import fact_value_equals
from ruhu.realtime import RealtimeEvent
from ruhu.schemas import ConversationState, FactUpdate, SemanticEventRecord, ToolCallRecord, TurnTrace
from ruhu.stores import ConversationStore, TraceStore

from .models import (
    JourneyDefinition,
    JourneyDefinitionVersion,
    JourneyEvent,
    JourneyEventSource,
    JourneyInstance,
    JourneyMilestoneRule,
    JourneyRulePredicate,
    JourneyTouchpoint,
)
from .rules import ALLOWED_OUTCOME_RULE_KEYS
from .store import JourneyDefinitionStore, JourneyInstanceStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RealtimeEventReadStore(Protocol):
    def load(self, event_id: str) -> RealtimeEvent | None: ...

    def replay(
        self,
        *,
        conversation_id: str,
        after_sequence: int | None = None,
        after_event_id: str | None = None,
    ) -> list[RealtimeEvent]: ...


class JourneyTracker:
    def __init__(
        self,
        *,
        definition_store: JourneyDefinitionStore,
        instance_store: JourneyInstanceStore,
        conversation_store: ConversationStore,
        trace_store: TraceStore,
        realtime_event_store: RealtimeEventReadStore | None = None,
    ) -> None:
        self._definition_store = definition_store
        self._instance_store = instance_store
        self._conversation_store = conversation_store
        self._trace_store = trace_store
        self._realtime_event_store = realtime_event_store

    def process_turn_trace(
        self,
        trace: TurnTrace,
        *,
        conversation: ConversationState | None = None,
    ) -> list[JourneyEvent]:
        conversation_state = conversation or self._conversation_store.load(trace.conversation_id)
        if conversation_state is None:
            raise ValueError(f"unknown conversation for trace: {trace.conversation_id}")
        context = _TrackerContext(
            conversation=conversation_state,
            trace=trace,
            occurred_at=_trace_recorded_at(trace, fallback=conversation_state.updated_at),
            is_first_conversation_evidence=self._is_first_trace(trace),
        )
        return self._process_context(context)

    def process_realtime_event(
        self,
        event: RealtimeEvent,
        *,
        conversation: ConversationState | None = None,
    ) -> list[JourneyEvent]:
        conversation_state = conversation or self._conversation_store.load(event.conversation_id)
        if conversation_state is None:
            raise ValueError(f"unknown conversation for realtime event: {event.conversation_id}")
        context = _TrackerContext(
            conversation=conversation_state,
            realtime_event=event,
            occurred_at=event.created_at,
            is_first_conversation_evidence=event.conversation_sequence == 1
            or (event.family == "conversation" and event.name in {"started", "created"}),
        )
        return self._process_context(context)

    def rebuild_from_conversation(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[JourneyEvent]:
        conversation = self._conversation_store.load(conversation_id)
        if conversation is None:
            raise ValueError(f"unknown conversation: {conversation_id}")
        if organization_id is not None and conversation.organization_id != organization_id:
            return []

        emitted: list[JourneyEvent] = []
        traces = self._trace_store.by_conversation(conversation_id, organization_id=organization_id)
        rolling = self._initial_rebuild_conversation(conversation, traces)
        evidence = [
            _RebuildEvidence(
                occurred_at=_trace_recorded_at(trace, fallback=conversation.updated_at),
                kind_priority=1,
                stable_id=trace.trace_id,
                trace=trace,
            )
            for trace in traces
        ]
        if self._realtime_event_store is not None:
            realtime_events = self._realtime_event_store.replay(conversation_id=conversation_id)
            for event in realtime_events:
                evidence.append(
                    _RebuildEvidence(
                        occurred_at=event.created_at,
                        kind_priority=0,
                        stable_id=event.event_id,
                        realtime_event=event,
                    )
                )
        for item in sorted(evidence, key=lambda entry: (entry.occurred_at, entry.kind_priority, entry.stable_id)):
            if item.trace is not None:
                rolling = _apply_trace_to_conversation(rolling, item.trace)
                emitted.extend(
                    self.process_turn_trace(
                        item.trace,
                        conversation=rolling.model_copy(deep=True),
                    )
                )
                continue
            if item.realtime_event is None:
                continue
            rolling.updated_at = max(rolling.updated_at, item.realtime_event.created_at)
            emitted.extend(
                self.process_realtime_event(
                    item.realtime_event,
                    conversation=rolling.model_copy(deep=True),
                )
            )
        return emitted

    def replay_definition_conversations(
        self,
        definition: JourneyDefinition,
        version: JourneyDefinitionVersion,
        conversation_ids: list[str],
        *,
        organization_id: str | None = None,
        journey_id_override: str | None = None,
    ) -> list[JourneyEvent]:
        if version.definition_id != definition.definition_id:
            raise ValueError("journey definition version does not belong to the supplied definition")

        emitted: list[JourneyEvent] = []
        rolling_by_conversation: dict[str, ConversationState] = {}
        evidence: list[_RebuildEvidence] = []
        ordered_conversation_ids = list(dict.fromkeys(conversation_ids))
        for conversation_id in ordered_conversation_ids:
            conversation = self._conversation_store.load(conversation_id)
            if conversation is None:
                continue
            if organization_id is not None and conversation.organization_id != organization_id:
                continue
            traces = self._trace_store.by_conversation(conversation_id, organization_id=organization_id)
            rolling_by_conversation[conversation_id] = self._initial_rebuild_conversation(conversation, traces)
            evidence.extend(
                _RebuildEvidence(
                    occurred_at=_trace_recorded_at(trace, fallback=conversation.updated_at),
                    kind_priority=1,
                    stable_id=trace.trace_id,
                    trace=trace,
                )
                for trace in traces
            )
            if self._realtime_event_store is not None:
                for event in self._realtime_event_store.replay(conversation_id=conversation_id):
                    evidence.append(
                        _RebuildEvidence(
                            occurred_at=event.created_at,
                            kind_priority=0,
                            stable_id=event.event_id,
                            realtime_event=event,
                        )
                    )

        journey_id_seed = journey_id_override
        replay_started_conversations: set[str] = set()
        for item in sorted(evidence, key=lambda entry: (entry.occurred_at, entry.kind_priority, entry.stable_id)):
            conversation_id = _evidence_conversation_id(item)
            rolling = rolling_by_conversation.get(conversation_id)
            if rolling is None:
                continue
            is_first_conversation_evidence = conversation_id not in replay_started_conversations
            replay_started_conversations.add(conversation_id)
            if item.trace is not None:
                rolling = _apply_trace_to_conversation(rolling, item.trace)
                rolling_by_conversation[conversation_id] = rolling
                replay_emitted = self._process_definition_context(
                    definition=definition,
                    version=version,
                    context=_TrackerContext(
                        conversation=rolling.model_copy(deep=True),
                        trace=item.trace,
                        occurred_at=_trace_recorded_at(item.trace, fallback=rolling.updated_at),
                        is_first_conversation_evidence=is_first_conversation_evidence,
                    ),
                    journey_id_override=journey_id_seed,
                )
            elif item.realtime_event is not None:
                rolling.updated_at = max(rolling.updated_at, item.realtime_event.created_at)
                rolling_by_conversation[conversation_id] = rolling
                replay_emitted = self._process_definition_context(
                    definition=definition,
                    version=version,
                    context=_TrackerContext(
                        conversation=rolling.model_copy(deep=True),
                        realtime_event=item.realtime_event,
                        occurred_at=item.realtime_event.created_at,
                        is_first_conversation_evidence=is_first_conversation_evidence,
                    ),
                    journey_id_override=journey_id_seed,
                )
            else:
                continue
            emitted.extend(replay_emitted)
            if journey_id_seed is not None and any(
                event.journey_id == journey_id_seed and event.event_type == "journey_opened"
                for event in replay_emitted
            ):
                journey_id_seed = None
        return emitted

    def discover_definition_conversations(
        self,
        definition: JourneyDefinition,
        *,
        organization_id: str | None = None,
    ) -> dict[str, list[str]]:
        grouped: dict[str, list[ConversationState]] = {}
        for conversation in self._conversation_store.list_conversations(organization_id=organization_id):
            if not self._definition_matches_conversation(definition, conversation):
                continue
            subject_key = _derive_subject_key(definition, conversation)
            if subject_key is None:
                continue
            if not self._conversation_has_replayable_evidence(
                conversation.conversation_id,
                organization_id=organization_id,
            ):
                continue
            grouped.setdefault(subject_key, []).append(conversation)
        return {
            subject_key: [
                item.conversation_id
                for item in sorted(
                    conversations,
                    key=lambda conversation: (
                        conversation.started_at,
                        conversation.updated_at,
                        conversation.conversation_id,
                    ),
                )
            ]
            for subject_key, conversations in grouped.items()
        }

    def _initial_rebuild_conversation(
        self,
        conversation: ConversationState,
        traces: list[TurnTrace],
    ) -> ConversationState:
        initial_step_id = traces[0].step_before if traces else conversation.step_id
        return conversation.model_copy(
            update={
                "step_id": initial_step_id,
                "status": "active",
                "facts": self._initial_rebuild_facts(conversation, traces),
                "outcome": None,
                "ended_at": None,
                "updated_at": conversation.started_at,
            },
            deep=True,
        )

    def _initial_rebuild_facts(
        self,
        conversation: ConversationState,
        traces: list[TurnTrace],
    ) -> dict[str, Any]:
        updated_fact_names = {
            fact_update.name
            for trace in traces
            for fact_update in trace.fact_updates
        }
        return {
            name: value
            for name, value in conversation.facts.items()
            if name not in updated_fact_names
        }

    def _conversation_has_replayable_evidence(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> bool:
        if self._trace_store.by_conversation(conversation_id, organization_id=organization_id):
            return True
        if self._realtime_event_store is None:
            return False
        return bool(self._realtime_event_store.replay(conversation_id=conversation_id))

    def _process_context(self, context: "_TrackerContext") -> list[JourneyEvent]:
        emitted: list[JourneyEvent] = []
        for definition in self._eligible_definitions(context.conversation):
            emitted.extend(self._process_live_definition(definition=definition, context=context))
        return emitted

    def _process_live_definition(
        self,
        *,
        definition: JourneyDefinition,
        context: "_TrackerContext",
    ) -> list[JourneyEvent]:
        emitted: list[JourneyEvent] = []
        force_attach_touchpoint = False
        subject_key = _derive_subject_key(definition, context.conversation)
        if subject_key is None:
            return emitted

        organization_id = context.conversation.organization_id
        blocked_reopen_journey_ids: set[str] = set()
        instance = self._instance_store.find_open_by_subject(
            organization_id=organization_id,
            definition_id=definition.definition_id,
            subject_key=subject_key,
        )
        version: JourneyDefinitionVersion | None = None

        if instance is not None:
            version = self._definition_store.load_version(
                instance.definition_version_id,
                organization_id=definition.organization_id,
            )
            if version is None:
                return emitted
            existing_keys = self._existing_event_keys(instance, organization_id=context.conversation.organization_id)
            auto_closed = self._maybe_auto_abandon_instance(
                instance=instance,
                version=version,
                context=context,
                existing_keys=existing_keys,
            )
            if auto_closed:
                emitted.extend(auto_closed)
                instance.updated_at = context.occurred_at
                self._instance_store.save_instance(instance)
                blocked_reopen_journey_ids.add(instance.journey_id)
                instance = None
                version = None

        if instance is None:
            reopen_candidate, reopen_version = self._find_merge_candidate(
                organization_id=organization_id,
                definition_id=definition.definition_id,
                subject_key=subject_key,
                occurred_at=context.occurred_at,
                organization_scope_id=definition.organization_id,
                excluded_journey_ids=blocked_reopen_journey_ids,
            )
            if (
                reopen_candidate is not None
                and reopen_version is not None
                and _predicates_match(reopen_version.rules.entry_rules, context)
            ):
                instance = reopen_candidate
                version = reopen_version
                existing_keys = self._existing_event_keys(
                    instance,
                    organization_id=context.conversation.organization_id,
                )
                emitted.extend(
                    self._reopen_instance(
                        instance=instance,
                        version=version,
                        context=context,
                        existing_keys=existing_keys,
                    )
                )
                force_attach_touchpoint = True
            else:
                if definition.current_published_version_id is None:
                    return emitted
                version = self._definition_store.load_version(
                    definition.current_published_version_id,
                    organization_id=definition.organization_id,
                )
                if version is None or not _predicates_match(version.rules.entry_rules, context):
                    return emitted
                instance = self._open_instance(
                    definition=definition,
                    version=version,
                    subject_key=subject_key,
                    context=context,
                )
                existing_keys = set()
                emitted.extend(
                    self._emit_event(
                        instance=instance,
                        existing_keys=existing_keys,
                        context=context,
                        event_type="journey_opened",
                        idempotency_suffix=f"journey_opened:{_evidence_key(context)}",
                        occurred_at=context.occurred_at,
                        payload={
                            "conversation_id": context.conversation.conversation_id,
                            "definition_version_id": version.definition_version_id,
                            "subject_key": subject_key,
                        },
                    )
                )
                force_attach_touchpoint = True
        else:
            existing_keys = self._existing_event_keys(instance, organization_id=context.conversation.organization_id)

        if version is None:
            return emitted
        touchpoint_events, attached = self._ensure_touchpoint(
            instance,
            version,
            context,
            existing_keys,
            force_attach=force_attach_touchpoint,
        )
        emitted.extend(touchpoint_events)
        if not attached:
            return emitted
        self._sync_instance_activity(instance, context)
        emitted.extend(self._apply_milestones(instance, version, context, existing_keys))
        emitted.extend(self._apply_outcomes(instance, version, context, existing_keys))
        instance.updated_at = context.occurred_at
        self._instance_store.save_instance(instance)
        return emitted

    def _eligible_definitions(self, conversation: ConversationState) -> list[JourneyDefinition]:
        definitions = self._definition_store.list_definitions(status="active")
        eligible: list[JourneyDefinition] = []
        for definition in definitions:
            if definition.current_published_version_id is None:
                continue
            if not self._definition_matches_conversation(definition, conversation):
                continue
            eligible.append(definition)
        return eligible

    def _definition_matches_conversation(
        self,
        definition: JourneyDefinition,
        conversation: ConversationState,
    ) -> bool:
        if definition.organization_id not in {None, conversation.organization_id}:
            return False
        if definition.scope.agent_ids and conversation.agent_id not in definition.scope.agent_ids:
            return False
        if definition.scope.channel_filters and conversation.channel not in definition.scope.channel_filters:
            return False
        if (
            definition.scope.conversation_mode_filters
            and conversation.mode not in definition.scope.conversation_mode_filters
        ):
            return False
        return True

    def _process_definition_context(
        self,
        *,
        definition: JourneyDefinition,
        version: JourneyDefinitionVersion,
        context: "_TrackerContext",
        journey_id_override: str | None = None,
    ) -> list[JourneyEvent]:
        if not self._definition_matches_conversation(definition, context.conversation):
            return []
        if version.definition_id != definition.definition_id:
            return []

        emitted: list[JourneyEvent] = []
        force_attach_touchpoint = False
        subject_key = _derive_subject_key(definition, context.conversation)
        if subject_key is None:
            return emitted
        organization_id = context.conversation.organization_id
        blocked_reopen_journey_ids: set[str] = set()
        instance = self._instance_store.find_open_by_subject(
            organization_id=organization_id,
            definition_id=definition.definition_id,
            subject_key=subject_key,
        )
        if instance is not None:
            existing_keys = self._existing_event_keys(instance, organization_id=context.conversation.organization_id)
            auto_closed = self._maybe_auto_abandon_instance(
                instance=instance,
                version=version,
                context=context,
                existing_keys=existing_keys,
            )
            if auto_closed:
                emitted.extend(auto_closed)
                instance.updated_at = context.occurred_at
                self._instance_store.save_instance(instance)
                blocked_reopen_journey_ids.add(instance.journey_id)
                instance = None

        if instance is None:
            if not _predicates_match(version.rules.entry_rules, context):
                return emitted
            reopen_candidate = self._find_merge_candidate_for_version(
                organization_id=organization_id,
                definition_id=definition.definition_id,
                subject_key=subject_key,
                version=version,
                occurred_at=context.occurred_at,
                excluded_journey_ids=blocked_reopen_journey_ids,
            )
            if reopen_candidate is not None:
                instance = reopen_candidate
                existing_keys = self._existing_event_keys(
                    instance,
                    organization_id=context.conversation.organization_id,
                )
                emitted.extend(
                    self._reopen_instance(
                        instance=instance,
                        version=version,
                        context=context,
                        existing_keys=existing_keys,
                    )
                )
                force_attach_touchpoint = True
            else:
                instance = self._open_instance(
                    definition=definition,
                    version=version,
                    subject_key=subject_key,
                    context=context,
                    journey_id_override=journey_id_override,
                )
                existing_keys = set()
                emitted.extend(
                    self._emit_event(
                        instance=instance,
                        existing_keys=existing_keys,
                        context=context,
                        event_type="journey_opened",
                        idempotency_suffix=f"journey_opened:{_evidence_key(context)}",
                        occurred_at=context.occurred_at,
                        payload={
                            "conversation_id": context.conversation.conversation_id,
                            "definition_version_id": version.definition_version_id,
                            "subject_key": subject_key,
                        },
                    )
                )
                force_attach_touchpoint = True
        else:
            existing_keys = self._existing_event_keys(instance, organization_id=context.conversation.organization_id)
        touchpoint_events, attached = self._ensure_touchpoint(
            instance,
            version,
            context,
            existing_keys,
            force_attach=force_attach_touchpoint,
        )
        emitted.extend(touchpoint_events)
        if not attached:
            return emitted
        self._sync_instance_activity(instance, context)
        emitted.extend(self._apply_milestones(instance, version, context, existing_keys))
        emitted.extend(self._apply_outcomes(instance, version, context, existing_keys))
        instance.updated_at = context.occurred_at
        self._instance_store.save_instance(instance)
        return emitted

    def _open_instance(
        self,
        *,
        definition: JourneyDefinition,
        version: JourneyDefinitionVersion,
        subject_key: str,
        context: "_TrackerContext",
        journey_id_override: str | None = None,
    ) -> JourneyInstance:
        instance_payload: dict[str, Any] = {
            "organization_id": context.conversation.organization_id,
            "definition_id": definition.definition_id,
            "definition_version_id": version.definition_version_id,
            "subject_key": subject_key,
            "subject_summary": {
                "subject_key": subject_key,
                "channel": context.conversation.channel,
                "mode": context.conversation.mode,
            },
            "first_conversation_id": context.conversation.conversation_id,
            "latest_conversation_id": context.conversation.conversation_id,
            "first_agent_id": context.conversation.agent_id,
            "first_agent_version_id": context.conversation.agent_version_id,
            "latest_agent_id": context.conversation.agent_id,
            "latest_agent_version_id": context.conversation.agent_version_id,
            "started_at": context.occurred_at,
            "last_activity_at": context.occurred_at,
            "metadata": {"opened_by_conversation_id": context.conversation.conversation_id},
            "created_at": context.occurred_at,
            "updated_at": context.occurred_at,
        }
        if journey_id_override is not None:
            instance_payload["journey_id"] = journey_id_override
        instance = JourneyInstance(**instance_payload)
        self._instance_store.save_instance(instance)
        return instance

    def _reopen_instance(
        self,
        *,
        instance: JourneyInstance,
        version: JourneyDefinitionVersion,
        context: "_TrackerContext",
        existing_keys: set[str],
    ) -> list[JourneyEvent]:
        previous_status = instance.status
        instance.definition_version_id = version.definition_version_id
        instance.status = "open"
        instance.outcome = None
        instance.ended_at = None
        instance.last_activity_at = context.occurred_at
        instance.updated_at = context.occurred_at
        return self._emit_event(
            instance=instance,
            existing_keys=existing_keys,
            context=context,
            event_type="journey_reopened",
            idempotency_suffix=f"journey_reopened:{_evidence_key(context)}",
            occurred_at=context.occurred_at,
            payload={
                "conversation_id": context.conversation.conversation_id,
                "definition_version_id": version.definition_version_id,
                "previous_status": previous_status,
            },
            conversation_id=context.conversation.conversation_id,
        )

    def _sync_instance_activity(self, instance: JourneyInstance, context: "_TrackerContext") -> None:
        instance.latest_conversation_id = context.conversation.conversation_id
        instance.latest_agent_id = context.conversation.agent_id
        instance.latest_agent_version_id = context.conversation.agent_version_id
        instance.last_activity_at = context.occurred_at

    def _existing_event_keys(
        self,
        instance: JourneyInstance,
        *,
        organization_id: str | None,
    ) -> set[str]:
        return {
            event.idempotency_key
            for event in self._instance_store.list_events(
                instance.journey_id,
                organization_id=organization_id,
            )
        }

    def _ensure_touchpoint(
        self,
        instance: JourneyInstance,
        version: JourneyDefinitionVersion,
        context: "_TrackerContext",
        existing_keys: set[str],
        *,
        force_attach: bool = False,
    ) -> tuple[list[JourneyEvent], bool]:
        existing_touchpoints = self._instance_store.list_touchpoints(
            instance.journey_id,
            organization_id=context.conversation.organization_id,
        )
        for touchpoint in existing_touchpoints:
            if touchpoint.conversation_id == context.conversation.conversation_id:
                return [], True

        is_opening_conversation = instance.first_conversation_id == context.conversation.conversation_id
        if (
            not force_attach
            and not is_opening_conversation
            and version.rules.touchpoint_rules
            and not _predicates_match(version.rules.touchpoint_rules, context)
        ):
            return [], False

        touchpoint = JourneyTouchpoint(
            organization_id=instance.organization_id,
            journey_id=instance.journey_id,
            conversation_id=context.conversation.conversation_id,
            agent_id=context.conversation.agent_id,
            agent_version_id=context.conversation.agent_version_id,
            channel=context.conversation.channel,
            mode=context.conversation.mode,
            entry_reason=(
                "journey_opened"
                if is_opening_conversation
                else ("touchpoint_rules" if version.rules.touchpoint_rules else "existing_journey")
            ),
            started_at=context.conversation.started_at,
            created_at=context.occurred_at,
            updated_at=context.occurred_at,
        )
        self._instance_store.save_touchpoint(touchpoint)
        return (
            self._emit_event(
            instance=instance,
            existing_keys=existing_keys,
            context=context,
            event_type="touchpoint_attached",
            idempotency_suffix=f"touchpoint_attached:{context.conversation.conversation_id}",
            occurred_at=context.occurred_at,
            payload={
                "touchpoint_id": touchpoint.touchpoint_id,
                "conversation_id": context.conversation.conversation_id,
                "entry_reason": touchpoint.entry_reason,
            },
            touchpoint_id=touchpoint.touchpoint_id,
            conversation_id=context.conversation.conversation_id,
            ),
            True,
        )

    def _apply_milestones(
        self,
        instance: JourneyInstance,
        version: JourneyDefinitionVersion,
        context: "_TrackerContext",
        existing_keys: set[str],
    ) -> list[JourneyEvent]:
        emitted: list[JourneyEvent] = []
        milestones = sorted(version.rules.milestones, key=lambda item: (item.order_index, item.milestone_id))
        completed_ids = set(instance.milestone_path)
        active = next(
            (
                item
                for item in milestones
                if item.milestone_id == instance.current_milestone_id and item.milestone_id not in completed_ids
            ),
            None,
        )

        if active is not None and not active.is_checkpoint and _predicates_match(active.complete_when, context):
            emitted.extend(self._complete_milestone(instance, active, context, existing_keys))
            return emitted

        next_milestone = next((item for item in milestones if item.milestone_id not in completed_ids), None)
        if next_milestone is None:
            return emitted
        if not _predicates_match(next_milestone.enter_when, context):
            return emitted

        emitted.extend(self._enter_milestone(instance, next_milestone, context, existing_keys))
        if next_milestone.is_checkpoint or _predicates_match(next_milestone.complete_when, context):
            emitted.extend(self._complete_milestone(instance, next_milestone, context, existing_keys))
        return emitted

    def _enter_milestone(
        self,
        instance: JourneyInstance,
        milestone: JourneyMilestoneRule,
        context: "_TrackerContext",
        existing_keys: set[str],
    ) -> list[JourneyEvent]:
        instance.current_milestone_id = milestone.milestone_id
        instance.current_milestone_order = milestone.order_index
        instance.last_activity_at = context.occurred_at
        return self._emit_event(
            instance=instance,
            existing_keys=existing_keys,
            context=context,
            event_type="milestone_entered",
            idempotency_suffix=f"milestone_entered:{milestone.milestone_id}:{_evidence_key(context)}",
            occurred_at=context.occurred_at,
            payload={"milestone_id": milestone.milestone_id, "order_index": milestone.order_index},
            milestone_id=milestone.milestone_id,
            conversation_id=context.conversation.conversation_id,
        )

    def _complete_milestone(
        self,
        instance: JourneyInstance,
        milestone: JourneyMilestoneRule,
        context: "_TrackerContext",
        existing_keys: set[str],
    ) -> list[JourneyEvent]:
        if milestone.milestone_id not in instance.milestone_path:
            instance.milestone_path.append(milestone.milestone_id)
        instance.current_milestone_id = milestone.milestone_id
        instance.current_milestone_order = milestone.order_index
        instance.last_activity_at = context.occurred_at
        return self._emit_event(
            instance=instance,
            existing_keys=existing_keys,
            context=context,
            event_type="milestone_completed",
            idempotency_suffix=f"milestone_completed:{milestone.milestone_id}:{_evidence_key(context)}",
            occurred_at=context.occurred_at,
            payload={"milestone_id": milestone.milestone_id, "order_index": milestone.order_index},
            milestone_id=milestone.milestone_id,
            conversation_id=context.conversation.conversation_id,
        )

    def _apply_outcomes(
        self,
        instance: JourneyInstance,
        version: JourneyDefinitionVersion,
        context: "_TrackerContext",
        existing_keys: set[str],
    ) -> list[JourneyEvent]:
        if instance.status != "open":
            return []
        matched_outcome = next(
            (
                outcome
                for outcome in sorted(ALLOWED_OUTCOME_RULE_KEYS)
                if _predicates_match(version.rules.outcome_rules.get(outcome, []), context)
            ),
            None,
        )
        if matched_outcome is None:
            return []
        return self._record_outcome_and_close(
            instance=instance,
            existing_keys=existing_keys,
            context=context,
            outcome=matched_outcome,
            idempotency_suffix_seed=_evidence_key(context),
        )

    def _maybe_auto_abandon_instance(
        self,
        *,
        instance: JourneyInstance,
        version: JourneyDefinitionVersion,
        context: "_TrackerContext",
        existing_keys: set[str],
    ) -> list[JourneyEvent]:
        policy = version.rules.abandonment_policy
        if instance.status != "open" or policy.inactive_after_seconds is None:
            return []
        inactive_seconds = (context.occurred_at - instance.last_activity_at).total_seconds()
        if inactive_seconds < policy.inactive_after_seconds:
            return []
        return self._record_outcome_and_close(
            instance=instance,
            existing_keys=existing_keys,
            context=context,
            outcome=policy.close_as,
            idempotency_suffix_seed=f"abandonment:{_evidence_key(context)}",
            payload={
                "reason": "inactive_timeout",
                "inactive_after_seconds": policy.inactive_after_seconds,
                "inactive_for_seconds": max(0, int(inactive_seconds)),
            },
        )

    def _record_outcome_and_close(
        self,
        *,
        instance: JourneyInstance,
        existing_keys: set[str],
        context: "_TrackerContext",
        outcome: str,
        idempotency_suffix_seed: str,
        payload: dict[str, Any] | None = None,
    ) -> list[JourneyEvent]:
        instance.status = outcome  # type: ignore[assignment]
        instance.outcome = outcome
        instance.ended_at = context.occurred_at
        instance.last_activity_at = context.occurred_at
        instance.updated_at = context.occurred_at
        extra_payload = dict(payload or {})
        outcome_events = self._emit_event(
            instance=instance,
            existing_keys=existing_keys,
            context=context,
            event_type="outcome_recorded",
            idempotency_suffix=f"outcome_recorded:{outcome}:{idempotency_suffix_seed}",
            occurred_at=context.occurred_at,
            payload={"outcome": outcome, **extra_payload},
            conversation_id=context.conversation.conversation_id,
        )
        closed_events = self._emit_event(
            instance=instance,
            existing_keys=existing_keys,
            context=context,
            event_type="journey_closed",
            idempotency_suffix=f"journey_closed:{outcome}:{idempotency_suffix_seed}",
            occurred_at=context.occurred_at,
            payload={"status": outcome, "outcome": outcome, **extra_payload},
            conversation_id=context.conversation.conversation_id,
        )
        return [*outcome_events, *closed_events]

    def _find_merge_candidate(
        self,
        *,
        organization_id: str,
        definition_id: str,
        subject_key: str,
        occurred_at: datetime,
        organization_scope_id: str | None,
        excluded_journey_ids: set[str] | None = None,
    ) -> tuple[JourneyInstance | None, JourneyDefinitionVersion | None]:
        excluded = excluded_journey_ids or set()
        for candidate in self._sorted_merge_candidates(
            organization_id=organization_id,
            definition_id=definition_id,
            subject_key=subject_key,
            occurred_at=occurred_at,
        ):
            if candidate.journey_id in excluded:
                continue
            version = self._definition_store.load_version(
                candidate.definition_version_id,
                organization_id=organization_scope_id,
            )
            if version is None:
                continue
            if not self._merge_policy_allows(version=version, candidate=candidate, occurred_at=occurred_at):
                continue
            return candidate, version
        return None, None

    def _find_merge_candidate_for_version(
        self,
        *,
        organization_id: str,
        definition_id: str,
        subject_key: str,
        version: JourneyDefinitionVersion,
        occurred_at: datetime,
        excluded_journey_ids: set[str] | None = None,
    ) -> JourneyInstance | None:
        excluded = excluded_journey_ids or set()
        for candidate in self._sorted_merge_candidates(
            organization_id=organization_id,
            definition_id=definition_id,
            subject_key=subject_key,
            occurred_at=occurred_at,
        ):
            if candidate.journey_id in excluded:
                continue
            if self._merge_policy_allows(version=version, candidate=candidate, occurred_at=occurred_at):
                return candidate
        return None

    def _sorted_merge_candidates(
        self,
        *,
        organization_id: str,
        definition_id: str,
        subject_key: str,
        occurred_at: datetime,
    ) -> list[JourneyInstance]:
        return sorted(
            [
                candidate
                for candidate in self._instance_store.list_instances(
                    organization_id=organization_id,
                    definition_id=definition_id,
                    subject_key=subject_key,
                )
                if candidate.status != "open"
                and candidate.ended_at is not None
                and candidate.ended_at <= occurred_at
            ],
            key=lambda candidate: (
                candidate.ended_at or candidate.updated_at,
                candidate.updated_at,
                candidate.journey_id,
            ),
            reverse=True,
        )

    def _merge_policy_allows(
        self,
        *,
        version: JourneyDefinitionVersion,
        candidate: JourneyInstance,
        occurred_at: datetime,
    ) -> bool:
        policy = version.rules.merge_policy
        if policy.reopen_closed_within_seconds is None:
            return False
        if candidate.status not in set(policy.reopen_statuses):
            return False
        if candidate.ended_at is None:
            return False
        elapsed_seconds = (occurred_at - candidate.ended_at).total_seconds()
        return 0 <= elapsed_seconds <= policy.reopen_closed_within_seconds

    def _emit_event(
        self,
        *,
        instance: JourneyInstance,
        existing_keys: set[str],
        context: "_TrackerContext",
        event_type: str,
        idempotency_suffix: str,
        occurred_at: datetime,
        payload: dict[str, Any],
        touchpoint_id: str | None = None,
        conversation_id: str | None = None,
        milestone_id: str | None = None,
        source: JourneyEventSource = "runtime_rule",
    ) -> list[JourneyEvent]:
        idempotency_key = f"{instance.journey_id}:{idempotency_suffix}"
        if idempotency_key in existing_keys:
            return []
        event = JourneyEvent(
            organization_id=instance.organization_id,
            journey_id=instance.journey_id,
            touchpoint_id=touchpoint_id,
            conversation_id=conversation_id,
            turn_trace_id=None if context.trace is None else context.trace.trace_id,
            realtime_event_id=None if context.realtime_event is None else context.realtime_event.event_id,
            tool_invocation_id=None,
            event_type=event_type,  # type: ignore[arg-type]
            milestone_id=milestone_id,
            source=source,
            idempotency_key=idempotency_key,
            payload=payload,
            occurred_at=occurred_at,
            created_at=occurred_at,
        )
        self._instance_store.append_events([event])
        existing_keys.add(idempotency_key)
        return [event]

    def _is_first_trace(self, trace: TurnTrace) -> bool:
        traces = self._trace_store.by_conversation(trace.conversation_id, organization_id=trace.organization_id)
        return bool(traces) and traces[0].trace_id == trace.trace_id


class _TrackerContext:
    def __init__(
        self,
        *,
        conversation: ConversationState,
        occurred_at: datetime,
        trace: TurnTrace | None = None,
        realtime_event: RealtimeEvent | None = None,
        is_first_conversation_evidence: bool = False,
    ) -> None:
        self.conversation = conversation
        self.trace = trace
        self.realtime_event = realtime_event
        self.occurred_at = occurred_at
        self.is_first_conversation_evidence = is_first_conversation_evidence


@dataclass(frozen=True)
class _RebuildEvidence:
    occurred_at: datetime
    kind_priority: int
    stable_id: str
    trace: TurnTrace | None = None
    realtime_event: RealtimeEvent | None = None


def _trace_recorded_at(trace: TurnTrace, *, fallback: datetime) -> datetime:
    return trace.recorded_at if trace.recorded_at.tzinfo is not None else fallback


def _evidence_conversation_id(item: _RebuildEvidence) -> str:
    if item.trace is not None:
        return item.trace.conversation_id
    if item.realtime_event is not None:
        return item.realtime_event.conversation_id
    raise ValueError("rebuild evidence is missing both trace and realtime event")


def _apply_trace_to_conversation(conversation: ConversationState, trace: TurnTrace) -> ConversationState:
    updated = conversation.model_copy(deep=True)
    occurred_at = _trace_recorded_at(trace, fallback=conversation.updated_at)
    updated.step_id = trace.step_after
    updated.updated_at = occurred_at
    for fact_update in trace.fact_updates:
        updated.facts[fact_update.name] = fact_update.value
    if trace.chosen_action.type == "end":
        outcome = trace.chosen_action.payload.get("outcome") or trace.chosen_action.payload.get("terminal_disposition")
        updated.status = "ended"
        updated.ended_at = occurred_at
        if isinstance(outcome, str):
            updated.outcome = outcome  # type: ignore[assignment]
    return updated


def _derive_subject_key(definition: JourneyDefinition, conversation: ConversationState) -> str | None:
    strategy = definition.subject_strategy

    def _resolve(kind: str, value: str) -> str | None:
        if kind == "fact_name":
            return _normalize_subject_value(conversation.facts.get(value))
        if kind == "metadata_path":
            return _normalize_subject_value(_lookup_path(conversation.metadata, value))
        if kind == "channel_identity":
            if value == "*":
                return _normalize_subject_value(
                    conversation.metadata.get("participant_identity")
                    or conversation.metadata.get("phone_number")
                    or conversation.metadata.get("participant_ref")
                )
            return _normalize_subject_value(
                conversation.metadata.get(value) or _lookup_path(conversation.metadata, value)
            )
        if kind == "external_ref":
            refs = conversation.metadata.get("external_refs")
            if isinstance(refs, dict):
                return _normalize_subject_value(refs.get(value))
            return _normalize_subject_value(_lookup_path(conversation.metadata, value))
        return None

    resolved = _resolve(strategy.kind, strategy.value)
    if resolved is not None:
        return resolved
    if strategy.fallback_kind is not None and strategy.fallback_value is not None:
        return _resolve(strategy.fallback_kind, strategy.fallback_value)
    return None


def _lookup_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _normalize_subject_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _predicates_match(predicates: list[JourneyRulePredicate], context: _TrackerContext) -> bool:
    if not predicates:
        return False
    return all(_predicate_matches(predicate, context) for predicate in predicates)


def _predicate_matches(predicate: JourneyRulePredicate, context: _TrackerContext) -> bool:
    conversation = context.conversation
    trace = context.trace
    event = context.realtime_event
    summary_payload = None if event is None else _summary_event_payload(event)

    if predicate.kind == "conversation_started":
        return context.is_first_conversation_evidence
    if predicate.kind == "step_entered":
        if trace is not None:
            return trace.step_after == predicate.value
        if event is not None:
            return (
                _normalize_subject_value(event.payload.get("step_id")) == predicate.value
                or _normalize_subject_value(event.payload.get("step_after")) == predicate.value
            )
        return False
    if predicate.kind == "terminal_disposition":
        outcome = conversation.outcome
        if outcome is None and event is not None:
            outcome = _normalize_subject_value(
                event.payload.get("outcome") or event.payload.get("terminal_disposition")
            )
        return outcome == predicate.value
    if predicate.kind == "fact_present":
        return predicate.value in conversation.facts and conversation.facts.get(predicate.value) is not None
    if predicate.kind == "fact_equals":
        expected = predicate.metadata.get("equals", predicate.metadata.get("expected"))
        path = predicate.metadata.get("path")
        return fact_value_equals(
            conversation.facts.get(predicate.value),
            expected,
            path=path if isinstance(path, str) else None,
        )
    if predicate.kind == "tool_succeeded":
        return _tool_status_matches(trace.tool_calls if trace else [], predicate.value, {"success"}) if trace else False
    if predicate.kind == "tool_failed":
        return _tool_status_matches(
            trace.tool_calls if trace else [],
            predicate.value,
            {"blocked", "timeout", "error", "cancelled"},
        ) if trace else False
    if predicate.kind == "semantic_event":
        return _semantic_event_matches(trace.semantic_events if trace else [], predicate.value) if trace else False
    if predicate.kind == "realtime_event":
        return _realtime_event_matches(event, predicate.value) if event is not None else False
    if predicate.kind == "summary_primary_intent":
        return _summary_value_matches(summary_payload, "primary_intent_name", predicate.value)
    if predicate.kind == "summary_tag":
        return _summary_tag_matches(summary_payload, predicate.value)
    if predicate.kind == "summary_outcome":
        return _summary_value_matches(summary_payload, "outcome", predicate.value)
    if predicate.kind == "summary_resolution_status":
        return _summary_value_matches(summary_payload, "resolution_status", predicate.value)
    return False


def _tool_status_matches(tool_calls: list[ToolCallRecord], tool_ref: str | None, statuses: set[str]) -> bool:
    if tool_ref is None:
        return False
    return any(call.tool_ref == tool_ref and call.status in statuses for call in tool_calls)


def _semantic_event_matches(events: list[SemanticEventRecord], target: str | None) -> bool:
    if target is None:
        return False
    return any(target in {event.key, event.name} for event in events)


def _realtime_event_matches(event: RealtimeEvent, target: str | None) -> bool:
    if target is None:
        return False
    return target in {event.name, f"{event.family}:{event.name}"}


def _summary_event_payload(event: RealtimeEvent) -> dict[str, Any] | None:
    if event.family != "semantic_summary" or event.name != "finalized":
        return None
    payload = event.payload
    return payload if isinstance(payload, dict) else None


def _summary_value_matches(payload: dict[str, Any] | None, key: str, target: str | None) -> bool:
    if payload is None or target is None:
        return False
    return _normalize_subject_value(payload.get(key)) == target


def _summary_tag_matches(payload: dict[str, Any] | None, target: str | None) -> bool:
    if payload is None or target is None:
        return False
    tag_names = payload.get("tag_names")
    if not isinstance(tag_names, list):
        return False
    normalized = {_normalize_subject_value(item) for item in tag_names}
    return target in normalized


def _evidence_key(context: _TrackerContext) -> str:
    if context.trace is not None:
        return f"trace:{context.trace.trace_id}"
    if context.realtime_event is not None:
        return f"event:{context.realtime_event.event_id}"
    return "context"
