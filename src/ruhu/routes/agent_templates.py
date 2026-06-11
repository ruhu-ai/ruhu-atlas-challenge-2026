"""Agent template endpoints — extracted from api.py (RP-3.1 step 3).

Covers the /agent-templates gallery CRUD, clone, required-tools state and
/agents/{agent_id}/save-as-template. The inline block was registered only
when ``template_store is not None`` — create_app() preserves that
conditional around the mount (hazard H1: these routes are invisible to the
schema export unless a template store is configured).

The template DTOs and the required-tools validation helpers still live in
``ruhu.api`` (tests import them from there; they migrate with the
presentation layer at blueprint step 10), so this module is imported by
``create_app()`` AT THE MOUNT SITE rather than at api.py's module top —
a top-level import would be circular while api.py is still mid-import.
No ``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request

# DTOs + shared template helpers at module top (hazard H7: PEP 563 return
# annotations resolve against this module's globals).
from ..api import (
    AgentTemplateCreateRequest,
    AgentTemplateDefaultSettings,
    AgentTemplateDetailResponse,
    AgentTemplateListResponse,
    AgentTemplatePatchRequest,
    AgentTemplateRequiredToolsResponse,
    CloneAgentTemplateRequest,
    CloneAgentTemplateResponse,
    SaveAgentAsTemplateRequest,
    TemplateRequiredToolWithSatisfaction,
    TemplateRequiredToolsValidationError,
    _agent_id_from_name,
    _auto_derive_required_tools,
    _current_builtin_tool_refs,
    validate_template_required_tools,
)
from ..api_auth import RequestAuthContext
from ..auth_deps import make_author_context_dep, make_reviewer_context_dep
from ..agent_document import AgentDocument
from ..services.org_scope import (
    make_organization_id_for_request,
    make_required_author_organization_id,
    user_id_for_context,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

    from ..kernel import ConversationKernel
    from ..registry import SQLAlchemyAgentRegistry


def build_agent_templates_router(
    *,
    template_store,
    agent_registry: "SQLAlchemyAgentRegistry",
    kernel: "ConversationKernel",
    runtime_session_factory: "sessionmaker",
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> APIRouter:
    """Build the agent-templates router.

    Auth dependencies and org-scope resolvers are built inside the builder
    from ``auth_enabled`` / ``bootstrap_organization_id`` (blueprint DI
    verdict) rather than passed as closures.
    """
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
    _user_id_for_context = user_id_for_context

    @router.get("/agent-templates", response_model=AgentTemplateListResponse)
    def list_agent_templates(
        request: Request,
        category: str | None = Query(default=None),
        agent_type: str | None = Query(default=None),
        is_featured: bool | None = Query(default=None),
        search: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> AgentTemplateListResponse:
        organization_id = _organization_id_for_request(request)
        return template_store.list_templates(
            organization_id=organization_id,
            category=category,
            agent_type=agent_type,
            is_featured=is_featured,
            search=search,
            page=page,
            page_size=page_size,
        )

    @router.get("/agent-templates/{template_id}", response_model=AgentTemplateDetailResponse)
    def get_agent_template(template_id: str, request: Request) -> AgentTemplateDetailResponse:
        organization_id = _organization_id_for_request(request)
        detail = template_store.get_template_detail(template_id, organization_id=organization_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"template not found: {template_id}")
        return detail

    @router.get(
        "/agent-templates/{template_id}/required-tools",
        response_model=AgentTemplateRequiredToolsResponse,
    )
    def get_template_required_tools(
        template_id: str,
        request: Request,
    ) -> AgentTemplateRequiredToolsResponse:
        """Return the template's required-tools metadata enriched with
        per-org satisfaction state.

        Auth'd callers get ``satisfied`` flags computed against the
        org-scoped ``ToolDefinitionStore`` (configuration layer, not
        execution).  Unauth'd callers get the static metadata only.
        See Template-Required-Tools-Onboarding-Spec §5.4.
        """
        organization_id = _organization_id_for_request(request)
        detail = template_store.get_template_detail(template_id, organization_id=organization_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"template not found: {template_id}")
        metadata_entries = list(detail.required_tools)
        if organization_id is None:
            tools = [
                TemplateRequiredToolWithSatisfaction(
                    **entry.model_dump(),
                    satisfied=None,
                )
                for entry in metadata_entries
            ]
            return AgentTemplateRequiredToolsResponse(
                template_id=template_id,
                tools=tools,
                all_required_satisfied=None,
            )
        from ..tools.management import ToolDefinitionStore as _TDS
        tool_definition_store = _TDS(runtime_session_factory)
        tools = []
        all_satisfied = True
        for entry in metadata_entries:
            record = tool_definition_store.get_by_ref(organization_id, entry.tool_ref)
            satisfied = record is not None
            if not satisfied:
                all_satisfied = False
            tools.append(
                TemplateRequiredToolWithSatisfaction(
                    **entry.model_dump(),
                    satisfied=satisfied,
                )
            )
        return AgentTemplateRequiredToolsResponse(
            template_id=template_id,
            tools=tools,
            all_required_satisfied=all_satisfied if metadata_entries else True,
        )

    @router.post("/agent-templates/{template_id}/clone", response_model=CloneAgentTemplateResponse)
    def clone_agent_template(
        template_id: str,
        payload: CloneAgentTemplateRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> CloneAgentTemplateResponse:
        organization_id = _required_author_organization_id(context)
        snapshot = template_store.get_template_snapshot(template_id, organization_id=organization_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"template not found: {template_id}")
        agent_document_json, default_settings, template_name = snapshot
        new_agent_id = _agent_id_from_name(payload.agent_name)
        try:
            agent_document = AgentDocument.model_validate(agent_document_json)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"template agent document is invalid: {exc}") from exc
        try:
            agent_registry.create_agent_document(
                agent_id=new_agent_id,
                agent_name=payload.agent_name,
                document=agent_document,
                organization_id=organization_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        agent_settings: dict = dict(default_settings)
        if payload.system_prompt is not None:
            agent_settings["system_prompt"] = payload.system_prompt
        if payload.agent_type is not None:
            agent_settings["agent_type"] = payload.agent_type
        agent_settings["source_template_id"] = template_id
        agent_registry.update_agent_settings(
            new_agent_id,
            {"agent_settings": agent_settings},
            organization_id=organization_id,
        )
        template_store.increment_usage_count(template_id)
        now = datetime.now(timezone.utc)
        return CloneAgentTemplateResponse(
            agent_id=new_agent_id,
            agent_name=payload.agent_name,
            template_id=template_id,
            template_name=template_name,
            created_at=now,
            message=f"Agent '{payload.agent_name}' created from template '{template_name}'.",
        )

    @router.post("/agent-templates", response_model=AgentTemplateDetailResponse, status_code=201)
    def create_agent_template(
        payload: AgentTemplateCreateRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentTemplateDetailResponse:
        organization_id = _required_author_organization_id(context)
        user_id = _user_id_for_context(context)
        try:
            validate_template_required_tools(
                agent_document_json=dict(payload.agent_document_json),
                required_tools=[entry.model_dump() for entry in payload.required_tools],
                builtin_refs=_current_builtin_tool_refs(kernel),
            )
        except TemplateRequiredToolsValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "required_tools metadata is inconsistent with the template's agent document",
                    "codes": exc.codes,
                },
            ) from exc
        try:
            return template_store.create_template(
                data=payload,
                organization_id=organization_id,
                created_by=user_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.patch("/agent-templates/{template_id}", response_model=AgentTemplateDetailResponse)
    def update_agent_template(
        template_id: str,
        patch: AgentTemplatePatchRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentTemplateDetailResponse:
        organization_id = _required_author_organization_id(context)
        detail = template_store.patch_template(template_id, patch, organization_id=organization_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"template not found: {template_id}")
        return detail

    @router.delete("/agent-templates/{template_id}", status_code=204, response_model=None)
    def delete_agent_template(
        template_id: str,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> None:
        organization_id = _required_author_organization_id(context)
        if not template_store.delete_template(template_id, organization_id=organization_id):
            raise HTTPException(status_code=404, detail=f"template not found: {template_id}")

    @router.post("/agents/{agent_id}/save-as-template", response_model=AgentTemplateDetailResponse, status_code=201)
    def save_agent_as_template(
        agent_id: str,
        payload: SaveAgentAsTemplateRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentTemplateDetailResponse:
        organization_id = _required_author_organization_id(context)
        user_id = _user_id_for_context(context)
        try:
            version_id = agent_registry.resolve_version_id(
                agent_id, target="draft", organization_id=organization_id
            )
            snapshot = agent_registry.get_version_snapshot(version_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        registration = agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
        raw_settings = dict((registration.settings or {}).get("agent_settings") or {})
        default_settings = AgentTemplateDefaultSettings(
            system_prompt=raw_settings.get("system_prompt", "You are a helpful AI voice assistant."),
            agent_type=raw_settings.get("agent_type", "voice"),
        )
        if snapshot.agent_document is None:
            raise HTTPException(
                status_code=409,
                detail="agent does not have an agent document to save as a template",
            )
        saved_agent_document = snapshot.agent_document.model_dump(mode="json")
        # Spec §5.9: auto-derive required_tools from the agent's
        # external refs with placeholder fields; the author can
        # refine display_name / description / setup_url_path
        # later via PATCH.
        _derived_required_tools = _auto_derive_required_tools(
            agent_document_json=saved_agent_document,
            builtin_refs=_current_builtin_tool_refs(kernel),
        )
        create_req = AgentTemplateCreateRequest(
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            category=payload.category,
            tags=payload.tags,
            agent_document_json=saved_agent_document,
            default_agent_settings=default_settings,
            required_tools=_derived_required_tools,
            is_published=payload.is_published,
            is_featured=False,
        )
        # Validation is a guardrail — auto-derive should be exact,
        # but enforcing the invariant here protects against future
        # divergence between _auto_derive and _collect_agent_document_tool_refs.
        try:
            validate_template_required_tools(
                agent_document_json=create_req.agent_document_json,
                required_tools=[t.model_dump() for t in create_req.required_tools],
                builtin_refs=_current_builtin_tool_refs(kernel),
            )
        except TemplateRequiredToolsValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "auto-derived required_tools failed consistency check",
                    "codes": exc.codes,
                },
            ) from exc
        try:
            return template_store.create_template(
                data=create_req,
                organization_id=organization_id,
                created_by=user_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
