from __future__ import annotations

from datetime import datetime, timezone

from ruhu.db import build_session_factory, tenant_db_context
from tests._fixtures.templates import load_template_agent_document
from ruhu.journeys import (
    JourneyAnalyticsSnapshot,
    JourneyDefinition,
    JourneyDefinitionRules,
    JourneyDefinitionVersion,
    JourneyEvent,
    JourneyInstance,
    JourneyMilestoneRule,
    JourneyRuntimeJob,
    JourneyRulePredicate,
    JourneyTouchpoint,
    SQLAlchemyJourneyDefinitionStore,
    SQLAlchemyJourneyInstanceStore,
    SQLAlchemyJourneyRuntimeJobStore,
    SubjectKeyStrategy,
)
from ruhu.registry import SQLAlchemyAgentRegistry
from ruhu.schemas import ConversationState
from ruhu.stores import SQLAlchemyConversationStore


def _definition(*, organization_id: str = "org-1") -> JourneyDefinition:
    return JourneyDefinition(
        definition_id="journey-def-1",
        organization_id=organization_id,
        slug="demo-booking",
        name="Demo booking",
        subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
    )


def _version(definition: JourneyDefinition, *, version_id: str = "journey-ver-1") -> JourneyDefinitionVersion:
    return JourneyDefinitionVersion(
        definition_version_id=version_id,
        organization_id=definition.organization_id,
        definition_id=definition.definition_id,
        version_number=1,
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
            outcome_rules={"completed": [JourneyRulePredicate(kind="fact_present", value="booking_id")]},
        ),
    )


