from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .analytics_tagging import (
    ClassificationReviewItem,
    ClassifierProfile,
    ConversationSummaryDetailReadModel,
    IntentDefinition,
    IntentTagsAnalyticsReadModel,
    IntentTagsInsightsReadModel,
    IntentTagsRuntime,
    ReviewDisposition,
    ReviewKind,
    ReviewQueueRowReadModel,
    SemanticSummaryWebhookTarget,
    SummaryListItemReadModel,
    TagDefinition,
    TaxonomySnapshotReadModel,
    TaxonomyVersion,
    TurnClassificationDecision,
    TurnClassificationEvent,
)
from .analytics_tagging.webhooks import SemanticSummaryWebhookDispatchResult, SemanticSummaryWebhookDispatcher

OrganizationResolver = Callable[[Request, str | None], str]
UserResolver = Callable[[Request], str | None]
AccessResolver = Callable[[Request], object | None]


class TaxonomyVersionCreateRequest(BaseModel):
    organization_id: str | None = None
    name: str
    notes: str | None = None


class IntentDefinitionCreateRequest(BaseModel):
    organization_id: str | None = None
    agent_id: str | None = None
    taxonomy_version_id: str | None = None
    name: str
    display_name: str
    description: str | None = None
    category: str | None = None
    example_phrases: list[str] = Field(default_factory=list)
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    priority: int = Field(default=0, ge=0)
    is_active: bool = True
    is_deprecated: bool = False
    color: str | None = None
    icon: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntentDefinitionUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    example_phrases: list[str] | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    priority: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    is_deprecated: bool | None = None
    color: str | None = None
    icon: str | None = None
    metadata: dict[str, Any] | None = None
    taxonomy_version_id: str | None = None


class TagDefinitionCreateRequest(BaseModel):
    organization_id: str | None = None
    agent_id: str | None = None
    taxonomy_version_id: str | None = None
    name: str
    display_name: str
    description: str | None = None
    tag_kind: str
    category: str | None = None
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    apply_scope: str = "conversation"
    related_intent_id: str | None = None
    is_active: bool = True
    is_deprecated: bool = False
    color: str | None = None
    icon: str | None = None
    rule_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TagDefinitionUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    tag_kind: str | None = None
    category: str | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    apply_scope: str | None = None
    related_intent_id: str | None = None
    is_active: bool | None = None
    is_deprecated: bool | None = None
    color: str | None = None
    icon: str | None = None
    rule_config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    taxonomy_version_id: str | None = None


class ClassifierProfileCreateRequest(BaseModel):
    organization_id: str | None = None
    agent_id: str | None = None
    adapter_name: str = "ruhu-general"
    supported_languages: list[str] = Field(default_factory=list)
    taxonomy_mode: str = "live"
    taxonomy_version_id: str | None = None
    tool_catalog: list[dict[str, Any]] = Field(default_factory=list)
    policy_profile: dict[str, Any] = Field(default_factory=dict)
    profile_metadata: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class ClassifierProfileUpdateRequest(BaseModel):
    agent_id: str | None = None
    adapter_name: str | None = None
    supported_languages: list[str] | None = None
    taxonomy_mode: str | None = None
    taxonomy_version_id: str | None = None
    tool_catalog: list[dict[str, Any]] | None = None
    policy_profile: dict[str, Any] | None = None
    profile_metadata: dict[str, Any] | None = None
    is_active: bool | None = None


class ProfileRebuildRequest(BaseModel):
    organization_id: str | None = None
    agent_id: str | None = None
    live_tool_catalog: list[dict[str, Any]] = Field(default_factory=list)


class ReviewClaimRequest(BaseModel):
    user_id: str | None = None


class TurnReviewResolutionRequest(BaseModel):
    user_id: str | None = None
    disposition: ReviewDisposition
    corrected_decision: TurnClassificationDecision | None = None
    review_notes: str | None = None


class SummaryReviewResolutionRequest(BaseModel):
    user_id: str | None = None
    disposition: ReviewDisposition
    corrected_fields: dict[str, Any] = Field(default_factory=dict)
    corrected_tag_definition_ids: list[str] = Field(default_factory=list)
    review_notes: str | None = None


class SemanticWebhookTargetCreateRequest(BaseModel):
    organization_id: str | None = None
    name: str
    url: str
    agent_ids: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    signing_secret_ref: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=5.0, gt=0.0, le=120.0)
    max_retries: int = Field(default=5, ge=0, le=25)
    retry_backoff_seconds: float = Field(default=5.0, ge=0.0, le=3600.0)
    is_active: bool = True


class SemanticWebhookTargetUpdateRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    agent_ids: list[str] | None = None
    channels: list[str] | None = None
    signing_secret_ref: str | None = None
    extra_headers: dict[str, str] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0.0, le=120.0)
    max_retries: int | None = Field(default=None, ge=0, le=25)
    retry_backoff_seconds: float | None = Field(default=None, ge=0.0, le=3600.0)
    is_active: bool | None = None


class SemanticWebhookTargetReadModel(BaseModel):
    webhook_target_id: str
    organization_id: str
    name: str
    url: str
    event_name: str
    agent_ids: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    is_active: bool
    has_signing_secret: bool = False
    signing_secret_source: str = "none"
    last_attempt_at: Any | None = None
    last_success_at: Any | None = None
    last_failure_at: Any | None = None
    consecutive_failure_count: int = 0
    last_error: str | None = None
    created_at: Any
    updated_at: Any


class SemanticWebhookDispatchResponse(BaseModel):
    publication_attempted: int = 0
    publication_fanned_out: int = 0
    publication_skipped: int = 0
    publication_failed: int = 0
    delivery_attempted: int = 0
    delivery_delivered: int = 0
    delivery_failed: int = 0
    delivery_retried: int = 0
    delivery_skipped: int = 0


def install_intent_tags_router(
    app: FastAPI,
    *,
    runtime: IntentTagsRuntime | None,
    resolve_organization_id: OrganizationResolver,
    resolve_user_id: UserResolver,
    require_read_access: AccessResolver | None = None,
    require_write_access: AccessResolver | None = None,
    semantic_webhook_dispatcher: SemanticSummaryWebhookDispatcher | None = None,
) -> None:
    router = APIRouter(tags=["intent-tags"])

    def _allow_any(_: Request) -> None:
        return None

    read_access = require_read_access or _allow_any
    write_access = require_write_access or _allow_any

    def _require_runtime() -> IntentTagsRuntime:
        if runtime is None:
            raise HTTPException(status_code=503, detail="intent-tags runtime is not configured")
        return runtime

    def _organization_id(request: Request, requested: str | None) -> str:
        return resolve_organization_id(request, requested)

    def _effective_user_id(request: Request, requested: str | None = None) -> str:
        if requested:
            return requested
        resolved = resolve_user_id(request)
        if resolved:
            return resolved
        return "operator"

    def _bad_request(exc: ValueError) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    def _intent(intent_definition_id: str, *, organization_id: str) -> IntentDefinition:
        item = _require_runtime().store.get_intent_definition(intent_definition_id)
        if item is None or item.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="intent definition not found")
        return item

    def _tag(tag_definition_id: str, *, organization_id: str) -> TagDefinition:
        item = _require_runtime().store.get_tag_definition(tag_definition_id)
        if item is None or item.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="tag definition not found")
        return item

    def _profile(classifier_profile_id: str, *, organization_id: str) -> ClassifierProfile:
        item = _require_runtime().store.get_classifier_profile(classifier_profile_id)
        if item is None or item.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="classifier profile not found")
        return item

    def _review(review_item_id: str, *, organization_id: str) -> ClassificationReviewItem:
        item = _require_runtime().store.get_review_item(review_item_id)
        if item is None or item.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="review item not found")
        return item

    def _summary(conversation_summary_id: str, *, organization_id: str) -> ConversationSummaryDetailReadModel:
        detail = _require_runtime().read_service.get_summary_detail(
            organization_id,
            conversation_summary_id=conversation_summary_id,
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="conversation summary not found")
        return detail

    def _webhook_target(
        webhook_target_id: str,
        *,
        organization_id: str,
    ) -> SemanticSummaryWebhookTarget:
        target = _require_runtime().webhook_service.get_target(webhook_target_id)
        if target is None or target.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="semantic webhook target not found")
        return target

    def _secret_source(value: str | None) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return "none"
        if normalized.startswith("env:"):
            return "env"
        if normalized.startswith("projects/"):
            return "secret_manager"
        return "inline"

    def _webhook_target_view(target: SemanticSummaryWebhookTarget) -> SemanticWebhookTargetReadModel:
        return SemanticWebhookTargetReadModel(
            webhook_target_id=target.webhook_target_id,
            organization_id=target.organization_id,
            name=target.name,
            url=target.url,
            event_name=target.event_name,
            agent_ids=list(target.agent_ids),
            channels=list(target.channels),
            extra_headers=dict(target.extra_headers),
            timeout_seconds=target.timeout_seconds,
            max_retries=target.max_retries,
            retry_backoff_seconds=target.retry_backoff_seconds,
            is_active=target.is_active,
            has_signing_secret=bool(str(target.signing_secret_ref or "").strip()),
            signing_secret_source=_secret_source(target.signing_secret_ref),
            last_attempt_at=target.last_attempt_at,
            last_success_at=target.last_success_at,
            last_failure_at=target.last_failure_at,
            consecutive_failure_count=target.consecutive_failure_count,
            last_error=target.last_error,
            created_at=target.created_at,
            updated_at=target.updated_at,
        )

    @router.get("/intent-tags/taxonomy", response_model=TaxonomySnapshotReadModel)
    def taxonomy_snapshot(
        request: Request,
        organization_id: str | None = None,
        agent_id: str | None = None,
        _: object | None = Depends(read_access),
    ) -> TaxonomySnapshotReadModel:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().read_service.get_taxonomy_snapshot(
            effective_organization_id,
            agent_id=agent_id,
        )

    @router.get("/intent-tags/versions", response_model=list[TaxonomyVersion])
    def list_taxonomy_versions(
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(read_access),
    ) -> list[TaxonomyVersion]:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().store.list_taxonomy_versions(effective_organization_id)

    @router.post("/intent-tags/versions", response_model=TaxonomyVersion)
    def create_taxonomy_version(
        payload: TaxonomyVersionCreateRequest,
        request: Request,
        _: object | None = Depends(write_access),
    ) -> TaxonomyVersion:
        effective_organization_id = _organization_id(request, payload.organization_id)
        return _require_runtime().taxonomy_service.save_taxonomy_version(
            TaxonomyVersion(
                organization_id=effective_organization_id,
                name=payload.name,
                notes=payload.notes,
            )
        )

    @router.post("/intent-tags/versions/{taxonomy_version_id}/publish", response_model=TaxonomyVersion)
    def publish_taxonomy_version(
        taxonomy_version_id: str,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(write_access),
    ) -> TaxonomyVersion:
        effective_organization_id = _organization_id(request, organization_id)
        version = _require_runtime().store.get_taxonomy_version(taxonomy_version_id)
        if version is None or version.organization_id != effective_organization_id:
            raise HTTPException(status_code=404, detail="taxonomy version not found")
        try:
            return _require_runtime().taxonomy_service.publish_taxonomy_version(taxonomy_version_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/intent-tags/intents", response_model=list[IntentDefinition])
    def list_intents(
        request: Request,
        organization_id: str | None = None,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = True,
        _: object | None = Depends(read_access),
    ) -> list[IntentDefinition]:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().taxonomy_service.list_effective_intents(
            effective_organization_id,
            agent_id=agent_id,
            taxonomy_version_id=taxonomy_version_id,
            include_inactive=include_inactive,
        )

    @router.post("/intent-tags/intents", response_model=IntentDefinition)
    def create_intent(
        payload: IntentDefinitionCreateRequest,
        request: Request,
        _: object | None = Depends(write_access),
    ) -> IntentDefinition:
        effective_organization_id = _organization_id(request, payload.organization_id)
        try:
            return _require_runtime().taxonomy_service.save_intent_definition(
                IntentDefinition(
                    organization_id=effective_organization_id,
                    agent_id=payload.agent_id,
                    taxonomy_version_id=payload.taxonomy_version_id,
                    name=payload.name,
                    display_name=payload.display_name,
                    description=payload.description,
                    category=payload.category,
                    example_phrases=list(payload.example_phrases),
                    confidence_threshold=payload.confidence_threshold,
                    priority=payload.priority,
                    is_active=payload.is_active,
                    is_deprecated=payload.is_deprecated,
                    color=payload.color,
                    icon=payload.icon,
                    metadata=dict(payload.metadata),
                )
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.put("/intent-tags/intents/{intent_definition_id}", response_model=IntentDefinition)
    def update_intent(
        intent_definition_id: str,
        payload: IntentDefinitionUpdateRequest,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(write_access),
    ) -> IntentDefinition:
        effective_organization_id = _organization_id(request, organization_id)
        existing = _intent(intent_definition_id, organization_id=effective_organization_id)
        updates = payload.model_dump(exclude_unset=True)
        try:
            return _require_runtime().taxonomy_service.save_intent_definition(existing.model_copy(update=updates))
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/intent-tags/tags", response_model=list[TagDefinition])
    def list_tags(
        request: Request,
        organization_id: str | None = None,
        agent_id: str | None = None,
        taxonomy_version_id: str | None = None,
        include_inactive: bool = True,
        _: object | None = Depends(read_access),
    ) -> list[TagDefinition]:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().taxonomy_service.list_effective_tags(
            effective_organization_id,
            agent_id=agent_id,
            taxonomy_version_id=taxonomy_version_id,
            include_inactive=include_inactive,
        )

    @router.post("/intent-tags/tags", response_model=TagDefinition)
    def create_tag(
        payload: TagDefinitionCreateRequest,
        request: Request,
        _: object | None = Depends(write_access),
    ) -> TagDefinition:
        effective_organization_id = _organization_id(request, payload.organization_id)
        try:
            return _require_runtime().taxonomy_service.save_tag_definition(
                TagDefinition(
                    organization_id=effective_organization_id,
                    agent_id=payload.agent_id,
                    taxonomy_version_id=payload.taxonomy_version_id,
                    name=payload.name,
                    display_name=payload.display_name,
                    description=payload.description,
                    tag_kind=payload.tag_kind,  # type: ignore[arg-type]
                    category=payload.category,
                    confidence_threshold=payload.confidence_threshold,
                    apply_scope=payload.apply_scope,  # type: ignore[arg-type]
                    related_intent_id=payload.related_intent_id,
                    is_active=payload.is_active,
                    is_deprecated=payload.is_deprecated,
                    color=payload.color,
                    icon=payload.icon,
                    rule_config=dict(payload.rule_config),
                    metadata=dict(payload.metadata),
                )
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.put("/intent-tags/tags/{tag_definition_id}", response_model=TagDefinition)
    def update_tag(
        tag_definition_id: str,
        payload: TagDefinitionUpdateRequest,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(write_access),
    ) -> TagDefinition:
        effective_organization_id = _organization_id(request, organization_id)
        existing = _tag(tag_definition_id, organization_id=effective_organization_id)
        updates = payload.model_dump(exclude_unset=True)
        try:
            return _require_runtime().taxonomy_service.save_tag_definition(existing.model_copy(update=updates))
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/intent-tags/profiles", response_model=list[ClassifierProfile])
    def list_profiles(
        request: Request,
        organization_id: str | None = None,
        agent_id: str | None = None,
        is_active: bool | None = None,
        _: object | None = Depends(read_access),
    ) -> list[ClassifierProfile]:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().profile_service.list_profiles(
            effective_organization_id,
            agent_id=agent_id,
            is_active=is_active,
        )

    @router.post("/intent-tags/profiles", response_model=ClassifierProfile)
    def create_profile(
        payload: ClassifierProfileCreateRequest,
        request: Request,
        _: object | None = Depends(write_access),
    ) -> ClassifierProfile:
        effective_organization_id = _organization_id(request, payload.organization_id)
        try:
            return _require_runtime().profile_service.save_profile(
                ClassifierProfile(
                    organization_id=effective_organization_id,
                    agent_id=payload.agent_id,
                    adapter_name=payload.adapter_name,
                    supported_languages=list(payload.supported_languages),
                    taxonomy_mode=payload.taxonomy_mode,  # type: ignore[arg-type]
                    taxonomy_version_id=payload.taxonomy_version_id,
                    tool_catalog=list(payload.tool_catalog),
                    policy_profile=dict(payload.policy_profile),
                    profile_metadata=dict(payload.profile_metadata),
                    is_active=payload.is_active,
                )
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.put("/intent-tags/profiles/{classifier_profile_id}", response_model=ClassifierProfile)
    def update_profile(
        classifier_profile_id: str,
        payload: ClassifierProfileUpdateRequest,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(write_access),
    ) -> ClassifierProfile:
        effective_organization_id = _organization_id(request, organization_id)
        existing = _profile(classifier_profile_id, organization_id=effective_organization_id)
        updates = payload.model_dump(exclude_unset=True)
        try:
            return _require_runtime().profile_service.save_profile(existing.model_copy(update=updates))
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/intent-tags/profiles/{classifier_profile_id}/rebuild", response_model=ClassifierProfile)
    def rebuild_profile_cache(
        classifier_profile_id: str,
        payload: ProfileRebuildRequest,
        request: Request,
        _: object | None = Depends(write_access),
    ) -> ClassifierProfile:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _profile(classifier_profile_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().profile_service.rebuild_profile_cache(
                classifier_profile_id,
                agent_id=payload.agent_id,
                live_tool_catalog=list(payload.live_tool_catalog),
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/intent-tags/webhook-targets", response_model=list[SemanticWebhookTargetReadModel])
    def list_semantic_webhook_targets(
        request: Request,
        organization_id: str | None = None,
        is_active: bool | None = None,
        _: object | None = Depends(read_access),
    ) -> list[SemanticWebhookTargetReadModel]:
        effective_organization_id = _organization_id(request, organization_id)
        return [
            _webhook_target_view(item)
            for item in _require_runtime().webhook_service.list_targets(
                effective_organization_id,
                is_active=is_active,
            )
        ]

    @router.post("/intent-tags/webhook-targets", response_model=SemanticWebhookTargetReadModel)
    def create_semantic_webhook_target(
        payload: SemanticWebhookTargetCreateRequest,
        request: Request,
        _: object | None = Depends(write_access),
    ) -> SemanticWebhookTargetReadModel:
        effective_organization_id = _organization_id(request, payload.organization_id)
        try:
            target = _require_runtime().webhook_service.save_target(
                SemanticSummaryWebhookTarget(
                    organization_id=effective_organization_id,
                    name=payload.name,
                    url=payload.url,
                    agent_ids=list(payload.agent_ids),
                    channels=list(payload.channels),  # type: ignore[arg-type]
                    signing_secret_ref=payload.signing_secret_ref,
                    extra_headers=dict(payload.extra_headers),
                    timeout_seconds=payload.timeout_seconds,
                    max_retries=payload.max_retries,
                    retry_backoff_seconds=payload.retry_backoff_seconds,
                    is_active=payload.is_active,
                )
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return _webhook_target_view(target)

    @router.put("/intent-tags/webhook-targets/{webhook_target_id}", response_model=SemanticWebhookTargetReadModel)
    def update_semantic_webhook_target(
        webhook_target_id: str,
        payload: SemanticWebhookTargetUpdateRequest,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(write_access),
    ) -> SemanticWebhookTargetReadModel:
        effective_organization_id = _organization_id(request, organization_id)
        existing = _webhook_target(webhook_target_id, organization_id=effective_organization_id)
        updates = payload.model_dump(exclude_unset=True)
        try:
            target = _require_runtime().webhook_service.save_target(existing.model_copy(update=updates))
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return _webhook_target_view(target)

    @router.delete("/intent-tags/webhook-targets/{webhook_target_id}", status_code=204, response_model=None)
    def delete_semantic_webhook_target(
        webhook_target_id: str,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(write_access),
    ) -> None:
        effective_organization_id = _organization_id(request, organization_id)
        _webhook_target(webhook_target_id, organization_id=effective_organization_id)
        _require_runtime().webhook_service.delete_target(webhook_target_id)

    @router.post("/intent-tags/webhooks/dispatch", response_model=SemanticWebhookDispatchResponse)
    def dispatch_semantic_summary_webhooks(
        request: Request,
        organization_id: str | None = None,
        conversation_id: str | None = None,
        mode: Annotated[str, Query(pattern="^(fanout|deliver|both)$")] = "both",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        _: object | None = Depends(write_access),
    ) -> SemanticWebhookDispatchResponse:
        if semantic_webhook_dispatcher is None:
            raise HTTPException(status_code=503, detail="semantic summary webhook dispatcher is not configured")
        effective_organization_id = _organization_id(request, organization_id)
        result = semantic_webhook_dispatcher.run_pending(
            organization_id=effective_organization_id,
            conversation_id=conversation_id,
            limit=limit,
            mode=mode,
        )
        return SemanticWebhookDispatchResponse(
            publication_attempted=result.publication_attempted,
            publication_fanned_out=result.publication_fanned_out,
            publication_skipped=result.publication_skipped,
            publication_failed=result.publication_failed,
            delivery_attempted=result.delivery_attempted,
            delivery_delivered=result.delivery_delivered,
            delivery_failed=result.delivery_failed,
            delivery_retried=result.delivery_retried,
            delivery_skipped=result.delivery_skipped,
        )

    @router.get("/intent-tags/events", response_model=list[TurnClassificationEvent])
    def list_events(
        request: Request,
        organization_id: str | None = None,
        conversation_id: str | None = None,
        intent_name: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        _: object | None = Depends(read_access),
    ) -> list[TurnClassificationEvent]:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().store.list_classification_events(
            effective_organization_id,
            conversation_id=conversation_id,
            intent_name=intent_name,
            limit=limit,
        )

    @router.get("/intent-tags/summaries", response_model=list[SummaryListItemReadModel])
    def list_summaries(
        request: Request,
        organization_id: str | None = None,
        agent_id: str | None = None,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        _: object | None = Depends(read_access),
    ) -> list[SummaryListItemReadModel]:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().read_service.list_summaries(
            effective_organization_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            status=status,
            limit=limit,
        )

    @router.get("/intent-tags/summaries/{conversation_summary_id}", response_model=ConversationSummaryDetailReadModel)
    def get_summary_detail(
        conversation_summary_id: str,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(read_access),
    ) -> ConversationSummaryDetailReadModel:
        effective_organization_id = _organization_id(request, organization_id)
        return _summary(conversation_summary_id, organization_id=effective_organization_id)

    @router.get("/intent-tags/reviews", response_model=list[ReviewQueueRowReadModel])
    def list_reviews(
        request: Request,
        organization_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        review_kind: ReviewKind | None = None,
        claimed_by_user_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        _: object | None = Depends(read_access),
    ) -> list[ReviewQueueRowReadModel]:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().read_service.list_review_queue(
            effective_organization_id,
            agent_id=agent_id,
            status=status,
            review_kind=review_kind,
            claimed_by_user_id=claimed_by_user_id,
            limit=limit,
        )

    @router.post("/intent-tags/reviews/{review_item_id}/claim", response_model=ClassificationReviewItem)
    def claim_review_item(
        review_item_id: str,
        payload: ReviewClaimRequest,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(read_access),
    ) -> ClassificationReviewItem:
        effective_organization_id = _organization_id(request, organization_id)
        _review(review_item_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().review_service.claim_review_item(
                review_item_id,
                user_id=_effective_user_id(request, payload.user_id),
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/intent-tags/reviews/{review_item_id}/resolve-turn", response_model=ClassificationReviewItem)
    def resolve_turn_review(
        review_item_id: str,
        payload: TurnReviewResolutionRequest,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(read_access),
    ) -> ClassificationReviewItem:
        effective_organization_id = _organization_id(request, organization_id)
        _review(review_item_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().review_service.resolve_turn_review(
                review_item_id,
                user_id=_effective_user_id(request, payload.user_id),
                disposition=payload.disposition,
                corrected_decision=payload.corrected_decision,
                review_notes=payload.review_notes,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/intent-tags/reviews/{review_item_id}/resolve-summary", response_model=ClassificationReviewItem)
    def resolve_summary_review(
        review_item_id: str,
        payload: SummaryReviewResolutionRequest,
        request: Request,
        organization_id: str | None = None,
        _: object | None = Depends(read_access),
    ) -> ClassificationReviewItem:
        effective_organization_id = _organization_id(request, organization_id)
        _review(review_item_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().review_service.resolve_summary_review(
                review_item_id,
                user_id=_effective_user_id(request, payload.user_id),
                disposition=payload.disposition,
                corrected_fields=dict(payload.corrected_fields),
                corrected_tag_definition_ids=list(payload.corrected_tag_definition_ids),
                review_notes=payload.review_notes,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/intent-tags/analytics", response_model=IntentTagsAnalyticsReadModel)
    def analytics_snapshot(
        request: Request,
        organization_id: str | None = None,
        agent_id: str | None = None,
        limit: Annotated[int, Query(ge=100, le=10000)] = 2500,
        _: object | None = Depends(read_access),
    ) -> IntentTagsAnalyticsReadModel:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().read_service.analytics_snapshot(
            effective_organization_id,
            agent_id=agent_id,
            limit=limit,
        )

    @router.get("/intent-tags/insights", response_model=IntentTagsInsightsReadModel)
    def semantic_insights_snapshot(
        request: Request,
        organization_id: str | None = None,
        agent_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        _: object | None = Depends(read_access),
    ) -> IntentTagsInsightsReadModel:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().read_service.semantic_insights_snapshot(
            effective_organization_id,
            agent_id=agent_id,
            limit=limit,
        )

    app.include_router(router)
