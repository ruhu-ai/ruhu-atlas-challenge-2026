from __future__ import annotations

from ruhu.analytics_tagging import (
    ClassifierProfile,
    ClassifierProfileService,
    ConversationSemanticContext,
    ConversationSummaryService,
    DeterministicTaggingService,
    InMemoryIntentTagsStore,
    IntentDefinition,
    ReviewQueueService,
    TagDefinition,
    TaxonomyService,
    TaxonomyVersion,
    TurnClassificationDecision,
    TurnClassificationService,
)


def test_live_effective_intents_prefer_agent_specific_override() -> None:
    store = InMemoryIntentTagsStore()
    taxonomy = TaxonomyService(store)

    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-intents",
            agent_id=None,
            name="transfer_funds",
            display_name="Transfer Funds",
            description="Org default transfer intent",
            priority=1,
        )
    )
    agent_specific = taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-intents",
            agent_id="payments-agent",
            name="transfer_funds",
            display_name="Move Money",
            description="Agent-specific transfer intent",
            priority=5,
        )
    )
    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-intents",
            agent_id=None,
            name="refund_request",
            display_name="Refund Request",
            description="Customer wants a refund",
            priority=3,
        )
    )

    effective = taxonomy.list_effective_intents("org-intents", agent_id="payments-agent")
    assert [item.name for item in effective] == ["transfer_funds", "refund_request"]
    assert effective[0].intent_definition_id == agent_specific.intent_definition_id
    assert effective[0].display_name == "Move Money"


def test_cached_live_profile_rebuilds_when_catalog_changes() -> None:
    store = InMemoryIntentTagsStore()
    taxonomy = TaxonomyService(store)
    profiles = ClassifierProfileService(store, taxonomy)

    intent = taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-cache",
            agent_id="booking-agent",
            name="book_appointment",
            display_name="Book Appointment",
            description="Initial description",
            priority=2,
        )
    )
    profile = profiles.save_profile(
        ClassifierProfile(
            organization_id="org-cache",
            agent_id="booking-agent",
            taxonomy_mode="cached_live",
        )
    )

    first = profiles.resolve_profile("org-cache", agent_id="booking-agent")
    assert first.classifier_profile_id == profile.classifier_profile_id
    assert first.catalog_cache_built_at is not None
    assert first.effective_intent_catalog[0]["display_name"] == "Book Appointment"

    taxonomy.save_intent_definition(
        intent.model_copy(update={"display_name": "Schedule Appointment"})
    )
    second = profiles.resolve_profile("org-cache", agent_id="booking-agent")
    assert second.catalog_cache_built_at is not None
    assert second.effective_intent_catalog[0]["display_name"] == "Schedule Appointment"


def test_pinned_profile_uses_version_catalog_and_low_confidence_creates_review_item() -> None:
    store = InMemoryIntentTagsStore()
    taxonomy = TaxonomyService(store)
    profiles = ClassifierProfileService(store, taxonomy)
    events = TurnClassificationService(store, low_confidence_threshold=0.7)

    version = taxonomy.publish_taxonomy_version(
        taxonomy.save_taxonomy_version(
            TaxonomyVersion(organization_id="org-pinned", name="spring-2026")
        ).taxonomy_version_id
    )
    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-pinned",
            taxonomy_version_id=version.taxonomy_version_id,
            name="check_balance",
            display_name="Check Balance",
            description="Pinned intent",
            priority=1,
        )
    )
    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-pinned",
            name="transfer_funds",
            display_name="Transfer Funds",
            description="Live intent",
            priority=1,
        )
    )
    profile = profiles.save_profile(
        ClassifierProfile(
            organization_id="org-pinned",
            taxonomy_mode="pinned",
            taxonomy_version_id=version.taxonomy_version_id,
            adapter_name="tenant-a",
        )
    )
    resolved = profiles.resolve_profile("org-pinned")
    assert resolved.classifier_profile_id == profile.classifier_profile_id
    assert [item["name"] for item in resolved.effective_intent_catalog] == ["check_balance"]

    event, review_item = events.record_event(
        organization_id="org-pinned",
        conversation_id="conv-pinned",
        channel="web_chat",
        decision=TurnClassificationDecision(
            intent_name="check_balance",
            confidence=0.52,
            language="en",
            response_language="en",
        ),
        resolved_profile=resolved,
    )
    assert event.taxonomy_mode == "pinned"
    assert event.taxonomy_version_id == version.taxonomy_version_id
    assert review_item is not None
    assert review_item.review_kind == "low_confidence_turn"


