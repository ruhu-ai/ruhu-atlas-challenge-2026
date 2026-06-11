"""HTTP router for tool connection and definition management.

Provides CRUD endpoints for API connections and tool definitions,
agent-scoped assignment endpoints, and OAuth 2.0 flow endpoints.

Install via ``install_tools_router(app, ...)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .api_auth import RequestAuthContext, require_authenticated_context
from .db_models import APIConnectionRecord
from .tools.ingestion import OpenAPIToolIngestionService
from .tools.management import (
    AgentToolBindingStore,
    APIConnectionStore,
    CredentialCipher,
    ToolAgentAssignmentStore,
    ToolDefinitionStore,
)
from .tools.oauth import OAuthFlowManager
from .tools.oauth_providers import get_client_credentials
from .tools.provider_templates import PROVIDER_TEMPLATES, list_templates, setup_provider
from .tools.runtime import ToolRuntime
from .tools.callable_aliases import callable_name_for_ref

if TYPE_CHECKING:
    from .runtime_config import RuntimeSettings


# ── Response models ───────────────────────────────────────────────────────────


class APIConnectionResponse(BaseModel):
    connection_id: str
    organization_id: str
    display_name: str
    provider: str
    auth_type: str
    base_url: str | None
    status: str
    error_message: str | None
    has_credentials: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class APIConnectionListResponse(BaseModel):
    items: list[APIConnectionResponse] = Field(default_factory=list)


class ToolDefinitionResponse(BaseModel):
    tool_definition_id: str
    organization_id: str
    connection_id: str | None
    kind: str
    tool_ref: str
    function_name: str | None
    display_name: str
    description: str
    endpoint_path: str | None
    http_method: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    timeout_ms: int
    read_only: bool
    enabled: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ToolDefinitionListResponse(BaseModel):
    items: list[ToolDefinitionResponse] = Field(default_factory=list)


class ToolAgentAssignmentResponse(BaseModel):
    assignment_id: str
    organization_id: str
    agent_id: str
    tool_definition_id: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AgentToolListResponse(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


# ── Request models ────────────────────────────────────────────────────────────


class APIConnectionCreateRequest(BaseModel):
    display_name: str
    provider: str
    auth_type: str
    base_url: str | None = None
    credentials: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class APIConnectionUpdateRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    credentials: dict[str, Any] | None = None
    status: str | None = None
    metadata: dict[str, Any] | None = None


class ToolDefinitionCreateRequest(BaseModel):
    connection_id: str | None = None
    kind: str = "code"
    tool_ref: str
    function_name: str | None = None
    display_name: str
    description: str
    endpoint_path: str | None = None
    http_method: str = "POST"
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    timeout_ms: int = 5000
    read_only: bool = False
    metadata: dict[str, Any] | None = None


class ToolDefinitionUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    function_name: str | None = None
    endpoint_path: str | None = None
    http_method: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    timeout_ms: int | None = None
    read_only: bool | None = None
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class OpenAPIToolIngestionRequest(BaseModel):
    openapi_spec: dict[str, Any]
    connection_id: str | None = None
    display_name: str | None = None
    provider: str = "openapi"
    auth_type: str = "none"
    base_url: str | None = None
    tool_ref_prefix: str | None = None
    agent_id: str | None = None


class DetectedAuthSchemeResponse(BaseModel):
    """Auth scheme parsed from the uploaded OpenAPI spec.

    Surfaced on ingestion responses so the UI can confirm to the user
    which scheme the platform picked (when ``auth_type="auto"``) or
    suggest re-authoring the connection (when the user chose a scheme
    that disagrees with the spec).
    """

    name: str
    auth_type: str
    description: str | None = None
    api_key_location: str | None = None
    api_key_name: str | None = None
    oauth_flow: str | None = None
    authorization_url: str | None = None
    token_url: str | None = None
    refresh_url: str | None = None
    scopes: list[str] = Field(default_factory=list)
    openid_connect_url: str | None = None


class OpenAPIToolIngestionResponse(BaseModel):
    connection_id: str
    created_tool_ids: list[str] = Field(default_factory=list)
    updated_tool_ids: list[str] = Field(default_factory=list)
    assigned_tool_ids: list[str] = Field(default_factory=list)
    detected_auth_schemes: list[DetectedAuthSchemeResponse] = Field(default_factory=list)


class ToolAssignRequest(BaseModel):
    tool_definition_id: str


class AgentToolBindingResponse(BaseModel):
    binding_id: str
    organization_id: str
    agent_id: str
    tool_definition_id: str
    connection_id: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AgentToolBindingListResponse(BaseModel):
    items: list[AgentToolBindingResponse] = Field(default_factory=list)


class AgentToolBindingCreateRequest(BaseModel):
    tool_definition_id: str
    connection_id: str
    enabled: bool = True


class OAuthExchangeRequest(BaseModel):
    code: str
    state: str


class CallableCatalogItem(BaseModel):
    """One callable operation visible to an agent for code authoring."""

    tool_definition_id: str
    kind: str
    ref: str
    function_name: str | None
    callable_name: str
    display_name: str
    description: str
    http_method: str | None = None
    endpoint_path: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = False
    provider_slug: str | None = None
    connection_status: str | None = None


class CallableCatalogResponse(BaseModel):
    apis: list[CallableCatalogItem] = Field(default_factory=list)
    integrations: list[CallableCatalogItem] = Field(default_factory=list)
    builtin: list[CallableCatalogItem] = Field(default_factory=list)


class ProviderTemplateStarterTool(BaseModel):
    ref: str
    function_name: str
    display_name: str
    description: str
    read_only: bool


class ProviderTemplateResponse(BaseModel):
    slug: str
    display_name: str
    category: str
    icon: str
    auth_type: str
    base_url: str
    capabilities: list[str]
    starter_tools: list[ProviderTemplateStarterTool]
    has_oauth: bool


class ProviderSetupRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    # Per-connection OAuth URL overrides. Used for:
    # - Per-tenant endpoints (Zendesk subdomains)
    # - Fully custom OAuth providers (self-hosted, custom IdP)
    auth_url_override: str | None = None
    token_url_override: str | None = None
    # Placeholder substitutions applied to the template's URLs. E.g.,
    # for Zendesk: {"subdomain": "acme"} -> "{subdomain}" replaced in URLs.
    template_config: dict[str, str] | None = None
    # Per-connection OAuth client credentials. Required for providers
    # where each customer must register their own OAuth app (Zendesk
    # per-subdomain, Custom OAuth). When null, falls back to the platform's
    # per-provider env credentials (HubSpot, Google, Microsoft, Salesforce).
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None


class ProviderSetupResponse(BaseModel):
    connection_id: str
    provider_slug: str
    status: str
    tools_created: int
    oauth_start_url: str | None = None


class OAuthStartResponse(BaseModel):
    """Returned by the OAuth start endpoint; the client navigates to this URL."""

    authorization_url: str


class ConnectionRevokeResponse(BaseModel):
    """Result of revoking an OAuth connection.

    ``provider_revoke_attempted`` is true when a configured RFC 7009
    revoke URL exists for the provider AND a token was present to send.
    ``provider_revoke_ok`` is true only when the provider returned 2xx;
    a false value here is informational, not an error — local cleanup
    always succeeds because we can't make provider failures block our
    own state hygiene.
    """

    connection_id: str
    status: str
    provider_revoke_attempted: bool
    provider_revoke_ok: bool


# ── Converter helpers ─────────────────────────────────────────────────────────


def _connection_to_response(record: Any) -> APIConnectionResponse:
    return APIConnectionResponse(
        connection_id=record.connection_id,
        organization_id=record.organization_id,
        display_name=record.display_name,
        provider=record.provider,
        auth_type=record.auth_type,
        base_url=record.base_url,
        status=record.status,
        error_message=record.error_message,
        has_credentials=bool(record.credentials_enc or record.credentials_ct),
        metadata=dict(record.metadata_json or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _definition_to_response(record: Any) -> ToolDefinitionResponse:
    return ToolDefinitionResponse(
        tool_definition_id=record.tool_definition_id,
        organization_id=record.organization_id,
        connection_id=record.connection_id,
        kind=getattr(record, "kind", "api"),
        tool_ref=record.tool_ref,
        function_name=getattr(record, "function_name", None),
        display_name=record.display_name,
        description=record.description,
        endpoint_path=record.endpoint_path,
        http_method=record.http_method,
        input_schema=dict(record.input_schema_json or {}),
        output_schema=dict(record.output_schema_json or {}),
        timeout_ms=record.timeout_ms,
        read_only=getattr(record, "read_only", False),
        enabled=record.enabled,
        metadata=dict(record.metadata_json or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _binding_to_response(record: Any) -> AgentToolBindingResponse:
    return AgentToolBindingResponse(
        binding_id=record.binding_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        tool_definition_id=record.tool_definition_id,
        connection_id=record.connection_id,
        enabled=record.enabled,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _assignment_to_response(record: Any) -> ToolAgentAssignmentResponse:
    return ToolAgentAssignmentResponse(
        assignment_id=record.assignment_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        tool_definition_id=record.tool_definition_id,
        enabled=record.enabled,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


# ── Router factory ────────────────────────────────────────────────────────────


def install_tools_router(
    app: FastAPI,
    *,
    connection_store: APIConnectionStore,
    definition_store: ToolDefinitionStore,
    assignment_store: ToolAgentAssignmentStore,
    binding_store: AgentToolBindingStore | None = None,
    tool_runtime: ToolRuntime | None = None,
    cipher: CredentialCipher | None = None,
    oauth_manager: OAuthFlowManager | None = None,
    settings: RuntimeSettings | None = None,
) -> None:
    router = APIRouter(tags=["tools"])
    ingestion_service = OpenAPIToolIngestionService(
        connection_store=connection_store,
        definition_store=definition_store,
        assignment_store=assignment_store,
    )

    def _context(request: Request) -> RequestAuthContext:
        return require_authenticated_context(request)

    def _organization_id(context: RequestAuthContext) -> str:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return principal.organization.organization_id

    def _resolve_oauth_client_credentials(
        record,  # APIConnectionRecord
    ) -> tuple[str, str] | None:
        """Return (client_id, client_secret) for an OAuth connection.

        Per-connection credentials take precedence over the platform-wide
        env-var defaults. This is required for providers where each
        customer must register their own OAuth client (e.g., Zendesk,
        Custom OAuth).

        Returns None when no credentials are available.
        """
        # Per-connection credentials (set at connection setup time)
        if record.oauth_client_id_override and record.oauth_client_secret_enc:
            if cipher is None:
                # Encrypted secret exists but we can't decrypt it — config error
                return None
            try:
                payload = cipher.decrypt(record.oauth_client_secret_enc)
                secret = payload.get("client_secret") if isinstance(payload, dict) else None
            except Exception:
                return None
            if not secret:
                return None
            return record.oauth_client_id_override, str(secret)

        # Fall back to platform-wide env credentials
        if settings is None:
            return None
        return get_client_credentials(record.provider, settings)

    def _handle_store_error(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail=str(exc))
        if isinstance(exc, IntegrityError):
            return HTTPException(status_code=409, detail="conflicting record")
        message = str(exc)
        if "already exists" in message or "not unique" in message:
            return HTTPException(status_code=409, detail=message)
        if "at least 20 characters" in message:
            return HTTPException(status_code=422, detail=message)
        return HTTPException(status_code=400, detail=message)

    # ── API Connections ───────────────────────────────────────────────────────

    @router.get("/api/tools/connections", response_model=APIConnectionListResponse)
    def list_connections(
        context: RequestAuthContext = Depends(_context),
    ) -> APIConnectionListResponse:
        org_id = _organization_id(context)
        records = connection_store.list_for_org(org_id)
        return APIConnectionListResponse(items=[_connection_to_response(r) for r in records])

    @router.post(
        "/api/tools/connections",
        response_model=APIConnectionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_connection(
        payload: APIConnectionCreateRequest,
        context: RequestAuthContext = Depends(_context),
    ) -> APIConnectionResponse:
        org_id = _organization_id(context)
        try:
            record = connection_store.create(
                organization_id=org_id,
                display_name=payload.display_name,
                provider=payload.provider,
                auth_type=payload.auth_type,
                base_url=payload.base_url,
                credentials_plain=payload.credentials,
                metadata=payload.metadata,
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc
        return _connection_to_response(record)

    @router.get(
        "/api/tools/connections/{connection_id}",
        response_model=APIConnectionResponse,
    )
    def get_connection(
        connection_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> APIConnectionResponse:
        org_id = _organization_id(context)
        record = connection_store.get(connection_id)
        if record is None or record.organization_id != org_id:
            raise HTTPException(status_code=404, detail="connection not found")
        return _connection_to_response(record)

    @router.put(
        "/api/tools/connections/{connection_id}",
        response_model=APIConnectionResponse,
    )
    def update_connection(
        connection_id: str,
        payload: APIConnectionUpdateRequest,
        context: RequestAuthContext = Depends(_context),
    ) -> APIConnectionResponse:
        org_id = _organization_id(context)
        existing = connection_store.get(connection_id)
        if existing is None or existing.organization_id != org_id:
            raise HTTPException(status_code=404, detail="connection not found")
        try:
            record = connection_store.update(
                connection_id,
                display_name=payload.display_name,
                base_url=payload.base_url,
                credentials_plain=payload.credentials,
                status=payload.status,
                metadata=payload.metadata,
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc
        return _connection_to_response(record)

    @router.delete(
        "/api/tools/connections/{connection_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
    )
    def delete_connection(
        connection_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> None:
        org_id = _organization_id(context)
        existing = connection_store.get(connection_id)
        if existing is None or existing.organization_id != org_id:
            raise HTTPException(status_code=404, detail="connection not found")
        try:
            connection_store.delete(connection_id)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="connection has tool definitions — delete them first",
            ) from exc

    @router.post(
        "/api/tools/connections/{connection_id}/revoke",
        response_model=ConnectionRevokeResponse,
    )
    async def revoke_connection(
        connection_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> ConnectionRevokeResponse:
        """Revoke OAuth tokens for *connection_id*.

        Best-effort POST to the provider's RFC 7009 revoke endpoint
        (when configured), then unconditional local cleanup: tokens are
        cleared and ``status`` is set to ``"revoked"``. The connection
        record, tool definitions, and agent bindings are preserved so
        the user can reconnect later without losing wiring.
        """
        org_id = _organization_id(context)
        existing = connection_store.get(connection_id)
        if existing is None or existing.organization_id != org_id:
            raise HTTPException(status_code=404, detail="connection not found")
        if existing.auth_type != "oauth2":
            raise HTTPException(
                status_code=400,
                detail="revoke is only supported for OAuth 2.0 connections",
            )
        if oauth_manager is None:
            raise HTTPException(
                status_code=503,
                detail="OAuth flow manager not configured",
            )
        try:
            result = await oauth_manager.revoke_connection(
                connection_id=connection_id,
                organization_id=org_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ConnectionRevokeResponse(
            connection_id=connection_id,
            status="revoked",
            provider_revoke_attempted=bool(result.get("provider_revoke_attempted")),
            provider_revoke_ok=bool(result.get("provider_revoke_ok")),
        )

    # ── Tool Definitions ──────────────────────────────────────────────────────

    @router.get("/api/tools/definitions", response_model=ToolDefinitionListResponse)
    def list_definitions(
        enabled_only: bool = True,
        kind: str | None = None,
        connection_id: str | None = None,
        context: RequestAuthContext = Depends(_context),
    ) -> ToolDefinitionListResponse:
        org_id = _organization_id(context)
        records = definition_store.list_for_org(
            org_id, enabled_only=enabled_only, kind=kind, connection_id=connection_id
        )
        return ToolDefinitionListResponse(items=[_definition_to_response(r) for r in records])

    @router.post(
        "/api/tools/definitions",
        response_model=ToolDefinitionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_definition(
        payload: ToolDefinitionCreateRequest,
        context: RequestAuthContext = Depends(_context),
    ) -> ToolDefinitionResponse:
        org_id = _organization_id(context)
        # Public creation allowlist (post-Library-redesign):
        #   - kind='code' is the primary authored callable; no connection.
        #   - kind='api' is created via this endpoint when an author wires a
        #     custom-host endpoint, and MUST carry connection_id.
        #   - kind='integration' / 'builtin' / 'mcp' are framework- or
        #     provider-templated; users cannot POST them directly.
        #   - kind='composite' is legacy-only; not creatable through the
        #     public surface (existing rows continue to execute).
        if payload.kind not in ("code", "api"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"kind={payload.kind!r} cannot be created through the public "
                    "endpoint; only 'code' (no connection) and 'api' (with connection_id) "
                    "are author-creatable"
                ),
            )
        if payload.kind == "api":
            if payload.connection_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="connection_id is required for kind='api'",
                )
            conn = connection_store.get(payload.connection_id)
            if conn is None or conn.organization_id != org_id:
                raise HTTPException(status_code=404, detail="connection not found")
        elif payload.connection_id is not None:
            # kind='code' must NOT carry a connection_id — it's a sandboxed
            # Python body, not an HTTP call. Reject early so authors don't
            # silently ship a misconfigured row.
            raise HTTPException(
                status_code=400,
                detail="connection_id must not be set for kind='code'",
            )
        try:
            record = definition_store.create(
                organization_id=org_id,
                connection_id=payload.connection_id,
                kind=payload.kind,
                tool_ref=payload.tool_ref,
                function_name=payload.function_name,
                display_name=payload.display_name,
                description=payload.description,
                endpoint_path=payload.endpoint_path,
                http_method=payload.http_method,
                input_schema=payload.input_schema,
                output_schema=payload.output_schema,
                timeout_ms=payload.timeout_ms,
                read_only=payload.read_only,
                metadata=payload.metadata,
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc
        return _definition_to_response(record)

    @router.get(
        "/api/tools/definitions/{definition_id}",
        response_model=ToolDefinitionResponse,
    )
    def get_definition(
        definition_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> ToolDefinitionResponse:
        org_id = _organization_id(context)
        record = definition_store.get(definition_id)
        if record is None or record.organization_id != org_id:
            raise HTTPException(status_code=404, detail="tool definition not found")
        return _definition_to_response(record)

    @router.put(
        "/api/tools/definitions/{definition_id}",
        response_model=ToolDefinitionResponse,
    )
    def update_definition(
        definition_id: str,
        payload: ToolDefinitionUpdateRequest,
        context: RequestAuthContext = Depends(_context),
    ) -> ToolDefinitionResponse:
        org_id = _organization_id(context)
        existing = definition_store.get(definition_id)
        if existing is None or existing.organization_id != org_id:
            raise HTTPException(status_code=404, detail="tool definition not found")
        try:
            record = definition_store.update(
                definition_id,
                display_name=payload.display_name,
                description=payload.description,
                function_name=payload.function_name,
                endpoint_path=payload.endpoint_path,
                http_method=payload.http_method,
                input_schema=payload.input_schema,
                output_schema=payload.output_schema,
                timeout_ms=payload.timeout_ms,
                read_only=payload.read_only,
                enabled=payload.enabled,
                metadata=payload.metadata,
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc
        return _definition_to_response(record)

    @router.delete(
        "/api/tools/definitions/{definition_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
    )
    def delete_definition(
        definition_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> None:
        org_id = _organization_id(context)
        existing = definition_store.get(definition_id)
        if existing is None or existing.organization_id != org_id:
            raise HTTPException(status_code=404, detail="tool definition not found")
        definition_store.delete(definition_id)

    @router.post(
        "/api/tools/import/openapi",
        response_model=OpenAPIToolIngestionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def import_openapi_tools(
        payload: OpenAPIToolIngestionRequest,
        context: RequestAuthContext = Depends(_context),
    ) -> OpenAPIToolIngestionResponse:
        org_id = _organization_id(context)
        if payload.connection_id is not None:
            existing = connection_store.get(payload.connection_id)
            if existing is None or existing.organization_id != org_id:
                raise HTTPException(status_code=404, detail="connection not found")
        try:
            result = ingestion_service.ingest(
                organization_id=org_id,
                spec=payload.openapi_spec,
                connection_id=payload.connection_id,
                display_name=payload.display_name,
                provider=payload.provider,
                auth_type=payload.auth_type,
                base_url=payload.base_url,
                tool_ref_prefix=payload.tool_ref_prefix,
                agent_id=payload.agent_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise _handle_store_error(exc) from exc
        return OpenAPIToolIngestionResponse(
            connection_id=result.connection_id,
            created_tool_ids=result.created_tool_ids,
            updated_tool_ids=result.updated_tool_ids,
            assigned_tool_ids=result.assigned_tool_ids,
            detected_auth_schemes=[
                DetectedAuthSchemeResponse(
                    name=s.name,
                    auth_type=s.auth_type,
                    description=s.description,
                    api_key_location=s.api_key_location,
                    api_key_name=s.api_key_name,
                    oauth_flow=s.oauth_flow,
                    authorization_url=s.authorization_url,
                    token_url=s.token_url,
                    refresh_url=s.refresh_url,
                    scopes=s.scopes,
                    openid_connect_url=s.openid_connect_url,
                )
                for s in result.detected_auth_schemes
            ],
        )

    # ── Agent tool assignments ─────────────────────────────────────────────────

    @router.get(
        "/api/agents/{agent_id}/tools",
        response_model=AgentToolListResponse,
    )
    def list_agent_tools(
        agent_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> AgentToolListResponse:
        """Return compiled ToolSpec objects visible to this agent.

        Merges builtins + custom catalog with builtin-precedence dedup.
        Falls back to assignment records if no ToolRuntime is available.
        """
        org_id = _organization_id(context)
        if tool_runtime is not None:
            specs = tool_runtime.list_for_agent(agent_id=agent_id, organization_id=org_id)
            items: list[dict[str, Any]] = [
                {
                    "ref": s.ref,
                    "kind": s.kind,
                    "display_name": s.display_name,
                    "description": s.description,
                }
                for s in specs
            ]
            return AgentToolListResponse(items=items)
        # Fallback: return assignment records
        assignments = assignment_store.list_for_agent(org_id, agent_id)
        items = [
            {
                "assignment_id": a.assignment_id,
                "tool_definition_id": a.tool_definition_id,
                "enabled": a.enabled,
            }
            for a in assignments
        ]
        return AgentToolListResponse(items=items)

    @router.get("/tools/agents/{agent_id}/tool-catalog")
    def get_agent_tool_catalog(
        agent_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> list[dict[str, Any]]:
        """Return the full tool catalog available to an agent.

        Used by the canvas state inspector for tool-ref selection.
        """
        org_id = _organization_id(context)
        if tool_runtime is not None:
            specs = tool_runtime.list_for_agent(agent_id=agent_id, organization_id=org_id)
            return [
                {
                    "ref": s.ref,
                    "provider": s.kind or "builtin",
                    "namespace": s.ref.rsplit(".", 1)[0] if "." in s.ref else "builtin",
                    "function_name": s.ref.rsplit(".", 1)[-1],
                    "display_name": s.display_name,
                    "description": s.description,
                    "input_schema": getattr(s, "input_schema", None) or {},
                    "output_schema": getattr(s, "output_schema", None),
                    "annotations": {},
                    "capability_group": None,
                    "tags": [],
                    "is_active": True,
                    "auth_status": "ready",
                }
                for s in specs
            ]
        return []

    @router.post(
        "/api/agents/{agent_id}/tools",
        response_model=ToolAgentAssignmentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def assign_tool_to_agent(
        agent_id: str,
        payload: ToolAssignRequest,
        context: RequestAuthContext = Depends(_context),
    ) -> ToolAgentAssignmentResponse:
        org_id = _organization_id(context)
        # Verify the definition belongs to this org
        defn = definition_store.get(payload.tool_definition_id)
        if defn is None or defn.organization_id != org_id:
            raise HTTPException(status_code=404, detail="tool definition not found")
        try:
            record = assignment_store.assign(
                organization_id=org_id,
                agent_id=agent_id,
                tool_definition_id=payload.tool_definition_id,
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc
        return _assignment_to_response(record)

    @router.delete(
        "/api/agents/{agent_id}/tools/{assignment_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
    )
    def unassign_tool_from_agent(
        agent_id: str,
        assignment_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> None:
        org_id = _organization_id(context)
        # Load all assignments for this agent to verify ownership
        assignments = assignment_store.list_for_agent(org_id, agent_id, enabled_only=False)
        match = next((a for a in assignments if a.assignment_id == assignment_id), None)
        if match is None:
            raise HTTPException(status_code=404, detail="assignment not found")
        assignment_store.unassign(assignment_id)

    # ── OAuth 2.0 flow ────────────────────────────────────────────────────────

    @router.post(
        "/api/tools/connections/{connection_id}/oauth/start",
        response_model=OAuthStartResponse,
    )
    def oauth_start(
        connection_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> OAuthStartResponse:
        """Build and return the provider authorization URL for an OAuth connection.

        The client should navigate the user to ``authorization_url`` (e.g. open
        it in a new tab).  After consent the provider will redirect back to
        ``GET /api/tools/oauth/callback``.
        """
        if oauth_manager is None or settings is None:
            raise HTTPException(status_code=503, detail="OAuth is not configured on this server")
        org_id = _organization_id(context)
        record = connection_store.get(connection_id)
        if record is None or record.organization_id != org_id:
            raise HTTPException(status_code=404, detail="connection not found")
        if record.auth_type != "oauth2":
            raise HTTPException(status_code=400, detail="connection is not an OAuth2 connection")
        creds = _resolve_oauth_client_credentials(record)
        if creds is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"OAuth credentials for provider '{record.provider}' are not configured. "
                    "Provide per-connection credentials at setup time, or configure platform-wide env vars."
                ),
            )
        client_id, _ = creds
        try:
            url = oauth_manager.build_authorization_url(
                connection_id=connection_id,
                organization_id=org_id,
                provider=record.provider,
                client_id=client_id,
            )
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported OAuth provider: {record.provider}",
            )
        return OAuthStartResponse(authorization_url=url)

    @router.get("/api/tools/oauth/callback")
    async def oauth_callback(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> Response:
        """Handle the OAuth provider redirect after user consent.

        On success: persists the access/refresh tokens and redirects to the
        frontend connections page.
        On failure: redirects to the frontend with an ``oauth_error`` query param
        (or returns a plain HTML error when no frontend URL is configured).
        """
        frontend_url = settings.frontend_url.rstrip("/") if (settings and settings.frontend_url) else None

        def _error_response(detail: str, http_status: int = 400) -> Response:
            if frontend_url:
                qs = urlencode({"oauth_error": detail[:300]})
                return RedirectResponse(f"{frontend_url}/tools?{qs}", status_code=302)
            return HTMLResponse(f"OAuth error: {detail}", status_code=http_status)

        if oauth_manager is None or settings is None:
            return HTMLResponse("OAuth is not configured on this server", status_code=503)

        if error:
            return _error_response(error_description or error)

        if not code or not state:
            return _error_response("missing code or state parameter")

        try:
            payload = oauth_manager.decode_state(state)
        except ValueError as exc:
            return _error_response(f"invalid state: {exc}", http_status=400)

        connection_id = payload.get("connection_id", "")
        organization_id = payload.get("organization_id", "")
        provider = payload.get("provider", "")
        code_verifier = payload.get("code_verifier")  # PKCE; None when provider doesn't use it
        requested_scopes_raw = payload.get("requested_scopes")
        requested_scopes = (
            [str(s) for s in requested_scopes_raw]
            if isinstance(requested_scopes_raw, list)
            else None
        )

        record = connection_store.get(connection_id)
        if record is None or record.organization_id != organization_id:
            return _error_response("connection not found", http_status=404)

        creds = _resolve_oauth_client_credentials(record)
        if creds is None:
            return _error_response(f"provider credentials for '{provider}' are not configured", http_status=503)

        client_id, client_secret = creds
        try:
            await oauth_manager.exchange_code(
                connection_id=connection_id,
                organization_id=organization_id,
                provider=provider,
                code=code,
                client_id=client_id,
                client_secret=client_secret,
                code_verifier=code_verifier if isinstance(code_verifier, str) else None,
                requested_scopes=requested_scopes,
            )
        except Exception as exc:
            return _error_response(f"token exchange failed: {exc}", http_status=502)

        if frontend_url:
            return RedirectResponse(
                f"{frontend_url}/tools/connections/{connection_id}?oauth=success",
                status_code=302,
            )
        return HTMLResponse(
            "OAuth flow complete. Tokens stored — you may close this window.",
            status_code=200,
        )

    @router.post("/api/tools/oauth/exchange")
    async def oauth_exchange(
        body: OAuthExchangeRequest,
        context: RequestAuthContext = Depends(_context),
    ) -> dict[str, str]:
        """Exchange an authorization code for tokens.

        Called by the frontend after the OAuth popup sends the code back via
        postMessage (popup+postMessage pattern, same as old Ruhu).  The ``state``
        parameter is Fernet-signed so it cannot be forged.
        """
        if oauth_manager is None:
            raise HTTPException(status_code=503, detail="OAuth is not configured on this server")

        try:
            payload = oauth_manager.decode_state(body.state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid state: {exc}") from exc

        connection_id = payload.get("connection_id", "")
        organization_id = payload.get("organization_id", "")
        provider = payload.get("provider", "")
        code_verifier = payload.get("code_verifier")  # PKCE; None when provider doesn't use it
        requested_scopes_raw = payload.get("requested_scopes")
        requested_scopes = (
            [str(s) for s in requested_scopes_raw]
            if isinstance(requested_scopes_raw, list)
            else None
        )

        # Verify the connection belongs to the caller's organisation.
        org_id = _organization_id(context)
        if organization_id != org_id:
            raise HTTPException(status_code=403, detail="organisation mismatch in OAuth state")

        record = connection_store.get(connection_id)
        if record is None or record.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="connection not found")

        creds = _resolve_oauth_client_credentials(record)
        if creds is None:
            raise HTTPException(
                status_code=503,
                detail=f"provider credentials for '{provider}' are not configured",
            )

        client_id, client_secret = creds
        try:
            await oauth_manager.exchange_code(
                connection_id=connection_id,
                organization_id=organization_id,
                provider=provider,
                code=body.code,
                client_id=client_id,
                client_secret=client_secret,
                code_verifier=code_verifier if isinstance(code_verifier, str) else None,
                requested_scopes=requested_scopes,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"token exchange failed: {exc}") from exc

        return {"status": "connected", "connection_id": connection_id}

    # ── Agent tool bindings (per-agent connection overrides) ────────────────

    if binding_store is not None:
        @router.get(
            "/api/agents/{agent_id}/tool-bindings",
            response_model=AgentToolBindingListResponse,
        )
        def list_agent_tool_bindings(
            agent_id: str,
            context: RequestAuthContext = Depends(_context),
        ) -> AgentToolBindingListResponse:
            org_id = _organization_id(context)
            records = binding_store.list_for_agent(org_id, agent_id)
            return AgentToolBindingListResponse(items=[_binding_to_response(r) for r in records])

        @router.post(
            "/api/agents/{agent_id}/tool-bindings",
            response_model=AgentToolBindingResponse,
            status_code=status.HTTP_201_CREATED,
        )
        def create_agent_tool_binding(
            agent_id: str,
            payload: AgentToolBindingCreateRequest,
            context: RequestAuthContext = Depends(_context),
        ) -> AgentToolBindingResponse:
            org_id = _organization_id(context)
            # Verify tool and connection belong to this org
            defn = definition_store.get(payload.tool_definition_id)
            if defn is None or defn.organization_id != org_id:
                raise HTTPException(status_code=404, detail="tool definition not found")
            conn = connection_store.get(payload.connection_id)
            if conn is None or conn.organization_id != org_id:
                raise HTTPException(status_code=404, detail="connection not found")
            try:
                record = binding_store.create_or_update(
                    organization_id=org_id,
                    agent_id=agent_id,
                    tool_definition_id=payload.tool_definition_id,
                    connection_id=payload.connection_id,
                    enabled=payload.enabled,
                )
            except Exception as exc:
                raise _handle_store_error(exc) from exc
            return _binding_to_response(record)

        @router.delete(
            "/api/agents/{agent_id}/tool-bindings/{binding_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            response_model=None,
        )
        def delete_agent_tool_binding(
            agent_id: str,
            binding_id: str,
            context: RequestAuthContext = Depends(_context),
        ) -> None:
            org_id = _organization_id(context)
            bindings = binding_store.list_for_agent(org_id, agent_id)
            match = next((b for b in bindings if b.binding_id == binding_id), None)
            if match is None:
                raise HTTPException(status_code=404, detail="binding not found")
            binding_store.delete(binding_id)

    # ── Agent callable catalog (for code editor APIs + Tools tabs) ─────────

    @router.get(
        "/api/agents/{agent_id}/callable-catalog",
        response_model=CallableCatalogResponse,
    )
    def get_agent_callable_catalog(
        agent_id: str,
        context: RequestAuthContext = Depends(_context),
    ) -> CallableCatalogResponse:
        """Return all callable operations for an agent, grouped by kind.

        Used by the step code editor's APIs and Tools tabs.
        """
        org_id = _organization_id(context)
        all_tools = definition_store.list_for_org(org_id, enabled_only=True)

        apis: list[CallableCatalogItem] = []
        integrations: list[CallableCatalogItem] = []
        builtin: list[CallableCatalogItem] = []
        seen_refs: set[str] = set()

        for tool in all_tools:
            kind = getattr(tool, "kind", "api")
            seen_refs.add(str(tool.tool_ref))
            # Look up connection status if connection exists
            conn_status = None
            provider_slug = None
            if tool.connection_id:
                conn = connection_store.get(tool.connection_id)
                if conn:
                    conn_status = conn.status
                    provider_slug = conn.provider

            item = CallableCatalogItem(
                tool_definition_id=tool.tool_definition_id,
                kind=kind,
                ref=tool.tool_ref,
                function_name=getattr(tool, "function_name", None),
                callable_name=callable_name_for_ref(tool.tool_ref),
                display_name=tool.display_name,
                description=tool.description,
                http_method=tool.http_method if kind == "api" else None,
                endpoint_path=tool.endpoint_path if kind == "api" else None,
                input_schema=dict(tool.input_schema_json or {}),
                read_only=getattr(tool, "read_only", False),
                provider_slug=provider_slug,
                connection_status=conn_status,
            )

            if kind == "api":
                apis.append(item)
            elif kind == "integration":
                integrations.append(item)
            elif kind == "builtin":
                builtin.append(item)
            else:
                apis.append(item)

        if tool_runtime is not None:
            for spec in tool_runtime.list_specs():
                if spec.ref in seen_refs:
                    continue
                builtin.append(
                    CallableCatalogItem(
                        tool_definition_id=f"builtin:{spec.ref}",
                        kind="builtin",
                        ref=spec.ref,
                        function_name=spec.ref.split(".")[-1] or spec.ref,
                        callable_name=callable_name_for_ref(spec.ref),
                        display_name=spec.display_name,
                        description=spec.description,
                        input_schema=dict(spec.input_schema or {}),
                        read_only=bool(spec.annotations.read_only),
                        provider_slug="builtin",
                        connection_status="ready",
                    )
                )

        return CallableCatalogResponse(
            apis=apis,
            integrations=integrations,
            builtin=builtin,
        )

    # ── Provider templates (one-click integration setup) ─────────────────

    @router.get(
        "/api/tools/provider-templates",
        response_model=list[ProviderTemplateResponse],
    )
    def list_provider_templates() -> list[ProviderTemplateResponse]:
        """List all known provider templates with their starter tools."""
        return [ProviderTemplateResponse(**t) for t in list_templates()]

    @router.post(
        "/api/tools/provider-templates/{slug}/setup",
        response_model=ProviderSetupResponse,
    )
    def setup_provider_from_template(
        slug: str,
        payload: ProviderSetupRequest | None = None,
        context: RequestAuthContext = Depends(_context),
    ) -> ProviderSetupResponse:
        """Create a connection and starter tools from a provider template.

        For OAuth providers, returns ``oauth_start_url`` so the client can
        redirect the user to the provider's consent screen.
        """
        org_id = _organization_id(context)
        template = PROVIDER_TEMPLATES.get(slug)
        if template is None:
            raise HTTPException(status_code=404, detail=f"unknown provider template: {slug}")

        # Use the runtime session factory from the definition store
        sf = definition_store._sf

        # Encrypt per-connection OAuth client secret if supplied. Required
        # for Zendesk (per-subdomain OAuth app) and Custom OAuth.
        oauth_client_id_override: str | None = None
        oauth_client_secret_enc: str | None = None
        if payload and payload.oauth_client_id and payload.oauth_client_secret:
            if cipher is None:
                raise HTTPException(
                    status_code=503,
                    detail="credential encryption is not configured on this server",
                )
            oauth_client_id_override = payload.oauth_client_id
            oauth_client_secret_enc = cipher.encrypt(
                {"client_secret": payload.oauth_client_secret}
            )

        try:
            connection, tools = setup_provider(
                template,
                session_factory=sf,
                organization_id=org_id,
                display_name=payload.display_name if payload else None,
                base_url=payload.base_url if payload else None,
                auth_url_override=payload.auth_url_override if payload else None,
                token_url_override=payload.token_url_override if payload else None,
                template_config=payload.template_config if payload else None,
                oauth_client_id_override=oauth_client_id_override,
                oauth_client_secret_enc=oauth_client_secret_enc,
            )
        except IntegrityError:
            # A connection for this provider already exists (e.g., a previous
            # setup attempt that didn't complete OAuth). Look it up and return
            # its OAuth URL so the user can resume — idempotent behaviour.
            with sf() as session:
                existing = session.scalar(
                    select(APIConnectionRecord).where(
                        APIConnectionRecord.organization_id == org_id,
                        APIConnectionRecord.provider == slug,
                    )
                )
            if existing is None:
                raise HTTPException(
                    status_code=409,
                    detail="provider connection already exists for this organization",
                )
            connection = existing
            tools = []
        except ValueError as exc:
            # Unresolved placeholder in URLs, etc.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # If OAuth provider, build the authorization URL.
        # Credentials resolved per-connection first (e.g., Zendesk/custom
        # OAuth provide their own client_id + secret at setup time), falling
        # back to platform env vars for providers like HubSpot/Google where
        # Ruhu registers a single OAuth app for all customers.
        oauth_url = None
        if template.auth_type == "oauth2" and oauth_manager is not None:
            creds = _resolve_oauth_client_credentials(connection)
            if creds is not None:
                client_id, _ = creds
                try:
                    oauth_url = oauth_manager.build_authorization_url(
                        connection_id=connection.connection_id,
                        organization_id=org_id,
                        provider=slug,
                        client_id=client_id,
                    )
                except KeyError:
                    pass

        return ProviderSetupResponse(
            connection_id=connection.connection_id,
            provider_slug=slug,
            status=connection.status,
            tools_created=len(tools),
            oauth_start_url=oauth_url,
        )

    app.include_router(router)
