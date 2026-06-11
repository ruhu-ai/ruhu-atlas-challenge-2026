from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ruhu.journeys import (
    JourneyAbandonmentSweepRequest,
    JourneyAnalyticsRebuildRequest,
    InMemoryJourneyDefinitionStore,
    InMemoryJourneyInstanceStore,
    JourneyDefinitionCreate,
    JourneyDefinitionRebuildRequest,
    JourneyDefinitionRules,
    JourneyDefinitionUpdate,
    JourneyDefinitionVersion,
    JourneyDefinitionVersionCreate,
    JourneyDefinitionVersionUpdate,
    JourneyAnnotationCreate,
    JourneyInstance,
    JourneyMilestoneRule,
    JourneyRulePredicate,
    JourneyService,
    JourneyServiceError,
    JourneyTracker,
    JourneyTouchpoint,
    SubjectKeyStrategy,
)
from ruhu.schemas import ActionRecord, ConversationState, TurnTrace
from ruhu.stores import InMemoryConversationStore, InMemoryTraceStore


def _rules(*, milestone_id: str = "discover") -> JourneyDefinitionRules:
    return JourneyDefinitionRules(
        entry_rules=[JourneyRulePredicate(kind="conversation_started")],
        milestones=[
            JourneyMilestoneRule(
                milestone_id=milestone_id,
                name=milestone_id.title(),
                order_index=1,
                enter_when=[JourneyRulePredicate(kind="step_entered", value=milestone_id)],
            )
        ],
        outcome_rules={"completed": [JourneyRulePredicate(kind="fact_present", value="booking_id")]},
    )


def _build_tracker(
    *,
    definition_store: InMemoryJourneyDefinitionStore,
    instance_store: InMemoryJourneyInstanceStore,
    conversation_store: InMemoryConversationStore,
    trace_store: InMemoryTraceStore,
) -> JourneyTracker:
    return JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )


def test_journey_service_create_definition_and_slug_conflict() -> None:
    service = JourneyService(InMemoryJourneyDefinitionStore())

    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
        created_by_user_id="user-1",
    )

    assert definition.slug == "demo-booking"
    assert definition.created_by_user_id == "user-1"

    with pytest.raises(JourneyServiceError) as exc_info:
        service.create_definition(
            JourneyDefinitionCreate(
                slug="demo-booking",
                name="Duplicate",
                subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
            ),
            organization_id="org-1",
        )

    assert exc_info.value.code == "journey.definition.slug_conflict"


def test_journey_service_update_definition() -> None:
    service = JourneyService(InMemoryJourneyDefinitionStore())
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )

    updated = service.update_definition(
        definition.definition_id,
        JourneyDefinitionUpdate(slug="enterprise-demo-booking", name="Enterprise demo booking", tags=["sales"]),
        organization_id="org-1",
    )

    assert updated.slug == "enterprise-demo-booking"
    assert updated.name == "Enterprise demo booking"
    assert updated.tags == ["sales"]


def test_journey_service_create_version_updates_current_draft_and_review_summary() -> None:
    store = InMemoryJourneyDefinitionStore()
    service = JourneyService(store)
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )

    version1 = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules(milestone_id="discover")),
        organization_id="org-1",
        created_by_user_id="user-1",
    )
    version2 = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules(milestone_id="book_demo")),
        organization_id="org-1",
    )

    reloaded_definition = service.get_definition(definition.definition_id, organization_id="org-1")
    assert version1.version_number == 1
    assert version2.version_number == 2
    assert version2.based_on_version_id == version1.definition_version_id
    assert reloaded_definition.current_draft_version_id == version2.definition_version_id
    assert version2.review_summary["can_publish"] is True
    assert version2.compiled_rules["milestone_ids_in_order"] == ["book_demo"]


def test_journey_service_update_version_recomputes_review_and_rejects_published() -> None:
    store = InMemoryJourneyDefinitionStore()
    service = JourneyService(store)
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules()),
        organization_id="org-1",
    )

    updated = service.update_version(
        version.definition_version_id,
        JourneyDefinitionVersionUpdate(rules=_rules(milestone_id="qualified")),
        organization_id="org-1",
    )
    assert updated.compiled_rules["milestone_ids_in_order"] == ["qualified"]

    store.save_version(
        updated.model_copy(
            update={
                "status": "published",
                "review_summary": updated.review_summary,
            }
        )
    )
    with pytest.raises(JourneyServiceError) as exc_info:
        service.update_version(
            updated.definition_version_id,
            JourneyDefinitionVersionUpdate(rules=_rules(milestone_id="closed")),
            organization_id="org-1",
        )

    assert exc_info.value.code == "journey.definition_version.read_only"