def test_sqlalchemy_journey_definition_store_roundtrip(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyJourneyDefinitionStore(session_factory)
    definition = _definition()
    version = _version(definition)

    store.save_definition(definition)
    store.save_version(version)

    loaded_definition = store.load_definition(definition.definition_id, organization_id="org-1")
    assert loaded_definition is not None
    assert loaded_definition.slug == "demo-booking"

    loaded_version = store.load_version(version.definition_version_id, organization_id="org-1")
    assert loaded_version is not None
    assert loaded_version.rules.milestones[0].milestone_id == "discover"

    updated_definition = store.set_current_draft(
        definition.definition_id,
        version.definition_version_id,
        organization_id="org-1",
    )
    assert updated_definition is not None
    assert updated_definition.current_draft_version_id == version.definition_version_id

    published_version = store.publish_version(
        definition.definition_id,
        version.definition_version_id,
        organization_id="org-1",
    )
    assert published_version is not None
    assert published_version.status == "published"

    refreshed_definition = store.load_definition(definition.definition_id, organization_id="org-1")
    assert refreshed_definition is not None
    assert refreshed_definition.current_published_version_id == version.definition_version_id
    assert refreshed_definition.current_draft_version_id is None
    assert store.set_current_draft(definition.definition_id, "missing-version", organization_id="org-1") is None
    assert store.load_definition(definition.definition_id, organization_id="org-2") is None


def test_sqlalchemy_journey_instance_store_roundtrip(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    registry = SQLAlchemyAgentRegistry(session_factory)
    snapshot = registry.create_agent_document(
        agent_id="sales",
        agent_name="Sales Agent",
        document=load_template_agent_document("sales-agent.json"),
        organization_id="org-1",
    )

    definition_store = SQLAlchemyJourneyDefinitionStore(session_factory)
    instance_store = SQLAlchemyJourneyInstanceStore(session_factory)
    conversation_store = SQLAlchemyConversationStore(session_factory)

    definition = _definition()
    version = _version(definition)
    definition_store.save_definition(definition)
    definition_store.save_version(version)

    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-1",
        organization_id="org-1",
        agent_id=snapshot.agent_id,
        agent_version_id=snapshot.version_id,
        step_id="discover",
        facts={"customer_id": "subject-1", "booking_id": "book-1"},
        updated_at=now,
    )
    conversation_store.save(conversation)

    instance = JourneyInstance(
        journey_id="journey-1",
        organization_id="org-1",
        definition_id=definition.definition_id,
        definition_version_id=version.definition_version_id,
        subject_key="subject-1",
        subject_summary={"kind": "lead"},
        current_milestone_id="discover",
        current_milestone_order=1,
        milestone_path=["discover"],
        first_conversation_id=conversation.conversation_id,
        latest_conversation_id=conversation.conversation_id,
        first_agent_id=snapshot.agent_id,
        first_agent_version_id=snapshot.version_id,
        latest_agent_id=snapshot.agent_id,
        latest_agent_version_id=snapshot.version_id,
        started_at=now,
        last_activity_at=now,
        created_at=now,
        updated_at=now,
    )
    instance_store.save_instance(instance)

    touchpoint = JourneyTouchpoint(
        touchpoint_id="touch-1",
        organization_id="org-1",
        journey_id=instance.journey_id,
        conversation_id=conversation.conversation_id,
        agent_id=snapshot.agent_id,
        agent_version_id=snapshot.version_id,
        channel="web_chat",
        mode="live",
        entry_reason="conversation_started",
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    instance_store.save_touchpoint(touchpoint)

    event = JourneyEvent(
        journey_event_id="event-1",
        organization_id="org-1",
        journey_id=instance.journey_id,
        touchpoint_id=touchpoint.touchpoint_id,
        conversation_id=conversation.conversation_id,
        event_type="milestone_entered",
        milestone_id="discover",
        source="runtime_rule",
        idempotency_key="conv-1:discover:1",
        payload={"state": "discover"},
        occurred_at=now,
        created_at=now,
    )
    instance_store.append_events([event])

    snapshot_record = JourneyAnalyticsSnapshot(
        snapshot_id="snap-1",
        organization_id="org-1",
        view_kind="funnel",
        definition_id=definition.definition_id,
        definition_version_id=version.definition_version_id,
        period_start=now,
        period_end=now,
        granularity="day",
        filter_key="all",
        metrics={"opened": 1, "completed": 1},
        created_at=now,
        updated_at=now,
    )
    instance_store.save_snapshot(snapshot_record)

    loaded = instance_store.load_instance(instance.journey_id, organization_id="org-1")
    assert loaded is not None
    assert loaded.subject_key == "subject-1"
    assert instance_store.find_open_by_subject(
        organization_id="org-1",
        definition_id=definition.definition_id,
        subject_key="subject-1",
    ) is not None

    touchpoints = instance_store.list_touchpoints(instance.journey_id, organization_id="org-1")
    assert [item.touchpoint_id for item in touchpoints] == ["touch-1"]

    events = instance_store.list_events(instance.journey_id, organization_id="org-1")
    assert [item.journey_event_id for item in events] == ["event-1"]

    snapshots = instance_store.list_snapshots(
        organization_id="org-1",
        view_kind="funnel",
        definition_id=definition.definition_id,
    )
    assert [item.snapshot_id for item in snapshots] == ["snap-1"]
    assert instance_store.load_instance(instance.journey_id, organization_id="org-2") is None


def test_sqlalchemy_journey_runtime_job_store_dedupes_live_jobs(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyJourneyRuntimeJobStore(session_factory)
    submitted_at = datetime.now(timezone.utc)

    first = JourneyRuntimeJob(
        job_id="job-1",
        organization_id="org-1",
        kind="abandonment_sweep",
        payload={"definition_id": None},
        submitted_at=submitted_at,
    )
    duplicate_candidate = JourneyRuntimeJob(
        job_id="job-2",
        organization_id="org-1",
        kind="abandonment_sweep",
        payload={"definition_id": None},
        submitted_at=submitted_at,
    )

    with tenant_db_context(organization_id="org-1"):
        queued = store.create_or_get_live_job(first)
        duplicate = store.create_or_get_live_job(duplicate_candidate)

        assert queued.job_id == "job-1"
        assert duplicate.job_id == "job-1"

        store.save_job(
            queued.model_copy(
                update={
                    "status": "completed",
                    "finished_at": submitted_at,
                    "result": {"abandoned_journey_ids": []},
                }
            )
        )
        fresh = store.create_or_get_live_job(duplicate_candidate)

        assert fresh.job_id == "job-2"
