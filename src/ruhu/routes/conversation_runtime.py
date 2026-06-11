"""Conversation-runtime routes — extracted from api.py (RP-3.1 step 16,
blueprint group 21 — the FINAL route extraction). SYNC-KERNEL on the turn
path: ``POST /conversations/{conversation_id}/turns`` calls
``turn_service.aprocess_turn`` — the IDENTICAL call the public-widget SSE
stream makes (site 3, the last of the eight kernel call sites) — and the
tool-invocation confirm/cancel routes call
``turn_service.confirm_tool_invocation`` / ``cancel_tool_invocation``
directly. No turn logic remains in any route.

Covers the authenticated runtime reads (conversation snapshot, traces,
citations, realtime events, tool invocations, provider-cost records), the
analysis-sweep trigger, the tool-integration job reads, and the turn +
confirm/cancel POSTs. Hazard H2: ``GET /tool-integration/jobs`` registers
before ``GET /tool-integration/jobs/{job_id}``, exactly as inline. The
tool-integration presentation helpers and the scoped-conversation loader
moved here with the routes — nothing else in api.py used them. The
org-scope resolvers and ``_build_runtime_turn_from_metadata`` are shared
with other routers and thread in as explicit kwargs; the citation reader
and sweep fact pipeline are built once per app from the threaded
``runtime_session_factory``, exactly as the inline block did.

The turn/job/cost DTOs still live in ``ruhu.api``, so this module is
imported by ``create_app()`` AT THE MOUNT SITE rather than at api.py's
module top (hazard H7: DTO imports sit at module top here).

No ``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

# DTOs at module top (hazard H7: PEP 563 handler annotations resolve against
# this module's globals).
from ..api import (
    ProviderCostListResponse,
    ToolIntegrationJobDetailResponse,
    ToolIntegrationJobListResponse,
    ToolIntegrationJobSummaryResponse,
    TurnRequest,
)
from ..analysis_sweep import (
    AnalysisSweepResult,
    TurnTranscript,
    run_analysis_sweep,
)
from ..agent_document import compile_agent_document
from ..api_models import (
    ConversationRuntimeResponse,
    ConversationTraceResponse,
    RealtimeConversationEventResponse,
    TurnExecutionResponse,
)
from ..capture import build_default_fact_pipeline as _build_sweep_pipeline
from ..capture.audit import SqlAuditWriter as _SqlAuditWriter
from ..citations import (
    ConversationCitationsResponse,
    SqlCitationReader,
    build_citations,
)
from ..services.conversation_responses import (
    conversation_runtime_response,
    conversation_trace_response,
    realtime_conversation_event_response,
    turn_execution_response,
)
from ..tools.types import ToolIntegrationJob, ToolInvocation

if TYPE_CHECKING:
    from ..kernel import ConversationKernel
    from ..provider_costs import SQLAlchemyProviderCostStore
    from ..realtime import RealtimeControlPlane
    from ..registry import SQLAlchemyAgentRegistry
    from ..schemas import ConversationState, RuntimeTurn
    from ..services.conversation_turns import ConversationTurnService

logger = logging.getLogger(__name__)


def _provider_for_tool_integration_job(job: ToolIntegrationJob) -> str:
    provider = job.metadata.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip()
    spec_payload = dict(job.payload.get("tool_spec") or {})
    executor_config = dict(spec_payload.get("executor_config") or {})
    candidate = executor_config.get("provider")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return job.executor_kind


def _conversation_id_for_tool_integration_job(job: ToolIntegrationJob) -> str | None:
    payload_call = dict(job.payload.get("tool_call") or {})
    caller = dict(payload_call.get("caller") or {})
    conversation_id = caller.get("conversation_id")
    if isinstance(conversation_id, str) and conversation_id.strip():
        return conversation_id.strip()
    return None


def _tool_integration_job_summary(job: ToolIntegrationJob) -> ToolIntegrationJobSummaryResponse:
    return ToolIntegrationJobSummaryResponse(
        job_id=job.job_id,
        invocation_id=job.invocation_id,
        conversation_id=_conversation_id_for_tool_integration_job(job),
        organization_id=job.organization_id,
        provider=_provider_for_tool_integration_job(job),
        tool_ref=job.tool_ref,
        executor_kind=job.executor_kind,
        resolution_mode=job.resolution_mode,
        status=job.status,
        queue_name=job.queue_name,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        external_job_id=job.external_job_id,
        callback_correlation_id=job.callback_correlation_id,
        submitted_at=job.submitted_at,
        last_progress_at=job.last_progress_at,
        next_poll_at=job.next_poll_at,
        next_retry_at=job.next_retry_at,
        finished_at=job.finished_at,
        error=job.error,
        metadata=dict(job.metadata),
    )


def _tool_integration_job_detail(job: ToolIntegrationJob) -> ToolIntegrationJobDetailResponse:
    payload_call = dict(job.payload.get("tool_call") or {})
    summary = _tool_integration_job_summary(job)
    return ToolIntegrationJobDetailResponse(
        **summary.model_dump(mode="json"),
        args=dict(payload_call.get("args") or {}),
        result=None if job.result is None else dict(job.result),
    )


def _tool_integration_counts_by_provider_status(
    jobs: list[ToolIntegrationJob],
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for job in jobs:
        provider = _provider_for_tool_integration_job(job)
        provider_counts = counts.setdefault(provider, {})
        provider_counts[job.status] = provider_counts.get(job.status, 0) + 1
    return counts


def build_conversation_runtime_router(
    *,
    kernel: "ConversationKernel",
    agent_registry: "SQLAlchemyAgentRegistry",
    turn_service: "ConversationTurnService",
    realtime_control_plane: "RealtimeControlPlane | None",
    provider_cost_store: "SQLAlchemyProviderCostStore | None",
    runtime_session_factory: object | None,
    organization_id_for_request: Callable[[Request], str | None],
    tool_integration_organization_id_for_request: Callable[[Request], str | None],
    build_runtime_turn_from_metadata: Callable[..., "RuntimeTurn"],
) -> APIRouter:
    router = APIRouter()

    _citation_reader = (
        SqlCitationReader(runtime_session_factory)
        if runtime_session_factory is not None
        else None
    )
    _sweep_fact_pipeline = (
        _build_sweep_pipeline(
            None,
            audit_writer=_SqlAuditWriter(runtime_session_factory),
        )
        if runtime_session_factory is not None
        else None
    )

    def _load_scoped_conversation(request: Request, conversation_id: str) -> "tuple[ConversationState, str | None]":
        organization_id = organization_id_for_request(request)
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        if organization_id is not None and conversation.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        return conversation, organization_id

    @router.get("/conversations/{conversation_id}", response_model=ConversationRuntimeResponse)
    def get_conversation(conversation_id: str, request: Request) -> ConversationRuntimeResponse:
        conversation, _ = _load_scoped_conversation(request, conversation_id)
        agent_document = None
        try:
            snapshot = agent_registry.get_version_snapshot(
                conversation.agent_version_id,
                organization_id=conversation.organization_id,
            )
        except KeyError:
            snapshot = None
        if snapshot is not None:
            agent_document = snapshot.agent_document
        return conversation_runtime_response(
            conversation,
            agent_document=agent_document,
        )

    @router.post("/conversations/{conversation_id}/turns", response_model=TurnExecutionResponse)
    async def process_turn(conversation_id: str, payload: TurnRequest, request: Request) -> TurnExecutionResponse:
        conversation, organization_id = _load_scoped_conversation(request, conversation_id)

        try:
            snapshot = agent_registry.get_version_snapshot(
                conversation.agent_version_id,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        turn_id = payload.turn_id or str(uuid4())
        dedupe_key = payload.dedupe_key or turn_id
        turn = build_runtime_turn_from_metadata(
            turn_id=turn_id,
            dedupe_key=dedupe_key,
            channel=payload.channel,
            modality=payload.modality,
            event_type=payload.event_type,
            text=payload.text,
            metadata=payload.metadata,
        )
        try:
            result = await turn_service.aprocess_turn(
                request.app,
                conversation_id,
                turn,
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                organization_id=organization_id,
            )
        except HTTPException:
            # Tool-policy / authorization rejections inside the kernel
            # propagate as HTTPException — let those carry their own
            # status codes through to the client.
            raise
        except Exception:
            # LLM timeout, circuit-breaker open, integration-job failure,
            # DB transient error. Log with full conversation context and
            # convert to 503 so the client knows the issue is transient
            # and retry-friendly. We deliberately do NOT leak exception
            # messages — they may include internal service URLs / tool
            # specs / prompt fragments.
            logger.exception(
                "authenticated turn processing failed",
                extra={
                    "conversation_id": conversation_id,
                    "organization_id": organization_id,
                    "agent_id": conversation.agent_id,
                    "turn_id": turn_id,
                },
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "turn_processing_failed",
                    "detail": "Turn could not be processed. Please retry.",
                    "turn_id": turn_id,
                },
            )
        return turn_execution_response(
            result,
            agent_document=snapshot.agent_document,
        )

    @router.get("/conversations/{conversation_id}/traces", response_model=list[ConversationTraceResponse])
    def get_traces(
        conversation_id: str,
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[ConversationTraceResponse]:
        _, organization_id = _load_scoped_conversation(request, conversation_id)
        # Pagination happens in the store (SQL LIMIT/OFFSET) — RP-3.3 retired
        # the load-everything-and-slice-in-memory pattern here.
        page = kernel.trace_store.by_conversation(
            conversation_id,
            organization_id=organization_id,
            limit=limit,
            offset=offset,
        )
        try:
            from ..observability.metrics import list_endpoint_row_count
            list_endpoint_row_count.labels(endpoint="traces").observe(len(page))
        except Exception:
            pass
        return [conversation_trace_response(trace) for trace in page]

    @router.get(
        "/conversations/{conversation_id}/citations",
        response_model=ConversationCitationsResponse,
    )
    def get_citations(
        conversation_id: str,
        request: Request,
    ) -> ConversationCitationsResponse:
        _, organization_id = _load_scoped_conversation(request, conversation_id)
        if _citation_reader is None:
            return ConversationCitationsResponse(
                conversation_id=conversation_id,
                citations=[],
            )
        rows = _citation_reader.citations_for(
            conversation_id, organization_id=organization_id
        )
        traces = kernel.trace_store.by_conversation(
            conversation_id, organization_id=organization_id
        )
        turn_text_by_id: dict[str, str] = {}
        for trace in traces:
            observation = trace.normalized_observation
            if observation is not None and observation.redacted_text:
                turn_text_by_id[trace.turn_id] = observation.redacted_text
        citations = build_citations(
            rows=rows,
            turn_text_lookup=turn_text_by_id.get,
        )
        return ConversationCitationsResponse(
            conversation_id=conversation_id,
            citations=citations,
        )

    @router.post(
        "/conversations/{conversation_id}/analysis-sweep",
        response_model=AnalysisSweepResult,
    )
    def trigger_analysis_sweep(
        conversation_id: str,
        request: Request,
    ) -> AnalysisSweepResult:
        state, organization_id = _load_scoped_conversation(request, conversation_id)
        if _sweep_fact_pipeline is None:
            return AnalysisSweepResult(conversation_id=conversation_id)
        try:
            document = agent_registry.get_agent_document(
                state.agent_id,
                target="published",
                organization_id=organization_id,
            )
        except Exception:
            document = agent_registry.get_agent_document(
                state.agent_id,
                target="draft",
                organization_id=organization_id,
            )
        compiled = compile_agent_document(document)
        if not compiled.analysis_schema:
            return AnalysisSweepResult(conversation_id=conversation_id)
        traces = kernel.trace_store.by_conversation(
            conversation_id, organization_id=organization_id
        )
        transcripts: list[TurnTranscript] = []
        for trace in traces:
            observation = trace.normalized_observation
            if observation is not None and observation.redacted_text:
                transcripts.append(
                    TurnTranscript(turn_id=trace.turn_id, text=observation.redacted_text)
                )
        return run_analysis_sweep(
            conversation_id=conversation_id,
            organization_id=organization_id,
            agent_document=compiled,
            transcripts=transcripts,
            existing_facts=dict(state.facts),
            existing_fact_metadata=None,
            fact_pipeline=_sweep_fact_pipeline,
        )

    @router.get("/conversations/{conversation_id}/realtime-events", response_model=list[RealtimeConversationEventResponse])
    def get_realtime_events(
        conversation_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        family: str | None = Query(default=None),
        name: str | None = Query(default=None),
    ) -> list[RealtimeConversationEventResponse]:
        _, _ = _load_scoped_conversation(request, conversation_id)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        events = realtime_control_plane.events.replay(
            conversation_id=conversation_id,
            after_sequence=after_sequence,
        )
        if family is not None:
            events = [event for event in events if event.family == family]
        if name is not None:
            events = [event for event in events if event.name == name]
        return [realtime_conversation_event_response(event) for event in events]

    @router.get("/conversations/{conversation_id}/tool-invocations", response_model=list[ToolInvocation])
    def list_tool_invocations(conversation_id: str, request: Request) -> list[ToolInvocation]:
        _, organization_id = _load_scoped_conversation(request, conversation_id)
        if kernel.tool_runtime is None:
            raise HTTPException(status_code=503, detail="tool runtime is not configured")
        return kernel.tool_runtime.list_conversation_invocations(
            conversation_id,
            organization_id=organization_id,
        )

    @router.get("/tool-integration/jobs", response_model=ToolIntegrationJobListResponse)
    def list_tool_integration_jobs(
        request: Request,
        status: str | None = Query(default=None),
        conversation_id: str | None = Query(default=None),
        stuck_only: bool = Query(default=False),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ToolIntegrationJobListResponse:
        if kernel.tool_runtime is None or kernel.tool_runtime.integration_runtime is None:
            raise HTTPException(status_code=503, detail="tool integration runtime is not configured")
        organization_id = tool_integration_organization_id_for_request(request)
        integration_runtime = kernel.tool_runtime.integration_runtime
        if stuck_only:
            jobs = integration_runtime.list_stuck_jobs(
                organization_id=organization_id,
                limit=limit,
            )
            if status is not None:
                jobs = [job for job in jobs if job.status == status]
            if conversation_id is not None:
                jobs = [job for job in jobs if _conversation_id_for_tool_integration_job(job) == conversation_id]
        else:
            jobs = integration_runtime.list_jobs(
                organization_id=organization_id,
                status=status,
                conversation_id=conversation_id,
                limit=limit,
            )
        all_jobs = integration_runtime.list_jobs(
            organization_id=organization_id,
            limit=1000,
        )
        return ToolIntegrationJobListResponse(
            items=[_tool_integration_job_summary(job) for job in jobs],
            counts_by_status=integration_runtime.count_jobs_by_status(organization_id=organization_id),
            counts_by_provider_status=_tool_integration_counts_by_provider_status(all_jobs),
        )

    @router.get("/tool-integration/jobs/{job_id}", response_model=ToolIntegrationJobDetailResponse)
    def get_tool_integration_job(job_id: str, request: Request) -> ToolIntegrationJobDetailResponse:
        if kernel.tool_runtime is None or kernel.tool_runtime.integration_runtime is None:
            raise HTTPException(status_code=503, detail="tool integration runtime is not configured")
        organization_id = tool_integration_organization_id_for_request(request)
        job = kernel.tool_runtime.integration_runtime.load_job(job_id, organization_id=organization_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown tool integration job")
        return _tool_integration_job_detail(job)

    @router.get("/conversations/{conversation_id}/provider-cost-records", response_model=ProviderCostListResponse)
    def list_provider_cost_records(conversation_id: str, request: Request) -> ProviderCostListResponse:
        _, organization_id = _load_scoped_conversation(request, conversation_id)
        if provider_cost_store is None:
            raise HTTPException(status_code=503, detail="provider cost store is not configured")
        return ProviderCostListResponse(
            items=provider_cost_store.by_conversation(
                conversation_id,
                organization_id=organization_id,
            )
        )

    @router.post("/conversations/{conversation_id}/tool-invocations/{invocation_id}/confirm", response_model=TurnExecutionResponse)
    def confirm_tool_invocation(conversation_id: str, invocation_id: str, request: Request) -> TurnExecutionResponse:
        conversation, organization_id = _load_scoped_conversation(request, conversation_id)
        if kernel.tool_runtime is None:
            raise HTTPException(status_code=503, detail="tool runtime is not configured")
        try:
            snapshot = agent_registry.get_version_snapshot(
                conversation.agent_version_id,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            result = turn_service.confirm_tool_invocation(
                conversation_id,
                invocation_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return turn_execution_response(
            result,
            agent_document=snapshot.agent_document,
        )

    @router.post("/conversations/{conversation_id}/tool-invocations/{invocation_id}/cancel", response_model=TurnExecutionResponse)
    def cancel_tool_invocation(conversation_id: str, invocation_id: str, request: Request) -> TurnExecutionResponse:
        conversation, organization_id = _load_scoped_conversation(request, conversation_id)
        if kernel.tool_runtime is None:
            raise HTTPException(status_code=503, detail="tool runtime is not configured")
        try:
            snapshot = agent_registry.get_version_snapshot(
                conversation.agent_version_id,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            result = turn_service.cancel_tool_invocation(
                conversation_id,
                invocation_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return turn_execution_response(
            result,
            agent_document=snapshot.agent_document,
        )

    return router
