from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from .api_auth import RequestAuthContext, require_authenticated_context
from .policy import has_minimum_organization_role
from .rules import RuleDecision, RuleEvaluationContext, RuleProgram
from .rules_compose import (
    ComposeBindingScope,
    ComposeExplainRequest,
    ComposeExplainResponse,
    ComposePolicyProposal,
    ComposePolicyRequest,
    compile_policy,
    explain_policy,
)
from .rules_resolver import RuleProgramResolutionInput
from .rules_store import (
    RuleBindingCreate,
    RuleBindingDocument,
    RuleBindingUpdate,
    RuleDefinitionRevisionDocument,
    RuleDefinitionSummary,
    RuleLibrarySummary,
    RuleLibraryVersionCreate,
    RuleLibraryVersionDocument,
    RuleRevisionStatus,
    RuleRevisionBody,
    RulesOrganizationScope,
    RulesRuntime,
)


class RuleDefinitionCreateRequest(RuleRevisionBody):
    rule_id: str
    organization_scope: Literal["organization", "system"] = "organization"


class RuleDefinitionListResponse(BaseModel):
    items: list[RuleDefinitionSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class RuleLibraryListResponse(BaseModel):
    items: list[RuleLibrarySummary] = Field(default_factory=list)


class RuleBindingListResponse(BaseModel):
    items: list[RuleBindingDocument] = Field(default_factory=list)


class RuleEvaluationRequest(BaseModel):
    program: RuleProgram
    context: RuleEvaluationContext


class ComposeSaveRequest(BaseModel):
    """Persist a composed proposal as a draft rule definition.

    The endpoint deliberately stops at draft (review-before-publish per
    Doc 04 \u00a73). Bindings cannot reference draft revisions, so the
    suggested scope is preserved in the rule metadata under
    ``compose_suggested_scope`` for the binding screen to pre-fill.
    """

    rule_id: str
    organization_scope: Literal["organization", "system"] = "organization"
    rule_body: RuleRevisionBody
    suggested_binding_scope: ComposeBindingScope | None = None


def install_rules_router(app: FastAPI, *, runtime: RulesRuntime | None, rate_limiter=None) -> None:
    router = APIRouter(
        prefix="/api/rules",
        tags=["rules"],
        dependencies=[rate_limiter] if rate_limiter else [],
    )

    def _require_runtime() -> RulesRuntime:
        if runtime is None:
            raise HTTPException(status_code=503, detail="rules runtime is not configured")
        return runtime

    def _context(request: Request) -> RequestAuthContext:
        return require_authenticated_context(request)

    def _require_rules_admin(request: Request) -> RequestAuthContext:
        context = require_authenticated_context(request)
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if principal.is_superuser or has_minimum_organization_role(context, "admin"):
            return context
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required for rules mutation",
        )

    def _organization_id(context: RequestAuthContext) -> str:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return principal.organization.organization_id

    def _assert_system_scope_allowed(
        context: RequestAuthContext,
        *,
        requested_scope: Literal["organization", "system"],
    ) -> None:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if requested_scope == "system" and not principal.is_superuser:
            raise HTTPException(status_code=403, detail="superuser required for system scope")

    def _resolve_preview_organization_id(
        context: RequestAuthContext,
        requested_organization_id: str | None,
    ) -> str | None:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if requested_organization_id is None:
            return principal.organization.organization_id
        if requested_organization_id == principal.organization.organization_id:
            return requested_organization_id
        if principal.is_superuser:
            return requested_organization_id
        raise HTTPException(status_code=403, detail="organization scope mismatch")

    def _allow_system_scope(context: RequestAuthContext) -> bool:
        principal = context.principal
        return principal is not None and principal.is_superuser

    def _handle_store_error(exc: Exception) -> HTTPException:
        if isinstance(exc, PermissionError):
            return HTTPException(status_code=403, detail=str(exc))
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail=str(exc))
        if isinstance(exc, IntegrityError):
            return HTTPException(status_code=409, detail="conflicting rules record")
        message = str(exc)
        if (
            "already exists" in message
            or "already published" in message
            or "draft" in message
            or "published revisions" in message
            or "published revisions may be retired" in message
        ):
            return HTTPException(status_code=409, detail=message)
        if "superuser" in message:
            return HTTPException(status_code=403, detail=message)
        if "scope" in message or "unsupported rule stage" in message or "effect" in message:
            return HTTPException(status_code=422, detail=message)
        return HTTPException(status_code=400, detail=message)

    @router.get("/definitions", response_model=RuleDefinitionListResponse)
    def list_rule_definitions(
        request: Request,
        organization_scope: RulesOrganizationScope = "all",
        stage: str | None = None,
        status: RuleRevisionStatus | None = None,
        tag: str | None = None,
        search: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        context: RequestAuthContext = Depends(_context),
    ) -> RuleDefinitionListResponse:
        del request
        items = _require_runtime().store.list_definitions(
            organization_id=_organization_id(context),
            organization_scope=organization_scope,
            stage=stage,
            status=status,
            tag=tag,
            search=search,
            limit=limit,
        )
        return RuleDefinitionListResponse(items=items)

    @router.post("/definitions", response_model=RuleDefinitionRevisionDocument, status_code=201)
    def create_rule_definition(
        payload: RuleDefinitionCreateRequest,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleDefinitionRevisionDocument:
        _assert_system_scope_allowed(context, requested_scope=payload.organization_scope)
        try:
            return _require_runtime().store.create_definition(
                organization_id=_organization_id(context),
                actor_user_id=context.principal.user.user_id if context.principal is not None else None,
                body=RuleRevisionBody.model_validate(payload.model_dump(mode="python")),
                rule_id=payload.rule_id,
                organization_scope=payload.organization_scope,
                allow_system_scope=_allow_system_scope(context),
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc

    @router.get("/definitions/{rule_id}/revisions/{revision}", response_model=RuleDefinitionRevisionDocument)
    def get_rule_definition_revision(
        rule_id: str,
        revision: int,
        organization_scope: RulesOrganizationScope = "all",
        context: RequestAuthContext = Depends(_context),
    ) -> RuleDefinitionRevisionDocument:
        item = _require_runtime().store.get_definition_revision(
            organization_id=_organization_id(context),
            rule_id=rule_id,
            revision=revision,
            organization_scope=organization_scope,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="rule revision not found")
        return item

    @router.put("/definitions/{rule_id}/revisions/{revision}", response_model=RuleDefinitionRevisionDocument)
    def update_rule_definition_revision(
        rule_id: str,
        revision: int,
        payload: RuleRevisionBody,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleDefinitionRevisionDocument:
        try:
            return _require_runtime().store.update_draft_revision(
                organization_id=_organization_id(context),
                actor_user_id=context.principal.user.user_id if context.principal is not None else None,
                rule_id=rule_id,
                revision=revision,
                body=payload,
                allow_system_scope=_allow_system_scope(context),
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc

    @router.post("/definitions/{rule_id}/revisions", response_model=RuleDefinitionRevisionDocument, status_code=201)
    def create_rule_definition_revision(
        rule_id: str,
        payload: RuleRevisionBody,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleDefinitionRevisionDocument:
        try:
            return _require_runtime().store.create_next_revision(
                organization_id=_organization_id(context),
                actor_user_id=context.principal.user.user_id if context.principal is not None else None,
                rule_id=rule_id,
                body=payload,
                allow_system_scope=_allow_system_scope(context),
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc

    @router.post("/definitions/{rule_id}/revisions/{revision}/publish", response_model=RuleDefinitionRevisionDocument)
    def publish_rule_definition_revision(
        rule_id: str,
        revision: int,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleDefinitionRevisionDocument:
        try:
            return _require_runtime().store.publish_revision(
                organization_id=_organization_id(context),
                rule_id=rule_id,
                revision=revision,
                allow_system_scope=_allow_system_scope(context),
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc

    @router.post("/definitions/{rule_id}/revisions/{revision}/retire", response_model=RuleDefinitionRevisionDocument)
    def retire_rule_definition_revision(
        rule_id: str,
        revision: int,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleDefinitionRevisionDocument:
        try:
            return _require_runtime().store.retire_revision(
                organization_id=_organization_id(context),
                rule_id=rule_id,
                revision=revision,
                allow_system_scope=_allow_system_scope(context),
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc

    @router.get("/libraries", response_model=RuleLibraryListResponse)
    def list_rule_libraries(
        visibility: Literal["system", "organization"] | None = None,
        organization_scope: RulesOrganizationScope = "all",
        search: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        context: RequestAuthContext = Depends(_context),
    ) -> RuleLibraryListResponse:
        items = _require_runtime().store.list_libraries(
            organization_id=_organization_id(context),
            organization_scope=organization_scope,
            visibility=visibility,
            search=search,
            limit=limit,
        )
        return RuleLibraryListResponse(items=items)

    @router.post("/libraries", response_model=RuleLibraryVersionDocument, status_code=201)
    def create_rule_library_version(
        payload: RuleLibraryVersionCreate,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleLibraryVersionDocument:
        _assert_system_scope_allowed(context, requested_scope=payload.organization_scope)
        try:
            return _require_runtime().store.create_library_version(
                organization_id=_organization_id(context),
                actor_user_id=context.principal.user.user_id if context.principal is not None else None,
                payload=payload,
                allow_system_scope=_allow_system_scope(context),
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc

    @router.get("/libraries/{library_id}/versions/{version}", response_model=RuleLibraryVersionDocument)
    def get_rule_library_version(
        library_id: str,
        version: str,
        organization_scope: RulesOrganizationScope = "all",
        context: RequestAuthContext = Depends(_context),
    ) -> RuleLibraryVersionDocument:
        item = _require_runtime().store.get_library_version(
            organization_id=_organization_id(context),
            library_id=library_id,
            version=version,
            organization_scope=organization_scope,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="rule library not found")
        return item

    @router.get("/bindings", response_model=RuleBindingListResponse)
    def list_rule_bindings(
        organization_scope: RulesOrganizationScope = "all",
        rule_id: str | None = None,
        revision: int | None = None,
        mode: Literal["enforce", "shadow", "disabled"] | None = None,
        agent_id: str | None = None,
        step_id: str | None = None,
        channel: str | None = None,
        tool_ref: str | None = None,
        event_type: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        context: RequestAuthContext = Depends(_context),
    ) -> RuleBindingListResponse:
        items = _require_runtime().store.list_bindings(
            organization_id=_organization_id(context),
            organization_scope=organization_scope,
            rule_id=rule_id,
            revision=revision,
            mode=mode,  # type: ignore[arg-type]
            agent_id=agent_id,
            step_id=step_id,
            channel=channel,
            tool_ref=tool_ref,
            event_type=event_type,
            limit=limit,
        )
        return RuleBindingListResponse(items=items)

    @router.post("/bindings", response_model=RuleBindingDocument, status_code=201)
    def create_rule_binding(
        payload: RuleBindingCreate,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleBindingDocument:
        _assert_system_scope_allowed(context, requested_scope=payload.organization_scope)
        try:
            return _require_runtime().store.create_binding(
                organization_id=_organization_id(context),
                actor_user_id=context.principal.user.user_id if context.principal is not None else None,
                payload=payload,
                allow_system_scope=_allow_system_scope(context),
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc

    @router.patch("/bindings/{binding_id}", response_model=RuleBindingDocument)
    def update_rule_binding(
        binding_id: str,
        payload: RuleBindingUpdate,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleBindingDocument:
        try:
            return _require_runtime().store.update_binding(
                organization_id=_organization_id(context),
                actor_user_id=context.principal.user.user_id if context.principal is not None else None,
                binding_id=binding_id,
                payload=payload,
                allow_system_scope=_allow_system_scope(context),
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc

    @router.post("/programs/resolve", response_model=RuleProgram)
    def resolve_rule_program_preview(
        payload: RuleProgramResolutionInput,
        context: RequestAuthContext = Depends(_context),
    ) -> RuleProgram:
        return _require_runtime().resolver.resolve(
            organization_id=_resolve_preview_organization_id(context, payload.organization_id),
            agent_id=payload.agent_id,
            step_id=payload.step_id,
            channel=payload.channel,
            event_type=payload.event_type,
            tool_ref=payload.tool_ref,
        )

    @router.post("/evaluate", response_model=RuleDecision)
    def evaluate_rule_program(
        payload: RuleEvaluationRequest,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleDecision:
        del context
        try:
            return _require_runtime().engine.evaluate(payload.program, payload.context)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/compose/compile", response_model=ComposePolicyProposal)
    def compose_compile_policy(
        payload: ComposePolicyRequest,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> ComposePolicyProposal:
        del context
        return compile_policy(payload)

    @router.post("/compose/explain", response_model=ComposeExplainResponse)
    def compose_explain_policy(
        payload: ComposeExplainRequest,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> ComposeExplainResponse:
        del context
        return explain_policy(payload)

    @router.post(
        "/compose/save",
        response_model=RuleDefinitionRevisionDocument,
        status_code=201,
    )
    def compose_save_draft(
        payload: ComposeSaveRequest,
        context: RequestAuthContext = Depends(_require_rules_admin),
    ) -> RuleDefinitionRevisionDocument:
        _assert_system_scope_allowed(context, requested_scope=payload.organization_scope)
        metadata = dict(payload.rule_body.metadata)
        metadata.setdefault("compose_source", "natural_language")
        if payload.suggested_binding_scope is not None:
            metadata["compose_suggested_scope"] = payload.suggested_binding_scope.model_dump(mode="json")
        body = payload.rule_body.model_copy(update={"metadata": metadata})
        try:
            return _require_runtime().store.create_definition(
                organization_id=_organization_id(context),
                actor_user_id=context.principal.user.user_id if context.principal is not None else None,
                body=body,
                rule_id=payload.rule_id,
                organization_scope=payload.organization_scope,
                allow_system_scope=_allow_system_scope(context),
            )
        except Exception as exc:
            raise _handle_store_error(exc) from exc

    app.include_router(router)