def test_journey_service_review_uses_current_draft() -> None:
    service = JourneyService(InMemoryJourneyDefinitionStore())
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules()),
        organization_id="org-1",
    )

    review = service.review_definition(definition.definition_id, organization_id="org-1")

    assert review.definition_version_id == version.definition_version_id
    assert review.can_publish is True


def test_journey_service_build_publish_readiness_warns_on_first_publish() -> None:
    service = JourneyService(InMemoryJourneyDefinitionStore())
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules()),
        organization_id="org-1",
    )

    readiness = service.build_publish_readiness(definition.definition_id, organization_id="org-1")

    assert readiness.definition_id == definition.definition_id
    assert readiness.draft_version_id == version.definition_version_id
    assert readiness.can_publish is True
    assert any(item.code == "journey.definition.first_publish_pending" for item in readiness.warnings)


def test_journey_service_publish_definition_updates_pointers() -> None:
    service = JourneyService(InMemoryJourneyDefinitionStore())
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules()),
        organization_id="org-1",
    )

    published = service.publish_definition(definition.definition_id, organization_id="org-1")
    refreshed_definition = service.get_definition(definition.definition_id, organization_id="org-1")

    assert published.definition_version_id == version.definition_version_id
    assert published.status == "published"
    assert published.published_at is not None
    assert refreshed_definition.current_published_version_id == version.definition_version_id
    assert refreshed_definition.current_draft_version_id is None


def test_journey_service_duplicates_definition_with_draft_version() -> None:
    service = JourneyService(InMemoryJourneyDefinitionStore())
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
        created_by_user_id="user-1",
    )
    source_version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules()),
        organization_id="org-1",
    )

    duplicate = service.duplicate_definition(
        definition.definition_id,
        organization_id="org-1",
        created_by_user_id="user-2",
    )

    assert duplicate.definition_id != definition.definition_id
    assert duplicate.slug == "demo-booking-copy"
    assert duplicate.name == "Demo booking Copy"
    assert duplicate.created_by_user_id == "user-2"
    assert duplicate.current_draft_version_id is not None
    duplicate_versions = service.list_versions(duplicate.definition_id, organization_id="org-1")
    assert len(duplicate_versions) == 1
    assert duplicate_versions[0].version_number == 1
    assert duplicate_versions[0].rules.model_dump(mode="json") == source_version.rules.model_dump(mode="json")


def test_journey_service_archives_definition() -> None:
    service = JourneyService(InMemoryJourneyDefinitionStore())
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )

    archived = service.archive_definition(definition.definition_id, organization_id="org-1")

    assert archived.status == "archived"


def test_journey_service_publish_definition_rejects_blocked_review() -> None:
    service = JourneyService(InMemoryJourneyDefinitionStore())
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(
            rules=JourneyDefinitionRules(
                entry_rules=[JourneyRulePredicate(kind="conversation_started")],
                milestones=[
                    JourneyMilestoneRule(
                        milestone_id="discover",
                        name="Discover",
                        order_index=1,
                        enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
                    )
                ],
                outcome_rules={"unexpected": [JourneyRulePredicate(kind="fact_present", value="booking_id")]},
            )
        ),
        organization_id="org-1",
    )

    with pytest.raises(JourneyServiceError) as exc_info:
        service.publish_definition(definition.definition_id, organization_id="org-1")

    assert exc_info.value.code == "journey.definition.publish_blocked"


