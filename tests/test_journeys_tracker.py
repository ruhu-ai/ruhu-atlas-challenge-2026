from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ruhu.journeys import (
    InMemoryJourneyDefinitionStore,
    InMemoryJourneyInstanceStore,
    JourneyDefinitionCreate,
    JourneyDefinitionVersionCreate,
    JourneyMilestoneRule,
    JourneyRulePredicate,
    JourneyService,
    JourneyTracker,
    SubjectKeyStrategy,
)
from ruhu.realtime import RealtimeEvent
from ruhu.schemas import ActionRecord, ConversationState, FactUpdate, TurnTrace
from ruhu.stores import InMemoryConversationStore, InMemoryTraceStore


class InMemoryRealtimeEventStore:
    def __init__(self) -> None:
        self._events: dict[str, RealtimeEvent] = {}

    def save(self, event: RealtimeEvent) -> None:
        self._events[event.event_id] = event

    def load(self, event_id: str) -> RealtimeEvent | None:
        return self._events.get(event_id)

    def replay(
        self,
        *,
        conversation_id: str,
        after_sequence: int | None = None,
        after_event_id: str | None = None,
    ) -> list[RealtimeEvent]:
        events = [event for event in self._events.values() if event.conversation_id == conversation_id]
        if after_sequence is not None:
            events = [event for event in events if event.conversation_sequence > after_sequence]
        elif after_event_id is not None and after_event_id in self._events:
            anchor = self._events[after_event_id]
            events = [event for event in events if event.conversation_sequence > anchor.conversation_sequence]
        return sorted(events, key=lambda item: item.conversation_sequence)


def _publish_definition(
    service: JourneyService,
    *,
    slug: str,
    milestones: list[JourneyMilestoneRule],
    outcome_rules: dict[str, list[JourneyRulePredicate]] | None = None,
    touchpoint_rules: list[JourneyRulePredicate] | None = None,
    abandonment_policy: dict[str, object] | None = None,
    merge_policy: dict[str, object] | None = None,
) -> tuple[str, str]:
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug=slug,
            name=slug,
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(
            rules={
                "entry_rules": [JourneyRulePredicate(kind="conversation_started")],
                "touchpoint_rules": touchpoint_rules or [],
                "milestones": milestones,
                "outcome_rules": outcome_rules or {},
                "abandonment_policy": abandonment_policy or {},
                "merge_policy": merge_policy or {},
            }
        ),
        organization_id="org-1",
    )
    published = service.publish_definition(definition.definition_id, organization_id="org-1")
    return definition.definition_id, published.definition_version_id


def test_journey_tracker_process_turn_trace_opens_and_closes_checkpoint_journey() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    realtime_event_store = InMemoryRealtimeEventStore()
    service = JourneyService(definition_store)

    definition_id, _ = _publish_definition(
        service,
        slug="demo-booking",
        milestones=[
            JourneyMilestoneRule(
                milestone_id="discover",
                name="Discover",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
            )
        ],
        outcome_rules={"completed": [JourneyRulePredicate(kind="fact_present", value="booking_id")]},
    )

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-1",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-1", "booking_id": "book-1"},
        started_at=now,
        updated_at=now,
    )
    trace = TurnTrace(
        trace_id="trace-1",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-1",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
    )
    conversation_store.save(conversation)
    trace_store.append(trace)

    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
        realtime_event_store=realtime_event_store,
    )

    emitted = tracker.process_turn_trace(trace, conversation=conversation)

    assert [event.event_type for event in emitted] == [
        "journey_opened",
        "touchpoint_attached",
        "milestone_entered",
        "milestone_completed",
        "outcome_recorded",
        "journey_closed",
    ]
    assert all(event.turn_trace_id == trace.trace_id for event in emitted)
    assert all(event.realtime_event_id is None for event in emitted)
    instances = instance_store.list_instances(organization_id="org-1", definition_id=definition_id)
    assert len(instances) == 1
    assert instances[0].status == "completed"
    assert instances[0].outcome == "completed"
    assert instances[0].milestone_path == ["discover"]


