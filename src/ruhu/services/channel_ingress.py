"""Channel ingress service extracted from ``create_app()`` (RP-3.1 step 11).

Owns live-channel session start and message ingestion for the synthetic
channel routes, the Meta WhatsApp webhook, and the LiveKit phone/voice
provider bridge. Composes :class:`ConversationTurnService` — the kernel call
sites inside these flows all go through ``self.turns.process_turn`` —
including the ``user_final_transcript`` idempotency wrapper
(``commit_final_transcript(process_turn=lambda: self.turns.process_turn(...))``).

``create_app()`` constructs one instance and REBINDS the old closure names
(``_start_live_channel_session``, ``_process_live_channel_message``,
``_process_existing_realtime_session_message``), so route call sites are
textually untouched until steps 14–15. The snapshot/session/observation/cost
helpers stay in api.py (they serve non-ingress routes too) and are threaded
explicitly as callables (blueprint closure-capture hazard).

The response envelopes (``ChannelTurnResponse``,
``ProviderPhoneBridgeResponse`` and its ``LiveKitTransportResponse`` leaf)
moved here with the flows that construct them — importing them from
``ruhu.api`` would be circular. They become router-local when the channel
and provider route groups extract (blueprint end state).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..schemas import Channel, RenderedMessage, RuntimeTurn
from ..tools.types import ToolInvocation
from .conversation_turns import ConversationTurnService

if TYPE_CHECKING:
    from ..provider_costs import ProviderCostRecord
    from ..realtime import RealtimeControlPlane, RealtimeSession
    from ..registry import AgentVersionSnapshot
    from ..schemas import Modality, RuntimeTurnEventType

__all__ = [
    "ChannelIngressService",
    "ChannelTurnResponse",
    "LiveKitTransportResponse",
    "ProviderPhoneBridgeResponse",
]


class LiveKitTransportResponse(BaseModel):
    provider: Literal["livekit"] = "livekit"
    url: str
    room_name: str
    token: str
    participant_identity: str
    agent_name: str
    sdk_version_target: str
    voice_mode: str
    dispatch_strategy: str
    dispatch: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class ChannelTurnResponse(BaseModel):
    conversation_id: str
    channel: Channel
    realtime_session_id: str | None = None
    step_after: str | None = None
    messages: list[RenderedMessage] = Field(default_factory=list)
    trace_id: str | None = None
    pending_tool_invocations: list[ToolInvocation] = Field(default_factory=list)


class ProviderPhoneBridgeResponse(BaseModel):
    conversation_id: str
    realtime_session_id: str | None = None
    step_after: str | None = None
    transport: LiveKitTransportResponse | None = None
    speak_texts: list[str] = Field(default_factory=list)
    messages: list[RenderedMessage] = Field(default_factory=list)
    trace_id: str | None = None
    pending_tool_invocations: list[ToolInvocation] = Field(default_factory=list)


@dataclass(frozen=True)
class ChannelIngressService:
    """Session start + message ingestion for live channels.

    Per-channel variation stays parameterized: dedupe-key strategy
    (``resolve_ingress_idempotency_key``), the final-transcript idempotency
    wrapper, and the provider-cost/observation side effects are threaded
    callables, never reimplemented here.
    """

    turns: ConversationTurnService
    realtime_control_plane: RealtimeControlPlane | None
    resolve_live_agent_snapshot: Callable[..., AgentVersionSnapshot]
    ensure_realtime_session: Callable[..., RealtimeSession | None]
    record_inbound_observation: Callable[..., None]
    record_provider_costs: Callable[..., list[ProviderCostRecord]]
    channel_conversation_id: Callable[[Channel, str], str]
    pending_tool_invocations: Callable[..., list[ToolInvocation]]
    assistant_texts: Callable[[list[RenderedMessage]], list[str]]
    assistant_history: Callable[..., list[RenderedMessage]]
    build_runtime_turn: Callable[..., RuntimeTurn]
    resolve_ingress_idempotency_key: Callable[[str | None, dict[str, object]], str | None]

    def start_channel_session(
        self,
        *,
        channel: Channel,
        agent_id: str,
        external_session_id: str,
        organization_id: str | None = None,
        provider: str | None = None,
        provider_session_id: str | None = None,
        participant_identity: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ChannelTurnResponse:
        snapshot = self.resolve_live_agent_snapshot(agent_id, organization_id=organization_id)
        conversation_id = self.channel_conversation_id(channel, external_session_id)
        existing = self.turns.kernel.load_conversation(conversation_id)
        if existing is None:
            start = self.turns.kernel.start_conversation(
                conversation_id,
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                agent_version_id=snapshot.version_id,
                mode="live",
                channel=channel,
                organization_id=organization_id,
            )
            realtime_session = self.ensure_realtime_session(
                conversation_id=conversation_id,
                organization_id=organization_id,
                channel=channel,
                external_session_id=external_session_id,
                provider=provider,
                provider_session_id=provider_session_id,
                participant_identity=participant_identity,
                metadata=dict(metadata or {}),
                allow_new_on_inactive=True,
            )
            self.record_provider_costs(
                conversation_id=conversation_id,
                organization_id=organization_id,
                realtime_session_id=None if realtime_session is None else realtime_session.realtime_session_id,
                provider=provider,
                payload=metadata,
                default_cost_type="provider_session_start",
            )
            return ChannelTurnResponse(
                conversation_id=conversation_id,
                channel=channel,
                realtime_session_id=None if realtime_session is None else realtime_session.realtime_session_id,
                step_after=start.step_after,
                messages=start.emitted_messages,
                trace_id=start.trace_id,
                pending_tool_invocations=self.pending_tool_invocations(
                    conversation_id,
                    organization_id=organization_id,
                ),
            )
        realtime_session = self.ensure_realtime_session(
            conversation_id=conversation_id,
            organization_id=existing.organization_id,
            channel=channel,
            external_session_id=external_session_id,
            provider=provider,
            provider_session_id=provider_session_id,
            participant_identity=participant_identity,
            metadata=dict(metadata or {}),
            allow_new_on_inactive=True,
        )
        self.record_provider_costs(
            conversation_id=conversation_id,
            organization_id=existing.organization_id,
            realtime_session_id=None if realtime_session is None else realtime_session.realtime_session_id,
            provider=provider,
            payload=metadata,
            default_cost_type="provider_session_resume",
        )
        return ChannelTurnResponse(
            conversation_id=conversation_id,
            channel=channel,
            realtime_session_id=None if realtime_session is None else realtime_session.realtime_session_id,
            step_after=existing.step_id,
            messages=self.assistant_history(conversation_id, organization_id=existing.organization_id),
            pending_tool_invocations=self.pending_tool_invocations(
                conversation_id,
                organization_id=existing.organization_id,
            ),
        )

    def process_live_channel_message(
        self,
        *,
        channel: Channel,
        external_session_id: str,
        agent_id: str | None,
        text: str,
        metadata: dict[str, object],
        modality: Modality,
        event_type: RuntimeTurnEventType,
        organization_id: str | None = None,
        emit_entry_prelude_on_autostart: bool = True,
        provider: str | None = None,
        provider_session_id: str | None = None,
        participant_identity: str | None = None,
        idempotency_key: str | None = None,
    ) -> ChannelTurnResponse:
        conversation_id = self.channel_conversation_id(channel, external_session_id)
        conversation = self.turns.kernel.load_conversation(conversation_id)
        prelude_messages: list[RenderedMessage] = []
        if conversation is None:
            if not agent_id:
                raise HTTPException(status_code=400, detail="agent_id is required")
            snapshot = self.resolve_live_agent_snapshot(agent_id, organization_id=organization_id)
            start = self.turns.kernel.start_conversation(
                conversation_id,
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                agent_version_id=snapshot.version_id,
                mode="live",
                channel=channel,
                organization_id=organization_id,
            )
            conversation = self.turns.kernel.load_conversation(conversation_id)
            if conversation is None:
                raise HTTPException(status_code=500, detail="conversation missing after live autostart")
            if emit_entry_prelude_on_autostart:
                prelude_messages = list(start.emitted_messages)
        else:
            snapshot = self.turns.agent_registry.get_version_snapshot(
                conversation.agent_version_id,
                organization_id=organization_id,
            )
        realtime_session = self.ensure_realtime_session(
            conversation_id=conversation_id,
            organization_id=conversation.organization_id,
            channel=channel,
            external_session_id=external_session_id,
            provider=provider,
            provider_session_id=provider_session_id,
            participant_identity=participant_identity,
            metadata=metadata,
            allow_new_on_inactive=(channel == "whatsapp"),
        )
        realtime_session_id = None if realtime_session is None else realtime_session.realtime_session_id
        if channel == "phone" and realtime_session is not None and realtime_session.status != "active":
            raise HTTPException(status_code=409, detail="phone session is no longer active")
        resolved_idempotency_key = self.resolve_ingress_idempotency_key(idempotency_key, metadata)
        if event_type == "user_final_transcript":
            if self.realtime_control_plane is None:
                raise HTTPException(status_code=503, detail="realtime control plane is not configured")
            if not resolved_idempotency_key:
                raise HTTPException(status_code=400, detail="idempotency_key is required for final transcripts")
            commit = self.realtime_control_plane.commit_final_transcript(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
                realtime_session_id=realtime_session_id or f"session:{conversation_id}",
                text=text,
                idempotency_key=resolved_idempotency_key,
                metadata={**metadata, "channel": channel, "modality": modality},
                process_turn=lambda: self.turns.process_turn(
                    conversation_id,
                    self.build_runtime_turn(
                        turn_id=f"turn:{resolved_idempotency_key}",
                        dedupe_key=resolved_idempotency_key,
                        channel=channel,
                        modality=modality,
                        event_type=event_type,
                        text=text,
                        metadata=metadata,
                    ),
                    agent_document=snapshot.agent_document,
                    agent_id=snapshot.agent_id,
                    agent_name=snapshot.name,
                    organization_id=organization_id,
                ),
            )
            if commit.turn_result is None:
                result_ref = commit.idempotency.result_ref
                duplicate_messages: list[RenderedMessage] = []
                for message_payload in result_ref.get("messages", []):
                    if isinstance(message_payload, dict):
                        duplicate_messages.append(RenderedMessage(**message_payload))
                latest_conversation = self.turns.kernel.load_conversation(conversation_id)
                return ChannelTurnResponse(
                    conversation_id=conversation_id,
                    channel=channel,
                    realtime_session_id=realtime_session_id,
                    step_after=(latest_conversation.step_id if latest_conversation else None),
                    messages=[*prelude_messages, *duplicate_messages],
                    trace_id=result_ref.get("trace_id") if isinstance(result_ref.get("trace_id"), str) else None,
                    pending_tool_invocations=self.pending_tool_invocations(
                        conversation_id,
                        organization_id=organization_id,
                    ),
                )
            result = commit.turn_result
            self.record_provider_costs(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
                realtime_session_id=realtime_session_id,
                provider=provider,
                payload=metadata,
                default_cost_type="provider_turn_ingress",
                turn_trace_id=result.trace_id,
            )
        elif event_type == "user_message":
            self.record_inbound_observation(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
                realtime_session_id=realtime_session_id,
                channel=channel,
                modality=modality,
                text=text,
                metadata=metadata,
                idempotency_key=resolved_idempotency_key,
            )
            result = self.turns.process_turn(
                conversation_id,
                self.build_runtime_turn(
                    turn_id=str(uuid4()),
                    dedupe_key=resolved_idempotency_key or str(uuid4()),
                    channel=channel,
                    modality=modality,
                    event_type=event_type,
                    text=text,
                    metadata=metadata,
                ),
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                organization_id=organization_id,
            )
            self.record_provider_costs(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
                realtime_session_id=realtime_session_id,
                provider=provider,
                payload=metadata,
                default_cost_type="provider_turn_ingress",
                turn_trace_id=result.trace_id,
            )
        else:
            if self.realtime_control_plane is not None:
                self.realtime_control_plane.provisional_transcript_observation(
                    conversation_id=conversation_id,
                    organization_id=conversation.organization_id,
                    realtime_session_id=realtime_session_id or f"session:{conversation_id}",
                    text=text,
                    metadata={**metadata, "channel": channel, "modality": modality},
                )
            self.record_provider_costs(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
                realtime_session_id=realtime_session_id,
                provider=provider,
                payload=metadata,
                default_cost_type="provider_partial_ingress",
            )
            latest_conversation = self.turns.kernel.load_conversation(conversation_id)
            return ChannelTurnResponse(
                conversation_id=conversation_id,
                channel=channel,
                realtime_session_id=realtime_session_id,
                step_after=(latest_conversation.step_id if latest_conversation else None),
                messages=prelude_messages,
                pending_tool_invocations=self.pending_tool_invocations(
                    conversation_id,
                    organization_id=organization_id,
                ),
            )
        return ChannelTurnResponse(
            conversation_id=conversation_id,
            channel=channel,
            realtime_session_id=realtime_session_id,
            step_after=result.step_after,
            messages=[*prelude_messages, *result.emitted_messages],
            trace_id=result.trace_id,
            pending_tool_invocations=self.pending_tool_invocations(
                conversation_id,
                organization_id=organization_id,
            ),
        )

    def process_session_message(
        self,
        *,
        session: RealtimeSession,
        text: str,
        metadata: dict[str, object],
        modality: Modality,
        event_type: RuntimeTurnEventType,
        organization_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ProviderPhoneBridgeResponse:
        conversation = self.turns.kernel.load_conversation(session.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        if session.status != "active":
            raise HTTPException(status_code=409, detail=f"{session.channel} session is no longer active")
        snapshot = self.turns.agent_registry.get_version_snapshot(
            conversation.agent_version_id,
            organization_id=organization_id,
        )
        self.realtime_control_plane.touch_session(
            session.realtime_session_id,
            provider_session_id=session.provider_session_id,
            participant_identity=session.participant_identity,
            metadata=metadata,
        )
        resolved_idempotency_key = self.resolve_ingress_idempotency_key(idempotency_key, metadata)

        # Typed messages during voice sessions must be processed as full
        # kernel turns, not provisional observations.  They bypass the
        # transcript idempotency layer (no STT dedup needed).
        if event_type == "user_message":
            turn_key = resolved_idempotency_key or f"msg:{session.realtime_session_id}:{uuid4().hex[:12]}"
            result = self.turns.process_turn(
                session.conversation_id,
                self.build_runtime_turn(
                    turn_id=f"turn:{turn_key}",
                    dedupe_key=turn_key,
                    channel=session.channel,
                    modality=modality,
                    event_type=event_type,
                    text=text,
                    metadata=metadata,
                ),
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                organization_id=organization_id,
            )
            self.record_provider_costs(
                conversation_id=session.conversation_id,
                organization_id=conversation.organization_id,
                realtime_session_id=session.realtime_session_id,
                provider=session.provider,
                payload=metadata,
                default_cost_type="provider_turn_ingress",
                turn_trace_id=result.trace_id,
            )
            return ProviderPhoneBridgeResponse(
                conversation_id=session.conversation_id,
                realtime_session_id=session.realtime_session_id,
                step_after=result.step_after,
                speak_texts=self.assistant_texts(result.emitted_messages),
                messages=result.emitted_messages,
                trace_id=result.trace_id,
                pending_tool_invocations=self.pending_tool_invocations(
                    session.conversation_id,
                    organization_id=organization_id,
                ),
            )

        if event_type == "user_final_transcript":
            if self.realtime_control_plane is None:
                raise HTTPException(status_code=503, detail="realtime control plane is not configured")
            if not resolved_idempotency_key:
                raise HTTPException(status_code=400, detail="idempotency_key is required for final transcripts")
            commit = self.realtime_control_plane.commit_final_transcript(
                conversation_id=session.conversation_id,
                organization_id=conversation.organization_id,
                realtime_session_id=session.realtime_session_id,
                text=text,
                idempotency_key=resolved_idempotency_key,
                metadata={**metadata, "channel": session.channel, "modality": modality},
                process_turn=lambda: self.turns.process_turn(
                    session.conversation_id,
                    self.build_runtime_turn(
                        turn_id=f"turn:{resolved_idempotency_key}",
                        dedupe_key=resolved_idempotency_key,
                        channel=session.channel,
                        modality=modality,
                        event_type=event_type,
                        text=text,
                        metadata=metadata,
                    ),
                    agent_document=snapshot.agent_document,
                    agent_id=snapshot.agent_id,
                    agent_name=snapshot.name,
                    organization_id=organization_id,
                ),
            )
            if commit.turn_result is None:
                result_ref = commit.idempotency.result_ref
                duplicate_messages: list[RenderedMessage] = []
                for message_payload in result_ref.get("messages", []):
                    if isinstance(message_payload, dict):
                        duplicate_messages.append(RenderedMessage(**message_payload))
                latest_conversation = self.turns.kernel.load_conversation(session.conversation_id)
                return ProviderPhoneBridgeResponse(
                    conversation_id=session.conversation_id,
                    realtime_session_id=session.realtime_session_id,
                    step_after=(latest_conversation.step_id if latest_conversation else None),
                    speak_texts=self.assistant_texts(duplicate_messages),
                    messages=duplicate_messages,
                    trace_id=result_ref.get("trace_id") if isinstance(result_ref.get("trace_id"), str) else None,
                    pending_tool_invocations=self.pending_tool_invocations(
                        session.conversation_id,
                        organization_id=organization_id,
                    ),
                )
            result = commit.turn_result
            self.record_provider_costs(
                conversation_id=session.conversation_id,
                organization_id=conversation.organization_id,
                realtime_session_id=session.realtime_session_id,
                provider=session.provider,
                payload=metadata,
                default_cost_type="provider_turn_ingress",
                turn_trace_id=result.trace_id,
            )
            return ProviderPhoneBridgeResponse(
                conversation_id=session.conversation_id,
                realtime_session_id=session.realtime_session_id,
                step_after=result.step_after,
                speak_texts=self.assistant_texts(result.emitted_messages),
                messages=result.emitted_messages,
                trace_id=result.trace_id,
                pending_tool_invocations=self.pending_tool_invocations(
                    session.conversation_id,
                    organization_id=organization_id,
                ),
            )
        self.realtime_control_plane.provisional_transcript_observation(
            conversation_id=session.conversation_id,
            organization_id=conversation.organization_id,
            realtime_session_id=session.realtime_session_id,
            text=text,
            metadata={**metadata, "channel": session.channel, "modality": modality},
        )
        self.record_provider_costs(
            conversation_id=session.conversation_id,
            organization_id=conversation.organization_id,
            realtime_session_id=session.realtime_session_id,
            provider=session.provider,
            payload=metadata,
            default_cost_type="provider_partial_ingress",
        )
        latest_conversation = self.turns.kernel.load_conversation(session.conversation_id)
        return ProviderPhoneBridgeResponse(
            conversation_id=session.conversation_id,
            realtime_session_id=session.realtime_session_id,
            step_after=(latest_conversation.step_id if latest_conversation else None),
            speak_texts=[],
            messages=[],
            pending_tool_invocations=self.pending_tool_invocations(
                session.conversation_id,
                organization_id=organization_id,
            ),
        )