def test_journey_service_lists_instances_annotations_and_analytics() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    service = JourneyService(definition_store, instance_store)
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules()),
        organization_id="org-1",
    )
    service.publish_definition(definition.definition_id, organization_id="org-1")

    now = datetime.now(timezone.utc)
    instance = JourneyInstance(
        organization_id="org-1",
        definition_id=definition.definition_id,
        definition_version_id=version.definition_version_id,
        subject_key="subject-1",
        current_milestone_id="discover",
        current_milestone_order=1,
        started_at=now,
        last_activity_at=now,
    )
    instance_store.save_instance(instance)
    instance_store.save_touchpoint(
        JourneyTouchpoint(
            organization_id="org-1",
            journey_id=instance.journey_id,
            conversation_id="conv-1",
            agent_id="agent-1",
            channel="web_chat",
            mode="live",
            started_at=now,
            created_at=now,
            updated_at=now,
        )
    )

    journeys, total_count = service.list_instances(organization_id="org-1", channel="web_chat")
    assert total_count == 1
    assert journeys[0].journey_id == instance.journey_id

    annotation = service.annotate_instance(
        instance.journey_id,
        payload=JourneyAnnotationCreate(note="Reviewed"),
        organization_id="org-1",
        actor_user_id="user-1",
    )
    assert annotation.event_type == "manual_annotation"

    funnel = service.analytics_funnel(
        organization_id="org-1",
        definition_id=definition.definition_id,
    )
    assert funnel.total_journeys == 1
    assert funnel.stages[0].entered_count == 1

    channel_mix = service.analytics_channel_mix(
        organization_id="org-1",
        definition_id=definition.definition_id,
    )
    assert channel_mix.rows[0].channel == "web_chat"


def test_journey_service_replay_journey_rebuilds_projection_and_preserves_manual_events() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    service = JourneyService(definition_store, instance_store)

    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="replay-booking",
            name="Replay booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules()),
        organization_id="org-1",
    )
    service.publish_definition(definition.definition_id, organization_id="org-1")

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-replay",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-replay", "booking_id": "book-1"},
        started_at=now,
        updated_at=now,
    )
    trace = TurnTrace(
        trace_id="trace-replay",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-replay",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
        recorded_at=now,
    )
    conversation_store.save(conversation)
    trace_store.append(trace)

    tracker = _build_tracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )
    tracker.process_turn_trace(trace, conversation=conversation)

    journey = instance_store.list_instances(
        organization_id="org-1",
        definition_id=definition.definition_id,
    )[0]
    service.annotate_instance(
        journey.journey_id,
        payload=JourneyAnnotationCreate(note="Preserve this"),
        organization_id="org-1",
        actor_user_id="user-1",
    )

    corrupted = journey.model_copy(
        update={
            "status": "open",
            "outcome": None,
            "current_milestone_id": None,
            "current_milestone_order": None,
            "milestone_path": [],
            "ended_at": None,
            "updated_at": now,
        }
    )
    instance_store.save_instance(corrupted)

    replay = service.replay_journey(
        journey.journey_id,
        organization_id="org-1",
        tracker=tracker,
    )

    assert replay.journey_id == journey.journey_id
    assert replay.definition_id == definition.definition_id
    assert replay.definition_version_id == version.definition_version_id
    assert replay.preserved_event_count == 1
    rebuilt = service.get_instance(journey.journey_id, organization_id="org-1")
    assert rebuilt.status == "completed"
    assert rebuilt.outcome == "completed"
    assert rebuilt.milestone_path == ["discover"]
    events = service.list_events(journey.journey_id, organization_id="org-1").events
    assert any(event.event_type == "manual_annotation" for event in events)
    assert any(event.event_type == "journey_opened" for event in events)


def test_journey_service_rebuild_analytics_persists_all_snapshot_views() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    service = JourneyService(definition_store, instance_store)
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="analytics-booking",
            name="Analytics booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules()),
        organization_id="org-1",
    )
    service.publish_definition(definition.definition_id, organization_id="org-1")

    now = datetime.now(timezone.utc)
    instance_store.save_instance(
        JourneyInstance(
            organization_id="org-1",
            definition_id=definition.definition_id,
            definition_version_id=version.definition_version_id,
            subject_key="subject-analytics",
            current_milestone_id="discover",
            current_milestone_order=1,
            milestone_path=["discover"],
            status="completed",
            outcome="completed",
            started_at=now,
            last_activity_at=now,
            ended_at=now,
        )
    )

    rebuilt = service.rebuild_analytics(
        JourneyAnalyticsRebuildRequest(definition_id=definition.definition_id),
        organization_id="org-1",
    )

    assert rebuilt.definition_id == definition.definition_id
    assert rebuilt.snapshot_count == 5
    assert rebuilt.rebuilt_views == ["funnel", "drop_off", "paths", "trends", "channel_mix"]
    assert len(instance_store.list_snapshots(organization_id="org-1", definition_id=definition.definition_id)) == 5