def test_summary_rollup_and_deterministic_tagging_use_conversation_evidence() -> None:
    store = InMemoryIntentTagsStore()
    taxonomy = TaxonomyService(store)
    profiles = ClassifierProfileService(store, taxonomy)
    events = TurnClassificationService(store, low_confidence_threshold=0.7)
    summaries = ConversationSummaryService(store, low_confidence_threshold=0.7)
    tags = DeterministicTaggingService(store, taxonomy)

    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-summary",
            agent_id="payments-agent",
            name="make_payment",
            display_name="Make Payment",
            description="Customer is trying to make a payment",
            priority=5,
        )
    )
    urgent_tag = taxonomy.save_tag_definition(
        TagDefinition(
            organization_id="org-summary",
            agent_id="payments-agent",
            name="urgent",
            display_name="Urgent",
            description="Urgency signal",
            tag_kind="priority",
            apply_scope="turn",
        )
    )
    blocker_tag = taxonomy.save_tag_definition(
        TagDefinition(
            organization_id="org-summary",
            agent_id="payments-agent",
            name="payment_declined",
            display_name="Payment Declined",
            description="Card or provider declined the charge",
            tag_kind="blocker",
            apply_scope="conversation",
        )
    )
    outcome_tag = taxonomy.save_tag_definition(
        TagDefinition(
            organization_id="org-summary",
            agent_id="payments-agent",
            name="failed",
            display_name="Failed",
            description="Conversation ended with failure",
            tag_kind="outcome_attribute",
            apply_scope="conversation",
        )
    )
    profiles.save_profile(
        ClassifierProfile(
            organization_id="org-summary",
            agent_id="payments-agent",
            adapter_name="tenant-a",
            taxonomy_mode="live",
        )
    )
    resolved = profiles.resolve_profile("org-summary", agent_id="payments-agent")

    first_event, _ = events.record_event(
        organization_id="org-summary",
        conversation_id="conv-summary",
        agent_id="payments-agent",
        agent_version_id="payments-agent:v1",
        channel="web_chat",
        decision=TurnClassificationDecision(
            intent_name="make_payment",
            confidence=0.82,
            language="en",
            response_language="en",
            signals={"urgent": True},
        ),
        resolved_profile=resolved,
        model_version="lane-c-test",
    )
    second_event, _ = events.record_event(
        organization_id="org-summary",
        conversation_id="conv-summary",
        agent_id="payments-agent",
        agent_version_id="payments-agent:v1",
        channel="web_chat",
        decision=TurnClassificationDecision(
            intent_name="make_payment",
            confidence=0.91,
            language="en",
            response_language="en",
            tool_route="card_charge",
            signals={"payment_declined": True},
        ),
        resolved_profile=resolved,
        model_version="lane-c-test",
    )

    turn_assignments = tags.assign_turn_tags(first_event)
    summary = summaries.rollup_conversation(
        organization_id="org-summary",
        conversation_id="conv-summary",
        conversation_context=ConversationSemanticContext(
            organization_id="org-summary",
            conversation_id="conv-summary",
            agent_id="payments-agent",
            agent_version_id="payments-agent:v1",
            channel="web_chat",
            status="ended",
            outcome="failed",
        ),
    )
    summary_assignments = tags.assign_summary_tags(summary)

    assert summary.primary_intent_name == "make_payment"
    assert summary.status == "final"
    assert summary.resolution_status == "failed"
    assert summary.generated_from_event_count == 2
    assert any(item.tag_definition_id == urgent_tag.tag_definition_id for item in turn_assignments)
    assert any(item.tag_definition_id == blocker_tag.tag_definition_id for item in summary_assignments)
    assert any(item.tag_definition_id == outcome_tag.tag_definition_id for item in summary_assignments)
    assert second_event.classification_event_id in summary.evidence_payload["classification_event_ids"]


def test_turn_review_correction_changes_effective_turn_and_future_summary_rollup() -> None:
    store = InMemoryIntentTagsStore()
    taxonomy = TaxonomyService(store)
    profiles = ClassifierProfileService(store, taxonomy)
    events = TurnClassificationService(store, low_confidence_threshold=0.7)
    summaries = ConversationSummaryService(store, low_confidence_threshold=0.7)
    reviews = ReviewQueueService(store)

    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-review",
            agent_id="support-agent",
            name="refund_request",
            display_name="Refund Request",
            priority=5,
        )
    )
    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-review",
            agent_id="support-agent",
            name="cancel_subscription",
            display_name="Cancel Subscription",
            priority=4,
        )
    )
    profiles.save_profile(
        ClassifierProfile(
            organization_id="org-review",
            agent_id="support-agent",
        )
    )
    resolved = profiles.resolve_profile("org-review", agent_id="support-agent")

    event, review_item = events.record_event(
        organization_id="org-review",
        conversation_id="conv-review",
        agent_id="support-agent",
        agent_version_id="support-agent:v1",
        channel="web_chat",
        decision=TurnClassificationDecision(
            intent_name="cancel_subscription",
            confidence=0.42,
            language="en",
            response_language="en",
        ),
        resolved_profile=resolved,
        model_version="lane-d-test",
    )
    assert review_item is not None

    claimed = reviews.claim_review_item(review_item.review_item_id, user_id="user-1")
    assert claimed.status == "in_review"
    assert claimed.claimed_by_user_id == "user-1"

    resolved_review = reviews.resolve_turn_review(
        review_item.review_item_id,
        user_id="user-1",
        disposition="corrected",
        corrected_decision=TurnClassificationDecision(
            intent_name="refund_request",
            confidence=0.93,
            language="en",
            response_language="en",
            signals={"refund_confirmed": True},
        ),
        review_notes="Operator corrected the customer goal.",
    )
    assert resolved_review.status == "resolved"
    assert resolved_review.review_disposition == "corrected"

    effective = reviews.get_effective_turn_classification(event.classification_event_id)
    assert effective.is_corrected is True
    assert effective.event.intent_name == "cancel_subscription"
    assert effective.effective_event.intent_name == "refund_request"

    summary = summaries.rollup_conversation(
        organization_id="org-review",
        conversation_id="conv-review",
        conversation_context=ConversationSemanticContext(
            organization_id="org-review",
            conversation_id="conv-review",
            agent_id="support-agent",
            agent_version_id="support-agent:v1",
            channel="web_chat",
            status="ended",
            outcome="resolved",
        ),
    )
    assert summary.primary_intent_name == "refund_request"


