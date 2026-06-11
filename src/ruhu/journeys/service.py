from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from ruhu.agent_document import AgentDocument

from .analytics import JourneyAnalyticsScope, JourneyAnalyticsService
from .io import export_definition_bundle
from .models import (
    JourneyDefinition,
    JourneyDefinitionReview,
    JourneyDefinitionVersion,
    JourneyEvent,
    JourneyInstance,
    JourneyPublishReadiness,
    JourneyTouchpoint,
)
from .review import build_definition_review, build_publish_readiness, build_review_summary
from .rules import compile_definition_rules
from .schemas import (
    JourneyAbandonmentSweepRequest,
    JourneyAbandonmentSweepResponse,
    JourneyAnalyticsRebuildRequest,
    JourneyAnalyticsRebuildResponse,
    JourneyAnnotationCreate,
    JourneyChannelMixAnalysis,
    JourneyDefinitionBundle,
    JourneyDefinitionCreate,
    JourneyDefinitionImportRequest,
    JourneyDefinitionImportResponse,
    JourneyDefinitionRebuildRequest,
    JourneyDefinitionReplayResponse,
    JourneyDropOffAnalysis,
    JourneyEventListResponse,
    JourneyFunnelAnalysis,
    JourneyInstanceDetail,
    JourneyDefinitionUpdate,
    JourneyDefinitionVersionCreate,
    JourneyDefinitionVersionUpdate,
    JourneyPathAnalysis,
    JourneyReplayFailure,
    JourneyReplayResponse,
    JourneyTouchpointListResponse,
    JourneyTrendAnalysis,
)
from .store import JourneyDefinitionStore, JourneyInstanceStore
from .tracker import JourneyTracker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JourneyServiceError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class _JourneyProjectionSnapshot:
    instance: JourneyInstance
    touchpoints: list[JourneyTouchpoint]
    events: list[JourneyEvent]