def test_journey_service_rebuild_definition_discovers_and_backfills_conversations() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    conversation_store = InMemoryConversationStore()
    trace_store = InMemoryTraceStore()
    service = JourneyService(definition_store, instance_store)

    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="backfill-booking",
            name="Backfill booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(rules=_rules()),
        organization_id="org-1",
    )
    service.publish_definition(definition.definition_id, organization_id="org-1")

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-backfill",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="agent-version-1",
        channel="web_chat",
        step_id="discover",
        facts={"customer_id": "subject-backfill", "booking_id": "book-1"},
        started_at=now,
        updated_at=now,
    )
    trace = TurnTrace(
        trace_id="trace-backfill",
        conversation_id=conversation.conversation_id,
        organization_id="org-1",
        turn_id="turn-backfill",
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        step_before="entry",
        step_after="discover",
        chosen_action=ActionRecord(type="reply", reason="hello"),
        recorded_at=now,
    )
    conversation_store.save(conversation)
    trace_store.append(trace)
    tracker = _build_tracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )

    rebuilt = service.rebuild_definition(
        definition.definition_id,
        JourneyDefinitionRebuildRequest(),
        organization_id="org-1",
        tracker=tracker,
    )

    assert rebuilt.definition_id == definition.definition_id
    assert rebuilt.discovered_conversation_count == 1
    assert rebuilt.discovered_subject_count == 1
    assert rebuilt.failures == []
    instance = service.get_instance(rebuilt.replayed_journey_ids[0], organization_id="org-1")
    assert instance.definition_version_id == version.definition_version_id
    assert instance.status == "completed"
    assert instance.outcome == "completed"


def test_journey_service_sweep_abandonment_closes_stale_open_journeys() -> None:
    definition_store = InMemoryJourneyDefinitionStore()
    instance_store = InMemoryJourneyInstanceStore()
    service = JourneyService(definition_store, instance_store)
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="abandon-booking",
            name="Abandon booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )
    version = service.create_version(
        definition.definition_id,
        JourneyDefinitionVersionCreate(
            rules=JourneyDefinitionRules(
                entry_rules=[JourneyRulePredicate(kind="conversation_started")],
                milestones=[
                    JourneyMilestoneRule(
                        milestone_id="discover",
                        name="Discover",
                        order_index=1,
                        enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
                    )
                ],
                outcome_rules={},
                abandonment_policy={"inactive_after_seconds": 60, "close_as": "abandoned"},
            )
        ),
        organization_id="org-1",
    )
    service.publish_definition(definition.definition_id, organization_id="org-1")

    now = datetime.now(timezone.utc)
    stale_instance = JourneyInstance(
        organization_id="org-1",
        definition_id=definition.definition_id,
        definition_version_id=version.definition_version_id,
        subject_key="subject-abandon",
        current_milestone_id="discover",
        current_milestone_order=1,
        started_at=now,
        last_activity_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=5),
    )
    instance_store.save_instance(stale_instance)

    swept = service.sweep_abandonment(
        JourneyAbandonmentSweepRequest(definition_id=definition.definition_id),
        organization_id="org-1",
    )

    assert swept.abandoned_journey_ids == [stale_instance.journey_id]
    updated = service.get_instance(stale_instance.journey_id, organization_id="org-1")
    assert updated.status == "abandoned"
    assert updated.outcome == "abandoned"


def test_journey_service_build_publish_readiness_requires_draft() -> None:
    service = JourneyService(InMemoryJourneyDefinitionStore())
    definition = service.create_definition(
        JourneyDefinitionCreate(
            slug="demo-booking",
            name="Demo booking",
            subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        ),
        organization_id="org-1",
    )

    readiness = service.build_publish_readiness(definition.definition_id, organization_id="org-1")

    assert readiness.can_publish is False
    assert any(item.code == "journey.definition.no_draft" for item in readiness.blockers)
