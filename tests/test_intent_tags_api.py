from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ruhu.api import create_app
from ruhu.composition import build_minimal_runtime
from ruhu.services.api_services import ApiServices
from ruhu.analytics_tagging import (
    ClassifierProfileService,
    ConversationSemanticContext,
    ConversationSummaryService,
    DeterministicTaggingService,
    InMemoryIntentTagsStore,
    IntentTagsReadService,
    IntentTagsRuntime,
    ReviewQueueService,
    SemanticSummaryWebhookService,
    TaxonomyService,
    TurnClassificationDecision,
    TurnClassificationService,
)
from ruhu.kernel import ConversationKernel
from ruhu.registry import FileAgentRegistry


def _build_runtime() -> IntentTagsRuntime:
    store = InMemoryIntentTagsStore()
    taxonomy_service = TaxonomyService(store)
    profile_service = ClassifierProfileService(store, taxonomy_service)
    webhook_service = SemanticSummaryWebhookService(store)
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
    return IntentTagsRuntime(
        store=store,
        taxonomy_service=taxonomy_service,
        profile_service=profile_service,
        webhook_service=webhook_service,
        turn_service=turn_service,
        summary_service=summary_service,
        tagging_service=tagging_service,
        review_service=review_service,
        read_service=read_service,
    )


