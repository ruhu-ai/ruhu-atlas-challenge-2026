from __future__ import annotations

import asyncio
import functools
import json
import os
from datetime import datetime, timezone
from typing import Callable

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .api_auth import RequestAuthContext
from .atlas_coordinator import AtlasCoordinator
from .auth_deps import make_internal_superuser_dep
from .atlas_models import AtlasApplyRequestRecordModel, AtlasSession
from .atlas_protocol import (
    AtlasAgentEnabledResponse,
    AtlasAgentEnabledToggleRequest,
    AtlasApplyRequest,
    AtlasApplyResponse,
    AtlasArchiveSessionResponse,
    AtlasEventEnvelope,
    AtlasEventsPageResponse,
    AtlasMessageItem,
    AtlasMessagesPageResponse,
    AtlasPermissionDecision,
    AtlasPermissionDecisionResponse,
    AtlasPermissionRequestModel,
    AtlasRolloutSummaryResponse,
    AtlasSessionResponse,
    AtlasSessionsPageResponse,
    AtlasSessionStartRequest,
    AtlasTurnRequest,
    AtlasTurnResponse,
)
from .atlas_readiness_models import (
    AtlasReadinessEventsPage,
    AtlasReadinessProviderHealth,
    AtlasReadinessProviderPolicy,
    AtlasReadinessReport,
    AtlasReadinessRunRequest,
    AtlasReadinessRunsPage,
    AtlasReadinessRunSummary,
)
from .atlas_readiness_service import AtlasReadinessService
from .atlas_readiness_store import AtlasReadinessStore
from .atlas_store import AtlasStore, new_atlas_apply_request_id, new_atlas_session_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_atlas_router(
    *,
    agent_registry,
    atlas_store: AtlasStore,
    tool_runtime=None,
    connection_store=None,
    definition_store=None,
    binding_store=None,
    conversation_store=None,
    trace_store=None,
    readiness_store: AtlasReadinessStore | None = None,
    readiness_artifact_store=None,
    get_organization_id: Callable[[Request], str | None],
    user_id_for_context: Callable[[RequestAuthContext | None], str | None],
    require_author_context: Callable[[Request], RequestAuthContext | None],
    required_author_organization_id: Callable[[RequestAuthContext | None], str],
    rate_limiter=None,
) -> APIRouter:
    router = APIRouter(
        prefix="/atlas",
        tags=["atlas"],
        dependencies=[rate_limiter] if rate_limiter else [],
    )
    coordinator = AtlasCoordinator(
        agent_registry=agent_registry,
        atlas_store=atlas_store,
        tool_runtime=tool_runtime,
        connection_store=connection_store,
        definition_store=definition_store,
        binding_store=binding_store,
        conversation_store=conversation_store,
        trace_store=trace_store,
    )
    readiness_service = (
        AtlasReadinessService(
            agent_registry=agent_registry,
            atlas_store=atlas_store,
            readiness_store=readiness_store,
            artifact_store=readiness_artifact_store,
            # AR-4.2: demo (microfinance) case set is opt-in via env; the
            # production default derives cases from the agent's own document.
            demo_case_set=(os.getenv("RUHU_ATLAS_READINESS_DEMO_CASES") or "").strip().lower()
            in {"1", "true", "yes", "on"},
        )
        if readiness_store is not None
        else None
    )

    _require_internal_superuser = make_internal_superuser_dep()

    def _get_session_or_404(session_id: str, organization_id: str | None) -> AtlasSession:
        session = coordinator.get_session(session_id, organization_id=organization_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown atlas session")
        return session

    def _ensure_session_not_archived(session_model: AtlasSession) -> None:
        """Archived sessions are read-only — reject turns, applies, and
        permission decisions with a stable detail code clients can match on."""
        if session_model.status == "archived":
            raise HTTPException(status_code=409, detail="atlas_session_archived")

    def _session_response(session_model: AtlasSession) -> AtlasSessionResponse:
        return AtlasSessionResponse(
            session_id=session_model.session_id,
            status=session_model.status,
            scope=session_model.scope,
            agent_id=session_model.agent_id,
            agent_version_id=session_model.agent_version_id,
            created_by=session_model.created_by,
            scenario_id=session_model.scenario_id,
            step_id=session_model.step_id,
            created_at=session_model.created_at,
            updated_at=session_model.updated_at,
        )

    def _event_envelope(item) -> AtlasEventEnvelope:
        return AtlasEventEnvelope(
            event_id=item.event_id,
            session_id=item.session_id,
            sequence_number=item.sequence_number,
            type=item.type,
            created_at=item.created_at,
            payload=item.payload,
        )

    def _session_state_response(session_model: AtlasSession) -> AtlasTurnResponse:
        document, compiled_document = coordinator.resolve_document_and_compiled(session_model)
        proposed_changes = atlas_store.load_proposed_changes(
            session_model.session_id,
            organization_id=session_model.organization_id,
        )
        validation = coordinator.build_validation(document)
        pending_permissions = coordinator.permission_models(session_model)
        next_action = coordinator.next_action_for(
            session=session_model,
            validation=validation,
            provisioning_manifest=[],
            pending_permissions=pending_permissions,
            attachment_results=[],
            dependencies=[],
            proposed_changes=proposed_changes,
        )
        review_state = coordinator.build_review_state(session_model, proposed_changes=proposed_changes)
        message = coordinator.assistant_summary(
            session=session_model,
            tool_calls=[],
            request_message=None,
            compiled_document=compiled_document,
            validation=validation,
            attachment_results=[],
            pending_permissions=pending_permissions,
            proposed_changes=proposed_changes,
        )
        return AtlasTurnResponse(
            session_id=session_model.session_id,
            message=message,
            next_action=next_action,
            dependencies=[],
            blockers=coordinator.build_blockers(validation),
            proposed_changes=proposed_changes,
            validation=validation,
            provisioning_manifest=[],
            api_discovery_results=[],
            attachment_ingestion_results=[],
            references=coordinator.build_references(session_model, compiled_document),
            review_state=review_state,
            pending_permission_requests=pending_permissions,
        )

    @router.post("/sessions", response_model=AtlasSessionResponse)
    def start_session(
        payload: AtlasSessionStartRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasSessionResponse:
        organization_id = required_author_organization_id(context)
        user_id = user_id_for_context(context)
        try:
            registration = agent_registry.get_agent_registration(
                payload.agent_id,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        policy = atlas_store.get_agent_policy(payload.agent_id, organization_id=organization_id)
        atlas_enabled = True if policy is None else policy.atlas_enabled
        if not atlas_enabled:
            raise HTTPException(status_code=409, detail="atlas is disabled for this agent")

        resolved_version_id = payload.agent_version_id
        if resolved_version_id is not None:
            snapshot = agent_registry.get_version_snapshot(
                resolved_version_id,
                organization_id=organization_id,
            )
            if snapshot.agent_id != payload.agent_id:
                raise HTTPException(status_code=409, detail="agent_version_id belongs to a different agent")
        else:
            try:
                resolved_version_id = agent_registry.resolve_version_id(
                    payload.agent_id,
                    target="draft",
                    organization_id=organization_id,
                )
            except KeyError:
                resolved_version_id = registration.current_published_version_id

        now = _utcnow()
        session_model = AtlasSession(
            session_id=new_atlas_session_id(),
            organization_id=organization_id,
            scope=payload.scope,
            status="active",
            agent_id=payload.agent_id,
            agent_version_id=resolved_version_id,
            title=f"Atlas session for {registration.name}",
            created_by=user_id,
            scenario_id=payload.scenario_id,
            step_id=payload.step_id,
            atlas_enabled_snapshot=atlas_enabled,
            created_at=now,
            updated_at=now,
        )
        session_model = atlas_store.create_session(session_model)
        if payload.initial_message:
            from .atlas_models import AtlasMessage
            from .atlas_store import new_atlas_message_id

            atlas_store.append_message(
                AtlasMessage(
                    message_id=new_atlas_message_id(),
                    session_id=session_model.session_id,
                    organization_id=organization_id,
                    sequence_number=0,
                    role="user",
                    content=payload.initial_message,
                    metadata={"source": "session_start"},
                    created_at=now,
                )
            )
        return _session_response(session_model)

    @router.get("/sessions/{session_id}", response_model=AtlasSessionResponse)
    def get_session(
        session_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasSessionResponse:
        organization_id = required_author_organization_id(context)
        session_model = _get_session_or_404(session_id, organization_id)
        return _session_response(session_model)

    @router.get("/sessions", response_model=AtlasSessionsPageResponse)
    def list_sessions(
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
        agent_id: str | None = Query(default=None),
        scope: str | None = Query(default=None),
        status: str | None = Query(default=None),
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> AtlasSessionsPageResponse:
        organization_id = required_author_organization_id(context)
        sessions, total_count, has_more = atlas_store.list_sessions(
            organization_id=organization_id,
            agent_id=agent_id,
            scope=scope,
            status=status,
            limit=limit,
            offset=offset,
        )
        return AtlasSessionsPageResponse(
            sessions=[
                _session_response(item)
                for item in sessions
            ],
            total_count=total_count,
            has_more=has_more,
        )

    @router.post("/sessions/{session_id}/archive", response_model=AtlasArchiveSessionResponse)
    def archive_session(
        session_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasArchiveSessionResponse:
        organization_id = required_author_organization_id(context)
        try:
            session_model = atlas_store.archive_session(session_id, organization_id=organization_id)
        except ValueError as exc:
            # AR-3.4: optimistic-lock conflict (a concurrent update/archive)
            # is a 409, not an unhandled 500.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if session_model is None:
            raise HTTPException(status_code=404, detail="unknown atlas session")
        assert session_model.archived_at is not None
        return AtlasArchiveSessionResponse(
            session_id=session_model.session_id,
            status=session_model.status,
            archived_at=session_model.archived_at,
        )

    @router.get("/sessions/{session_id}/messages", response_model=AtlasMessagesPageResponse)
    def list_messages(
        session_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
        before_sequence: int | None = Query(default=None, ge=1),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> AtlasMessagesPageResponse:
        organization_id = required_author_organization_id(context)
        _get_session_or_404(session_id, organization_id)
        messages, total_count, has_more = atlas_store.list_messages(
            session_id,
            organization_id=organization_id,
            before_sequence=before_sequence,
            limit=limit,
        )
        return AtlasMessagesPageResponse(
            session_id=session_id,
            messages=[
                AtlasMessageItem(
                    message_id=item.message_id,
                    role=item.role,
                    content=item.content,
                    sequence_number=item.sequence_number,
                    metadata=item.metadata,
                    created_at=item.created_at,
                )
                for item in messages
            ],
            has_more=has_more,
            total_count=total_count,
        )

    @router.get("/sessions/{session_id}/state", response_model=AtlasTurnResponse)
    def get_session_state(
        session_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasTurnResponse:
        organization_id = required_author_organization_id(context)
        session_model = _get_session_or_404(session_id, organization_id)
        return _session_state_response(session_model)

    @router.get("/sessions/{session_id}/events", response_model=AtlasEventsPageResponse)
    def list_events(
        session_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
        after_sequence: int | None = Query(default=None, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> AtlasEventsPageResponse:
        organization_id = required_author_organization_id(context)
        _get_session_or_404(session_id, organization_id)
        events, total_count, has_more = atlas_store.list_events(
            session_id,
            organization_id=organization_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return AtlasEventsPageResponse(
            session_id=session_id,
            events=[
                _event_envelope(item)
                for item in events
            ],
            has_more=has_more,
            total_count=total_count,
        )

    @router.get("/sessions/{session_id}/events/stream")
    async def stream_events(
        session_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
        after_sequence: int | None = Query(default=None, ge=0),
        poll_interval_seconds: float = Query(default=0.25, ge=0.05, le=5.0),
        idle_timeout_seconds: float = Query(default=15.0, ge=0.1, le=60.0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> StreamingResponse:
        organization_id = required_author_organization_id(context)
        _get_session_or_404(session_id, organization_id)

        async def _generate():
            cursor = after_sequence
            last_emit = asyncio.get_running_loop().time()
            while True:
                if await request.is_disconnected():
                    break
                # The store call is synchronous (SQLAlchemy); offload each
                # poll to a worker thread so the event loop never blocks.
                events, _, _ = await anyio.to_thread.run_sync(
                    functools.partial(
                        atlas_store.list_events,
                        session_id,
                        organization_id=organization_id,
                        after_sequence=cursor,
                        limit=limit,
                    )
                )
                if events:
                    for item in events:
                        cursor = item.sequence_number
                        envelope = AtlasEventEnvelope(
                            event_id=item.event_id,
                            session_id=item.session_id,
                            sequence_number=item.sequence_number,
                            type=item.type,
                            created_at=item.created_at,
                            payload=item.payload,
                        )
                        payload = envelope.model_dump(mode="json")
                        yield f"id: {item.sequence_number}\n".encode()
                        yield f"event: {item.type}\n".encode()
                        yield f"data: {json.dumps(payload)}\n\n".encode()
                    last_emit = asyncio.get_running_loop().time()
                    continue
                if asyncio.get_running_loop().time() - last_emit >= idle_timeout_seconds:
                    break
                yield b": keep-alive\n\n"
                await asyncio.sleep(poll_interval_seconds)

        return StreamingResponse(_generate(), media_type="text/event-stream")

    @router.get("/agents/{agent_id}/enabled", response_model=AtlasAgentEnabledResponse)
    def get_agent_enabled(
        agent_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasAgentEnabledResponse:
        organization_id = required_author_organization_id(context)
        try:
            agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        policy = atlas_store.get_agent_policy(agent_id, organization_id=organization_id)
        return AtlasAgentEnabledResponse(
            agent_id=agent_id,
            atlas_enabled=True if policy is None else policy.atlas_enabled,
        )

    @router.put("/agents/{agent_id}/enabled", response_model=AtlasAgentEnabledResponse)
    def set_agent_enabled(
        agent_id: str,
        payload: AtlasAgentEnabledToggleRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasAgentEnabledResponse:
        organization_id = required_author_organization_id(context)
        try:
            agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        updated = atlas_store.set_agent_policy(
            agent_id,
            organization_id=organization_id,
            atlas_enabled=payload.atlas_enabled,
            updated_by_user_id=user_id_for_context(context),
        )
        return AtlasAgentEnabledResponse(agent_id=agent_id, atlas_enabled=updated.atlas_enabled)

    @router.get("/admin/rollout-summary", response_model=AtlasRolloutSummaryResponse)
    def get_rollout_summary(
        request: Request,
        context: RequestAuthContext = Depends(_require_internal_superuser),
    ) -> AtlasRolloutSummaryResponse:
        # Process-wide, cross-tenant counters: superuser/staff only — same
        # gate as the /internal/* platform admin surface (always enforced,
        # even in bootstrap dev mode).
        return coordinator.rollout_summary()

    @router.post("/sessions/{session_id}/permission-decisions", response_model=AtlasPermissionDecisionResponse)
    def apply_permission_decisions(
        session_id: str,
        decisions: list[AtlasPermissionDecision],
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasPermissionDecisionResponse:
        organization_id = required_author_organization_id(context)
        user_id = user_id_for_context(context)
        session_model = _get_session_or_404(session_id, organization_id)
        _ensure_session_not_archived(session_model)
        # Note: the session creator MAY approve their own permission
        # requests. The design requirement is explicit user confirmation
        # ("never auto-apply"), not a second human — a four-eyes rule made
        # apply impossible for single-author organizations.
        try:
            updated = atlas_store.apply_permission_decisions(
                session_id,
                [item.model_dump(mode="json") for item in decisions],
                organization_id=organization_id,
                decided_by_user_id=user_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        pending = atlas_store.list_permission_requests(
            session_id,
            organization_id=organization_id,
            status="pending",
        )
        next_status = "blocked" if pending else "active"
        if session_model.status != next_status:
            atlas_store.update_session_status(
                session_model.session_id,
                next_status,
                organization_id=organization_id,
                updated_at=_utcnow(),
            )
        return AtlasPermissionDecisionResponse(
            session_id=session_id,
            updated_requests=[
                AtlasPermissionRequestModel(
                    request_id=item.request_id,
                    kind=item.kind,
                    status=item.status,
                    reason=item.reason,
                    risk_summary=item.risk_summary,
                    scope_ref=item.scope_ref,
                    delta_ids=item.delta_ids,
                    requested_actions=item.requested_actions,
                    created_at=item.created_at,
                    expires_at=item.expires_at,
                )
                for item in updated
            ],
        )

    @router.post("/sessions/{session_id}/apply", response_model=AtlasApplyResponse)
    def apply_changes(
        session_id: str,
        payload: AtlasApplyRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasApplyResponse:
        organization_id = required_author_organization_id(context)
        session_model = _get_session_or_404(session_id, organization_id)
        _ensure_session_not_archived(session_model)
        pending_permissions = atlas_store.list_permission_requests(
            session_id,
            organization_id=organization_id,
            status="pending",
        )
        approved_permission = atlas_store.find_approved_apply_permission(
            session_id,
            payload.delta_ids,
            organization_id=organization_id,
        )
        now = _utcnow()
        if pending_permissions or approved_permission is None:
            error = (
                "permission is still pending for this atlas session"
                if pending_permissions
                else "matching approved, unexpired apply permission is required for the requested deltas"
            )
            coordinator.observe_apply_outcome(
                session=_get_session_or_404(session_id, organization_id),
                delta_ids=payload.delta_ids,
                organization_id=organization_id,
                outcome="rejected",
            )
            apply_record = atlas_store.create_apply_request(
                AtlasApplyRequestRecordModel(
                    apply_request_id=new_atlas_apply_request_id(),
                    session_id=session_id,
                    organization_id=organization_id,
                    status="rejected",
                    delta_ids=payload.delta_ids,
                    apply_note=payload.apply_note,
                    confirmed_by_user_id=payload.confirmed_by or user_id_for_context(context),
                    error=error,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            try:
                coordinator.apply_requested_deltas(
                    session=_get_session_or_404(session_id, organization_id),
                    delta_ids=payload.delta_ids,
                    organization_id=organization_id,
                )
            except ValueError as exc:
                coordinator.observe_apply_outcome(
                    session=_get_session_or_404(session_id, organization_id),
                    delta_ids=payload.delta_ids,
                    organization_id=organization_id,
                    outcome="failed",
                )
                apply_record = atlas_store.create_apply_request(
                    AtlasApplyRequestRecordModel(
                        apply_request_id=new_atlas_apply_request_id(),
                        session_id=session_id,
                        organization_id=organization_id,
                        status="failed",
                        delta_ids=payload.delta_ids,
                        apply_note=payload.apply_note,
                        confirmed_by_user_id=payload.confirmed_by or user_id_for_context(context),
                        error=str(exc),
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                coordinator.observe_apply_outcome(
                    session=_get_session_or_404(session_id, organization_id),
                    delta_ids=payload.delta_ids,
                    organization_id=organization_id,
                    outcome="applied",
                )
                apply_record = atlas_store.create_apply_request(
                    AtlasApplyRequestRecordModel(
                        apply_request_id=new_atlas_apply_request_id(),
                        session_id=session_id,
                        organization_id=organization_id,
                        status="applied",
                        delta_ids=payload.delta_ids,
                        apply_note=payload.apply_note,
                        confirmed_by_user_id=payload.confirmed_by or user_id_for_context(context),
                        error=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
        return AtlasApplyResponse(
            apply_request_id=apply_record.apply_request_id,
            session_id=session_id,
            status=apply_record.status,
            error=apply_record.error,
        )

    @router.post("/turns", response_model=AtlasTurnResponse)
    def run_turn(
        payload: AtlasTurnRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasTurnResponse:
        organization_id = required_author_organization_id(context)
        user_id = user_id_for_context(context)
        session_model = _get_session_or_404(payload.session_id, organization_id)
        _ensure_session_not_archived(session_model)
        try:
            return coordinator.run_turn(
                session=session_model,
                payload=payload,
                organization_id=organization_id,
                user_id=user_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/readiness/runs", response_model=AtlasReadinessRunSummary)
    def create_readiness_run(
        payload: AtlasReadinessRunRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasReadinessRunSummary:
        if readiness_service is None:
            raise HTTPException(status_code=503, detail="atlas readiness store is not configured")
        organization_id = required_author_organization_id(context)
        user_id = user_id_for_context(context)
        try:
            return readiness_service.start_run(payload, organization_id=organization_id, user_id=user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/readiness/runs", response_model=AtlasReadinessRunsPage)
    def list_readiness_runs(
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
        agent_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> AtlasReadinessRunsPage:
        if readiness_store is None:
            raise HTTPException(status_code=503, detail="atlas readiness store is not configured")
        organization_id = required_author_organization_id(context)
        runs, total_count, has_more = readiness_store.list_runs(
            organization_id=organization_id,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )
        return AtlasReadinessRunsPage(runs=runs, total_count=total_count, has_more=has_more)

    @router.get("/readiness/provider-health", response_model=AtlasReadinessProviderHealth)
    def get_readiness_provider_health(
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
        provider_policy: AtlasReadinessProviderPolicy | None = Query(default=None),
    ) -> AtlasReadinessProviderHealth:
        if readiness_service is None:
            raise HTTPException(status_code=503, detail="atlas readiness store is not configured")
        required_author_organization_id(context)
        return readiness_service.provider_health(provider_policy=provider_policy)

    @router.get("/readiness/runs/{run_id}", response_model=AtlasReadinessRunSummary)
    def get_readiness_run(
        run_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasReadinessRunSummary:
        if readiness_service is None:
            raise HTTPException(status_code=503, detail="atlas readiness store is not configured")
        organization_id = required_author_organization_id(context)
        summary = readiness_service.get_run_summary(run_id, organization_id=organization_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="unknown atlas readiness run")
        return summary

    @router.get("/readiness/runs/{run_id}/events", response_model=AtlasReadinessEventsPage)
    def list_readiness_events(
        run_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
        after_sequence: int | None = Query(default=None, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> AtlasReadinessEventsPage:
        if readiness_store is None:
            raise HTTPException(status_code=503, detail="atlas readiness store is not configured")
        organization_id = required_author_organization_id(context)
        try:
            events, total_count, has_more = readiness_store.list_events(
                run_id,
                organization_id=organization_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return AtlasReadinessEventsPage(
            run_id=run_id,
            events=events,
            has_more=has_more,
            total_count=total_count,
        )

    @router.get("/readiness/runs/{run_id}/report", response_model=AtlasReadinessReport)
    def get_readiness_report(
        run_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasReadinessReport:
        if readiness_store is None:
            raise HTTPException(status_code=503, detail="atlas readiness store is not configured")
        organization_id = required_author_organization_id(context)
        report = readiness_store.get_report(run_id, organization_id=organization_id)
        if report is None:
            raise HTTPException(status_code=404, detail="unknown atlas readiness report")
        return report

    @router.post("/readiness/runs/{run_id}/propose-deltas", response_model=AtlasReadinessRunSummary)
    def propose_readiness_deltas(
        run_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasReadinessRunSummary:
        if readiness_service is None:
            raise HTTPException(status_code=503, detail="atlas readiness store is not configured")
        organization_id = required_author_organization_id(context)
        summary = readiness_service.get_run_summary(run_id, organization_id=organization_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="unknown atlas readiness run")
        fix_request = summary.run.request.model_copy(
            update={
                "scope": "fix",
                "reuse_case_set_id": summary.run.case_set_id,
            }
        )
        try:
            return readiness_service.start_run(
                fix_request,
                organization_id=organization_id,
                user_id=user_id_for_context(context),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/readiness/runs/{run_id}/rerun", response_model=AtlasReadinessRunSummary)
    def rerun_readiness_suite(
        run_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasReadinessRunSummary:
        if readiness_service is None:
            raise HTTPException(status_code=503, detail="atlas readiness store is not configured")
        organization_id = required_author_organization_id(context)
        user_id = user_id_for_context(context)
        try:
            return readiness_service.rerun(run_id, organization_id=organization_id, user_id=user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/readiness/runs/{run_id}/cancel", response_model=AtlasReadinessRunSummary)
    def cancel_readiness_run(
        run_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(require_author_context),
    ) -> AtlasReadinessRunSummary:
        if readiness_service is None:
            raise HTTPException(status_code=503, detail="atlas readiness store is not configured")
        organization_id = required_author_organization_id(context)
        try:
            return readiness_service.cancel_run(run_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
