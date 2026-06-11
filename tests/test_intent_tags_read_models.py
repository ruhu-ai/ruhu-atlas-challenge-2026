from __future__ import annotations

from ruhu.analytics_tagging import (
    ClassifierProfileService,
    ConversationSemanticContext,
    ConversationSummaryService,
    DeterministicTaggingService,
    InMemoryIntentTagsStore,
    IntentDefinition,
    IntentTagsReadService,
    ReviewQueueService,
    TagDefinition,
    TaxonomyService,
    TurnClassificationDecision,
    TurnClassificationService,
)


def test_intent_tags_read_models_cover_taxonomy_analytics_and_summary_detail() -> None:
    store = InMemoryIntentTagsStore()
    taxonomy_service = TaxonomyService(store)
    profile_service = ClassifierProfileService(store, taxonomy_service)
    turn_service = TurnClassificationService(store, low_confidence_threshold=0.6)
    summary_service = ConversationSummaryService(store, low_confidence_threshold=0.6)
    tagging_service = DeterministicTaggingService(store, taxonomy_service)
    review_service = ReviewQueueService(store)
    read_service = IntentTagsReadService(
        store,
        taxonomy_service=taxonomy_service,
        profile_service=profile_service,
        review_service=review_service,
    )

    refund_intent = taxonomy_service.save_intent_definition(
        IntentDefinition(
            organization_id="org-intents",
            agent_id="sales_agent",
            name="refund_request",
            display_name="Refund request",
            priority=10,
        )
    )
    transfer_intent = taxonomy_service.save_intent_definition(
        IntentDefinition(
            organization_id="org-intents",
            agent_id="sales_agent",
            name="billing_escalation",
            display_name="Billing escalation",
            priority=8,
        )
    )
    followup_tag = taxonomy_service.save_tag_definition(
        TagDefinition(
            organization_id="org-intents",
            agent_id="sales_agent",
            name="requires_human_followup",
            display_name="Requires human followup",
            tag_kind="blocker",
            apply_scope="both",
            rule_config={"any_signals": ["requires_human_followup"]},
        )
    )
    outcome_tag = taxonomy_service.save_tag_definition(
        TagDefinition(
            organization_id="org-intents",
            agent_id="sales_agent",
            name="transferred",
            display_name="Transferred",
            tag_kind="outcome_attribute",
            apply_scope="conversation",
        )
    )

    first_event, review_item = turn_service.record_event(
        organization_id="org-intents",
        conversation_id="conversation-1",
        channel="web_widget",
        agent_id="sales_agent",
        decision=TurnClassificationDecision(
            intent_name=refund_intent.name,
            confidence=0.42,
            language="en",
            response_language="en",
            signals={"requires_human_followup": True},
        ),
    )
    second_event, _ = turn_service.record_event(
        organization_id="org-intents",
        conversation_id="conversation-1",
        channel="web_widget",
        agent_id="sales_agent",
        decision=TurnClassificationDecision(
            intent_name=transfer_intent.name,
            confidence=0.91,
            language="en",
            response_language="en",
            tool_route="billing_escalation",
        ),
    )
    assert review_item is not None
    tagging_service.assign_turn_tags(first_event)
    tagging_service.assign_turn_tags(second_event)

    review_service.resolve_turn_review(
        review_item.review_item_id,
        user_id="operator-1",
        disposition="corrected",
        corrected_decision=TurnClassificationDecision(
            intent_name=transfer_intent.name,
            confidence=0.86,
            language="en",
            response_language="en",
            signals={"requires_human_followup": True},
        ),
    )

    summary = summary_service.rollup_conversation(
        organization_id="org-intents",
        conversation_id="conversation-1",
        conversation_context=ConversationSemanticContext(
            organization_id="org-intents",
            conversation_id="conversation-1",
            agent_id="sales_agent",
            channel="web_widget",
            status="ended",
            outcome="transferred",
        ),
        summary_version=1,
        target_status="final",
    )
    tagging_service.assign_summary_tags(summary)
    review_service.create_review_item(
        organization_id="org-intents",
        review_kind="summary_correction",
        conversation_summary_id=summary.conversation_summary_id,
        review_notes="Check escalation semantics",
    )

    taxonomy_snapshot = read_service.get_taxonomy_snapshot("org-intents", agent_id="sales_agent")
    assert len(taxonomy_snapshot.intents) == 2
    assert len(taxonomy_snapshot.tags) == 2

    analytics = read_service.analytics_snapshot("org-intents", agent_id="sales_agent")
    intent_rows = {row.intent_name: row for row in analytics.intent_rows}
    assert intent_rows["billing_escalation"].summary_count == 1
    assert intent_rows["refund_request"].corrected_turn_count == 1

    tag_rows = {row.tag_name: row for row in analytics.tag_rows}
    assert tag_rows[followup_tag.name].turn_assignment_count == 1
    assert tag_rows[outcome_tag.name].conversation_assignment_count == 1

    queue = read_service.list_review_queue("org-intents", agent_id="sales_agent")
    assert len(queue) == 2
    assert {item.target_kind for item in queue} == {"turn", "summary"}

    summary_detail = read_service.get_summary_detail(
        "org-intents",
        conversation_summary_id=summary.conversation_summary_id,
    )
    assert summary_detail is not None
    assert summary_detail.effective_summary.effective_summary.primary_intent_name == transfer_intent.name
    assert len(summary_detail.turn_evidence) == 2
    assert any(item.is_corrected for item in summary_detail.turn_evidence)
