from __future__ import annotations

from ruhu.db import build_session_factory
from ruhu.db_models import ConversationRecord, AgentRecord, TurnTraceRecord
from ruhu.analytics_tagging import (
    ClassifierProfile,
    ClassifierProfileService,
    ConversationSummaryService,
    DeterministicTaggingService,
    IntentDefinition,
    ReviewQueueService,
    SQLAlchemyIntentTagsStore,
    TagDefinition,
    TaxonomyService,
    TurnClassificationDecision,
    TurnClassificationService,
)
from ruhu.analytics_tagging.models import utc_now


def test_sqlalchemy_intent_tags_round_trip_and_runtime_projection(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyIntentTagsStore(session_factory)
    taxonomy = TaxonomyService(store)
    profiles = ClassifierProfileService(store, taxonomy)
    events = TurnClassificationService(store, low_confidence_threshold=0.65)

    with session_factory.begin() as session:
        session.add(
            AgentRecord(
                agent_id="agent-a",
                organization_id="org-intent-tags-sql",
                name="Agent A",
                settings_json={},
                current_draft_version_id=None,
                current_published_version_id=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.add(
            ConversationRecord(
                conversation_id="conv-intent-tags-sql",
                organization_id="org-intent-tags-sql",
                agent_id="agent-a",
                agent_version_id="agent-a:v1",
                mode="live",
                channel="web_chat",
                status="active",
                outcome=None,
                step_id="start",
                facts_json={},
                metadata_json={},
                processed_dedupe_keys_json=[],
                last_event_sequence=0,
                started_at=utc_now(),
                ended_at=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.add(
            TurnTraceRecord(
                trace_id="trace-intent-tags-sql",
                organization_id="org-intent-tags-sql",
                conversation_id="conv-intent-tags-sql",
                turn_id="turn-1",
                agent_id="agent-a",
                agent_version_id="agent-a:v1",
                step_before="start",
                step_after="ask_balance",
                semantic_events_json=[],
                fact_updates_json=[],
                chosen_action_json={},
                emitted_messages_json=[],
                tool_calls_json=[],
                latency_breakdown_ms_json={},
                recorded_at=utc_now(),
            )
        )

    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-intent-tags-sql",
            agent_id="agent-a",
            name="check_balance",
            display_name="Check Balance",
            description="Customer checks account balance",
            priority=2,
        )
    )
    profiles.save_profile(
        ClassifierProfile(
            organization_id="org-intent-tags-sql",
            agent_id="agent-a",
            taxonomy_mode="cached_live",
            adapter_name="tenant-a",
        )
    )
    resolved = profiles.resolve_profile(
        "org-intent-tags-sql",
        agent_id="agent-a",
        live_tool_catalog=[{"name": "get_balance", "tool_type": "http"}],
    )
    assert resolved.effective_intent_catalog[0]["name"] == "check_balance"
    assert resolved.effective_tool_catalog[0]["name"] == "get_balance"

    event, review_item = events.record_event(
        organization_id="org-intent-tags-sql",
        conversation_id="conv-intent-tags-sql",
        agent_id="agent-a",
        agent_version_id="agent-a:v1",
        turn_trace_id="trace-intent-tags-sql",
        channel="web_chat",
        decision=TurnClassificationDecision(
            intent_name="check_balance",
            confidence=0.61,
            language="en",
            response_language="en",
            tool_route="get_balance",
            slots={"account_type": "checking"},
        ),
        resolved_profile=resolved,
        model_version="intent-tags-test",
        apply_runtime_cache=True,
    )
    assert event.classifier_profile_id == resolved.classifier_profile_id
    assert review_item is not None

    saved_event = store.get_classification_event(event.classification_event_id)
    assert saved_event is not None
    assert saved_event.intent_name == "check_balance"
    assert saved_event.tool_route == "get_balance"

    review_items = store.list_review_items("org-intent-tags-sql")
    assert len(review_items) == 1
    assert review_items[0].classification_event_id == event.classification_event_id

    with session_factory() as session:
        conversation = session.get(ConversationRecord, "conv-intent-tags-sql")
        assert conversation is not None
        cache = dict(conversation.metadata_json.get("intent_tags") or {})
        assert cache["last_classification_event_id"] == event.classification_event_id
        assert cache["last_intent"] == "check_balance"

        trace = session.get(TurnTraceRecord, "trace-intent-tags-sql")
        assert trace is not None
        assert any(
            item.get("classification_event_id") == event.classification_event_id
            for item in trace.semantic_events_json
            if isinstance(item, dict)
        )


def test_sqlalchemy_summary_rollup_and_summary_tag_assignments(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyIntentTagsStore(session_factory)
    taxonomy = TaxonomyService(store)
    profiles = ClassifierProfileService(store, taxonomy)
    events = TurnClassificationService(store, low_confidence_threshold=0.65)
    summaries = ConversationSummaryService(store, low_confidence_threshold=0.65)
    tags = DeterministicTaggingService(store, taxonomy)

    with session_factory.begin() as session:
        session.add(
            AgentRecord(
                agent_id="summary-agent",
                organization_id="org-summary-sql",
                name="Summary Agent",
                settings_json={},
                current_draft_version_id=None,
                current_published_version_id=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.add(
            ConversationRecord(
                conversation_id="conv-summary-sql",
                organization_id="org-summary-sql",
                agent_id="summary-agent",
                agent_version_id="summary-agent:v1",
                mode="live",
                channel="web_chat",
                status="ended",
                outcome="failed",
                step_id="terminal",
                facts_json={},
                metadata_json={},
                processed_dedupe_keys_json=[],
                last_event_sequence=0,
                started_at=utc_now(),
                ended_at=utc_now(),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )

    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-summary-sql",
            agent_id="summary-agent",
            name="make_payment",
            display_name="Make Payment",
            description="Customer wants to pay",
            priority=4,
        )
    )
    taxonomy.save_tag_definition(
        TagDefinition(
            organization_id="org-summary-sql",
            agent_id="summary-agent",
            name="payment_declined",
            display_name="Payment Declined",
            description="Payment could not be processed",
            tag_kind="blocker",
            apply_scope="conversation",
        )
    )
    taxonomy.save_tag_definition(
        TagDefinition(
            organization_id="org-summary-sql",
            agent_id="summary-agent",
            name="failed",
            display_name="Failed",
            description="Conversation failed",
            tag_kind="outcome_attribute",
            apply_scope="conversation",
        )
    )
    profiles.save_profile(
        ClassifierProfile(
            organization_id="org-summary-sql",
            agent_id="summary-agent",
            adapter_name="tenant-a",
        )
    )
    resolved = profiles.resolve_profile("org-summary-sql", agent_id="summary-agent")

    events.record_event(
        organization_id="org-summary-sql",
        conversation_id="conv-summary-sql",
        agent_id="summary-agent",
        agent_version_id="summary-agent:v1",
        channel="web_chat",
        decision=TurnClassificationDecision(
            intent_name="make_payment",
            confidence=0.88,
            language="en",
            response_language="en",
            signals={"payment_declined": True},
        ),
        resolved_profile=resolved,
        model_version="lane-c-test",
    )

    summary = summaries.rollup_conversation(
        organization_id="org-summary-sql",
        conversation_id="conv-summary-sql",
    )
    assignments = tags.assign_summary_tags(summary)

    assert summary.status == "final"
    assert summary.primary_intent_name == "make_payment"
    assert {item.assignment_scope for item in assignments} == {"conversation"}

    stored_summaries = store.list_conversation_summaries(
        "org-summary-sql",
        conversation_id="conv-summary-sql",
    )
    assert len(stored_summaries) == 1
    stored_assignments = store.list_tag_assignments(
        "org-summary-sql",
        conversation_summary_id=summary.conversation_summary_id,
    )
    assert len(stored_assignments) == 2


def test_sqlalchemy_turn_review_correction_round_trip(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyIntentTagsStore(session_factory)
    taxonomy = TaxonomyService(store)
    profiles = ClassifierProfileService(store, taxonomy)
    events = TurnClassificationService(store, low_confidence_threshold=0.7)
    reviews = ReviewQueueService(store)

    with session_factory.begin() as session:
        session.add(
            AgentRecord(
                agent_id="review-agent-sql",
                organization_id="org-review-sql",
                name="Review Agent",
                settings_json={},
                current_draft_version_id=None,
                current_published_version_id=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.add(
            ConversationRecord(
                conversation_id="conv-review-sql",
                organization_id="org-review-sql",
                agent_id="review-agent-sql",
                agent_version_id="review-agent-sql:v1",
                mode="live",
                channel="web_chat",
                status="ended",
                outcome="resolved",
                step_id="terminal",
                facts_json={},
                metadata_json={},
                processed_dedupe_keys_json=[],
                last_event_sequence=0,
                started_at=utc_now(),
                ended_at=utc_now(),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )

    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-review-sql",
            agent_id="review-agent-sql",
            name="refund_request",
            display_name="Refund Request",
            priority=5,
        )
    )
    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-review-sql",
            agent_id="review-agent-sql",
            name="cancel_subscription",
            display_name="Cancel Subscription",
            priority=4,
        )
    )
    profiles.save_profile(
        ClassifierProfile(
            organization_id="org-review-sql",
            agent_id="review-agent-sql",
        )
    )
    resolved = profiles.resolve_profile("org-review-sql", agent_id="review-agent-sql")

    event, review_item = events.record_event(
        organization_id="org-review-sql",
        conversation_id="conv-review-sql",
        agent_id="review-agent-sql",
        agent_version_id="review-agent-sql:v1",
        channel="web_chat",
        decision=TurnClassificationDecision(
            intent_name="cancel_subscription",
            confidence=0.4,
            language="en",
            response_language="en",
        ),
        resolved_profile=resolved,
        model_version="lane-d-sql",
    )
    assert review_item is not None

    reviews.claim_review_item(review_item.review_item_id, user_id="reviewer-sql")
    reviews.resolve_turn_review(
        review_item.review_item_id,
        user_id="reviewer-sql",
        disposition="corrected",
        corrected_decision=TurnClassificationDecision(
            intent_name="refund_request",
            confidence=0.95,
            language="en",
            response_language="en",
        ),
    )
    effective = reviews.get_effective_turn_classification(event.classification_event_id)
    assert effective.is_corrected is True
    assert effective.effective_event.intent_name == "refund_request"