def test_summary_review_creates_corrected_summary_and_effective_assignments() -> None:
    store = InMemoryIntentTagsStore()
    taxonomy = TaxonomyService(store)
    profiles = ClassifierProfileService(store, taxonomy)
    events = TurnClassificationService(store, low_confidence_threshold=0.7)
    summaries = ConversationSummaryService(store, low_confidence_threshold=0.7)
    tags = DeterministicTaggingService(store, taxonomy)
    reviews = ReviewQueueService(store)

    taxonomy.save_intent_definition(
        IntentDefinition(
            organization_id="org-summary-review",
            agent_id="payments-agent",
            name="make_payment",
            display_name="Make Payment",
            priority=5,
        )
    )
    taxonomy.save_tag_definition(
        TagDefinition(
            organization_id="org-summary-review",
            agent_id="payments-agent",
            name="failed",
            display_name="Failed",
            tag_kind="outcome_attribute",
            apply_scope="conversation",
        )
    )
    escalated_tag = taxonomy.save_tag_definition(
        TagDefinition(
            organization_id="org-summary-review",
            agent_id="payments-agent",
            name="transferred",
            display_name="Transferred",
            tag_kind="outcome_attribute",
            apply_scope="conversation",
        )
    )
    profiles.save_profile(
        ClassifierProfile(
            organization_id="org-summary-review",
            agent_id="payments-agent",
        )
    )
    resolved = profiles.resolve_profile("org-summary-review", agent_id="payments-agent")
    events.record_event(
        organization_id="org-summary-review",
        conversation_id="conv-summary-review",
        agent_id="payments-agent",
        agent_version_id="payments-agent:v1",
        channel="web_chat",
        decision=TurnClassificationDecision(
            intent_name="make_payment",
            confidence=0.92,
            language="en",
            response_language="en",
            signals={"payment_declined": True},
        ),
        resolved_profile=resolved,
    )
    original_summary = summaries.rollup_conversation(
        organization_id="org-summary-review",
        conversation_id="conv-summary-review",
        conversation_context=ConversationSemanticContext(
            organization_id="org-summary-review",
            conversation_id="conv-summary-review",
            agent_id="payments-agent",
            agent_version_id="payments-agent:v1",
            channel="web_chat",
            status="ended",
            outcome="failed",
        ),
    )
    tags.assign_summary_tags(original_summary)

    review_item = reviews.create_review_item(
        organization_id="org-summary-review",
        conversation_summary_id=original_summary.conversation_summary_id,
        review_kind="summary_correction",
        review_notes="Operator says the call was transferred.",
    )
    resolved_review = reviews.resolve_summary_review(
        review_item.review_item_id,
        user_id="reviewer-1",
        disposition="corrected",
        corrected_fields={
            "outcome": "transferred",
            "resolution_status": "escalated",
            "requires_human_followup": True,
        },
        corrected_tag_definition_ids=[escalated_tag.tag_definition_id],
        review_notes="Customer needed human help, not a failed payment.",
    )
    assert resolved_review.corrected_conversation_summary_id is not None

    original_after = store.get_conversation_summary(original_summary.conversation_summary_id)
    assert original_after is not None
    assert original_after.status == "superseded"

    effective = reviews.get_effective_summary(
        conversation_summary_id=original_summary.conversation_summary_id,
    )
    assert effective.is_corrected is True
    assert effective.effective_summary.outcome == "transferred"
    assert effective.effective_summary.resolution_status == "escalated"
    assert {item.tag_definition_id for item in effective.tag_assignments} == {escalated_tag.tag_definition_id}
