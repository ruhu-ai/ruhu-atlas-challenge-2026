"""Agents core routes — extracted from api.py (RP-3.1 step 10).

Blueprint group 13 (the largest single group): agent CRUD, the agent
document (the unit of edit), versions, draft/publish/unpublish, diff /
publish-review / audit, settings, evaluation policy, metadata, and
``/agents:reload``. ``POST /agents/{agent_id}/test-session`` is NOT here —
it is group 18 (SYNC-KERNEL, blueprint step 13) and stays in api.py with
the other kernel-starting routes.

Three builders mirror the three non-contiguous inline blocks so every
route keeps its original registration position (hazard H2 — e.g.
``GET/PUT /agents/{agent_id}/agent-document`` registers before
``PUT /agents/{agent_id}``; the journeys router mount sits between the
CRUD block and the authoring block exactly where its inline block lived):

- ``build_agents_router`` — list/create/delete/get, validation, metadata.
- ``build_agent_authoring_router`` — settings, evaluation policy, agent
  document, versions, diff/publish-review/audit, draft/publish/unpublish.
- ``build_agents_reload_router`` — the internal ``/agents:reload`` seed
  hook.

Presentation logic lives in ``ruhu.services.agent_presentation``; the
builders receive the create_app()-bound resolvers as explicit kwargs and
import the stateless helpers directly. ``_agent_id_from_name`` still lives
in ``ruhu.api``, so this module is imported by ``create_app()`` AT THE
MOUNT SITE rather than at api.py's module top. No ``tags=`` / ``prefix=``
and unchanged handler names (hazard H1).
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request

# DTOs at module top (hazard H7: PEP 563 annotations resolve against this
# module's globals).
from ..agent_document import AgentDocument, AgentValidationReport
from ..agent_review import (
    AgentAuditTrail,
    AgentPublishReadiness,
    AgentVersionDiff,
    build_agent_audit_trail,
    build_agent_diff,
)
from ..api import _agent_id_from_name
from ..api_auth import RequestAuthContext
from ..api_models import (
    AgentCreateRequest,
    AgentDocumentResponse,
    AgentDraftCreateRequest,
    AgentEvaluationPolicyPatchRequest,
    AgentEvaluationPolicyResponse,
    AgentMetadataPatchRequest,
    AgentSettingsPatchRequest,
    AgentSettingsResponse,
    AgentSummary,
    AgentVersionSummary,
    AgentVersionTargetResponse,
)
from ..auth_deps import make_author_context_dep
from ..notifications.service import emit_notification
from ..schemas import AgentVersionStatus
from ..services.agent_presentation import agent_version_summary as _agent_version_summary
from ..services.agent_presentation import validation_report as _validation_report
from ..services.org_scope import (
    make_organization_id_for_request,
    make_required_author_organization_id,
)


def build_agents_router(
    *,
    agent_registry,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
    resolve_agent_snapshot: Callable,
    agent_summary: Callable[..., AgentSummary | None],
    validate_classifier_strategy: Callable[..., None],
) -> APIRouter:
    """Build the agents CRUD router (list/create/delete/get, validation,
    metadata)."""
    router = APIRouter()

    _require_runtime_author_context = make_author_context_dep(auth_enabled)
    _organization_id_for_request = make_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _required_author_organization_id = make_required_author_organization_id(
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _agent_summary = agent_summary
    _resolve_agent_snapshot = resolve_agent_snapshot
    _validate_classifier_strategy = validate_classifier_strategy

    @router.get("/agents", response_model=list[AgentSummary])
    def list_agents(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[AgentSummary]:
        organization_id = _organization_id_for_request(request)
        all_agents = [
            s
            for registration in agent_registry.list_agents(organization_id=organization_id)
            if (s := _agent_summary(registration, organization_id=organization_id)) is not None
        ]
        page = all_agents[offset : offset + limit]
        try:
            from ..observability.metrics import list_endpoint_row_count
            list_endpoint_row_count.labels(endpoint="agents").observe(len(page))
        except Exception:
            pass
        return page

    @router.post("/agents", response_model=AgentVersionTargetResponse)
    def create_agent(
        payload: AgentCreateRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentVersionTargetResponse:
        organization_id = _required_author_organization_id(context)
        new_agent_id = _agent_id_from_name(payload.name)
        # Same gate as on PATCH /agents/{id}/settings: a brand-new agent
        # cannot land on classifier.strategy = "prefill" because no LoRA can
        # exist yet. Reject early so operators don't have to wait for a
        # ``classifier_unavailable`` event to discover the misconfig.
        _validate_classifier_strategy(
            new_agent_id,
            payload.settings,
            organization_id=organization_id,
        )
        try:
            snapshot = agent_registry.create_agent_document(
                agent_id=new_agent_id,
                agent_name=payload.name,
                document=payload.document,
                settings={"agent_settings": payload.settings.model_dump(mode="json")},
                organization_id=organization_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return AgentVersionTargetResponse(
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            document=snapshot.agent_document or agent_registry.get_agent_document(new_agent_id, organization_id=organization_id),
            version=_agent_version_summary(snapshot),
        )

    @router.delete("/agents/{agent_id}", status_code=204, response_model=None)
    def delete_agent(
        agent_id: str,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> None:
        organization_id = _required_author_organization_id(context)
        try:
            agent_registry.delete_agent(agent_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/agents/{agent_id}", response_model=AgentVersionTargetResponse)
    def get_agent(
        agent_id: str,
        request: Request,
        target: AgentVersionStatus = "draft",
    ) -> AgentVersionTargetResponse:
        snapshot, _ = _resolve_agent_snapshot(request, agent_id, target=target)
        return AgentVersionTargetResponse(
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            document=snapshot.agent_document or agent_registry.get_agent_document(agent_id, target=target, organization_id=_organization_id_for_request(request)),
            version=_agent_version_summary(snapshot),
        )

    @router.get("/agents/{agent_id}/validation", response_model=AgentValidationReport)
    def get_agent_validation(
        agent_id: str,
        request: Request,
        target: AgentVersionStatus = "draft",
    ) -> AgentValidationReport:
        snapshot, _ = _resolve_agent_snapshot(request, agent_id, target=target)
        return _validation_report(snapshot)

    @router.patch("/agents/{agent_id}/metadata", response_model=AgentVersionTargetResponse)
    def update_agent_metadata(
        agent_id: str,
        payload: AgentMetadataPatchRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentVersionTargetResponse:
        organization_id = _required_author_organization_id(context)
        try:
            if payload.name is not None:
                agent_registry.update_agent_name(
                    agent_id,
                    payload.name,
                    organization_id=organization_id,
                )
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
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return AgentVersionTargetResponse(
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            document=snapshot.agent_document or agent_registry.get_agent_document(agent_id, organization_id=organization_id),
            version=_agent_version_summary(snapshot),
        )

    return router


def build_agent_authoring_router(
    *,
    agent_registry,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
    agent_evaluation_policy: Callable,
    agent_settings: Callable,
    resolved_agent_settings: Callable,
    validate_classifier_strategy: Callable[..., None],
    build_agent_publish_review: Callable[..., AgentPublishReadiness],
    version_summary_by_id: Callable,
    notification_store,
) -> APIRouter:
    """Build the agent authoring router (settings, evaluation policy,
    agent document, versions, diff/publish-review/audit,
    draft/publish/unpublish)."""
    router = APIRouter()

    _require_runtime_author_context = make_author_context_dep(auth_enabled)
    _organization_id_for_request = make_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _required_author_organization_id = make_required_author_organization_id(
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _agent_evaluation_policy = agent_evaluation_policy
    _agent_settings = agent_settings
    _resolved_agent_settings = resolved_agent_settings
    _validate_classifier_strategy = validate_classifier_strategy
    _build_agent_publish_review = build_agent_publish_review
    _version_summary_by_id = version_summary_by_id
    effective_notification_store = notification_store

    @router.get("/agents/{agent_id}/evaluation-policy", response_model=AgentEvaluationPolicyResponse)
    def get_agent_evaluation_policy(agent_id: str, request: Request) -> AgentEvaluationPolicyResponse:
        organization_id = _organization_id_for_request(request)
        try:
            policy = _agent_evaluation_policy(agent_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return AgentEvaluationPolicyResponse(agent_id=agent_id, policy=policy)

    @router.get("/agents/{agent_id}/settings", response_model=AgentSettingsResponse)
    def get_agent_settings(agent_id: str, request: Request) -> AgentSettingsResponse:
        organization_id = _organization_id_for_request(request)
        try:
            settings = _agent_settings(agent_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return AgentSettingsResponse(agent_id=agent_id, settings=settings)

    @router.patch("/agents/{agent_id}/settings", response_model=AgentSettingsResponse)
    def update_agent_settings(
        agent_id: str,
        payload: AgentSettingsPatchRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentSettingsResponse:
        organization_id = _required_author_organization_id(context)
        try:
            next_settings = _resolved_agent_settings(
                agent_id,
                payload,
                organization_id=organization_id,
            )
            settings = agent_registry.get_agent_registration(agent_id, organization_id=organization_id).settings
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        # Backend-side gate on classifier.strategy = "prefill". UI greys it
        # out, but this is the authoritative check.
        _validate_classifier_strategy(
            agent_id,
            next_settings,
            organization_id=organization_id,
        )

        settings["agent_settings"] = next_settings.model_dump(mode="python")
        try:
            agent_registry.update_agent_settings(
                agent_id,
                settings,
                organization_id=organization_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return AgentSettingsResponse(agent_id=agent_id, settings=next_settings)

    @router.patch("/agents/{agent_id}/evaluation-policy", response_model=AgentEvaluationPolicyResponse)
    def update_agent_evaluation_policy(
        agent_id: str,
        payload: AgentEvaluationPolicyPatchRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentEvaluationPolicyResponse:
        organization_id = _required_author_organization_id(context)
        try:
            current = _agent_evaluation_policy(agent_id, organization_id=organization_id)
            settings = agent_registry.get_agent_registration(agent_id, organization_id=organization_id).settings
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        updates = {
            field_name: getattr(payload, field_name)
            for field_name in payload.model_fields_set
        }
        policy = current.model_copy(update=updates)
        agent_registry.update_agent_settings(
            agent_id,
            {
                **settings,
                "evaluation_policy": policy.model_dump(mode="json"),
            },
            organization_id=organization_id,
        )
        return AgentEvaluationPolicyResponse(agent_id=agent_id, policy=policy)

    @router.get("/agents/{agent_id}/agent-document", response_model=AgentDocumentResponse)
    def get_agent_document(
        agent_id: str,
        request: Request,
        target: AgentVersionStatus = "draft",
    ) -> AgentDocumentResponse:
        organization_id = _organization_id_for_request(request)
        try:
            document = agent_registry.get_agent_document(
                agent_id,
                target=target,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return AgentDocumentResponse(agent_id=agent_id, target=target, document=document)

    @router.put("/agents/{agent_id}/agent-document", response_model=AgentDocumentResponse)
    def update_agent_document(
        agent_id: str,
        payload: AgentDocument,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentDocumentResponse:
        organization_id = _required_author_organization_id(context)
        try:
            document = agent_registry.update_draft_agent_document(
                agent_id,
                payload,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return AgentDocumentResponse(agent_id=agent_id, target="draft", document=document)

    @router.put("/agents/{agent_id}", response_model=AgentDocumentResponse)
    def update_agent_draft(
        agent_id: str,
        document: AgentDocument,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentDocumentResponse:
        organization_id = _required_author_organization_id(context)
        try:
            updated_document = agent_registry.update_draft_agent_document(
                agent_id,
                document,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return AgentDocumentResponse(agent_id=agent_id, target="draft", document=updated_document)

    @router.get("/agents/{agent_id}/versions", response_model=list[AgentVersionSummary])
    def list_agent_versions(agent_id: str, request: Request) -> list[AgentVersionSummary]:
        organization_id = _organization_id_for_request(request)
        try:
            versions = agent_registry.list_versions(agent_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [_agent_version_summary(version) for version in versions]

    @router.get("/agent-versions/{version_id}", response_model=AgentVersionTargetResponse)
    def get_agent_version(version_id: str, request: Request) -> AgentVersionTargetResponse:
        organization_id = _organization_id_for_request(request)
        try:
            snapshot = agent_registry.get_version_snapshot(version_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return AgentVersionTargetResponse(
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            document=snapshot.agent_document or agent_registry.get_agent_document(snapshot.agent_id, target=snapshot.status, organization_id=organization_id),
            version=_agent_version_summary(snapshot),
        )

    @router.get("/agents/{agent_id}/diff", response_model=AgentVersionDiff)
    def get_agent_diff(
        agent_id: str,
        request: Request,
        source_version_id: str | None = None,
        against_version_id: str | None = None,
    ) -> AgentVersionDiff:
        organization_id = _organization_id_for_request(request)
        try:
            if source_version_id is None:
                source_version_id = agent_registry.resolve_version_id(
                    agent_id,
                    target="draft",
                    organization_id=organization_id,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if against_version_id is None:
            try:
                against_version_id = agent_registry.resolve_version_id(
                    agent_id,
                    target="published",
                    organization_id=organization_id,
                )
            except KeyError:
                # No published version yet — compare draft against itself (empty diff).
                against_version_id = source_version_id
        source_snapshot = _version_summary_by_id(
            agent_id,
            source_version_id,
            organization_id=organization_id,
        )
        against_snapshot = _version_summary_by_id(
            agent_id,
            against_version_id,
            organization_id=organization_id,
        )
        return build_agent_diff(source_snapshot, against_snapshot)

    @router.get("/agents/{agent_id}/publish-review", response_model=AgentPublishReadiness)
    def get_agent_publish_review(agent_id: str, request: Request) -> AgentPublishReadiness:
        organization_id = _organization_id_for_request(request)
        return _build_agent_publish_review(agent_id, organization_id=organization_id)

    @router.get("/agents/{agent_id}/audit", response_model=AgentAuditTrail)
    def get_agent_audit(agent_id: str, request: Request) -> AgentAuditTrail:
        organization_id = _organization_id_for_request(request)
        registrations = {
            registration.agent_id: registration
            for registration in agent_registry.list_agents(organization_id=organization_id)
        }
        registration = registrations.get(agent_id)
        if registration is None:
            raise HTTPException(status_code=404, detail=f"unknown agent id: {agent_id}")
        versions = agent_registry.list_versions(agent_id, organization_id=organization_id)
        return build_agent_audit_trail(registration=registration, versions=versions)

    @router.post("/agents/{agent_id}/draft", response_model=AgentVersionTargetResponse)
    def create_agent_draft(
        agent_id: str,
        payload: AgentDraftCreateRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentVersionTargetResponse:
        organization_id = _required_author_organization_id(context)
        try:
            snapshot = agent_registry.create_draft(
                agent_id,
                organization_id=organization_id,
                source_version_id=payload.source_version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return AgentVersionTargetResponse(
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            document=snapshot.agent_document or agent_registry.get_agent_document(agent_id, organization_id=organization_id),
            version=_agent_version_summary(snapshot),
        )

    @router.post("/agents/{agent_id}/publish", response_model=AgentVersionTargetResponse)
    def publish_agent(
        agent_id: str,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentVersionTargetResponse:
        organization_id = _required_author_organization_id(context)
        try:
            review = _build_agent_publish_review(agent_id, organization_id=organization_id)
            if not review.can_publish:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "agent draft failed publish review",
                        "review": review.model_dump(mode="json"),
                        "validation": review.validation.model_dump(mode="json"),
                    },
                )
            snapshot = agent_registry.publish(agent_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        emit_notification(
            effective_notification_store,
            organization_id=organization_id,
            category="agent.published",
            title=f'"{snapshot.name}" published',
            level="info",
            urgency="fyi",
            user_id=context.principal.user.user_id if context and context.principal else None,
            source_type="agent",
            source_id=agent_id,
            payload={"agent_id": agent_id, "version_id": snapshot.version_id},
        )
        return AgentVersionTargetResponse(
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            document=snapshot.agent_document or agent_registry.get_agent_document(agent_id, target="published", organization_id=organization_id),
            version=_agent_version_summary(snapshot),
        )

    @router.post("/agents/{agent_id}/unpublish", response_model=AgentVersionTargetResponse)
    def unpublish_agent(
        agent_id: str,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> AgentVersionTargetResponse:
        organization_id = _required_author_organization_id(context)
        try:
            snapshot = agent_registry.unpublish(agent_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        emit_notification(
            effective_notification_store,
            organization_id=organization_id,
            category="agent.unpublished",
            title=f'"{snapshot.name}" reverted to draft',
            level="info",
            urgency="fyi",
            user_id=context.principal.user.user_id if context and context.principal else None,
            source_type="agent",
            source_id=agent_id,
            payload={"agent_id": agent_id},
        )
        return AgentVersionTargetResponse(
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            document=snapshot.agent_document or agent_registry.get_agent_document(agent_id, organization_id=organization_id),
            version=_agent_version_summary(snapshot),
        )

    return router


def build_agents_reload_router(
    *,
    agent_registry,
    agent_seed_root,
    require_internal_api_access: Callable[[Request], None],
) -> APIRouter:
    """Build the internal ``/agents:reload`` seed-hook router."""
    router = APIRouter()

    _require_internal_api_access = require_internal_api_access

    @router.post("/agents:reload")
    def reload_agents(request: Request) -> dict[str, int]:
        _require_internal_api_access(request)
        if agent_seed_root is None:
            raise HTTPException(status_code=503, detail="agent seed root is not configured")
        agent_registry.bootstrap_from_directory(agent_seed_root)
        return {"agent_count": len(agent_registry.list_agents())}

    return router