def test_journey_tracker_process_turn_trace_uses_trace_recorded_at() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    service = JourneyService(definition_store)

    _publish_definition(
        service,
        slug="timed-booking",
        milestones=[
            JourneyMilestoneRule(
                milestone_id="discover",
                name="Discover",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
            )
        ],
    )

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-time",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-time"},
        started_at=now,
        updated_at=now + timedelta(minutes=5),
    )
    trace = TurnTrace(
        trace_id="trace-time",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-time",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
        recorded_at=now,
    )
    conversation_store.save(conversation)
    trace_store.append(trace)
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )

    emitted = tracker.process_turn_trace(trace, conversation=conversation)

    assert emitted
    assert all(event.occurred_at == now for event in emitted)


def test_journey_tracker_requires_touchpoint_rule_before_attaching_new_conversation() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    service = JourneyService(definition_store)

    definition_id, _ = _publish_definition(
        service,
        slug="touchpoint-booking",
        touchpoint_rules=[JourneyRulePredicate(kind="step_entered", value="handoff")],
        milestones=[
            JourneyMilestoneRule(
                milestone_id="discover",
                name="Discover",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
            ),
            JourneyMilestoneRule(
                milestone_id="handoff",
                name="Handoff",
                order_index=2,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="handoff")],
            ),
        ],
    )

    now = datetime.now(timezone.utc)
    first_conversation = ConversationState(
        conversation_id="conv-touch-1",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-touch"},
        started_at=now,
        updated_at=now,
    )
    first_trace = TurnTrace(
        trace_id="trace-touch-1",
        conversation_id=first_conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-touch-1",
        agent_id=first_conversation.agent_id,
        agent_version_id=first_conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
        recorded_at=now,
    )
    second_conversation = ConversationState(
        conversation_id="conv-touch-2",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-touch"},
        started_at=now + timedelta(minutes=5),
        updated_at=now + timedelta(minutes=5),
    )
    blocked_trace = TurnTrace(
        trace_id="trace-touch-2",
        conversation_id=second_conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-touch-2",
        agent_id=second_conversation.agent_id,
        agent_version_id=second_conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
        recorded_at=now + timedelta(minutes=5),
    )
    attach_trace = TurnTrace(
        trace_id="trace-touch-3",
        conversation_id=second_conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-touch-3",
        agent_id=second_conversation.agent_id,
        agent_version_id=second_conversation.agent_version_id,
        step_before="discover",
        step_after="handoff",
        chosen_action=ActionRecord(type="reply", reason="handoff"),
        recorded_at=now + timedelta(minutes=6),
    )

    conversation_store.save(first_conversation)
    conversation_store.save(second_conversation)
    trace_store.append(first_trace)
    trace_store.append(blocked_trace)
    trace_store.append(attach_trace)

    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )

    tracker.process_turn_trace(first_trace, conversation=first_conversation)
    blocked_events = tracker.process_turn_trace(blocked_trace, conversation=second_conversation)
    attached_events = tracker.process_turn_trace(
        attach_trace,
        conversation=second_conversation.model_copy(update={"step_id": "handoff", "updated_at": now + timedelta(minutes=6)}),
    )

    instances = instance_store.list_instances(organization_id="org-1", definition_id=definition_id)
    assert len(instances) == 1
    assert [touchpoint.conversation_id for touchpoint in instance_store.list_touchpoints(instances[0].journey_id, organization_id="org-1")] == [
        "conv-touch-1",
        "conv-touch-2",
    ]
    assert all(event.event_type != "touchpoint_attached" for event in blocked_events)
    assert all(event.event_type != "milestone_entered" or event.milestone_id != "handoff" for event in blocked_events)
    assert any(event.event_type == "touchpoint_attached" for event in attached_events)
    assert any(event.event_type == "milestone_entered" and event.milestone_id == "handoff" for event in attached_events)


