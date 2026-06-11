"""Journey endpoints — extracted from api.py (RP-3.1 step 5).

Covers the 31 inline journey routes: /journey-definitions CRUD, versions,
review, publish, replay/rebuild, /journey-runtime status + jobs,
/journeys instance reads, annotations, evidence, replay, and the
/journey-analytics surface. Registration order inside this router preserves
the original inline order (hazard H2: /journey-definitions/export and
/journey-definitions/import register before
/journey-definitions/{definition_id}).

``journey_tracker_provider`` / ``journey_runtime_provider`` are zero-arg
callables supplied by create_app() (they read ``app.state``, which stays
composition-side, like ``_journey_review_agent_documents``). No ``tags=`` /
``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..api_auth import RequestAuthContext
from ..auth_deps import make_author_context_dep, make_reviewer_context_dep
from ..journeys import (
    JourneyAbandonmentSweepRequest,
    JourneyAbandonmentSweepResponse,
    JourneyAnalyticsRebuildRequest,
    JourneyAnalyticsRebuildResponse,
    JourneyAnnotationCreate,
    JourneyChannelMixAnalysis,
    JourneyDefinition,
    JourneyDefinitionBundle,
    JourneyDefinitionCreate,
    JourneyDefinitionImportRequest,
    JourneyDefinitionImportResponse,
    JourneyDefinitionListResponse,
    JourneyDefinitionPublishRequest,
    JourneyDefinitionRebuildRequest,
    JourneyDefinitionReplayResponse,
    JourneyDefinitionSummary,
    JourneyDefinitionUpdate,
    JourneyDefinitionVersion,
    JourneyDefinitionVersionCreate,
    JourneyDefinitionVersionListResponse,
    JourneyDefinitionVersionUpdate,
    JourneyDropOffAnalysis,
    JourneyEvent,
    JourneyEventListResponse,
    JourneyFunnelAnalysis,
    JourneyInstance,
    JourneyInstanceDetail,
    JourneyInstanceEvidenceResponse,
    JourneyInstanceListResponse,
    JourneyInstanceSummary,
    JourneyPathAnalysis,
    JourneyPublishReadinessResponse,
    JourneyReplayRequest,
    JourneyReplayResponse,
    JourneyRuntime,
    JourneyRuntimeJob,
    JourneyRuntimeStatus,
    JourneyService,
    JourneyServiceError,
    JourneyTracker,
    JourneyTouchpointListResponse,
    JourneyTrendAnalysis,
)
from ..realtime import RealtimeEvent
from ..schemas import Channel, ConversationState, TurnTrace
from ..services.org_scope import (
    make_journey_organization_id_for_request,
    make_organization_id_for_request,
    make_required_author_organization_id,
    user_id_for_context,
)
from ..tools.types import ToolInvocation

if TYPE_CHECKING:
    from ..kernel import ConversationKernel
    from ..realtime import RealtimeControlPlane


def _journey_definition_summary(definition: JourneyDefinition) -> JourneyDefinitionSummary:
    return JourneyDefinitionSummary(
        definition_id=definition.definition_id,
        organization_id=definition.organization_id,
        slug=definition.slug,
        name=definition.name,
        description=definition.description,
        status=definition.status,
        current_draft_version_id=definition.current_draft_version_id,
        current_published_version_id=definition.current_published_version_id,
        updated_at=definition.updated_at,
    )


def _raise_for_journey_error(exc: JourneyServiceError) -> None:
    if exc.code.endswith(".not_found"):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if exc.code.endswith(".unavailable"):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    detail: object = str(exc)
    if exc.details:
        detail = {
            "message": str(exc),
            **exc.details,
        }
    raise HTTPException(status_code=409, detail=detail) from exc


def build_journeys_router(
    *,
    journey_service: JourneyService | None,
    journey_instance_store,
    journey_tracker_provider: Callable[[], "JourneyTracker | None"],
    journey_runtime_provider: Callable[[], "JourneyRuntime | None"],
    kernel: "ConversationKernel",
    realtime_control_plane: "RealtimeControlPlane | None",
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> APIRouter:
    """Build the journeys router (definitions, runtime, instances, analytics)."""
    router = APIRouter()

    _require_runtime_author_context = make_author_context_dep(auth_enabled)
    _require_runtime_reviewer_context = make_reviewer_context_dep(auth_enabled)
    _organization_id_for_request = make_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _required_author_organization_id = make_required_author_organization_id(
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _journey_organization_id_for_request = make_journey_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _user_id_for_context = user_id_for_context

    def _journey_instance_summary(
        instance: JourneyInstance,
        *,
        organization_id: str,
    ) -> JourneyInstanceSummary:
        touchpoints = journey_instance_store.list_touchpoints(
            instance.journey_id,
            organization_id=organization_id,
        )
        channels = sorted({touchpoint.channel for touchpoint in touchpoints if touchpoint.channel})
        return JourneyInstanceSummary(
            journey_id=instance.journey_id,
            definition_id=instance.definition_id,
            definition_version_id=instance.definition_version_id,
            subject_key=instance.subject_key,
            status=instance.status,
            outcome=instance.outcome,
            current_milestone_id=instance.current_milestone_id,
            current_milestone_order=instance.current_milestone_order,
            channels=channels,
            latest_agent_id=instance.latest_agent_id,
            started_at=instance.started_at,
            last_activity_at=instance.last_activity_at,
            ended_at=instance.ended_at,
        )

    def _journey_service_or_500() -> JourneyService:
        if journey_service is None:
            raise HTTPException(status_code=500, detail="journey service unavailable")
        return journey_service

    def _journey_tracker_or_500() -> JourneyTracker:
        tracker = journey_tracker_provider()
        if tracker is None:
            raise HTTPException(status_code=500, detail="journey tracker unavailable")
        return tracker

    def _journey_runtime_or_500() -> JourneyRuntime:
        runtime = journey_runtime_provider()
        if runtime is None:
            raise HTTPException(status_code=500, detail="journey runtime unavailable")
        return runtime

    def _all_tool_invocations(
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[ToolInvocation]:
        if kernel.tool_runtime is None:
            return []
        return kernel.tool_runtime.list_conversation_invocations(
            conversation_id,
            organization_id=organization_id,
        )

    @router.post("/journey-definitions", response_model=JourneyDefinition)
    def create_journey_definition(
        payload: JourneyDefinitionCreate,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinition:
        organization_id = _required_author_organization_id(context)
        user_id = _user_id_for_context(context)
        service = _journey_service_or_500()
        try:
            return service.create_definition(
                payload,
                organization_id=organization_id,
                created_by_user_id=user_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-definitions", response_model=JourneyDefinitionListResponse)
    def list_journey_definitions(
        request: Request,
        status: str | None = Query(default=None),
    ) -> JourneyDefinitionListResponse:
        organization_id = _organization_id_for_request(request)
        service = _journey_service_or_500()
        definitions = service.list_definitions(organization_id=organization_id, status=status)
        return JourneyDefinitionListResponse(
            definitions=[_journey_definition_summary(item) for item in definitions]
        )

    @router.get("/journey-definitions/export", response_model=JourneyDefinitionBundle)
    def export_journey_definitions(
        request: Request,
        definition_id: list[str] = Query(default=[]),
    ) -> JourneyDefinitionBundle:
        organization_id = _organization_id_for_request(request)
        service = _journey_service_or_500()
        return service.export_definitions(
            organization_id=organization_id,
            definition_ids=definition_id,
        )

    @router.post("/journey-definitions/import", response_model=JourneyDefinitionImportResponse)
    def import_journey_definitions(
        payload: JourneyDefinitionImportRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinitionImportResponse:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        try:
            return service.import_definitions(
                payload,
                organization_id=organization_id,
                created_by_user_id=_user_id_for_context(context),
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-definitions/{definition_id}", response_model=JourneyDefinition)
    def get_journey_definition(definition_id: str, request: Request) -> JourneyDefinition:
        organization_id = _organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.get_definition(definition_id, organization_id=organization_id)
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.patch("/journey-definitions/{definition_id}", response_model=JourneyDefinition)
    def update_journey_definition(
        definition_id: str,
        payload: JourneyDefinitionUpdate,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinition:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        try:
            return service.update_definition(
                definition_id,
                payload,
                organization_id=organization_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.post("/journey-definitions/{definition_id}/duplicate", response_model=JourneyDefinition)
    def duplicate_journey_definition(
        definition_id: str,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinition:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        try:
            return service.duplicate_definition(
                definition_id,
                organization_id=organization_id,
                created_by_user_id=_user_id_for_context(context),
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.post("/journey-definitions/{definition_id}/archive", response_model=JourneyDefinition)
    def archive_journey_definition(
        definition_id: str,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinition:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        try:
            return service.archive_definition(
                definition_id,
                organization_id=organization_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.post("/journey-definitions/{definition_id}/versions", response_model=JourneyDefinitionVersion)
    def create_journey_definition_version(
        definition_id: str,
        payload: JourneyDefinitionVersionCreate,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinitionVersion:
        organization_id = _required_author_organization_id(context)
        user_id = _user_id_for_context(context)
        service = _journey_service_or_500()
        try:
            return service.create_version(
                definition_id,
                payload,
                organization_id=organization_id,
                created_by_user_id=user_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-definitions/{definition_id}/versions", response_model=JourneyDefinitionVersionListResponse)
    def list_journey_definition_versions(
        definition_id: str,
        request: Request,
    ) -> JourneyDefinitionVersionListResponse:
        organization_id = _organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            versions = service.list_versions(definition_id, organization_id=organization_id)
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)
        return JourneyDefinitionVersionListResponse(versions=versions)

    @router.get("/journey-definition-versions/{definition_version_id}", response_model=JourneyDefinitionVersion)
    def get_journey_definition_version(
        definition_version_id: str,
        request: Request,
    ) -> JourneyDefinitionVersion:
        organization_id = _organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.get_version(definition_version_id, organization_id=organization_id)
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.patch("/journey-definition-versions/{definition_version_id}", response_model=JourneyDefinitionVersion)
    def update_journey_definition_version(
        definition_version_id: str,
        payload: JourneyDefinitionVersionUpdate,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinitionVersion:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        try:
            return service.update_version(
                definition_version_id,
                payload,
                organization_id=organization_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-definitions/{definition_id}/review", response_model=JourneyPublishReadinessResponse)
    def get_journey_definition_review(
        definition_id: str,
        request: Request,
        definition_version_id: str | None = Query(default=None),
    ) -> JourneyPublishReadinessResponse:
        organization_id = _organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            definition = service.get_definition(definition_id, organization_id=organization_id)
            readiness = service.build_publish_readiness(
                definition_id,
                definition_version_id=definition_version_id,
                organization_id=organization_id,
            )
            draft_version = None
            if readiness.draft_version_id is not None:
                draft_version = service.get_version(readiness.draft_version_id, organization_id=organization_id)
            published_version = None
            if readiness.published_version_id is not None:
                published_version = service.get_version(readiness.published_version_id, organization_id=organization_id)
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)
        return JourneyPublishReadinessResponse(
            definition=definition,
            draft_version=draft_version,
            published_version=published_version,
            readiness=readiness,
        )

    @router.post("/journey-definitions/{definition_id}/publish", response_model=JourneyDefinitionVersion)
    def publish_journey_definition(
        definition_id: str,
        payload: JourneyDefinitionPublishRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinitionVersion:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        try:
            return service.publish_definition(
                definition_id,
                definition_version_id=payload.definition_version_id,
                organization_id=organization_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.post(
        "/journey-definitions/{definition_id}/replay",
        response_model=JourneyDefinitionReplayResponse | JourneyRuntimeJob,
    )
    def replay_journey_definition(
        definition_id: str,
        payload: JourneyReplayRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinitionReplayResponse | JourneyRuntimeJob:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        tracker = _journey_tracker_or_500()
        if payload.execution_mode == "async":
            runtime = _journey_runtime_or_500()
            return runtime.schedule_definition_replay(
                definition_id=definition_id,
                payload=payload,
                organization_id=organization_id,
            )
        try:
            return service.replay_definition(
                definition_id,
                organization_id=organization_id,
                tracker=tracker,
                preserve_manual_events=payload.preserve_manual_events,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.post(
        "/journey-definitions/{definition_id}/rebuild",
        response_model=JourneyDefinitionReplayResponse | JourneyRuntimeJob,
    )
    def rebuild_journey_definition(
        definition_id: str,
        payload: JourneyDefinitionRebuildRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyDefinitionReplayResponse | JourneyRuntimeJob:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        tracker = _journey_tracker_or_500()
        if payload.execution_mode == "async":
            runtime = _journey_runtime_or_500()
            return runtime.schedule_definition_rebuild(
                definition_id=definition_id,
                payload=payload,
                organization_id=organization_id,
            )
        try:
            return service.rebuild_definition(
                definition_id,
                payload,
                organization_id=organization_id,
                tracker=tracker,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-runtime/status", response_model=JourneyRuntimeStatus)
    def get_journey_runtime_status(
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyRuntimeStatus:
        organization_id = _required_author_organization_id(context)
        runtime = _journey_runtime_or_500()
        return runtime.status(organization_id=organization_id)

    @router.get("/journey-runtime/jobs/{job_id}", response_model=JourneyRuntimeJob)
    def get_journey_runtime_job(
        job_id: str,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyRuntimeJob:
        organization_id = _required_author_organization_id(context)
        runtime = _journey_runtime_or_500()
        job = runtime.get_job(job_id, organization_id=organization_id)
        if job is None or job.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="unknown journey runtime job")
        return job

    @router.get("/journeys", response_model=JourneyInstanceListResponse)
    def list_journeys(
        request: Request,
        definition_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        outcome: str | None = Query(default=None),
        subject_key: str | None = Query(default=None),
        started_after: datetime | None = Query(default=None),
        started_before: datetime | None = Query(default=None),
        channel: Channel | None = Query(default=None),
        agent_id: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> JourneyInstanceListResponse:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            journeys, total_count = service.list_instances(
                organization_id=organization_id,
                definition_id=definition_id,
                status=status,
                outcome=outcome,
                subject_key=subject_key,
                started_after=started_after,
                started_before=started_before,
                channel=channel,
                agent_id=agent_id,
                page=page,
                page_size=page_size,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)
        return JourneyInstanceListResponse(
            journeys=[_journey_instance_summary(item, organization_id=organization_id) for item in journeys],
            total_count=total_count,
            page=page,
            page_size=page_size,
        )

    @router.get("/journeys/{journey_id}", response_model=JourneyInstanceDetail)
    def get_journey_instance(journey_id: str, request: Request) -> JourneyInstanceDetail:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.get_instance_detail(journey_id, organization_id=organization_id)
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journeys/{journey_id}/touchpoints", response_model=JourneyTouchpointListResponse)
    def list_journey_touchpoints(journey_id: str, request: Request) -> JourneyTouchpointListResponse:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.list_touchpoints(journey_id, organization_id=organization_id)
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journeys/{journey_id}/events", response_model=JourneyEventListResponse)
    def list_journey_events(journey_id: str, request: Request) -> JourneyEventListResponse:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.list_events(journey_id, organization_id=organization_id)
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.post("/journeys/{journey_id}/annotations", response_model=JourneyEvent)
    def create_journey_annotation(
        journey_id: str,
        payload: JourneyAnnotationCreate,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> JourneyEvent:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.annotate_instance(
                journey_id,
                payload,
                organization_id=organization_id,
                actor_user_id=_user_id_for_context(context),
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journeys/{journey_id}/evidence", response_model=JourneyInstanceEvidenceResponse)
    def get_journey_evidence(journey_id: str, request: Request) -> JourneyInstanceEvidenceResponse:
        organization_id = _journey_organization_id_for_request(request)
        runtime_organization_id = organization_id
        service = _journey_service_or_500()
        try:
            detail = service.get_instance_detail(journey_id, organization_id=organization_id)
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)
        conversation_ids = list(dict.fromkeys(touchpoint.conversation_id for touchpoint in detail.touchpoints))
        conversations: list[ConversationState] = []
        traces_by_conversation: dict[str, list[TurnTrace]] = {}
        realtime_events_by_conversation: dict[str, list[RealtimeEvent]] = {}
        tool_invocations_by_conversation: dict[str, list[ToolInvocation]] = {}
        for conversation_id in conversation_ids:
            conversation = kernel.load_conversation(conversation_id)
            if conversation is not None and conversation.organization_id in {runtime_organization_id, organization_id, None}:
                conversations.append(conversation)
            traces_by_conversation[conversation_id] = kernel.trace_store.by_conversation(
                conversation_id,
                organization_id=runtime_organization_id,
            )
            realtime_events_by_conversation[conversation_id] = (
                []
                if realtime_control_plane is None
                else realtime_control_plane.events.replay(conversation_id=conversation_id)
            )
            tool_invocations_by_conversation[conversation_id] = _all_tool_invocations(
                conversation_id,
                organization_id=runtime_organization_id,
            )
        return JourneyInstanceEvidenceResponse(
            journey_id=journey_id,
            conversations=conversations,
            traces_by_conversation=traces_by_conversation,
            realtime_events_by_conversation=realtime_events_by_conversation,
            tool_invocations_by_conversation=tool_invocations_by_conversation,
        )

    @router.post("/journeys/{journey_id}/replay", response_model=JourneyReplayResponse | JourneyRuntimeJob)
    def replay_journey_instance(
        journey_id: str,
        payload: JourneyReplayRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyReplayResponse | JourneyRuntimeJob:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        tracker = _journey_tracker_or_500()
        if payload.execution_mode == "async":
            runtime = _journey_runtime_or_500()
            return runtime.schedule_journey_replay(
                journey_id=journey_id,
                payload=payload,
                organization_id=organization_id,
            )
        try:
            return service.replay_journey(
                journey_id,
                organization_id=organization_id,
                tracker=tracker,
                preserve_manual_events=payload.preserve_manual_events,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.post(
        "/journey-analytics/rebuild",
        response_model=JourneyAnalyticsRebuildResponse | JourneyRuntimeJob,
    )
    def rebuild_journey_analytics(
        payload: JourneyAnalyticsRebuildRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyAnalyticsRebuildResponse | JourneyRuntimeJob:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        if payload.execution_mode == "async":
            runtime = _journey_runtime_or_500()
            return runtime.schedule_analytics_rebuild(payload, organization_id=organization_id)
        try:
            return service.rebuild_analytics(
                payload,
                organization_id=organization_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.post(
        "/journey-runtime/abandonment-sweep",
        response_model=JourneyAbandonmentSweepResponse | JourneyRuntimeJob,
    )
    def sweep_journey_abandonment(
        payload: JourneyAbandonmentSweepRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> JourneyAbandonmentSweepResponse | JourneyRuntimeJob:
        organization_id = _required_author_organization_id(context)
        service = _journey_service_or_500()
        if payload.execution_mode == "async":
            runtime = _journey_runtime_or_500()
            return runtime.schedule_abandonment_sweep(payload, organization_id=organization_id)
        try:
            return service.sweep_abandonment(payload, organization_id=organization_id)
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-analytics/funnel", response_model=JourneyFunnelAnalysis)
    def get_journey_funnel(
        request: Request,
        definition_id: str = Query(...),
        definition_version_id: str | None = Query(default=None),
        period_start: datetime | None = Query(default=None),
        period_end: datetime | None = Query(default=None),
        channel: Channel | None = Query(default=None),
        agent_id: str | None = Query(default=None),
    ) -> JourneyFunnelAnalysis:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.analytics_funnel(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=definition_version_id,
                period_start=period_start,
                period_end=period_end,
                channel=channel,
                agent_id=agent_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-analytics/drop-off", response_model=JourneyDropOffAnalysis)
    def get_journey_drop_off(
        request: Request,
        definition_id: str = Query(...),
        definition_version_id: str | None = Query(default=None),
        period_start: datetime | None = Query(default=None),
        period_end: datetime | None = Query(default=None),
        channel: Channel | None = Query(default=None),
        agent_id: str | None = Query(default=None),
    ) -> JourneyDropOffAnalysis:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.analytics_drop_off(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=definition_version_id,
                period_start=period_start,
                period_end=period_end,
                channel=channel,
                agent_id=agent_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-analytics/paths", response_model=JourneyPathAnalysis)
    def get_journey_paths(
        request: Request,
        definition_id: str = Query(...),
        definition_version_id: str | None = Query(default=None),
        period_start: datetime | None = Query(default=None),
        period_end: datetime | None = Query(default=None),
        channel: Channel | None = Query(default=None),
        agent_id: str | None = Query(default=None),
    ) -> JourneyPathAnalysis:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.analytics_paths(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=definition_version_id,
                period_start=period_start,
                period_end=period_end,
                channel=channel,
                agent_id=agent_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-analytics/trends", response_model=JourneyTrendAnalysis)
    def get_journey_trends(
        request: Request,
        definition_id: str | None = Query(default=None),
        definition_version_id: str | None = Query(default=None),
        period_start: datetime | None = Query(default=None),
        period_end: datetime | None = Query(default=None),
        granularity: str = Query(default="day"),
        channel: Channel | None = Query(default=None),
        agent_id: str | None = Query(default=None),
    ) -> JourneyTrendAnalysis:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.analytics_trends(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=definition_version_id,
                period_start=period_start,
                period_end=period_end,
                granularity=granularity,
                channel=channel,
                agent_id=agent_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    @router.get("/journey-analytics/channel-mix", response_model=JourneyChannelMixAnalysis)
    def get_journey_channel_mix(
        request: Request,
        definition_id: str | None = Query(default=None),
        definition_version_id: str | None = Query(default=None),
        period_start: datetime | None = Query(default=None),
        period_end: datetime | None = Query(default=None),
        channel: Channel | None = Query(default=None),
        agent_id: str | None = Query(default=None),
    ) -> JourneyChannelMixAnalysis:
        organization_id = _journey_organization_id_for_request(request)
        service = _journey_service_or_500()
        try:
            return service.analytics_channel_mix(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=definition_version_id,
                period_start=period_start,
                period_end=period_end,
                channel=channel,
                agent_id=agent_id,
            )
        except JourneyServiceError as exc:
            _raise_for_journey_error(exc)

    return router
