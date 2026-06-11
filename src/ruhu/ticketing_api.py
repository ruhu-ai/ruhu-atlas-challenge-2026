from __future__ import annotations

from datetime import datetime
import json

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .api_auth import RequestAuthContext, get_request_auth_context
from .policy import require_organization_role
from .provider_integrations import provider_secret_is_valid
from .ticket_system import (
    ExternalCaseLink,
    ExternalTicketingProvider,
    SupportCase,
    SupportCaseEvent,
    SupportCaseNote,
    SupportCaseNoteVisibility,
    SupportCasePriority,
    SupportCaseSource,
    SupportCaseStatus,
    TicketConversationDetail,
    TicketDashboardResponse,
    TicketSystemService,
    TicketingActivity,
    TicketingConnection,
    TicketingProviderError,
)
from .ticketing_providers import ProviderConnectionConfig, verify_ticketing_webhook_signature
from .ticket_ui import tickets_page_html


class SupportCaseCreateRequest(BaseModel):
    title: str
    description: str
    priority: SupportCasePriority = "medium"
    category: str
    source: SupportCaseSource = "manual"
    primary_conversation_id: str | None = None
    related_conversation_ids: list[str] = Field(default_factory=list)
    assigned_to_user_id: str | None = None
    assigned_team: str | None = None
    owning_agent_id: str | None = None
    participant_ref: str | None = None
    participant_display: str | None = None
    participant_email: str | None = None
    participant_phone: str | None = None
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, object] = Field(default_factory=dict)
    case_metadata: dict[str, object] = Field(default_factory=dict)


class SupportCasePatchRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    status: SupportCaseStatus | None = None
    priority: SupportCasePriority | None = None
    category: str | None = None
    assigned_to_user_id: str | None = None
    assigned_team: str | None = None
    participant_ref: str | None = None
    participant_display: str | None = None
    participant_email: str | None = None
    participant_phone: str | None = None
    tags: list[str] | None = None
    custom_fields: dict[str, object] | None = None
    case_metadata: dict[str, object] | None = None
    related_conversation_ids: list[str] | None = None


class SupportCaseNoteCreateRequest(BaseModel):
    body: str
    visibility: SupportCaseNoteVisibility = "internal"


class SupportCaseResolveRequest(BaseModel):
    resolution_type: str
    summary: str
    details: str | None = None
    requires_follow_up: bool = False
    follow_up_at: datetime | None = None


class TicketingConnectionCreateRequest(BaseModel):
    provider: ExternalTicketingProvider
    display_name: str
    auth_type: str
    credentials_ref: str | None = None
    provider_config: dict[str, object] = Field(default_factory=dict)
    field_mappings: dict[str, object] = Field(default_factory=dict)
    status_mappings: dict[str, object] = Field(default_factory=dict)
    priority_mappings: dict[str, object] = Field(default_factory=dict)
    default_queue: str | None = None


class TicketingConnectionPatchRequest(BaseModel):
    display_name: str | None = None
    status: str | None = None
    auth_type: str | None = None
    credentials_ref: str | None = None
    provider_config: dict[str, object] | None = None
    field_mappings: dict[str, object] | None = None
    status_mappings: dict[str, object] | None = None
    priority_mappings: dict[str, object] | None = None
    default_queue: str | None = None


class ExternalCaseLinkCreateRequest(BaseModel):
    provider: ExternalTicketingProvider
    connection_id: str
    external_case_id: str | None = None
    external_case_key: str | None = None
    external_case_url: str | None = None
    external_case_status: str | None = None
    external_case_priority: str | None = None
    support_case_id: str | None = None
    conversation_id: str | None = None
    title: str | None = None
    description: str | None = None
    participant_email: str | None = None
    participant_display: str | None = None
    tags: list[str] = Field(default_factory=list)
    provider_payload_snapshot: dict[str, object] = Field(default_factory=dict)


class ExternalCaseCommentRequest(BaseModel):
    body: str
    visibility: str = "internal"


class ExternalCaseTransitionRequest(BaseModel):
    status: str


class TicketingRetryProcessRequest(BaseModel):
    connection_id: str | None = None
    limit: int = Field(default=25, ge=1, le=100)
    force: bool = False