def test_journey_tracker_handles_dwell_milestone_completion_across_traces() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    service = JourneyService(definition_store)

    _publish_definition(
        service,
        slug="qualification",
        milestones=[
            JourneyMilestoneRule(
                milestone_id="qualified",
                name="Qualified",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
                complete_when=[JourneyRulePredicate(kind="fact_present", value="email")],
            )
        ],
    )

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-2",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-2"},
        started_at=now,
        updated_at=now,
    )
    trace1 = TurnTrace(
        trace_id="trace-1",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-1",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
    )
    trace2 = TurnTrace(
        trace_id="trace-2",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-2",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="discover",
        step_after="discover",
        fact_updates=[FactUpdate(name="email", value="user@example.com", source="deterministic")],
        chosen_action=ActionRecord(type="reply", reason="captured"),
    )
    conversation_store.save(conversation)
    trace_store.append(trace1)
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )

    first_events = tracker.process_turn_trace(trace1, conversation=conversation)
    assert [event.event_type for event in first_events] == [
        "journey_opened",
        "touchpoint_attached",
        "milestone_entered",
    ]

    conversation_with_email = conversation.model_copy(
        update={
            "facts": {"customer_id": "subject-2", "email": "user@example.com"},
            "updated_at": now + timedelta(minutes=1),
        }
    )
    conversation_store.save(conversation_with_email)
    trace_store.append(trace2)
    second_events = tracker.process_turn_trace(trace2, conversation=conversation_with_email)
    assert [event.event_type for event in second_events] == ["milestone_completed"]

    instance = instance_store.list_instances(organization_id="org-1")[0]
    assert instance.status == "open"
    assert instance.current_milestone_id == "qualified"
    assert instance.milestone_path == ["qualified"]


def test_journey_tracker_process_realtime_event_records_transfer_outcome() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    realtime_event_store = InMemoryRealtimeEventStore()
    service = JourneyService(definition_store)

    definition_id, _ = _publish_definition(
        service,
        slug="handoff-flow",
        milestones=[
            JourneyMilestoneRule(
                milestone_id="discover",
                name="Discover",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
            )
        ],
        outcome_rules={"transferred": [JourneyRulePredicate(kind="realtime_event", value="handoff:transferred")]},
    )

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-3",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-3"},
        started_at=now,
        updated_at=now,
    )
    trace = TurnTrace(
        trace_id="trace-1",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-1",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
    )
    conversation_store.save(conversation)
    trace_store.append(trace)
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
        realtime_event_store=realtime_event_store,
    )
    tracker.process_turn_trace(trace, conversation=conversation)

    event = RealtimeEvent(
        event_id="evt-1",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        family="handoff",
        name="transferred",
        conversation_sequence=2,
        created_at=now + timedelta(minutes=1),
    )
    realtime_event_store.save(event)

    emitted = tracker.process_realtime_event(event, conversation=conversation)
    assert [item.event_type for item in emitted] == ["outcome_recorded", "journey_closed"]
    assert all(item.realtime_event_id == event.event_id for item in emitted)
    assert all(item.turn_trace_id is None for item in emitted)

    instances = instance_store.list_instances(organization_id="org-1", definition_id=definition_id)
    assert len(instances) == 1
    assert instances[0].status == "transferred"
    assert instances[0].outcome == "transferred"


def test_journey_tracker_reopens_recent_closed_journey_when_merge_policy_allows() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    service = JourneyService(definition_store)

    definition_id, _ = _publish_definition(
        service,
        slug="reopenable-booking",
        milestones=[
            JourneyMilestoneRule(
                milestone_id="discover",
                name="Discover",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
            )
        ],
        outcome_rules={"abandoned": [JourneyRulePredicate(kind="fact_present", value="close_now")]},
        merge_policy={
            "reopen_closed_within_seconds": 600,
            "reopen_statuses": ["abandoned"],
        },
    )

    now = datetime.now(timezone.utc)
    first_conversation = ConversationState(
        conversation_id="conv-reopen-1",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-reopen", "close_now": True},
        started_at=now,
        updated_at=now,
    )
    first_trace = TurnTrace(
        trace_id="trace-reopen-1",
        conversation_id=first_conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-reopen-1",
        agent_id=first_conversation.agent_id,
        agent_version_id=first_conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
        recorded_at=now,
    )
    second_conversation = ConversationState(
        conversation_id="conv-reopen-2",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-reopen"},
        started_at=now + timedelta(minutes=5),
        updated_at=now + timedelta(minutes=5),
    )
    second_trace = TurnTrace(
        trace_id="trace-reopen-2",
        conversation_id=second_conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-reopen-2",
        agent_id=second_conversation.agent_id,
        agent_version_id=second_conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello again"),
        recorded_at=now + timedelta(minutes=5),
    )
    conversation_store.save(first_conversation)
    conversation_store.save(second_conversation)
    trace_store.append(first_trace)
    trace_store.append(second_trace)
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )

    first_events = tracker.process_turn_trace(first_trace, conversation=first_conversation)
    second_events = tracker.process_turn_trace(second_trace, conversation=second_conversation)

    assert any(event.event_type == "journey_closed" for event in first_events)
    assert [event.event_type for event in second_events][:2] == ["journey_reopened", "touchpoint_attached"]
    instances = instance_store.list_instances(organization_id="org-1", definition_id=definition_id)
    assert len(instances) == 1
    assert instances[0].status == "open"
    assert instances[0].latest_conversation_id == second_conversation.conversation_id