class JourneyService:
    def __init__(
        self,
        definition_store: JourneyDefinitionStore,
        instance_store: JourneyInstanceStore | None = None,
        *,
        agent_resolver: Callable[[JourneyDefinition, str | None], tuple[list[AgentDocument], list[str]]] | None = None,
        available_tool_refs_provider: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._definition_store = definition_store
        self._instance_store = instance_store
        self._analytics = None if instance_store is None else JourneyAnalyticsService(instance_store)
        self._agent_resolver = agent_resolver
        self._available_tool_refs_provider = available_tool_refs_provider

    @staticmethod
    def _definition_scope_organization_id(organization_id: str | None) -> str | None:
        # Historically mapped the "public" sentinel to None; now that "public"
        # has been stripped across the codebase, pass through as-is.
        return organization_id

    @staticmethod
    def _runtime_organization_id(organization_id: str | None) -> str | None:
        return organization_id

    def list_definitions(
        self,
        *,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> list[JourneyDefinition]:
        return self._definition_store.list_definitions(
            organization_id=self._definition_scope_organization_id(organization_id),
            status=status,
        )

    def get_definition(
        self,
        definition_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinition:
        definition = self._definition_store.load_definition(
            definition_id,
            organization_id=self._definition_scope_organization_id(organization_id),
        )
        if definition is None:
            raise JourneyServiceError(
                f"unknown journey definition: {definition_id}",
                code="journey.definition.not_found",
                details={"definition_id": definition_id},
            )
        return definition

    def create_definition(
        self,
        payload: JourneyDefinitionCreate,
        *,
        organization_id: str | None,
        created_by_user_id: str | None = None,
    ) -> JourneyDefinition:
        self._ensure_slug_available(payload.slug, organization_id=organization_id)
        now = _utcnow()
        definition = JourneyDefinition(
            organization_id=organization_id,
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            subject_strategy=payload.subject_strategy,
            scope=payload.scope,
            tags=list(payload.tags),
            settings=dict(payload.settings),
            created_by_user_id=created_by_user_id,
            created_at=now,
            updated_at=now,
        )
        self._definition_store.save_definition(definition)
        return self.get_definition(definition.definition_id, organization_id=organization_id)

    def update_definition(
        self,
        definition_id: str,
        payload: JourneyDefinitionUpdate,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinition:
        definition = self.get_definition(definition_id, organization_id=organization_id)
        updates = payload.model_dump(exclude_unset=True)
        next_slug = updates.get("slug", definition.slug)
        if next_slug != definition.slug:
            self._ensure_slug_available(next_slug, organization_id=organization_id, ignore_definition_id=definition_id)
        updated = definition.model_copy(
            update={
                **updates,
                "updated_at": _utcnow(),
            }
        )
        self._definition_store.save_definition(updated)
        return self.get_definition(definition_id, organization_id=organization_id)

    def duplicate_definition(
        self,
        definition_id: str,
        *,
        organization_id: str | None = None,
        created_by_user_id: str | None = None,
    ) -> JourneyDefinition:
        source = self.get_definition(definition_id, organization_id=organization_id)
        now = _utcnow()
        duplicate = JourneyDefinition(
            organization_id=source.organization_id,
            slug=self._next_duplicate_slug(source.slug, organization_id=organization_id),
            name=self._duplicate_name(source.name),
            description=source.description,
            subject_strategy=source.subject_strategy.model_copy(deep=True),
            scope=source.scope.model_copy(deep=True),
            status="active",
            tags=list(source.tags),
            settings=dict(source.settings),
            created_by_user_id=created_by_user_id,
            created_at=now,
            updated_at=now,
        )
        self._definition_store.save_definition(duplicate)

        source_version_id = source.current_draft_version_id or source.current_published_version_id
        if source_version_id is not None:
            source_version = self.get_version(source_version_id, organization_id=organization_id)
            pending_version = JourneyDefinitionVersion(
                organization_id=duplicate.organization_id,
                definition_id=duplicate.definition_id,
                version_number=1,
                status="draft",
                based_on_version_id=None,
                rules=source_version.rules.model_copy(deep=True),
                compiled_rules=compile_definition_rules(source_version.rules),
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
            )
            scoped_agent_documents, missing_agent_ids = self._review_agents(duplicate, organization_id=organization_id)
            duplicate_version = pending_version.model_copy(
                update={
                    "review_summary": build_review_summary(
                        duplicate,
                        pending_version,
                        scoped_agent_documents=scoped_agent_documents,
                        missing_agent_ids=missing_agent_ids,
                        available_tool_refs=self._available_tool_refs(),
                    ),
                }
            )
            self._definition_store.save_version(duplicate_version)
            updated_duplicate = self._definition_store.set_current_draft(
                duplicate.definition_id,
                duplicate_version.definition_version_id,
                organization_id=self._definition_scope_organization_id(organization_id),
            )
            if updated_duplicate is None:
                raise RuntimeError(f"failed to set current draft for journey definition {duplicate.definition_id}")
        return self.get_definition(duplicate.definition_id, organization_id=organization_id)

    def archive_definition(
        self,
        definition_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinition:
        return self.update_definition(
            definition_id,
            JourneyDefinitionUpdate(status="archived"),
            organization_id=organization_id,
        )

    def list_versions(
        self,
        definition_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[JourneyDefinitionVersion]:
        self.get_definition(definition_id, organization_id=organization_id)
        return self._definition_store.list_versions(
            definition_id,
            organization_id=self._definition_scope_organization_id(organization_id),
        )

    def get_version(
        self,
        definition_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinitionVersion:
        version = self._definition_store.load_version(
            definition_version_id,
            organization_id=self._definition_scope_organization_id(organization_id),
        )
        if version is None:
            raise JourneyServiceError(
                f"unknown journey definition version: {definition_version_id}",
                code="journey.definition_version.not_found",
                details={"definition_version_id": definition_version_id},
            )
        return version

    def create_version(
        self,
        definition_id: str,
        payload: JourneyDefinitionVersionCreate,
        *,
        organization_id: str | None = None,
        created_by_user_id: str | None = None,
    ) -> JourneyDefinitionVersion:
        definition = self.get_definition(definition_id, organization_id=organization_id)
        versions = self._definition_store.list_versions(
            definition_id,
            organization_id=self._definition_scope_organization_id(organization_id),
        )
        next_version_number = max((item.version_number for item in versions), default=0) + 1
        based_on_version_id = payload.based_on_version_id
        if based_on_version_id is None:
            based_on_version_id = definition.current_draft_version_id or definition.current_published_version_id
        if based_on_version_id is not None:
            based_on = self.get_version(based_on_version_id, organization_id=organization_id)
            if based_on.definition_id != definition_id:
                raise JourneyServiceError(
                    "based_on_version_id must belong to the same journey definition",
                    code="journey.definition_version.mismatched_definition",
                    details={
                        "definition_id": definition_id,
                        "definition_version_id": based_on_version_id,
                    },
                )

        now = _utcnow()
        pending = JourneyDefinitionVersion(
            organization_id=definition.organization_id,
            definition_id=definition.definition_id,
            version_number=next_version_number,
            status="draft",
            based_on_version_id=based_on_version_id,
            rules=payload.rules,
            compiled_rules=compile_definition_rules(payload.rules),
            created_by_user_id=created_by_user_id,
            created_at=now,
            updated_at=now,
        )
        scoped_agent_documents, missing_agent_ids = self._review_agents(definition, organization_id=organization_id)
        version = pending.model_copy(
            update={
                "review_summary": build_review_summary(
                    definition,
                    pending,
                    scoped_agent_documents=scoped_agent_documents,
                    missing_agent_ids=missing_agent_ids,
                    available_tool_refs=self._available_tool_refs(),
                ),
            }
        )
        self._definition_store.save_version(version)
        updated_definition = self._definition_store.set_current_draft(
            definition.definition_id,
            version.definition_version_id,
            organization_id=self._definition_scope_organization_id(organization_id),
        )
        if updated_definition is None:
            raise RuntimeError(f"failed to set current draft for journey definition {definition.definition_id}")
        return self.get_version(version.definition_version_id, organization_id=organization_id)

    def update_version(
        self,
        definition_version_id: str,
        payload: JourneyDefinitionVersionUpdate,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinitionVersion:
        version = self.get_version(definition_version_id, organization_id=organization_id)
        if version.status != "draft":
            raise JourneyServiceError(
                "published journey definition versions are immutable",
                code="journey.definition_version.read_only",
                details={"definition_version_id": definition_version_id, "status": version.status},
            )
        definition = self.get_definition(version.definition_id, organization_id=organization_id)
        updates: dict[str, object] = {}
        if payload.rules is not None:
            updates["rules"] = payload.rules
        next_rules = payload.rules or version.rules
        pending = version.model_copy(
            update={
                **updates,
                "compiled_rules": compile_definition_rules(next_rules),
                "updated_at": _utcnow(),
            }
        )
        scoped_agent_documents, missing_agent_ids = self._review_agents(definition, organization_id=organization_id)
        updated = pending.model_copy(
            update={
                "review_summary": build_review_summary(
                    definition,
                    pending,
                    scoped_agent_documents=scoped_agent_documents,
                    missing_agent_ids=missing_agent_ids,
                    available_tool_refs=self._available_tool_refs(),
                ),
            }
        )
        self._definition_store.save_version(updated)
        return self.get_version(definition_version_id, organization_id=organization_id)

    def review_definition(
        self,
        definition_id: str,
        *,
        definition_version_id: str | None = None,
        organization_id: str | None = None,
    ) -> JourneyDefinitionReview:
        definition = self.get_definition(definition_id, organization_id=organization_id)
        version_id = definition_version_id or definition.current_draft_version_id or definition.current_published_version_id
        if version_id is None:
            raise JourneyServiceError(
                "journey definition has no version to review",
                code="journey.definition.no_versions",
                details={"definition_id": definition_id},
            )
        version = self.get_version(version_id, organization_id=organization_id)
        if version.definition_id != definition.definition_id:
            raise JourneyServiceError(
                "journey definition version does not belong to the supplied definition",
                code="journey.definition_version.mismatched_definition",
                details={"definition_id": definition_id, "definition_version_id": version_id},
            )
        scoped_agent_documents, missing_agent_ids = self._review_agents(definition, organization_id=organization_id)
        return build_definition_review(
            definition,
            version,
            scoped_agent_documents=scoped_agent_documents,
            missing_agent_ids=missing_agent_ids,
            available_tool_refs=self._available_tool_refs(),
        )

    def build_publish_readiness(
        self,
        definition_id: str,
        *,
        definition_version_id: str | None = None,
        organization_id: str | None = None,
    ) -> JourneyPublishReadiness:
        definition = self.get_definition(definition_id, organization_id=organization_id)
        draft_version: JourneyDefinitionVersion | None = None
        if definition_version_id is not None:
            candidate = self.get_version(definition_version_id, organization_id=organization_id)
            if candidate.definition_id != definition.definition_id:
                raise JourneyServiceError(
                    "journey definition version does not belong to the supplied definition",
                    code="journey.definition_version.mismatched_definition",
                    details={"definition_id": definition_id, "definition_version_id": definition_version_id},
                )
            if candidate.status != "draft":
                raise JourneyServiceError(
                    "only draft journey definition versions can be reviewed for publish",
                    code="journey.definition_version.not_draft",
                    details={"definition_version_id": definition_version_id, "status": candidate.status},
                )
            draft_version = candidate
        elif definition.current_draft_version_id is not None:
            draft_version = self.get_version(definition.current_draft_version_id, organization_id=organization_id)

        published_version: JourneyDefinitionVersion | None = None
        if definition.current_published_version_id is not None:
            published_version = self.get_version(definition.current_published_version_id, organization_id=organization_id)

        scoped_agent_documents, missing_agent_ids = self._review_agents(definition, organization_id=organization_id)
        return build_publish_readiness(
            definition,
            draft_version=draft_version,
            published_version=published_version,
            scoped_agent_documents=scoped_agent_documents,
            missing_agent_ids=missing_agent_ids,
            available_tool_refs=self._available_tool_refs(),
        )

    def publish_definition(
        self,
        definition_id: str,
        *,
        definition_version_id: str | None = None,
        organization_id: str | None = None,
    ) -> JourneyDefinitionVersion:
        definition = self.get_definition(definition_id, organization_id=organization_id)
        target_version_id = definition_version_id or definition.current_draft_version_id
        if target_version_id is None:
            raise JourneyServiceError(
                "journey definition has no draft version to publish",
                code="journey.definition.no_draft",
                details={"definition_id": definition_id},
            )
        version = self.get_version(target_version_id, organization_id=organization_id)
        if version.definition_id != definition.definition_id:
            raise JourneyServiceError(
                "journey definition version does not belong to the supplied definition",
                code="journey.definition_version.mismatched_definition",
                details={"definition_id": definition_id, "definition_version_id": target_version_id},
            )
        if version.status != "draft":
            raise JourneyServiceError(
                "only draft journey definition versions can be published",
                code="journey.definition_version.not_draft",
                details={"definition_version_id": target_version_id, "status": version.status},
            )

        readiness = self.build_publish_readiness(
            definition_id,
            definition_version_id=target_version_id,
            organization_id=organization_id,
        )
        if readiness.blockers:
            raise JourneyServiceError(
                "journey definition publish blocked by review errors",
                code="journey.definition.publish_blocked",
                details={
                    "definition_id": definition_id,
                    "definition_version_id": target_version_id,
                    "blockers": [item.model_dump(mode="json") for item in readiness.blockers],
                },
            )

        scoped_agent_documents, missing_agent_ids = self._review_agents(definition, organization_id=organization_id)
        refreshed = version.model_copy(
            update={
                "compiled_rules": compile_definition_rules(version.rules),
                "review_summary": build_review_summary(
                    definition,
                    version,
                    scoped_agent_documents=scoped_agent_documents,
                    missing_agent_ids=missing_agent_ids,
                    available_tool_refs=self._available_tool_refs(),
                ),
                "updated_at": _utcnow(),
            }
        )
        self._definition_store.save_version(refreshed)
        published = self._definition_store.publish_version(
            definition_id,
            target_version_id,
            organization_id=self._definition_scope_organization_id(organization_id),
        )
        if published is None:
            raise RuntimeError(
                f"failed to publish journey definition version {target_version_id} for {definition_id}"
            )
        return self.get_version(published.definition_version_id, organization_id=organization_id)

    def export_definitions(
        self,
        *,
        organization_id: str | None = None,
        definition_ids: list[str] | None = None,
    ) -> JourneyDefinitionBundle:
        definitions = self.list_definitions(organization_id=organization_id)
        if definition_ids:
            wanted = set(definition_ids)
            definitions = [definition for definition in definitions if definition.definition_id in wanted]
        versions_by_definition_id = {
            definition.definition_id: self.list_versions(
                definition.definition_id,
                organization_id=organization_id,
            )
            for definition in definitions
        }
        return export_definition_bundle(definitions, versions_by_definition_id)

    def import_definitions(
        self,
        payload: JourneyDefinitionImportRequest,
        *,
        organization_id: str | None,
        created_by_user_id: str | None = None,
    ) -> JourneyDefinitionImportResponse:
        imported_definition_ids: list[str] = []
        imported_version_ids: list[str] = []
        for entry in payload.bundle.definitions:
            source_definition = entry.definition.model_copy(deep=True)
            source_versions = sorted(
                (version.model_copy(deep=True) for version in entry.versions),
                key=lambda version: (version.version_number, version.definition_version_id),
            )
            new_definition_id = source_definition.definition_id if payload.preserve_ids else str(uuid4())
            slug = self._resolve_import_slug(
                source_definition.slug,
                organization_id=organization_id,
                preserve_ids=payload.preserve_ids,
                definition_id=new_definition_id,
            )
            version_id_map = {
                version.definition_version_id: (
                    version.definition_version_id if payload.preserve_ids else str(uuid4())
                )
                for version in source_versions
            }
            imported_definition = source_definition.model_copy(
                update={
                    "definition_id": new_definition_id,
                    "organization_id": organization_id,
                    "slug": slug,
                    "current_draft_version_id": (
                        None
                        if source_definition.current_draft_version_id is None
                        else version_id_map.get(source_definition.current_draft_version_id)
                    ),
                    "current_published_version_id": (
                        None
                        if source_definition.current_published_version_id is None
                        else version_id_map.get(source_definition.current_published_version_id)
                    ),
                    "created_by_user_id": created_by_user_id or source_definition.created_by_user_id,
                    "updated_at": _utcnow(),
                }
            )
            scoped_agent_documents, missing_agent_ids = self._review_agents(
                imported_definition,
                organization_id=organization_id,
            )
            imported_versions: list[JourneyDefinitionVersion] = []
            for source_version in source_versions:
                pending_version = source_version.model_copy(
                    update={
                        "definition_version_id": version_id_map[source_version.definition_version_id],
                        "organization_id": organization_id,
                        "definition_id": imported_definition.definition_id,
                        "based_on_version_id": (
                            None
                            if source_version.based_on_version_id is None
                            else version_id_map.get(source_version.based_on_version_id)
                        ),
                        "compiled_rules": compile_definition_rules(source_version.rules),
                        "created_by_user_id": created_by_user_id or source_version.created_by_user_id,
                        "updated_at": _utcnow(),
                    }
                )
                imported_versions.append(
                    pending_version.model_copy(
                        update={
                            "review_summary": build_review_summary(
                                imported_definition,
                                pending_version,
                                scoped_agent_documents=scoped_agent_documents,
                                missing_agent_ids=missing_agent_ids,
                                available_tool_refs=self._available_tool_refs(),
                            ),
                        }
                    )
                )
            self._definition_store.save_definition(imported_definition)
            for version in imported_versions:
                self._definition_store.save_version(version)
                imported_version_ids.append(version.definition_version_id)
            imported_definition_ids.append(imported_definition.definition_id)
        return JourneyDefinitionImportResponse(
            imported_definition_ids=imported_definition_ids,
            imported_version_ids=imported_version_ids,
        )

    def _ensure_slug_available(
        self,
        slug: str,
        *,
        organization_id: str | None,
        ignore_definition_id: str | None = None,
    ) -> None:
        for definition in self._definition_store.list_definitions(
            organization_id=self._definition_scope_organization_id(organization_id)
        ):
            # Enterprise posture: both definitions and instances live under the same tenant.
            if definition.slug != slug:
                continue
            if ignore_definition_id is not None and definition.definition_id == ignore_definition_id:
                continue
            raise JourneyServiceError(
                f"journey definition slug already exists: {slug}",
                code="journey.definition.slug_conflict",
                details={"slug": slug},
            )

    def _next_duplicate_slug(self, slug: str, *, organization_id: str | None) -> str:
        base_slug = f"{slug}-copy"
        candidate = base_slug
        suffix = 2
        while self._slug_exists(candidate, organization_id=organization_id):
            candidate = f"{base_slug}-{suffix}"
            suffix += 1
        return candidate

    def _slug_exists(self, slug: str, *, organization_id: str | None) -> bool:
        try:
            self._ensure_slug_available(slug, organization_id=organization_id)
        except JourneyServiceError:
            return True
        return False

    def _duplicate_name(self, name: str) -> str:
        stripped = name.strip()
        if stripped.endswith(" Copy"):
            return f"{stripped} 2"
        return f"{stripped} Copy"

    def _review_agents(
        self,
        definition: JourneyDefinition,
        *,
        organization_id: str | None,
    ) -> tuple[list[AgentDocument], list[str]]:
        if self._agent_resolver is None:
            return [], []
        return self._agent_resolver(
            definition,
            self._definition_scope_organization_id(organization_id),
        )

    def _available_tool_refs(self) -> list[str] | None:
        if self._available_tool_refs_provider is None:
            return None
        return list(self._available_tool_refs_provider())

    def _instance_store_or_error(self) -> JourneyInstanceStore:
        if self._instance_store is None:
            raise JourneyServiceError(
                "journey instance store unavailable",
                code="journey.instance_store.unavailable",
            )
        return self._instance_store

    def _analytics_or_error(self) -> JourneyAnalyticsService:
        if self._analytics is None:
            raise JourneyServiceError(
                "journey analytics unavailable",
                code="journey.analytics.unavailable",
            )
        return self._analytics

    def list_instances(
        self,
        *,
        organization_id: str,
        definition_id: str | None = None,
        status: str | None = None,
        outcome: str | None = None,
        subject_key: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        channel: str | None = None,
        agent_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[JourneyInstance], int]:
        store = self._instance_store_or_error()
        items = store.list_instances(
            organization_id=organization_id,
            definition_id=definition_id,
            status=status,
            subject_key=subject_key,
        )
        filtered: list[JourneyInstance] = []
        for item in items:
            if outcome is not None and item.outcome != outcome:
                continue
            if started_after is not None and item.started_at < started_after:
                continue
            if started_before is not None and item.started_at > started_before:
                continue
            if channel is not None or agent_id is not None:
                touchpoints = store.list_touchpoints(item.journey_id, organization_id=organization_id)
                if channel is not None and not any(touchpoint.channel == channel for touchpoint in touchpoints):
                    continue
                if agent_id is not None and agent_id not in {item.first_agent_id, item.latest_agent_id}:
                    if not any(touchpoint.agent_id == agent_id for touchpoint in touchpoints):
                        continue
            filtered.append(item)
        total_count = len(filtered)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        start = (page - 1) * page_size
        return filtered[start : start + page_size], total_count

    def get_instance(
        self,
        journey_id: str,
        *,
        organization_id: str,
    ) -> JourneyInstance:
        store = self._instance_store_or_error()
        instance = store.load_instance(journey_id, organization_id=organization_id)
        if instance is None:
            raise JourneyServiceError(
                f"unknown journey instance: {journey_id}",
                code="journey.instance.not_found",
                details={"journey_id": journey_id},
            )
        return instance

    def list_touchpoints(
        self,
        journey_id: str,
        *,
        organization_id: str,
    ) -> JourneyTouchpointListResponse:
        store = self._instance_store_or_error()
        instance = self.get_instance(journey_id, organization_id=organization_id)
        return JourneyTouchpointListResponse(
            touchpoints=store.list_touchpoints(instance.journey_id, organization_id=organization_id)
        )

    def list_events(
        self,
        journey_id: str,
        *,
        organization_id: str,
    ) -> JourneyEventListResponse:
        store = self._instance_store_or_error()
        instance = self.get_instance(journey_id, organization_id=organization_id)
        return JourneyEventListResponse(
            events=store.list_events(instance.journey_id, organization_id=organization_id)
        )

    def get_instance_detail(
        self,
        journey_id: str,
        *,
        organization_id: str,
    ) -> JourneyInstanceDetail:
        store = self._instance_store_or_error()
        instance = self.get_instance(journey_id, organization_id=organization_id)
        definition = self.get_definition(instance.definition_id, organization_id=organization_id)
        version = self.get_version(instance.definition_version_id, organization_id=organization_id)
        return JourneyInstanceDetail(
            instance=instance,
            definition=definition,
            version=version,
            touchpoints=store.list_touchpoints(instance.journey_id, organization_id=organization_id),
            events=store.list_events(instance.journey_id, organization_id=organization_id),
        )

    def annotate_instance(
        self,
        journey_id: str,
        payload: JourneyAnnotationCreate,
        *,
        organization_id: str,
        actor_user_id: str | None = None,
    ) -> JourneyEvent:
        store = self._instance_store_or_error()
        instance = self.get_instance(journey_id, organization_id=organization_id)
        now = _utcnow()
        event = JourneyEvent(
            organization_id=organization_id,
            journey_id=journey_id,
            event_type="manual_annotation",
            source="manual",
            idempotency_key=f"{journey_id}:annotation:{uuid4().hex}",
            payload={
                "note": payload.note,
                "label": payload.label,
                "metadata": dict(payload.metadata),
                "actor_user_id": actor_user_id,
            },
            occurred_at=now,
            created_at=now,
        )
        store.append_events([event])
        instance.last_activity_at = now
        instance.updated_at = now
        store.save_instance(instance)
        return event

    def replay_journey(
        self,
        journey_id: str,
        *,
        organization_id: str,
        tracker: JourneyTracker,
        preserve_manual_events: bool = True,
    ) -> JourneyReplayResponse:
        store = self._instance_store_or_error()
        snapshot = self._snapshot_projection(journey_id, organization_id=organization_id)
        competing_open_instances = [
            item.journey_id
            for item in store.list_instances(
                organization_id=organization_id,
                definition_id=snapshot.instance.definition_id,
                status="open",
                subject_key=snapshot.instance.subject_key,
            )
            if item.journey_id != journey_id
        ]
        if competing_open_instances:
            raise JourneyServiceError(
                "journey replay is ambiguous while another open journey exists for the same subject",
                code="journey.replay.ambiguous_open_subject",
                details={
                    "journey_id": journey_id,
                    "definition_id": snapshot.instance.definition_id,
                    "subject_key": snapshot.instance.subject_key,
                    "competing_journey_ids": competing_open_instances,
                },
            )

        conversation_ids = self._projection_conversation_ids(snapshot)
        if not conversation_ids:
            raise JourneyServiceError(
                "journey replay requires at least one recorded conversation",
                code="journey.replay.no_conversations",
                details={"journey_id": journey_id},
            )

        definition = self.get_definition(snapshot.instance.definition_id, organization_id=organization_id)
        version = self.get_version(snapshot.instance.definition_version_id, organization_id=organization_id)
        preserved_events = (
            self._preservable_events(snapshot.events)
            if preserve_manual_events
            else []
        )

        store.delete_instance(journey_id, organization_id=organization_id)
        try:
            emitted = tracker.replay_definition_conversations(
                definition,
                version,
                conversation_ids,
                organization_id=self._runtime_organization_id(organization_id),
                journey_id_override=journey_id,
            )
            rebuilt = store.load_instance(journey_id, organization_id=organization_id)
            if rebuilt is None:
                raise JourneyServiceError(
                    "journey replay produced no projection",
                    code="journey.replay.empty_projection",
                    details={
                        "journey_id": journey_id,
                        "definition_id": snapshot.instance.definition_id,
                        "definition_version_id": snapshot.instance.definition_version_id,
                    },
                )
            if (
                rebuilt.definition_id != snapshot.instance.definition_id
                or rebuilt.definition_version_id != snapshot.instance.definition_version_id
                or rebuilt.subject_key != snapshot.instance.subject_key
            ):
                raise JourneyServiceError(
                    "journey replay changed the projection identity",
                    code="journey.replay.identity_mismatch",
                    details={
                        "journey_id": journey_id,
                        "definition_id": snapshot.instance.definition_id,
                        "definition_version_id": snapshot.instance.definition_version_id,
                        "subject_key": snapshot.instance.subject_key,
                    },
                )
            competing_replayed_instances = [
                item.journey_id
                for item in store.list_instances(
                    organization_id=organization_id,
                    definition_id=snapshot.instance.definition_id,
                    subject_key=snapshot.instance.subject_key,
                )
                if item.journey_id != journey_id
            ]
            if competing_replayed_instances:
                raise JourneyServiceError(
                    "journey replay produced additional journey projections for the same subject",
                    code="journey.replay.extra_projections",
                    details={
                        "journey_id": journey_id,
                        "definition_id": snapshot.instance.definition_id,
                        "subject_key": snapshot.instance.subject_key,
                        "competing_journey_ids": competing_replayed_instances,
                    },
                )
            if preserved_events:
                store.append_events(preserved_events)
                rebuilt.last_activity_at = max(
                    rebuilt.last_activity_at,
                    max(event.occurred_at for event in preserved_events),
                )
                rebuilt.updated_at = max(
                    rebuilt.updated_at,
                    max(event.created_at for event in preserved_events),
                )
                store.save_instance(rebuilt)
        except JourneyServiceError:
            self._restore_projection(snapshot, organization_id=organization_id)
            raise
        except Exception as exc:
            self._restore_projection(snapshot, organization_id=organization_id)
            raise JourneyServiceError(
                "journey replay failed",
                code="journey.replay.failed",
                details={"journey_id": journey_id},
            ) from exc

        return JourneyReplayResponse(
            journey_id=journey_id,
            definition_id=snapshot.instance.definition_id,
            definition_version_id=snapshot.instance.definition_version_id,
            conversation_ids=conversation_ids,
            emitted_event_count=len(emitted),
            preserved_event_count=len(preserved_events),
        )

    def replay_definition(
        self,
        definition_id: str,
        *,
        organization_id: str,
        tracker: JourneyTracker,
        preserve_manual_events: bool = True,
    ) -> JourneyDefinitionReplayResponse:
        self.get_definition(definition_id, organization_id=organization_id)
        store = self._instance_store_or_error()
        journey_ids = [
            item.journey_id
            for item in store.list_instances(
                organization_id=organization_id,
                definition_id=definition_id,
            )
        ]
        replayed_journey_ids: list[str] = []
        failures: list[JourneyReplayFailure] = []
        emitted_event_count = 0
        preserved_event_count = 0
        for journey_id in journey_ids:
            try:
                replay = self.replay_journey(
                    journey_id,
                    organization_id=organization_id,
                    tracker=tracker,
                    preserve_manual_events=preserve_manual_events,
                )
            except JourneyServiceError as exc:
                failures.append(
                    JourneyReplayFailure(
                        journey_id=journey_id,
                        code=exc.code,
                        message=str(exc),
                    )
                )
                continue
            replayed_journey_ids.append(replay.journey_id)
            emitted_event_count += replay.emitted_event_count
            preserved_event_count += replay.preserved_event_count
        return JourneyDefinitionReplayResponse(
            definition_id=definition_id,
            total_candidates=len(journey_ids),
            replayed_journey_ids=replayed_journey_ids,
            failures=failures,
            emitted_event_count=emitted_event_count,
            preserved_event_count=preserved_event_count,
        )

    def rebuild_definition(
        self,
        definition_id: str,
        payload: JourneyDefinitionRebuildRequest,
        *,
        organization_id: str,
        tracker: JourneyTracker,
    ) -> JourneyDefinitionReplayResponse:
        definition = self.get_definition(definition_id, organization_id=organization_id)
        version_id = (
            payload.definition_version_id
            or definition.current_published_version_id
            or definition.current_draft_version_id
        )
        if version_id is None:
            raise JourneyServiceError(
                "journey definition has no version to rebuild",
                code="journey.definition.no_versions",
                details={"definition_id": definition_id},
            )
        version = self.get_version(version_id, organization_id=organization_id)
        if version.definition_id != definition.definition_id:
            raise JourneyServiceError(
                "journey definition version does not belong to the supplied definition",
                code="journey.definition_version.mismatched_definition",
                details={"definition_id": definition_id, "definition_version_id": version_id},
            )

        discovered = tracker.discover_definition_conversations(
            definition,
            organization_id=self._runtime_organization_id(organization_id),
        )
        replayed_journey_ids: list[str] = []
        failures: list[JourneyReplayFailure] = []
        emitted_event_count = 0
        preserved_event_count = 0

        for subject_key, conversation_ids in sorted(discovered.items()):
            try:
                rebuilt_journey_ids, emitted_count, preserved_count = self._rebuild_subject_projection(
                    definition=definition,
                    version=version,
                    subject_key=subject_key,
                    conversation_ids=conversation_ids,
                    organization_id=organization_id,
                    tracker=tracker,
                    preserve_manual_events=payload.preserve_manual_events,
                )
            except JourneyServiceError as exc:
                failures.append(
                    JourneyReplayFailure(
                        journey_id=f"subject:{subject_key}",
                        code=exc.code,
                        message=str(exc),
                    )
                )
                continue
            replayed_journey_ids.extend(rebuilt_journey_ids)
            emitted_event_count += emitted_count
            preserved_event_count += preserved_count

        return JourneyDefinitionReplayResponse(
            definition_id=definition_id,
            total_candidates=len(discovered),
            replayed_journey_ids=replayed_journey_ids,
            failures=failures,
            emitted_event_count=emitted_event_count,
            preserved_event_count=preserved_event_count,
            discovered_conversation_count=sum(len(ids) for ids in discovered.values()),
            discovered_subject_count=len(discovered),
        )

    def sweep_abandonment(
        self,
        payload: JourneyAbandonmentSweepRequest,
        *,
        organization_id: str,
    ) -> JourneyAbandonmentSweepResponse:
        store = self._instance_store_or_error()
        abandoned_journey_ids: list[str] = []
        definitions = {
            definition.definition_id: definition
            for definition in self.list_definitions(organization_id=organization_id, status="active")
        }
        now = _utcnow()
        for instance in store.list_instances(
            organization_id=organization_id,
            definition_id=payload.definition_id,
            status="open",
        ):
            definition = definitions.get(instance.definition_id)
            if definition is None:
                continue
            version = self._definition_store.load_version(
                instance.definition_version_id,
                organization_id=definition.organization_id,
            )
            if version is None:
                continue
            policy = version.rules.abandonment_policy
            if policy.inactive_after_seconds is None:
                continue
            inactive_seconds = (now - instance.last_activity_at).total_seconds()
            if inactive_seconds < policy.inactive_after_seconds:
                continue
            events = self._build_abandonment_events(
                instance=instance,
                occurred_at=now,
                outcome=policy.close_as,
                idempotency_suffix=f"abandonment_sweep:{int(now.timestamp())}",
                payload={
                    "reason": "inactive_timeout",
                    "inactive_after_seconds": policy.inactive_after_seconds,
                    "inactive_for_seconds": max(0, int(inactive_seconds)),
                },
            )
            store.append_events(events)
            instance.status = policy.close_as  # type: ignore[assignment]
            instance.outcome = policy.close_as
            instance.ended_at = now
            instance.last_activity_at = now
            instance.updated_at = now
            store.save_instance(instance)
            abandoned_journey_ids.append(instance.journey_id)
        return JourneyAbandonmentSweepResponse(
            definition_id=payload.definition_id,
            abandoned_journey_ids=abandoned_journey_ids,
        )

    def rebuild_analytics(
        self,
        payload: JourneyAnalyticsRebuildRequest,
        *,
        organization_id: str,
    ) -> JourneyAnalyticsRebuildResponse:
        effective_definition_id = payload.definition_id
        if payload.definition_version_id is not None:
            version = self.get_version(payload.definition_version_id, organization_id=organization_id)
            if effective_definition_id is not None and version.definition_id != effective_definition_id:
                raise JourneyServiceError(
                    "journey definition version does not belong to the supplied definition",
                    code="journey.definition_version.mismatched_definition",
                    details={
                        "definition_id": effective_definition_id,
                        "definition_version_id": payload.definition_version_id,
                    },
                )
            effective_definition_id = version.definition_id

        rebuilt_views: list[str] = []
        if effective_definition_id is not None:
            self.analytics_funnel(
                organization_id=organization_id,
                definition_id=effective_definition_id,
                definition_version_id=payload.definition_version_id,
                period_start=payload.period_start,
                period_end=payload.period_end,
                channel=payload.channel,
                agent_id=payload.agent_id,
            )
            rebuilt_views.append("funnel")
            self.analytics_drop_off(
                organization_id=organization_id,
                definition_id=effective_definition_id,
                definition_version_id=payload.definition_version_id,
                period_start=payload.period_start,
                period_end=payload.period_end,
                channel=payload.channel,
                agent_id=payload.agent_id,
            )
            rebuilt_views.append("drop_off")
            self.analytics_paths(
                organization_id=organization_id,
                definition_id=effective_definition_id,
                definition_version_id=payload.definition_version_id,
                period_start=payload.period_start,
                period_end=payload.period_end,
                channel=payload.channel,
                agent_id=payload.agent_id,
            )
            rebuilt_views.append("paths")

        self.analytics_trends(
            organization_id=organization_id,
            definition_id=effective_definition_id,
            definition_version_id=payload.definition_version_id,
            period_start=payload.period_start,
            period_end=payload.period_end,
            granularity=payload.granularity,
            channel=payload.channel,
            agent_id=payload.agent_id,
        )
        rebuilt_views.append("trends")
        self.analytics_channel_mix(
            organization_id=organization_id,
            definition_id=effective_definition_id,
            definition_version_id=payload.definition_version_id,
            period_start=payload.period_start,
            period_end=payload.period_end,
            channel=payload.channel,
            agent_id=payload.agent_id,
        )
        rebuilt_views.append("channel_mix")
        return JourneyAnalyticsRebuildResponse(
            definition_id=effective_definition_id,
            definition_version_id=payload.definition_version_id,
            period_start=payload.period_start,
            period_end=payload.period_end,
            rebuilt_views=rebuilt_views,
            snapshot_count=len(rebuilt_views),
        )

    def analytics_funnel(
        self,
        *,
        organization_id: str,
        definition_id: str,
        definition_version_id: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        channel: str | None = None,
        agent_id: str | None = None,
    ) -> JourneyFunnelAnalysis:
        version = self._analytics_definition_version(
            definition_id=definition_id,
            definition_version_id=definition_version_id,
            organization_id=organization_id,
        )
        return self._analytics_or_error().funnel(
            scope=JourneyAnalyticsScope(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=version.definition_version_id,
                period_start=period_start,
                period_end=period_end,
                channel=channel,
                agent_id=agent_id,
            ),
            definition_version=version,
        )

    def analytics_drop_off(
        self,
        *,
        organization_id: str,
        definition_id: str,
        definition_version_id: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        channel: str | None = None,
        agent_id: str | None = None,
    ) -> JourneyDropOffAnalysis:
        version = self._analytics_definition_version(
            definition_id=definition_id,
            definition_version_id=definition_version_id,
            organization_id=organization_id,
        )
        return self._analytics_or_error().drop_off(
            scope=JourneyAnalyticsScope(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=version.definition_version_id,
                period_start=period_start,
                period_end=period_end,
                channel=channel,
                agent_id=agent_id,
            ),
            definition_version=version,
        )

    def analytics_paths(
        self,
        *,
        organization_id: str,
        definition_id: str,
        definition_version_id: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        channel: str | None = None,
        agent_id: str | None = None,
    ) -> JourneyPathAnalysis:
        version = self._analytics_definition_version(
            definition_id=definition_id,
            definition_version_id=definition_version_id,
            organization_id=organization_id,
        )
        return self._analytics_or_error().paths(
            scope=JourneyAnalyticsScope(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=version.definition_version_id,
                period_start=period_start,
                period_end=period_end,
                channel=channel,
                agent_id=agent_id,
            ),
            definition_version=version,
        )

    def analytics_trends(
        self,
        *,
        organization_id: str,
        definition_id: str | None = None,
        definition_version_id: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        granularity: str = "day",
        channel: str | None = None,
        agent_id: str | None = None,
    ) -> JourneyTrendAnalysis:
        return self._analytics_or_error().trends(
            scope=JourneyAnalyticsScope(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=definition_version_id,
                period_start=period_start,
                period_end=period_end,
                granularity=granularity,
                channel=channel,
                agent_id=agent_id,
            ),
        )

    def analytics_channel_mix(
        self,
        *,
        organization_id: str,
        definition_id: str | None = None,
        definition_version_id: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        channel: str | None = None,
        agent_id: str | None = None,
    ) -> JourneyChannelMixAnalysis:
        return self._analytics_or_error().channel_mix(
            scope=JourneyAnalyticsScope(
                organization_id=organization_id,
                definition_id=definition_id,
                definition_version_id=definition_version_id,
                period_start=period_start,
                period_end=period_end,
                channel=channel,
                agent_id=agent_id,
            ),
        )

    def _analytics_definition_version(
        self,
        *,
        definition_id: str,
        definition_version_id: str | None,
        organization_id: str,
    ) -> JourneyDefinitionVersion:
        if definition_version_id is not None:
            version = self.get_version(definition_version_id, organization_id=organization_id)
            if version.definition_id != definition_id:
                raise JourneyServiceError(
                    "journey definition version does not belong to the supplied definition",
                    code="journey.definition_version.mismatched_definition",
                    details={"definition_id": definition_id, "definition_version_id": definition_version_id},
                )
            return version
        definition = self.get_definition(definition_id, organization_id=organization_id)
        version_id = definition.current_published_version_id or definition.current_draft_version_id
        if version_id is None:
            raise JourneyServiceError(
                "journey definition has no version for analytics",
                code="journey.definition.no_versions",
                details={"definition_id": definition_id},
            )
        return self.get_version(version_id, organization_id=organization_id)

    def _resolve_import_slug(
        self,
        slug: str,
        *,
        organization_id: str | None,
        preserve_ids: bool,
        definition_id: str,
    ) -> str:
        definitions = self._definition_store.list_definitions(
            organization_id=self._definition_scope_organization_id(organization_id)
        )
        conflicts = [
            definition
            for definition in definitions
            if definition.slug == slug and definition.definition_id != definition_id
        ]
        if not conflicts:
            return slug
        if preserve_ids:
            raise JourneyServiceError(
                f"journey definition slug already exists: {slug}",
                code="journey.definition.slug_conflict",
                details={"slug": slug},
            )
        candidate = f"{slug}-import"
        suffix = 2
        used = {definition.slug for definition in definitions}
        while candidate in used:
            candidate = f"{slug}-import-{suffix}"
            suffix += 1
        return candidate

    def _rebuild_subject_projection(
        self,
        *,
        definition: JourneyDefinition,
        version: JourneyDefinitionVersion,
        subject_key: str,
        conversation_ids: list[str],
        organization_id: str,
        tracker: JourneyTracker,
        preserve_manual_events: bool,
    ) -> tuple[list[str], int, int]:
        store = self._instance_store_or_error()
        snapshots = [
            self._snapshot_projection(instance.journey_id, organization_id=organization_id)
            for instance in store.list_instances(
                organization_id=organization_id,
                definition_id=definition.definition_id,
                subject_key=subject_key,
            )
        ]
        for snapshot in snapshots:
            store.delete_instance(snapshot.instance.journey_id, organization_id=organization_id)
        try:
            emitted = tracker.replay_definition_conversations(
                definition,
                version,
                conversation_ids,
                organization_id=self._runtime_organization_id(organization_id),
            )
            rebuilt_instances = store.list_instances(
                organization_id=organization_id,
                definition_id=definition.definition_id,
                subject_key=subject_key,
            )
            preserved_count = 0
            if preserve_manual_events and snapshots:
                preserved_count = self._restore_preserved_subject_events(
                    previous_snapshots=snapshots,
                    rebuilt_instances=rebuilt_instances,
                    organization_id=organization_id,
                )
            return (
                [instance.journey_id for instance in rebuilt_instances],
                len(emitted),
                preserved_count,
            )
        except JourneyServiceError:
            self._restore_subject_projection(
                definition_id=definition.definition_id,
                subject_key=subject_key,
                snapshots=snapshots,
                organization_id=organization_id,
            )
            raise
        except Exception as exc:
            self._restore_subject_projection(
                definition_id=definition.definition_id,
                subject_key=subject_key,
                snapshots=snapshots,
                organization_id=organization_id,
            )
            raise JourneyServiceError(
                "journey definition rebuild failed",
                code="journey.definition.rebuild.failed",
                details={"definition_id": definition.definition_id, "subject_key": subject_key},
            ) from exc

    def _restore_subject_projection(
        self,
        *,
        definition_id: str,
        subject_key: str,
        snapshots: list[_JourneyProjectionSnapshot],
        organization_id: str,
    ) -> None:
        store = self._instance_store_or_error()
        for instance in store.list_instances(
            organization_id=organization_id,
            definition_id=definition_id,
            subject_key=subject_key,
        ):
            store.delete_instance(instance.journey_id, organization_id=organization_id)
        for snapshot in snapshots:
            self._restore_projection(snapshot, organization_id=organization_id)

    def _restore_preserved_subject_events(
        self,
        *,
        previous_snapshots: list[_JourneyProjectionSnapshot],
        rebuilt_instances: list[JourneyInstance],
        organization_id: str,
    ) -> int:
        store = self._instance_store_or_error()
        rebuilt_snapshots = [
            _JourneyProjectionSnapshot(
                instance=instance,
                touchpoints=store.list_touchpoints(instance.journey_id, organization_id=organization_id),
                events=store.list_events(instance.journey_id, organization_id=organization_id),
            )
            for instance in rebuilt_instances
        ]
        matched_pairs = self._match_snapshots_to_rebuilt_instances(previous_snapshots, rebuilt_snapshots)
        restored_events: list[JourneyEvent] = []
        last_activity_by_journey: dict[str, datetime] = {}
        updated_at_by_journey: dict[str, datetime] = {}
        matched_old_ids = {old.instance.journey_id for old, _ in matched_pairs}
        for old_snapshot in previous_snapshots:
            preservable = self._preservable_events(old_snapshot.events)
            if preservable and old_snapshot.instance.journey_id not in matched_old_ids:
                raise JourneyServiceError(
                    "journey definition rebuild could not safely preserve manual/import events",
                    code="journey.definition.rebuild.unmatched_preserved_events",
                    details={"journey_id": old_snapshot.instance.journey_id},
                )
        for old_snapshot, new_snapshot in matched_pairs:
            for event in self._preservable_events(old_snapshot.events):
                restored_events.append(
                    event.model_copy(
                        update={
                            "journey_id": new_snapshot.instance.journey_id,
                            "idempotency_key": f"{new_snapshot.instance.journey_id}:preserved:{event.journey_event_id}",
                        }
                    )
                )
                last_activity_by_journey[new_snapshot.instance.journey_id] = max(
                    last_activity_by_journey.get(new_snapshot.instance.journey_id, new_snapshot.instance.last_activity_at),
                    event.occurred_at,
                )
                updated_at_by_journey[new_snapshot.instance.journey_id] = max(
                    updated_at_by_journey.get(new_snapshot.instance.journey_id, new_snapshot.instance.updated_at),
                    event.created_at,
                )
        if restored_events:
            store.append_events(restored_events)
        for snapshot in rebuilt_snapshots:
            if snapshot.instance.journey_id not in last_activity_by_journey:
                continue
            snapshot.instance.last_activity_at = last_activity_by_journey[snapshot.instance.journey_id]
            snapshot.instance.updated_at = updated_at_by_journey[snapshot.instance.journey_id]
            store.save_instance(snapshot.instance)
        return len(restored_events)

    @staticmethod
    def _match_snapshots_to_rebuilt_instances(
        previous_snapshots: list[_JourneyProjectionSnapshot],
        rebuilt_snapshots: list[_JourneyProjectionSnapshot],
    ) -> list[tuple[_JourneyProjectionSnapshot, _JourneyProjectionSnapshot]]:
        previous_by_key = {
            frozenset(JourneyService._projection_conversation_ids(snapshot)): snapshot
            for snapshot in previous_snapshots
        }
        rebuilt_by_key = {
            frozenset(JourneyService._projection_conversation_ids(snapshot)): snapshot
            for snapshot in rebuilt_snapshots
        }
        matches: list[tuple[_JourneyProjectionSnapshot, _JourneyProjectionSnapshot]] = []
        used_previous_ids: set[str] = set()
        used_rebuilt_ids: set[str] = set()
        for conversation_key, previous_snapshot in previous_by_key.items():
            rebuilt_snapshot = rebuilt_by_key.get(conversation_key)
            if rebuilt_snapshot is None:
                continue
            matches.append((previous_snapshot, rebuilt_snapshot))
            used_previous_ids.add(previous_snapshot.instance.journey_id)
            used_rebuilt_ids.add(rebuilt_snapshot.instance.journey_id)

        remaining_previous = [
            snapshot
            for snapshot in previous_snapshots
            if snapshot.instance.journey_id not in used_previous_ids
        ]
        remaining_rebuilt = [
            snapshot
            for snapshot in rebuilt_snapshots
            if snapshot.instance.journey_id not in used_rebuilt_ids
        ]
        if len(remaining_previous) == 1 and len(remaining_rebuilt) == 1:
            matches.append((remaining_previous[0], remaining_rebuilt[0]))
            return matches

        rebuilt_conversation_sets = {
            snapshot.instance.journey_id: set(JourneyService._projection_conversation_ids(snapshot))
            for snapshot in remaining_rebuilt
        }
        for previous_snapshot in remaining_previous:
            previous_set = set(JourneyService._projection_conversation_ids(previous_snapshot))
            candidate = max(
                (
                    rebuilt_snapshot
                    for rebuilt_snapshot in remaining_rebuilt
                    if rebuilt_snapshot.instance.journey_id not in used_rebuilt_ids
                ),
                key=lambda rebuilt_snapshot: len(
                    previous_set & rebuilt_conversation_sets[rebuilt_snapshot.instance.journey_id]
                ),
                default=None,
            )
            if candidate is None:
                continue
            if not previous_set & rebuilt_conversation_sets[candidate.instance.journey_id]:
                continue
            matches.append((previous_snapshot, candidate))
            used_rebuilt_ids.add(candidate.instance.journey_id)
        return matches

    @staticmethod
    def _build_abandonment_events(
        *,
        instance: JourneyInstance,
        occurred_at: datetime,
        outcome: str,
        idempotency_suffix: str,
        payload: dict[str, object] | None = None,
    ) -> list[JourneyEvent]:
        extra_payload = dict(payload or {})
        return [
            JourneyEvent(
                organization_id=instance.organization_id,
                journey_id=instance.journey_id,
                event_type="outcome_recorded",
                source="runtime_rule",
                idempotency_key=f"{instance.journey_id}:outcome_recorded:{outcome}:{idempotency_suffix}",
                payload={"outcome": outcome, **extra_payload},
                occurred_at=occurred_at,
                created_at=occurred_at,
            ),
            JourneyEvent(
                organization_id=instance.organization_id,
                journey_id=instance.journey_id,
                event_type="journey_closed",
                source="runtime_rule",
                idempotency_key=f"{instance.journey_id}:journey_closed:{outcome}:{idempotency_suffix}",
                payload={"status": outcome, "outcome": outcome, **extra_payload},
                occurred_at=occurred_at,
                created_at=occurred_at,
            ),
        ]

    def _snapshot_projection(
        self,
        journey_id: str,
        *,
        organization_id: str,
    ) -> _JourneyProjectionSnapshot:
        store = self._instance_store_or_error()
        instance = self.get_instance(journey_id, organization_id=organization_id)
        return _JourneyProjectionSnapshot(
            instance=instance,
            touchpoints=store.list_touchpoints(journey_id, organization_id=organization_id),
            events=store.list_events(journey_id, organization_id=organization_id),
        )

    def _restore_projection(
        self,
        snapshot: _JourneyProjectionSnapshot,
        *,
        organization_id: str,
    ) -> None:
        store = self._instance_store_or_error()
        store.delete_instance(snapshot.instance.journey_id, organization_id=organization_id)
        store.save_instance(snapshot.instance)
        for touchpoint in snapshot.touchpoints:
            store.save_touchpoint(touchpoint)
        if snapshot.events:
            store.append_events(snapshot.events)

    @staticmethod
    def _projection_conversation_ids(snapshot: _JourneyProjectionSnapshot) -> list[str]:
        return list(
            dict.fromkeys(
                conversation_id
                for conversation_id in [
                    *(touchpoint.conversation_id for touchpoint in snapshot.touchpoints),
                    snapshot.instance.first_conversation_id,
                    snapshot.instance.latest_conversation_id,
                ]
                if conversation_id
            )
        )

    @staticmethod
    def _preservable_events(events: list[JourneyEvent]) -> list[JourneyEvent]:
        return [
            event.model_copy(deep=True)
            for event in events
            if event.source in {"manual", "import"} and event.touchpoint_id is None
        ]
