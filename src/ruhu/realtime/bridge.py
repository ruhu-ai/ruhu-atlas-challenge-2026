from __future__ import annotations

import hashlib

from ..schemas import ConversationState, RuntimeTurn, RuntimeTurnResult
from .service import RealtimeControlPlane


class KernelRealtimeBridge:
    def __init__(self, control_plane: RealtimeControlPlane) -> None:
        self._control_plane = control_plane

    @staticmethod
    def _event_payload(
        *,
        turn_id: str,
        trace_id: str,
        result: RuntimeTurnResult,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "turn_id": turn_id,
            "trace_id": trace_id,
        }
        if result.interaction_debug_snapshot is not None:
            payload["interaction_debug_snapshot"] = (
                result.interaction_debug_snapshot.model_dump(mode="json")
            )
        if extra:
            payload.update(extra)
        return payload

    def record_conversation_started(self, conversation: ConversationState, *, channel: str) -> None:
        if conversation.mode != "live":
            return
        self._control_plane.events.append(
            conversation_id=conversation.conversation_id,
            organization_id=conversation.organization_id,
            family="conversation",
            name="started",
            payload={
                "agent_id": conversation.agent_id,
                "agent_version_id": conversation.agent_version_id,
                "step_id": conversation.step_id,
                "channel": channel,
                "status": conversation.status,
                "started_at": conversation.started_at.isoformat(),
            },
            actor_type="system",
            visibility="surface",
            outbox_topic="conversation_projection",
        )

    def record_turn(self, *, conversation: ConversationState, turn: RuntimeTurn, result: RuntimeTurnResult) -> None:
        if conversation.mode != "live":
            return
        if turn.event_type in {"user_message", "user_final_transcript"} and turn.text:
            self._control_plane.events.append(
                conversation_id=conversation.conversation_id,
                organization_id=conversation.organization_id,
                family="message",
                name="user_accepted",
                payload={
                    **self._event_payload(
                        turn_id=turn.turn_id,
                        trace_id=result.trace_id,
                        result=result,
                        extra={
                            "channel": turn.channel,
                            "modality": turn.modality,
                            "event_type": turn.event_type,
                            "text": turn.text,
                        },
                    )
                },
                actor_type="user",
                visibility="surface",
                causation_id=turn.turn_id,
                correlation_id=result.trace_id,
                outbox_topic="conversation_projection",
            )
        if result.step_before != result.step_after:
            self._control_plane.events.append(
                conversation_id=conversation.conversation_id,
                organization_id=conversation.organization_id,
                family="conversation",
                name="step_changed",
                payload={
                    **self._event_payload(
                        turn_id=turn.turn_id,
                        trace_id=result.trace_id,
                        result=result,
                        extra={
                            "step_before": result.step_before,
                            "step_after": result.step_after,
                        },
                    )
                },
                actor_type="system",
                visibility="surface",
                causation_id=turn.turn_id,
                correlation_id=result.trace_id,
                outbox_topic="conversation_projection",
            )
        if conversation.status == "ended" and conversation.outcome is not None:
            self._control_plane.events.append(
                conversation_id=conversation.conversation_id,
                organization_id=conversation.organization_id,
                family="conversation",
                name="ended",
                payload={
                    **self._event_payload(
                        turn_id=turn.turn_id,
                        trace_id=result.trace_id,
                        result=result,
                        extra={
                            "step_after": conversation.step_id,
                            "outcome": conversation.outcome,
                            "ended_at": None if conversation.ended_at is None else conversation.ended_at.isoformat(),
                            "channel": conversation.channel,
                        },
                    )
                },
                actor_type="system",
                visibility="internal",
                causation_id=turn.turn_id,
                correlation_id=result.trace_id,
                outbox_topic="conversation_projection",
            )
        # Pending action events — surface confirmation requests/outcomes
        if result.tool_calls:
            for tc in result.tool_calls:
                if tc.status in ("confirmation_required", "success", "blocked"):
                    self._control_plane.events.append(
                        conversation_id=conversation.conversation_id,
                        organization_id=conversation.organization_id,
                        family="action",
                        name=f"tool_{tc.status}",
                        payload={
                            **self._event_payload(
                                turn_id=turn.turn_id,
                                trace_id=result.trace_id,
                                result=result,
                                extra={
                                    "tool_ref": tc.tool_ref,
                                    "invocation_id": tc.invocation_id,
                                    "status": tc.status,
                                },
                            )
                        },
                        actor_type="system",
                        visibility="surface",
                        causation_id=turn.turn_id,
                        correlation_id=result.trace_id,
                        outbox_topic="conversation_projection",
                    )
        if result.chosen_action.reason in (
            "pending_action_cancelled",
            "pending_action_execution_failed",
            "pending_action_completion_uncertain",
            "pending_action_late_result_reconciled",
        ):
            self._control_plane.events.append(
                conversation_id=conversation.conversation_id,
                organization_id=conversation.organization_id,
                family="action",
                name=result.chosen_action.reason,
                payload={
                    **self._event_payload(
                        turn_id=turn.turn_id,
                        trace_id=result.trace_id,
                        result=result,
                        extra=(result.chosen_action.payload or {}),
                    )
                },
                actor_type="system",
                visibility="surface",
                causation_id=turn.turn_id,
                correlation_id=result.trace_id,
                outbox_topic="conversation_projection",
            )

        for semantic_event in result.semantic_events:
            if semantic_event.family not in {"interaction", "grounding", "artifact", "narration"}:
                continue
            self._control_plane.events.append(
                conversation_id=conversation.conversation_id,
                organization_id=conversation.organization_id,
                family=semantic_event.family,
                name=semantic_event.name,
                payload={
                    **self._event_payload(
                        turn_id=turn.turn_id,
                        trace_id=result.trace_id,
                        result=result,
                        extra=semantic_event.payload,
                    )
                },
                actor_type="system",
                visibility="surface",
                causation_id=turn.turn_id,
                correlation_id=result.trace_id,
                outbox_topic="conversation_projection",
            )

        for index, message in enumerate(result.emitted_messages):
            # Build a content-hash dedupe key so identical assistant messages
            # from the same turn are not duplicated in the outbox.
            _dedupe_content = f"{turn.turn_id}:{index}:{message.text or ''}"
            _dedupe_key = f"assistant_emitted:{hashlib.sha256(_dedupe_content.encode()).hexdigest()[:24]}"
            event = self._control_plane.events.append(
                conversation_id=conversation.conversation_id,
                organization_id=conversation.organization_id,
                family="message",
                name="assistant_emitted",
                payload={
                    **self._event_payload(
                        turn_id=turn.turn_id,
                        trace_id=result.trace_id,
                        result=result,
                        extra={
                            "message_index": index,
                            "channel": conversation.channel,
                            "role": message.role,
                            "text": message.text,
                            **({"message_type": message.message_type} if message.message_type else {}),
                            **({"payload": message.payload} if message.payload else {}),
                        },
                    )
                },
                actor_type="assistant",
                visibility="surface",
                causation_id=turn.turn_id,
                correlation_id=result.trace_id,
                outbox_topic="conversation_projection",
                outbox_dedupe_key=_dedupe_key,
            )
            if turn.channel == "whatsapp" and (message.text or "").strip():
                self._control_plane.outbox.enqueue(
                    event_id=event.event_id,
                    topic="provider_projection.meta_whatsapp",
                    conversation_id=conversation.conversation_id,
                    organization_id=conversation.organization_id,
                    payload={
                        "channel": "whatsapp",
                        "provider": "meta_whatsapp",
                        "message_index": index,
                        "trace_id": result.trace_id,
                    },
                )