def test_journey_tracker_auto_abandons_stale_open_journey_before_opening_new_one() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    service = JourneyService(definition_store)

    definition_id, _ = _publish_definition(
        service,
        slug="stale-booking",
        milestones=[
            JourneyMilestoneRule(
                milestone_id="discover",
                name="Discover",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
            )
        ],
        abandonment_policy={
            "inactive_after_seconds": 60,
            "close_as": "abandoned",
        },
    )

    now = datetime.now(timezone.utc)
    first_conversation = ConversationState(
        conversation_id="conv-stale-1",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-stale"},
        started_at=now,
        updated_at=now,
    )
    first_trace = TurnTrace(
        trace_id="trace-stale-1",
        conversation_id=first_conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-stale-1",
        agent_id=first_conversation.agent_id,
        agent_version_id=first_conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
        recorded_at=now,
    )
    second_conversation = ConversationState(
        conversation_id="conv-stale-2",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-stale"},
        started_at=now + timedelta(minutes=2),
        updated_at=now + timedelta(minutes=2),
    )
    second_trace = TurnTrace(
        trace_id="trace-stale-2",
        conversation_id=second_conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-stale-2",
        agent_id=second_conversation.agent_id,
        agent_version_id=second_conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="follow up"),
        recorded_at=now + timedelta(minutes=2),
    )
    conversation_store.save(first_conversation)
    conversation_store.save(second_conversation)
    trace_store.append(first_trace)
    trace_store.append(second_trace)
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )

    tracker.process_turn_trace(first_trace, conversation=first_conversation)
    second_events = tracker.process_turn_trace(second_trace, conversation=second_conversation)

    assert [event.event_type for event in second_events[:4]] == [
        "outcome_recorded",
        "journey_closed",
        "journey_opened",
        "touchpoint_attached",
    ]
    instances = instance_store.list_instances(organization_id="org-1", definition_id=definition_id)
    assert len(instances) == 2
    assert sorted(instance.status for instance in instances) == ["abandoned", "open"]


def test_journey_tracker_process_realtime_event_matches_semantic_summary_predicates() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    realtime_event_store = InMemoryRealtimeEventStore()
    service = JourneyService(definition_store)

    definition_id, _ = _publish_definition(
        service,
        slug="semantic-summary-flow",
        milestones=[
            JourneyMilestoneRule(
                milestone_id="resolved_summary",
                name="Resolved summary",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="summary_tag", value="resolved")],
            )
        ],
        outcome_rules={
            "completed": [JourneyRulePredicate(kind="summary_resolution_status", value="resolved")]
        },
    )
    definition = definition_store.load_definition(definition_id, organization_id="org-1")
    assert definition is not None
    version = definition_store.load_version(
        definition.current_published_version_id or "",
        organization_id="org-1",
    )
    assert version is not None
    version = version.model_copy(
        update={
            "rules": version.rules.model_copy(
                update={
                    "entry_rules": [JourneyRulePredicate(kind="summary_primary_intent", value="demo_request")]
                }
            )
        }
    )
    definition_store.save_version(version)

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-summary",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        status="ended",
        outcome="resolved",
        step_id="demo_requested_done",
        facts={"customer_id": "subject-summary"},
        started_at=now,
        ended_at=now + timedelta(minutes=2),
        updated_at=now + timedelta(minutes=2),
    )
    conversation_store.save(conversation)
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
        realtime_event_store=realtime_event_store,
    )

    event = RealtimeEvent(
        event_id="evt-summary-1",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        family="semantic_summary",
        name="finalized",
        conversation_sequence=3,
        payload={
            "conversation_summary_id": "sum-1",
            "primary_intent_name": "demo_request",
            "resolution_status": "resolved",
            "outcome": "resolved",
            "tag_names": ["resolved", "followup_complete"],
        },
        created_at=conversation.updated_at,
    )
    realtime_event_store.save(event)

    emitted = tracker.process_realtime_event(event, conversation=conversation)

    assert [item.event_type for item in emitted] == [
        "journey_opened",
        "touchpoint_attached",
        "milestone_entered",
        "milestone_completed",
        "outcome_recorded",
        "journey_closed",
    ]
    assert all(item.realtime_event_id == event.event_id for item in emitted)
    instances = instance_store.list_instances(organization_id="org-1", definition_id=definition_id)
    assert len(instances) == 1
    assert instances[0].status == "completed"
    assert instances[0].outcome == "completed"
    assert instances[0].milestone_path == ["resolved_summary"]


