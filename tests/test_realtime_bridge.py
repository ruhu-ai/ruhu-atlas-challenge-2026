from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from ruhu.realtime.bridge import KernelRealtimeBridge
from ruhu.schemas import (
    ActionRecord,
    ConversationState,
    InteractionDebugSnapshot,
    InteractionDebugVoicePolicy,
    RenderedMessage,
    RuntimeTurn,
    RuntimeTurnResult,
    SemanticEventRecord,
)


class _FakeEvents:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def append(self, **kwargs):
        self.items.append(kwargs)
        return SimpleNamespace(event_id=f"evt-{len(self.items)}")


class _FakeOutbox:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def enqueue(self, **kwargs) -> None:
        self.items.append(kwargs)


class _FakeControlPlane:
    def __init__(self) -> None:
        self.events = _FakeEvents()
        self.outbox = _FakeOutbox()


def test_kernel_realtime_bridge_projects_interaction_and_grounding_events() -> None:
    control_plane = _FakeControlPlane()
    bridge = KernelRealtimeBridge(control_plane=control_plane)  # type: ignore[arg-type]

    conversation = ConversationState(
        conversation_id="conv-bridge-1",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="local:agent-1",
        step_id="discover",
        updated_at=datetime.now(timezone.utc),
        mode="live",
        channel="web_chat",
    )
    turn = RuntimeTurn(
        turn_id="turn-1",
        dedupe_key="turn-1",
        channel="web_chat",
        modality="text",
        event_type="user_message",
        text="Book that for me",
        received_at=datetime.now(timezone.utc),
    )
    result = RuntimeTurnResult(
        turn_id="turn-1",
        conversation_id="conv-bridge-1",
        step_before="discover",
        step_after="book_demo",
        semantic_events=[
            SemanticEventRecord(
                family="interaction",
                name="activity_started",
                source="system",
                confidence=1.0,
                payload={"tool_ref": "calendar.lookup"},
            ),
            SemanticEventRecord(
                family="grounding",
                name="updated",
                source="system",
                confidence=1.0,
                payload={"acknowledged_fact_keys": ["email"]},
            ),
            SemanticEventRecord(
                family="artifact",
                name="created",
                source="system",
                confidence=1.0,
                payload={"artifact_id": "art-1", "artifact_type": "booking", "status": "confirmed"},
            ),
            SemanticEventRecord(
                family="narration",
                name="narration_rendered",
                source="system",
                confidence=1.0,
                payload={
                    "response_mode": "activity_started",
                    "claimed_class": "pending",
                    "allowed_claim_classes": ["pending"],
                    "narrator_mode": "llm",
                    "fallback_used": False,
                },
            ),
            SemanticEventRecord(
                family="interaction",
                name="status_trail_updated",
                source="system",
                confidence=1.0,
                payload={
                    "items": [
                        {
                            "item_id": "activity:calendar.lookup",
                            "item_type": "activity",
                            "summary": "Checking calendar: completed",
                            "started_at": datetime.now(timezone.utc).isoformat(),
                            "expires_at": datetime.now(timezone.utc).isoformat(),
                            "source_ref": "calendar.lookup",
                        }
                    ]
                },
            ),
        ],
        fact_updates=[],
        chosen_action=ActionRecord(type="reply", reason="llm_context_render"),
        emitted_messages=[RenderedMessage(text="Okay, let me check that.")],
        tool_calls=[],
        trace_id="trace-1",
        latency_breakdown_ms={"total": 0},
        interaction_debug_snapshot=InteractionDebugSnapshot(
            step_id="book_demo",
            channel="web_chat",
            voice_interaction_policy=InteractionDebugVoicePolicy(
                step_id="book_demo",
                endpointing_ms=650,
                soft_timeout_ms=800,
                turn_eagerness="normal",
                interruptibility_policy="interruptible_except_policy",
            ),
        ),
    )

    bridge.record_turn(conversation=conversation, turn=turn, result=result)

    event_names = [(item["family"], item["name"]) for item in control_plane.events.items]
    assert ("interaction", "activity_started") in event_names
    assert ("grounding", "updated") in event_names
    assert ("artifact", "created") in event_names
    assert ("narration", "narration_rendered") in event_names
    assert ("interaction", "status_trail_updated") in event_names
    interaction_event = next(
        item for item in control_plane.events.items
        if item["family"] == "interaction" and item["name"] == "activity_started"
    )
    assert interaction_event["payload"]["trace_id"] == "trace-1"
    assert interaction_event["payload"]["tool_ref"] == "calendar.lookup"
    assert interaction_event["payload"]["interaction_debug_snapshot"]["step_id"] == "book_demo"
    status_event = next(
        item for item in control_plane.events.items
        if item["family"] == "interaction" and item["name"] == "status_trail_updated"
    )
    assert status_event["payload"]["items"][0]["item_id"] == "activity:calendar.lookup"
    assert status_event["payload"]["items"][0]["expires_at"] is not None
    narration_event = next(
        item for item in control_plane.events.items
        if item["family"] == "narration" and item["name"] == "narration_rendered"
    )
    assert (
        narration_event["payload"]["interaction_debug_snapshot"]["voice_interaction_policy"]["endpointing_ms"]
        == 650
    )
