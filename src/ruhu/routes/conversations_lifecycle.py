"""Conversation-lifecycle routes — extracted from api.py (RP-3.1 step 13,
blueprint group 18). SYNC-KERNEL: every route here calls
``kernel.start_conversation`` directly, and replay drives turns through
``turn_service.process_turn`` — the same ConversationTurnService call the
widget and ``/turns`` routes make.

Three builders because the inline routes sat at three registration
positions (hazard H2 / H1 schema-order neutrality):

- ``build_test_session_router`` — ``POST /agents/{agent_id}/test-session``
  (between the agent-authoring router and ``/agents:reload``);
- ``build_internal_phone_routes_router`` —
  ``POST /internal/phone-number-routes/resolve`` (after ``/agents:reload``,
  before the public-widget block);
- ``build_conversations_lifecycle_router`` — ``POST /conversations``,
  ``POST /simulations``, ``POST /agents/{agent_id}/replay`` (after the
  widget-analytics router, before the simulation-fixtures router).

The lifecycle DTOs still live in ``ruhu.api``, so this module is imported
by ``create_app()`` AT THE MOUNT SITE rather than at api.py's module top
(hazard H7: DTO imports sit at module top here). ``resolve_agent_snapshot``
and ``build_runtime_turn_from_metadata`` are create_app() closures shared
with the fixtures/evaluation routers and the still-inline channel/provider
groups (blueprint steps 14–16) — they thread in as explicit kwargs.
No ``tags=`` / ``prefix=`` and unchanged handler names (H1).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

# DTOs at module top (hazard H7: PEP 563 handler annotations resolve against
# this module's globals).
from ..api import (
    AgentReplayRequest,
    AgentReplayResponse,
    CanvasTestSessionCreateRequest,
    InternalPhoneNumberRouteResolveRequest,
    InternalPhoneNumberRouteResolveResponse,
    StartConversationRequest,
    WidgetSessionResponse,
)
from ..agent_review import build_agent_metrics
from ..api_auth import RequestAuthContext
from ..api_models import StartConversationResponse
from ..auth_deps import make_reviewer_context_dep
from ..phone_numbers import resolve_phone_number_route
from ..schemas import (
    RuntimeTurnResult,
    SimulationRun,
    SimulationSource,
    SimulationTurnInput,
)
from ..services.conversation_responses import (
    conversation_runtime_response,
    turn_execution_response,
)
from ..services.org_scope import organization_id_for_context
from ..services.widget_sessions import (
    WIDGET_SESSION_TOKEN_METADATA_KEY,
    hash_widget_session_token,
    issue_widget_session_token,
)

if TYPE_CHECKING:
    from ..kernel import ConversationKernel
    from ..phone_numbers import PhoneNumberRouteConfig
    from ..registry import SQLAlchemyAgentRegistry
    from ..services.conversation_turns import ConversationTurnService
    from ..services.widget_sessions import WidgetSessionAccessService

logger = logging.getLogger(__name__)


def _internal_phone_route_response(route: "PhoneNumberRouteConfig") -> InternalPhoneNumberRouteResolveResponse:
    return InternalPhoneNumberRouteResolveResponse(
        route_key=route.route_key,
        phone_number=route.phone_number,
        agent_id=route.agent_id,
        channel=route.channel,
        organization_id=route.organization_id,
        provider=route.provider,
        provider_resource_id=route.provider_resource_id,
        display_name=route.display_name,
        country_code=route.country_code,
        enabled=route.enabled,
        capabilities=list(route.capabilities),
        metadata=dict(route.metadata),
    )


def _build_simulation_metadata(
    *,
    source: SimulationSource,
    starting_step_id: str | None,
    starting_scenario_id: str | None,
    seed_facts: dict[str, object],
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    merged_metadata = dict(metadata or {})
    simulation_metadata = dict(merged_metadata.get("simulation", {}))
    simulation_metadata.update(
        {
            "source": source,
            "starting_step_id": starting_step_id,
            "starting_scenario_id": starting_scenario_id,
            "seed_facts": dict(seed_facts),
        }
    )
    merged_metadata["simulation"] = simulation_metadata
    return merged_metadata


def _normalize_replay_turns(payload: AgentReplayRequest) -> list[SimulationTurnInput]:
    if payload.turns:
        return payload.turns
    return [
        SimulationTurnInput(
            event_type="user_message",
            modality="text",
            text=utterance,
            metadata={},
        )
        for utterance in payload.utterances
    ]


def build_test_session_router(
    *,
    kernel: "ConversationKernel",
    agent_registry: "SQLAlchemyAgentRegistry",
    widget_session_access: "WidgetSessionAccessService",
    auth_enabled: bool,
    widget_transcript_history: Callable,
    widget_messages_from_rendered: Callable,
    pending_tool_invocations: Callable,
) -> APIRouter:
    """Build the canvas test-session router.

    The transcript/pending-invocation projections are create_app() closures
    shared with the public-widget router — threaded as explicit kwargs.
    """
    router = APIRouter()

    _require_runtime_reviewer_context = make_reviewer_context_dep(auth_enabled)
    _organization_id_for_context = organization_id_for_context
    _require_public_widget_session_access = (
        widget_session_access.require_public_widget_session_access
    )

    @router.post("/agents/{agent_id}/test-session", response_model=WidgetSessionResponse)
    def create_canvas_test_session(
        agent_id: str,
        payload: CanvasTestSessionCreateRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> WidgetSessionResponse:
        organization_id = _organization_id_for_context(context)
        if organization_id is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if payload.channel != "web_widget":
            raise HTTPException(status_code=400, detail="canvas test sessions must use the web_widget channel")
        try:
            version_id = agent_registry.resolve_version_id(
                agent_id,
                target="draft",
                organization_id=organization_id,
            )
            snapshot = agent_registry.get_version_snapshot(
                version_id,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        if payload.conversation_id:
            existing = kernel.load_conversation(payload.conversation_id)
            if existing is not None:
                if existing.organization_id != organization_id:
                    raise HTTPException(
                        status_code=403,
                        detail="conversation belongs to a different organization",
                    )
                presented_token = _require_public_widget_session_access(
                    request,
                    existing,
                    explicit_token=payload.session_token,
                )
                if existing.agent_id != agent_id:
                    raise HTTPException(status_code=409, detail="conversation belongs to a different agent")
                return WidgetSessionResponse(
                    conversation_id=existing.conversation_id,
                    agent_id=existing.agent_id,
                    step_id=existing.step_id,
                    resumed=True,
                    session_token=presented_token,
                    messages=widget_transcript_history(
                        existing.conversation_id,
                        organization_id=existing.organization_id,
                    ),
                    pending_tool_invocations=pending_tool_invocations(
                        existing.conversation_id,
                        organization_id=existing.organization_id,
                    ),
                )

        conversation_id = payload.conversation_id or str(uuid4())
        session_token = issue_widget_session_token()
        try:
            start = kernel.start_conversation(
                conversation_id,
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                agent_version_id=snapshot.version_id,
                mode="live",
                channel=payload.channel,
                organization_id=organization_id,
                metadata={
                    WIDGET_SESSION_TOKEN_METADATA_KEY: hash_widget_session_token(session_token),
                    **(
                        {"anonymous_id": payload.anonymous_id.strip()}
                        if isinstance(payload.anonymous_id, str) and payload.anonymous_id.strip()
                        else {}
                    ),
                    "canvas_test_session": True,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return WidgetSessionResponse(
            conversation_id=conversation_id,
            agent_id=snapshot.agent_id,
            step_id=start.step_after,
            resumed=False,
            session_token=session_token,
            messages=widget_messages_from_rendered(start.emitted_messages),
            pending_tool_invocations=pending_tool_invocations(
                conversation_id,
                organization_id=organization_id,
            ),
        )

    return router


def build_internal_phone_routes_router(
    *,
    require_internal_api_access: Callable,
    phone_number_registry: object | None,
    phone_number_routes: list,
) -> APIRouter:
    """Build the internal phone-number-route resolve router.

    ``require_internal_api_access`` is create_app()'s closure (shared with
    the ``/agents:reload`` router) — threaded as an explicit kwarg.
    """
    router = APIRouter()

    @router.post("/internal/phone-number-routes/resolve", response_model=InternalPhoneNumberRouteResolveResponse)
    def resolve_internal_phone_number_route(
        payload: InternalPhoneNumberRouteResolveRequest,
        request: Request,
    ) -> InternalPhoneNumberRouteResolveResponse:
        require_internal_api_access(request)
        try:
            resolved: "PhoneNumberRouteConfig | None" = None
            if phone_number_registry is not None:
                resolved = phone_number_registry.resolve_route(
                    phone_number=payload.phone_number,
                    channel=payload.channel,
                    provider=payload.provider,
                )
            if resolved is None:
                resolved = resolve_phone_number_route(
                    phone_number_routes,
                    phone_number=payload.phone_number,
                    channel=payload.channel,
                    provider=payload.provider,
                )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if resolved is None:
            raise HTTPException(status_code=404, detail="no phone number route matched")
        return _internal_phone_route_response(resolved)

    return router


def build_conversations_lifecycle_router(
    *,
    kernel: "ConversationKernel",
    turn_service: "ConversationTurnService",
    auth_enabled: bool,
    resolve_agent_snapshot: Callable,
    build_runtime_turn_from_metadata: Callable,
) -> APIRouter:
    """Build the conversations/simulations/replay router.

    ``resolve_agent_snapshot`` and ``build_runtime_turn_from_metadata`` are
    create_app() closures shared with the fixtures/evaluation routers and
    the still-inline channel/provider groups — threaded as explicit kwargs.
    """
    router = APIRouter()

    _require_runtime_reviewer_context = make_reviewer_context_dep(auth_enabled)

    @router.post("/conversations", response_model=StartConversationResponse)
    def start_conversation(payload: StartConversationRequest, request: Request) -> StartConversationResponse:
        snapshot, organization_id = resolve_agent_snapshot(
            request,
            payload.agent_id,
            target="published",
            agent_version_id=payload.agent_version_id,
        )
        conversation_id = payload.conversation_id or str(uuid4())
        try:
            start = kernel.start_conversation(
                conversation_id,
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                agent_version_id=snapshot.version_id,
                mode="live",
                channel=payload.channel,
                organization_id=organization_id,
                starting_step_id=payload.starting_step_id,
                starting_scenario_id=payload.starting_scenario_id,
                seed_facts=payload.seed_facts,
                metadata=payload.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=500, detail="conversation was not created")
        return StartConversationResponse(
            conversation=conversation_runtime_response(
                conversation,
                agent_document=snapshot.agent_document,
            ),
            start=turn_execution_response(
                start,
                agent_document=snapshot.agent_document,
            ),
        )

    @router.post("/simulations", response_model=StartConversationResponse)
    def start_simulation(payload: StartConversationRequest, request: Request) -> StartConversationResponse:
        snapshot, organization_id = resolve_agent_snapshot(
            request,
            payload.agent_id,
            target="draft",
            agent_version_id=payload.agent_version_id,
        )
        conversation_id = payload.conversation_id or str(uuid4())
        simulation_metadata = _build_simulation_metadata(
            source=payload.simulation_source or "interactive",
            starting_step_id=payload.starting_step_id,
            starting_scenario_id=payload.starting_scenario_id,
            seed_facts=payload.seed_facts,
            metadata=payload.metadata,
        )
        try:
            start = kernel.start_conversation(
                conversation_id,
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                agent_version_id=snapshot.version_id,
                mode="simulation",
                channel=payload.channel,
                organization_id=organization_id,
                starting_step_id=payload.starting_step_id,
                starting_scenario_id=payload.starting_scenario_id,
                seed_facts=payload.seed_facts,
                metadata=simulation_metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=500, detail="conversation was not created")
        return StartConversationResponse(
            conversation=conversation_runtime_response(
                conversation,
                agent_document=snapshot.agent_document,
            ),
            start=turn_execution_response(
                start,
                agent_document=snapshot.agent_document,
            ),
        )

    @router.post("/agents/{agent_id}/replay", response_model=AgentReplayResponse)
    def replay_agent_transcript(
        agent_id: str,
        payload: AgentReplayRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> AgentReplayResponse:
        snapshot, organization_id = resolve_agent_snapshot(
            request,
            agent_id,
            target="draft",
            agent_version_id=payload.agent_version_id,
        )
        conversation_id = payload.conversation_id or str(uuid4())
        simulation_metadata = _build_simulation_metadata(
            source="replay",
            starting_step_id=payload.starting_step_id,
            starting_scenario_id=payload.starting_scenario_id,
            seed_facts=payload.seed_facts,
            metadata=payload.metadata,
        )
        try:
            start = kernel.start_conversation(
                conversation_id,
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                agent_version_id=snapshot.version_id,
                mode="simulation",
                channel=payload.channel,
                organization_id=organization_id,
                starting_step_id=payload.starting_step_id,
                starting_scenario_id=payload.starting_scenario_id,
                seed_facts=payload.seed_facts,
                metadata=simulation_metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        results: list[RuntimeTurnResult] = []
        for replay_turn in _normalize_replay_turns(payload):
            turn_id = replay_turn.turn_id or str(uuid4())
            dedupe_key = replay_turn.dedupe_key or turn_id
            result = turn_service.process_turn(
                conversation_id,
                build_runtime_turn_from_metadata(
                    turn_id=turn_id,
                    dedupe_key=dedupe_key,
                    channel=payload.channel,
                    modality=replay_turn.modality,
                    event_type=replay_turn.event_type,
                    text=replay_turn.text,
                    metadata=replay_turn.metadata,
                    attachments=getattr(replay_turn, "attachments", None),
                ),
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                organization_id=organization_id,
            )
            results.append(result)
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=500, detail="simulation conversation missing after replay")
        simulation = SimulationRun(
            start=start,
            turns=results,
            final_step_id=conversation.step_id,
            final_facts=conversation.facts,
        )
        replay_traces = kernel.trace_store.by_conversation(conversation_id, organization_id=organization_id)
        metrics = build_agent_metrics(
            agent_id=agent_id,
            agent_version_id=snapshot.version_id,
            conversations=[conversation],
            traces=replay_traces,
        )
        return AgentReplayResponse(simulation=simulation, metrics=metrics)

    return router