def test_journey_tracker_rebuild_replays_realtime_events_against_historical_state() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    realtime_event_store = InMemoryRealtimeEventStore()
    service = JourneyService(definition_store)

    _publish_definition(
        service,
        slug="handoff-review",
        milestones=[
            JourneyMilestoneRule(
                milestone_id="discover",
                name="Discover",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
            )
        ],
        outcome_rules={
            "transferred": [
                JourneyRulePredicate(kind="realtime_event", value="handoff:transferred"),
                JourneyRulePredicate(kind="fact_present", value="agent_requested"),
            ]
        },
    )

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-rebuild-order",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-order", "agent_requested": True},
        started_at=now,
        updated_at=now + timedelta(minutes=2),
    )
    trace1 = TurnTrace(
        trace_id="trace-order-1",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-order-1",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
        recorded_at=now,
    )
    event = RealtimeEvent(
        event_id="evt-order-1",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        family="handoff",
        name="transferred",
        conversation_sequence=2,
        created_at=now + timedelta(minutes=1),
    )
    trace2 = TurnTrace(
        trace_id="trace-order-2",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-order-2",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="discover",
        step_after="discover",
        fact_updates=[FactUpdate(name="agent_requested", value=True, source="deterministic")],
        chosen_action=ActionRecord(type="reply", reason="flagged"),
        recorded_at=now + timedelta(minutes=2),
    )
    conversation_store.save(conversation)
    trace_store.append(trace1)
    trace_store.append(trace2)
    realtime_event_store.save(event)
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
        realtime_event_store=realtime_event_store,
    )

    emitted = tracker.rebuild_from_conversation(conversation.conversation_id, organization_id="org-1")

    assert [item.event_type for item in emitted] == [
        "journey_opened",
        "touchpoint_attached",
        "milestone_entered",
        "milestone_completed",
    ]
    assert all(item.event_type != "journey_closed" for item in emitted)
    instances = instance_store.list_instances(organization_id="org-1")
    assert len(instances) == 1
    assert instances[0].status == "open"
    assert instances[0].outcome is None


def test_journey_tracker_rebuild_from_conversation_is_idempotent() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    service = JourneyService(definition_store)

    _publish_definition(
        service,
        slug="rebuildable",
        milestones=[
            JourneyMilestoneRule(
                milestone_id="discover",
                name="Discover",
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
            )
        ],
    )

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-4",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-4"},
        started_at=now,
        updated_at=now,
    )
    trace = TurnTrace(
        trace_id="trace-1",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-1",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
    )
    conversation_store.save(conversation)
    trace_store.append(trace)
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )

    first = tracker.rebuild_from_conversation(conversation.conversation_id, organization_id="org-1")
    second = tracker.rebuild_from_conversation(conversation.conversation_id, organization_id="org-1")

    assert len(first) == 4
    assert second == []
    instances = instance_store.list_instances(organization_id="org-1")
    assert len(instances) == 1
    events = instance_store.list_events(instances[0].journey_id, organization_id="org-1")
    assert len(events) == 4