def install_ticketing_router(
    app: FastAPI,
    *,
    ticket_system_service: TicketSystemService | None,
    auth_enabled: bool,
) -> None:
    def _require_provider_secret(provided_secret: str | None) -> None:
        runtime_settings = getattr(app.state, "runtime_settings", None)
        expected_secret = None if runtime_settings is None else getattr(runtime_settings, "provider_shared_secret", None)
        if expected_secret is None or not str(expected_secret).strip():
            raise HTTPException(status_code=503, detail="provider webhook bridge is not configured")
        if not provider_secret_is_valid(expected_secret, provided_secret):
            raise HTTPException(status_code=403, detail="invalid provider secret")

    def _build_connection_config(connection: TicketingConnection) -> ProviderConnectionConfig:
        return ProviderConnectionConfig(
            connection_id=connection.connection_id,
            provider=connection.provider,
            auth_type=connection.auth_type,
            credentials_ref=connection.credentials_ref,
            provider_config=dict(connection.provider_config),
            field_mappings=dict(connection.field_mappings),
            status_mappings=dict(connection.status_mappings),
            priority_mappings=dict(connection.priority_mappings),
            default_queue=connection.default_queue,
        )

    def _require_ticketing_webhook_auth(
        *,
        connection: TicketingConnection,
        headers: dict[str, str],
        body: bytes,
        provided_secret: str | None,
    ) -> None:
        try:
            verification_result = verify_ticketing_webhook_signature(
                _build_connection_config(connection),
                body=body,
                headers=headers,
            )
        except TicketingProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if verification_result is True:
            return
        if verification_result is False:
            raise HTTPException(status_code=403, detail="invalid ticketing webhook signature")
        _require_provider_secret(provided_secret)

    def _translate_provider_error(exc: TicketingProviderError) -> HTTPException:
        return HTTPException(
            status_code=502 if exc.status_code is None else min(max(exc.status_code, 400), 502),
            detail=str(exc),
        )

    if not auth_enabled:
        return

    @app.get("/tickets", response_class=HTMLResponse, response_model=None)
    def tickets_page(request: Request) -> Response:
        context = get_request_auth_context(request)
        if context.principal is None:
            return RedirectResponse(url="/login", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return HTMLResponse(tickets_page_html())

    if ticket_system_service is None:
        return

    router = APIRouter(tags=["ticket-system"])

    def _service() -> TicketSystemService:
        return ticket_system_service

    @router.get("/api/tickets/dashboard", response_model=TicketDashboardResponse)
    def get_tickets_dashboard(
        q: str | None = None,
        handler_id: str | None = None,
        channel: str | None = None,
        outcome: str | None = None,
        days: int | None = Query(default=7, ge=1, le=365),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        sort_by: str = Query(default="started_at", pattern="^(started_at|duration_seconds|sentiment_score|outcome|message_count)$"),
        sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> TicketDashboardResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().get_dashboard(
            organization_id=principal.organization.organization_id,
            q=q,
            handler_id=handler_id,
            channel=channel,
            outcome=outcome,
            days=days,
            limit=limit,
            offset=offset,
            sort_by=sort_by,  # type: ignore[arg-type]
            sort_dir=sort_dir,  # type: ignore[arg-type]
        )

    @router.get("/api/tickets/conversations/{conversation_id}", response_model=TicketConversationDetail)
    def get_ticket_conversation_detail(
        conversation_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> TicketConversationDetail:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        detail = _service().get_conversation_detail(
            organization_id=principal.organization.organization_id,
            conversation_id=conversation_id,
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="unknown conversation")
        return detail

    @router.post("/support-cases", response_model=SupportCase)
    def create_support_case(
        payload: SupportCaseCreateRequest,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> SupportCase:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().create_support_case(
            organization_id=principal.organization.organization_id,
            actor_user_id=principal.user.user_id,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            category=payload.category,
            source=payload.source,
            primary_conversation_id=payload.primary_conversation_id,
            related_conversation_ids=payload.related_conversation_ids,
            assigned_to_user_id=payload.assigned_to_user_id,
            assigned_team=payload.assigned_team,
            owning_agent_id=payload.owning_agent_id,
            participant_ref=payload.participant_ref,
            participant_display=payload.participant_display,
            participant_email=payload.participant_email,
            participant_phone=payload.participant_phone,
            tags=payload.tags,
            custom_fields=payload.custom_fields,
            case_metadata=payload.case_metadata,
        )

    @router.get("/support-cases", response_model=list[SupportCase])
    def list_support_cases(
        status: str | None = None,
        priority: str | None = None,
        category: str | None = None,
        assigned_to_user_id: str | None = None,
        assigned_team: str | None = None,
        source: str | None = None,
        conversation_id: str | None = None,
        q: str | None = None,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[SupportCase]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().list_support_cases(
            organization_id=principal.organization.organization_id,
            status=status,
            priority=priority,
            category=category,
            assigned_to_user_id=assigned_to_user_id,
            assigned_team=assigned_team,
            source=source,
            conversation_id=conversation_id,
            q=q,
        )

    @router.get("/support-cases/{case_id}", response_model=SupportCase)
    def get_support_case(
        case_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> SupportCase:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        case = _service().get_support_case(
            organization_id=principal.organization.organization_id,
            case_id=case_id,
        )
        if case is None:
            raise HTTPException(status_code=404, detail="unknown support case")
        return case

    @router.patch("/support-cases/{case_id}", response_model=SupportCase)
    def update_support_case(
        case_id: str,
        payload: SupportCasePatchRequest,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> SupportCase:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        case = _service().update_support_case(
            organization_id=principal.organization.organization_id,
            case_id=case_id,
            actor_user_id=principal.user.user_id,
            updates=payload.model_dump(exclude_none=True),
        )
        if case is None:
            raise HTTPException(status_code=404, detail="unknown support case")
        return case

    @router.get("/support-cases/{case_id}/notes", response_model=list[SupportCaseNote])
    def list_support_case_notes(
        case_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[SupportCaseNote]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().list_support_case_notes(
            organization_id=principal.organization.organization_id,
            case_id=case_id,
        )

    @router.get("/support-cases/{case_id}/events", response_model=list[SupportCaseEvent])
    def list_support_case_events(
        case_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[SupportCaseEvent]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().list_support_case_events(
            organization_id=principal.organization.organization_id,
            case_id=case_id,
        )

    @router.post("/support-cases/{case_id}/notes", response_model=SupportCaseNote)
    def add_support_case_note(
        case_id: str,
        payload: SupportCaseNoteCreateRequest,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> SupportCaseNote:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        note = _service().add_support_case_note(
            organization_id=principal.organization.organization_id,
            case_id=case_id,
            author_user_id=principal.user.user_id,
            body=payload.body,
            visibility=payload.visibility,
        )
        if note is None:
            raise HTTPException(status_code=404, detail="unknown support case")
        return note

    @router.post("/support-cases/{case_id}/resolve", response_model=SupportCase)
    def resolve_support_case(
        case_id: str,
        payload: SupportCaseResolveRequest,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> SupportCase:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        case = _service().resolve_support_case(
            organization_id=principal.organization.organization_id,
            case_id=case_id,
            actor_user_id=principal.user.user_id,
            resolution_type=payload.resolution_type,
            summary=payload.summary,
            details=payload.details,
            requires_follow_up=payload.requires_follow_up,
            follow_up_at=payload.follow_up_at,
        )
        if case is None:
            raise HTTPException(status_code=404, detail="unknown support case")
        return case

    @router.post("/support-cases/{case_id}/close", response_model=SupportCase)
    def close_support_case(
        case_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> SupportCase:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        case = _service().close_support_case(
            organization_id=principal.organization.organization_id,
            case_id=case_id,
            actor_user_id=principal.user.user_id,
        )
        if case is None:
            raise HTTPException(status_code=404, detail="unknown support case")
        return case

    @router.post("/ticketing/connections", response_model=TicketingConnection)
    def create_ticketing_connection(
        payload: TicketingConnectionCreateRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> TicketingConnection:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().create_connection(
            organization_id=principal.organization.organization_id,
            provider=payload.provider,
            display_name=payload.display_name,
            auth_type=payload.auth_type,
            credentials_ref=payload.credentials_ref,
            provider_config=payload.provider_config,
            field_mappings=payload.field_mappings,
            status_mappings=payload.status_mappings,
            priority_mappings=payload.priority_mappings,
            default_queue=payload.default_queue,
        )

    @router.get("/ticketing/connections", response_model=list[TicketingConnection])
    def list_ticketing_connections(
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> list[TicketingConnection]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().list_connections(organization_id=principal.organization.organization_id)

    @router.get("/ticketing/connections/{connection_id}", response_model=TicketingConnection)
    def get_ticketing_connection(
        connection_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> TicketingConnection:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        connection = _service().get_connection(
            organization_id=principal.organization.organization_id,
            connection_id=connection_id,
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="unknown ticketing connection")
        return connection

    @router.get("/ticketing/connections/{connection_id}/activity", response_model=list[TicketingActivity])
    def list_ticketing_connection_activity(
        connection_id: str,
        limit: int = Query(default=100, ge=1, le=250),
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> list[TicketingActivity]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().list_connection_activity(
            organization_id=principal.organization.organization_id,
            connection_id=connection_id,
            limit=limit,
        )

    @router.get("/ticketing/activities/retry-queue", response_model=list[TicketingActivity])
    def list_ticketing_retry_queue(
        connection_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=250),
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> list[TicketingActivity]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().list_retry_queue(
            organization_id=principal.organization.organization_id,
            connection_id=connection_id,
            limit=limit,
        )

    @router.post("/ticketing/activities/{activity_id}/retry", response_model=TicketingActivity)
    def retry_ticketing_activity(
        activity_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> TicketingActivity:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            activity = _service().retry_activity(
                organization_id=principal.organization.organization_id,
                activity_id=activity_id,
            )
        except TicketingProviderError as exc:
            raise _translate_provider_error(exc) from exc
        if activity is None:
            raise HTTPException(status_code=404, detail="unknown ticketing activity")
        return activity

    @router.post("/ticketing/activities/process-retries", response_model=list[TicketingActivity])
    def process_ticketing_retries(
        payload: TicketingRetryProcessRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> list[TicketingActivity]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            return _service().process_pending_retries(
                organization_id=principal.organization.organization_id,
                connection_id=payload.connection_id,
                limit=payload.limit,
                force=payload.force,
            )
        except TicketingProviderError as exc:
            raise _translate_provider_error(exc) from exc

    @router.patch("/ticketing/connections/{connection_id}", response_model=TicketingConnection)
    def update_ticketing_connection(
        connection_id: str,
        payload: TicketingConnectionPatchRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> TicketingConnection:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        connection = _service().update_connection(
            organization_id=principal.organization.organization_id,
            connection_id=connection_id,
            updates=payload.model_dump(exclude_none=True),
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="unknown ticketing connection")
        return connection

    @router.post("/ticketing/connections/{connection_id}/health-check", response_model=TicketingConnection)
    def health_check_ticketing_connection(
        connection_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> TicketingConnection:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            connection = _service().health_check_connection(
                organization_id=principal.organization.organization_id,
                connection_id=connection_id,
            )
        except TicketingProviderError as exc:
            raise _translate_provider_error(exc) from exc
        if connection is None:
            raise HTTPException(status_code=404, detail="unknown ticketing connection")
        return connection

    @router.get("/ticketing/connections/{connection_id}/remote-search", response_model=list[ExternalCaseLink])
    def search_remote_cases(
        connection_id: str,
        q: str = Query(min_length=1),
        limit: int = Query(default=20, ge=1, le=50),
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[ExternalCaseLink]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            return _service().search_remote_cases(
                organization_id=principal.organization.organization_id,
                connection_id=connection_id,
                query=q,
                limit=limit,
            )
        except TicketingProviderError as exc:
            raise _translate_provider_error(exc) from exc

    @router.post("/ticketing/external-cases", response_model=ExternalCaseLink)
    def create_external_case_link(
        payload: ExternalCaseLinkCreateRequest,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> ExternalCaseLink:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        connection = _service().get_connection(
            organization_id=principal.organization.organization_id,
            connection_id=payload.connection_id,
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="unknown ticketing connection")
        try:
            return _service().create_external_case_link(
                organization_id=principal.organization.organization_id,
                provider=payload.provider,
                connection_id=payload.connection_id,
                external_case_id=payload.external_case_id,
                external_case_key=payload.external_case_key,
                external_case_url=payload.external_case_url,
                external_case_status=payload.external_case_status,
                external_case_priority=payload.external_case_priority,
                support_case_id=payload.support_case_id,
                conversation_id=payload.conversation_id,
                provider_payload_snapshot=payload.provider_payload_snapshot,
                title=payload.title,
                description=payload.description,
                participant_email=payload.participant_email,
                participant_display=payload.participant_display,
                tags=payload.tags,
            )
        except TicketingProviderError as exc:
            raise _translate_provider_error(exc) from exc

    @router.get("/ticketing/external-cases/search", response_model=list[ExternalCaseLink])
    def search_external_case_links(
        provider: str | None = None,
        connection_id: str | None = None,
        conversation_id: str | None = None,
        support_case_id: str | None = None,
        q: str | None = None,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[ExternalCaseLink]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _service().search_external_case_links(
            organization_id=principal.organization.organization_id,
            provider=provider,
            connection_id=connection_id,
            conversation_id=conversation_id,
            support_case_id=support_case_id,
            q=q,
        )

    @router.post("/ticketing/external-cases/{link_id}/comment", response_model=ExternalCaseLink)
    def add_external_case_comment(
        link_id: str,
        payload: ExternalCaseCommentRequest,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> ExternalCaseLink:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            link = _service().add_external_case_comment(
                organization_id=principal.organization.organization_id,
                link_id=link_id,
                author_user_id=principal.user.user_id,
                body=payload.body,
                visibility=payload.visibility,
            )
        except TicketingProviderError as exc:
            raise _translate_provider_error(exc) from exc
        if link is None:
            raise HTTPException(status_code=404, detail="unknown external case link")
        return link

    @router.post("/ticketing/external-cases/{link_id}/transition", response_model=ExternalCaseLink)
    def transition_external_case(
        link_id: str,
        payload: ExternalCaseTransitionRequest,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> ExternalCaseLink:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            link = _service().transition_external_case(
                organization_id=principal.organization.organization_id,
                link_id=link_id,
                status_value=payload.status,
            )
        except TicketingProviderError as exc:
            raise _translate_provider_error(exc) from exc
        if link is None:
            raise HTTPException(status_code=404, detail="unknown external case link")
        return link

    @router.post("/ticketing/external-cases/{link_id}/sync", response_model=ExternalCaseLink)
    def sync_external_case(
        link_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> ExternalCaseLink:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            link = _service().sync_external_case(
                organization_id=principal.organization.organization_id,
                link_id=link_id,
            )
        except TicketingProviderError as exc:
            raise _translate_provider_error(exc) from exc
        if link is None:
            raise HTTPException(status_code=404, detail="unknown external case link")
        return link

    @router.post("/ticketing/webhooks/{provider}/{connection_id}", response_model=TicketingActivity)
    async def ticketing_webhook(
        request: Request,
        provider: str,
        connection_id: str,
        x_ruhu_provider_secret: str | None = Header(default=None),
    ) -> TicketingActivity:
        headers = {key.lower(): value for key, value in request.headers.items()}
        body = await request.body()
        connection = _service().get_connection_by_id(connection_id=connection_id)
        if connection is None or connection.provider != provider:
            raise HTTPException(status_code=404, detail="unknown ticketing connection")
        _require_ticketing_webhook_auth(
            connection=connection,
            headers=headers,
            body=body,
            provided_secret=x_ruhu_provider_secret,
        )
        try:
            payload = {} if not body else json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid webhook payload") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid webhook payload")
        activity = _service().process_connection_webhook(
            connection_id=connection_id,
            provider=provider,
            payload=payload,
            headers=headers,
        )
        if activity is None:
            raise HTTPException(status_code=404, detail="unknown ticketing connection")
        return activity

    app.include_router(router)