def test_intent_tags_api_exposes_taxonomy_analytics_review_and_summary_surfaces() -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime = _build_runtime()
        app = create_app(
            build_minimal_runtime(
                kernel=ConversationKernel(),
                agent_registry=FileAgentRegistry(agent_root_path),
            ),
            ApiServices(intent_tags_runtime=runtime),
        )
        organization_id = "org-intent-tags-api"
        conversation_id = "web_widget:intent-tags-1"

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            page = await client.get("/intent-tags")
            assert page.status_code == 200
            assert "Intent &amp; Tags Console" in page.text

            refund_intent = await client.post(
                "/intent-tags/intents",
                json={
                    "organization_id": organization_id,
                    "agent_id": "sales",
                    "name": "refund_request",
                    "display_name": "Refund request",
                    "priority": 10,
                },
            )
            assert refund_intent.status_code == 200
            refund_intent_id = refund_intent.json()["intent_definition_id"]

            escalation_intent = await client.post(
                "/intent-tags/intents",
                json={
                    "organization_id": organization_id,
                    "agent_id": "sales",
                    "name": "billing_escalation",
                    "display_name": "Billing escalation",
                    "priority": 8,
                },
            )
            assert escalation_intent.status_code == 200

            followup_tag = await client.post(
                "/intent-tags/tags",
                json={
                    "organization_id": organization_id,
                    "agent_id": "sales",
                    "name": "requires_human_followup",
                    "display_name": "Requires human followup",
                    "tag_kind": "blocker",
                    "apply_scope": "both",
                    "rule_config": {
                        "any_signals": ["requires_human_followup"],
                        "requires_human_followup": True,
                    },
                },
            )
            assert followup_tag.status_code == 200

            transferred_tag = await client.post(
                "/intent-tags/tags",
                json={
                    "organization_id": organization_id,
                    "agent_id": "sales",
                    "name": "transferred",
                    "display_name": "Transferred",
                    "tag_kind": "outcome_attribute",
                    "apply_scope": "conversation",
                },
            )
            assert transferred_tag.status_code == 200

            profile_response = await client.post(
                "/intent-tags/profiles",
                json={
                    "organization_id": organization_id,
                    "agent_id": "sales",
                    "adapter_name": "ruhu-general",
                    "taxonomy_mode": "live",
                    "supported_languages": ["en"],
                },
            )
            assert profile_response.status_code == 200

            taxonomy = await client.get(
                "/intent-tags/taxonomy",
                params={"organization_id": organization_id, "agent_id": "sales"},
            )
            assert taxonomy.status_code == 200
            assert len(taxonomy.json()["intents"]) == 2
            assert len(taxonomy.json()["tags"]) == 2
            assert len(taxonomy.json()["profiles"]) == 1

            first_event, review_item = runtime.turn_service.record_event(
                organization_id=organization_id,
                conversation_id=conversation_id,
                channel="web_widget",
                agent_id="sales",
                decision=TurnClassificationDecision(
                    intent_name="refund_request",
                    confidence=0.44,
                    language="en",
                    response_language="en",
                    signals={"requires_human_followup": True},
                ),
            )
            second_event, _ = runtime.turn_service.record_event(
                organization_id=organization_id,
                conversation_id=conversation_id,
                channel="web_widget",
                agent_id="sales",
                decision=TurnClassificationDecision(
                    intent_name="billing_escalation",
                    confidence=0.92,
                    language="en",
                    response_language="en",
                    tool_route="billing_escalation",
                ),
            )
            assert review_item is not None
            runtime.tagging_service.assign_turn_tags(first_event)
            runtime.tagging_service.assign_turn_tags(second_event)
            summary = runtime.summary_service.rollup_conversation(
                organization_id=organization_id,
                conversation_id=conversation_id,
                conversation_context=ConversationSemanticContext(
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    agent_id="sales",
                    channel="web_widget",
                    status="ended",
                    outcome="transferred",
                ),
                summary_version=1,
                target_status="final",
            )
            runtime.tagging_service.assign_summary_tags(summary)
            runtime.review_service.create_review_item(
                organization_id=organization_id,
                review_kind="summary_correction",
                conversation_summary_id=summary.conversation_summary_id,
                review_notes="Verify final outcome tags",
            )
            second_conversation_id = "web_widget:intent-tags-2"
            third_event, _ = runtime.turn_service.record_event(
                organization_id=organization_id,
                conversation_id=second_conversation_id,
                channel="web_widget",
                agent_id="sales",
                decision=TurnClassificationDecision(
                    intent_name="refund_request",
                    confidence=0.61,
                    language="en",
                    response_language="en",
                    signals={"requires_human_followup": True},
                ),
            )
            runtime.tagging_service.assign_turn_tags(third_event)
            second_summary = runtime.summary_service.rollup_conversation(
                organization_id=organization_id,
                conversation_id=second_conversation_id,
                conversation_context=ConversationSemanticContext(
                    organization_id=organization_id,
                    conversation_id=second_conversation_id,
                    agent_id="sales",
                    channel="web_widget",
                    status="ended",
                    outcome="transferred",
                ),
                summary_version=1,
                target_status="final",
            )
            runtime.tagging_service.assign_summary_tags(second_summary)

            analytics = await client.get(
                "/intent-tags/analytics",
                params={"organization_id": organization_id, "agent_id": "sales"},
            )
            assert analytics.status_code == 200
            analytics_payload = analytics.json()
            assert analytics_payload["totals"]["turn_events"] == 3
            assert analytics_payload["totals"]["conversation_summaries"] == 2
            assert "insight_rows" in analytics_payload

            insights = await client.get(
                "/intent-tags/insights",
                params={"organization_id": organization_id, "agent_id": "sales"},
            )
            assert insights.status_code == 200
            insights_payload = insights.json()
            assert insights_payload["totals"]["conversation_summaries"] == 2
            assert "rows" in insights_payload

            created_target = await client.post(
                "/intent-tags/webhook-targets",
                json={
                    "organization_id": organization_id,
                    "name": "Ops Receiver",
                    "url": "https://hooks.example.com/semantic",
                    "agent_ids": ["sales"],
                    "channels": ["web_widget"],
                    "signing_secret_ref": "inline-secret",
                    "extra_headers": {"X-Intent-Tags": "enabled"},
                },
            )
            assert created_target.status_code == 200
            webhook_target_id = created_target.json()["webhook_target_id"]
            assert created_target.json()["has_signing_secret"] is True
            assert created_target.json()["signing_secret_source"] == "inline"

            listed_targets = await client.get(
                "/intent-tags/webhook-targets",
                params={"organization_id": organization_id},
            )
            assert listed_targets.status_code == 200
            assert len(listed_targets.json()) == 1

            updated_target = await client.put(
                f"/intent-tags/webhook-targets/{webhook_target_id}",
                params={"organization_id": organization_id},
                json={"is_active": False},
            )
            assert updated_target.status_code == 200
            assert updated_target.json()["is_active"] is False

            deleted_target = await client.delete(
                f"/intent-tags/webhook-targets/{webhook_target_id}",
                params={"organization_id": organization_id},
            )
            assert deleted_target.status_code == 204

            reviews = await client.get(
                "/intent-tags/reviews",
                params={"organization_id": organization_id, "agent_id": "sales"},
            )
            assert reviews.status_code == 200
            review_rows = reviews.json()
            assert len(review_rows) == 2
            turn_review_id = next(
                row["review_item"]["review_item_id"]
                for row in review_rows
                if row["target_kind"] == "turn"
            )

            claim = await client.post(
                f"/intent-tags/reviews/{turn_review_id}/claim",
                params={"organization_id": organization_id},
                json={"user_id": "operator-1"},
            )
            assert claim.status_code == 200

            resolve = await client.post(
                f"/intent-tags/reviews/{turn_review_id}/resolve-turn",
                params={"organization_id": organization_id},
                json={
                    "user_id": "operator-1",
                    "disposition": "corrected",
                    "corrected_decision": {
                        "intent_name": "billing_escalation",
                        "confidence": 0.88,
                        "language": "en",
                        "response_language": "en",
                        "signals": {"requires_human_followup": True},
                        "slots": {},
                    },
                },
            )
            assert resolve.status_code == 200

            summaries = await client.get(
                "/intent-tags/summaries",
                params={"organization_id": organization_id, "agent_id": "sales"},
            )
            assert summaries.status_code == 200
            summary_id = next(
                item["summary"]["conversation_summary_id"]
                for item in summaries.json()
                if item["summary"]["conversation_id"] == conversation_id
            )

            detail = await client.get(
                f"/intent-tags/summaries/{summary_id}",
                params={"organization_id": organization_id},
            )
            assert detail.status_code == 200
            detail_payload = detail.json()
            assert detail_payload["effective_summary"]["effective_summary"]["conversation_id"] == conversation_id
            assert len(detail_payload["turn_evidence"]) == 2
            assert any(item["is_corrected"] for item in detail_payload["turn_evidence"])

            update_intent = await client.put(
                f"/intent-tags/intents/{refund_intent_id}",
                params={"organization_id": organization_id},
                json={"display_name": "Refund and credit request"},
            )
            assert update_intent.status_code == 200
            assert update_intent.json()["display_name"] == "Refund and credit request"

    asyncio.run(run())
