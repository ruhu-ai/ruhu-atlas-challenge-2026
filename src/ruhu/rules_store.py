from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .rules import (
    RuleBinding,
    RuleBindingMode,
    RuleBindingScope,
    RuleDefinition,
    RuleEffect,
    RuleEngine,
    RuleEvaluationContext,
    RuleLibrary,
    RulePredicate,
    starter_rule_program,
)
from .rules_resolver import SQLAlchemyRuleProgramResolver
from .rules_sqlalchemy_models import (
    RuleBindingRecord,
    RuleDefinitionRecord,
    RuleDefinitionRevisionRecord,
    RuleLibraryEntryRecord,
    RuleLibraryRecord,
)

RulesOrganizationScope = Literal["system", "organization", "all"]
RulesLibraryVisibility = Literal["system", "organization"]
RuleRevisionStatus = Literal["draft", "published", "retired"]

_ALLOWED_EFFECTS_BY_STAGE: dict[str, set[str]] = {
    "turn_ingress": {"block", "warn", "trace"},
    "before_tool": {"block", "warn", "trace", "suppress_tool", "require_confirmation"},
    "after_tool": {"block", "warn", "trace"},
    "before_response": {"block", "warn", "trace"},
    "before_emit": {"block", "warn", "trace"},
}
_STARTER_LIBRARY_NAME = "Ruhu Starter Rules"
_STARTER_LIBRARY_SUMMARY = "Starter compliance and operations guardrails."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RulesRuntime:
    store: "RulesStore"
    resolver: SQLAlchemyRuleProgramResolver
    engine: RuleEngine


class RuleRevisionBody(BaseModel):
    name: str
    summary: str
    stage: str
    predicate: RulePredicate | None = None
    expression: str | None = None
    effect: RuleEffect
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_definition(self, *, rule_id: str, revision: int) -> RuleDefinition:
        return RuleDefinition(
            rule_id=rule_id,
            revision=revision,
            name=self.name,
            summary=self.summary,
            stage=self.stage,  # type: ignore[arg-type]
            predicate=self.predicate,
            expression=self.expression,
            effect=self.effect,
            tags=list(self.tags),
            metadata=dict(self.metadata),
        )


class RuleDefinitionSummary(BaseModel):
    rule_id: str
    organization_id: str | None = None
    latest_revision: int
    latest_status: RuleRevisionStatus
    published_revision: int | None = None
    name: str
    stage: str
    tags: list[str] = Field(default_factory=list)


class RuleDefinitionRevisionDocument(BaseModel):
    organization_id: str | None = None
    rule_id: str
    revision: int
    status: RuleRevisionStatus
    stage: str
    name: str
    summary: str
    predicate: RulePredicate
    expression: str | None = None
    effect: RuleEffect
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by_user_id: str | None = None
    published_at: datetime | None = None


class RuleLibraryEntryDocument(BaseModel):
    library_entry_id: str
    rule_id: str
    revision: int
    sort_order: int = 0
    notes: str | None = None


class RuleLibraryEntryCreate(BaseModel):
    rule_id: str
    revision: int
    sort_order: int = 0
    notes: str | None = None


class RuleLibraryVersionDocument(BaseModel):
    organization_id: str | None = None
    library_id: str
    version: str
    visibility: RulesLibraryVisibility
    name: str
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    entries: list[RuleLibraryEntryDocument] = Field(default_factory=list)
    created_at: datetime
    created_by_user_id: str | None = None
    published_at: datetime


class RuleLibrarySummary(BaseModel):
    library_id: str
    organization_id: str | None = None
    version: str
    visibility: RulesLibraryVisibility
    name: str
    summary: str
    published_at: datetime


class RuleBindingDocument(BaseModel):
    binding_id: str
    organization_id: str | None = None
    rule_id: str
    revision: int
    mode: RuleBindingMode
    order: int
    scope: RuleBindingScope = Field(default_factory=RuleBindingScope)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by_user_id: str | None = None
    updated_at: datetime
    updated_by_user_id: str | None = None

    def to_domain(self) -> RuleBinding:
        return RuleBinding(
            binding_id=self.binding_id,
            rule_id=self.rule_id,
            revision=self.revision,
            mode=self.mode,
            order=self.order,
            scope=self.scope.model_copy(deep=True),
            metadata=dict(self.metadata),
        )


class RuleLibraryVersionCreate(BaseModel):
    organization_scope: Literal["organization", "system"] = "organization"
    library_id: str
    version: str
    visibility: RulesLibraryVisibility = "organization"
    name: str
    summary: str
    entries: list[RuleLibraryEntryCreate] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuleBindingCreate(BaseModel):
    organization_scope: Literal["organization", "system"] = "organization"
    binding_id: str
    rule_id: str
    revision: int = Field(ge=1)
    mode: RuleBindingMode = "enforce"
    order: int = Field(default=100, ge=1)
    scope: RuleBindingScope = Field(default_factory=RuleBindingScope)
    metadata: dict[str, Any] = Field(default_factory=dict)
    confirm_broad_scope: bool = False


class RuleBindingUpdate(BaseModel):
    revision: int | None = Field(default=None, ge=1)
    mode: RuleBindingMode | None = None
    order: int | None = Field(default=None, ge=1)
    scope: RuleBindingScope | None = None
    metadata: dict[str, Any] | None = None
    confirm_broad_scope: bool = False


class RulesStore(Protocol):
    def list_definitions(
        self,
        *,
        organization_id: str,
        organization_scope: RulesOrganizationScope = "all",
        stage: str | None = None,
        status: RuleRevisionStatus | None = None,
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> list[RuleDefinitionSummary]: ...

    def create_definition(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        body: RuleRevisionBody,
        rule_id: str,
        organization_scope: Literal["organization", "system"] = "organization",
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument: ...

    def get_definition_revision(
        self,
        *,
        organization_id: str,
        rule_id: str,
        revision: int,
        organization_scope: RulesOrganizationScope = "all",
    ) -> RuleDefinitionRevisionDocument | None: ...

    def update_draft_revision(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        rule_id: str,
        revision: int,
        body: RuleRevisionBody,
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument: ...

    def create_next_revision(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        rule_id: str,
        body: RuleRevisionBody,
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument: ...

    def publish_revision(
        self,
        *,
        organization_id: str,
        rule_id: str,
        revision: int,
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument: ...

    def retire_revision(
        self,
        *,
        organization_id: str,
        rule_id: str,
        revision: int,
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument: ...

    def list_libraries(
        self,
        *,
        organization_id: str,
        organization_scope: RulesOrganizationScope = "all",
        visibility: RulesLibraryVisibility | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> list[RuleLibrarySummary]: ...

    def create_library_version(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        payload: RuleLibraryVersionCreate,
        allow_system_scope: bool = False,
    ) -> RuleLibraryVersionDocument: ...

    def get_library_version(
        self,
        *,
        organization_id: str,
        library_id: str,
        version: str,
        organization_scope: RulesOrganizationScope = "all",
    ) -> RuleLibraryVersionDocument | None: ...

    def list_bindings(
        self,
        *,
        organization_id: str,
        organization_scope: RulesOrganizationScope = "all",
        rule_id: str | None = None,
        revision: int | None = None,
        mode: RuleBindingMode | None = None,
        agent_id: str | None = None,
        step_id: str | None = None,
        channel: str | None = None,
        tool_ref: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[RuleBindingDocument]: ...

    def create_binding(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        payload: RuleBindingCreate,
        allow_system_scope: bool = False,
    ) -> RuleBindingDocument: ...

    def update_binding(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        binding_id: str,
        payload: RuleBindingUpdate,
        allow_system_scope: bool = False,
    ) -> RuleBindingDocument: ...

    def seed_starter_library(self) -> None: ...


class SQLAlchemyRulesStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_definitions(
        self,
        *,
        organization_id: str,
        organization_scope: RulesOrganizationScope = "all",
        stage: str | None = None,
        status: RuleRevisionStatus | None = None,
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> list[RuleDefinitionSummary]:
        with self._session_factory() as session:
            records = session.execute(
                _definition_scope_statement(organization_id=organization_id, organization_scope=organization_scope)
            ).scalars().all()
            items: list[RuleDefinitionSummary] = []
            normalized_search = (search or "").strip().lower()
            normalized_tag = (tag or "").strip().lower()
            for record in records:
                revisions = self._list_revisions_for_rule(session, rule_id=record.rule_id)
                if not revisions:
                    continue
                latest = max(revisions, key=lambda item: item.revision)
                published_revision = max(
                    (item.revision for item in revisions if item.status == "published"),
                    default=None,
                )
                tags = [str(item) for item in list(latest.tags_json or [])]
                haystack = " ".join([record.rule_id, latest.name, latest.summary, *tags]).lower()
                if stage is not None and latest.stage != stage:
                    continue
                if status is not None and latest.status != status:
                    continue
                if normalized_tag and normalized_tag not in {item.lower() for item in tags}:
                    continue
                if normalized_search and normalized_search not in haystack:
                    continue
                items.append(
                    RuleDefinitionSummary(
                        rule_id=record.rule_id,
                        organization_id=record.organization_id,
                        latest_revision=latest.revision,
                        latest_status=latest.status,
                        published_revision=published_revision,
                        name=latest.name,
                        stage=latest.stage,
                        tags=tags,
                    )
                )
            items.sort(key=lambda item: (item.rule_id, item.latest_revision))
            return items[:limit]

    def create_definition(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        body: RuleRevisionBody,
        rule_id: str,
        organization_scope: Literal["organization", "system"] = "organization",
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument:
        self._validate_revision_body(body)
        target_organization_id = None if organization_scope == "system" else organization_id
        if target_organization_id is None and not allow_system_scope:
            raise PermissionError("superuser required for system scope")
        now = _utcnow()
        checksum = _revision_checksum(body)
        # Compile expression to predicate if needed
        definition = body.to_definition(rule_id=rule_id, revision=1)
        compiled_predicate = definition.predicate

        record = RuleDefinitionRecord(
            rule_id=rule_id,
            organization_id=target_organization_id,
            created_by_user_id=actor_user_id,
            archived_at=None,
            created_at=now,
        )
        revision_record = RuleDefinitionRevisionRecord(
            revision_id=str(uuid4()),
            organization_id=target_organization_id,
            rule_id=rule_id,
            revision=1,
            status="draft",
            stage=body.stage,
            name=body.name,
            summary=body.summary,
            predicate_json=_json_payload(compiled_predicate),
            expression_json=body.expression,
            effect_json=_json_payload(body.effect),
            tags_json=list(body.tags),
            metadata_json=dict(body.metadata),
            checksum=checksum,
            created_at=now,
            created_by_user_id=actor_user_id,
            published_at=None,
        )
        with self._session_factory.begin() as session:
            session.add(record)
            session.add(revision_record)
        return _revision_document_from_record(revision_record)

    def get_definition_revision(
        self,
        *,
        organization_id: str,
        rule_id: str,
        revision: int,
        organization_scope: RulesOrganizationScope = "all",
    ) -> RuleDefinitionRevisionDocument | None:
        with self._session_factory() as session:
            definition = self._get_definition(session, rule_id=rule_id)
            if definition is None or not _definition_in_scope(
                definition.organization_id,
                organization_id=organization_id,
                organization_scope=organization_scope,
            ):
                return None
            revision_record = self._get_revision(session, rule_id=rule_id, revision=revision)
            if revision_record is None:
                return None
            return _revision_document_from_record(revision_record)

    def update_draft_revision(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        rule_id: str,
        revision: int,
        body: RuleRevisionBody,
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument:
        self._validate_revision_body(body)
        with self._session_factory.begin() as session:
            definition = self._require_definition(session, organization_id=organization_id, rule_id=rule_id)
            self._require_mutable_scope(
                target_organization_id=definition.organization_id,
                allow_system_scope=allow_system_scope,
            )
            revision_record = self._require_revision(session, rule_id=rule_id, revision=revision)
            if revision_record.status != "draft":
                raise ValueError("only draft revisions may be updated")
            if revision_record.organization_id != definition.organization_id:
                raise ValueError("revision scope does not match rule definition scope")
            revision_record.stage = body.stage
            revision_record.name = body.name
            revision_record.summary = body.summary
            revision_record.predicate_json = _json_payload(body.predicate)
            revision_record.effect_json = _json_payload(body.effect)
            revision_record.tags_json = list(body.tags)
            revision_record.metadata_json = dict(body.metadata)
            revision_record.checksum = _revision_checksum(body)
            revision_record.created_by_user_id = actor_user_id
        return _revision_document_from_record(revision_record)

    def create_next_revision(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        rule_id: str,
        body: RuleRevisionBody,
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument:
        self._validate_revision_body(body)
        now = _utcnow()
        with self._session_factory.begin() as session:
            definition = self._require_definition(session, organization_id=organization_id, rule_id=rule_id)
            self._require_mutable_scope(
                target_organization_id=definition.organization_id,
                allow_system_scope=allow_system_scope,
            )
            revisions = self._list_revisions_for_rule(session, rule_id=rule_id)
            next_revision = max((item.revision for item in revisions), default=0) + 1
            # Compile expression to predicate if needed
            compiled_def = body.to_definition(rule_id=rule_id, revision=next_revision)
            compiled_predicate = compiled_def.predicate

            revision_record = RuleDefinitionRevisionRecord(
                revision_id=str(uuid4()),
                organization_id=definition.organization_id,
                rule_id=rule_id,
                revision=next_revision,
                status="draft",
                stage=body.stage,
                name=body.name,
                summary=body.summary,
                predicate_json=_json_payload(compiled_predicate),
                expression_json=body.expression,
                effect_json=_json_payload(body.effect),
                tags_json=list(body.tags),
                metadata_json=dict(body.metadata),
                checksum=_revision_checksum(body),
                created_at=now,
                created_by_user_id=actor_user_id,
                published_at=None,
            )
            session.add(revision_record)
        return _revision_document_from_record(revision_record)

    def publish_revision(
        self,
        *,
        organization_id: str,
        rule_id: str,
        revision: int,
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument:
        with self._session_factory.begin() as session:
            definition = self._require_definition(session, organization_id=organization_id, rule_id=rule_id)
            self._require_mutable_scope(
                target_organization_id=definition.organization_id,
                allow_system_scope=allow_system_scope,
            )
            revision_record = self._require_revision(session, rule_id=rule_id, revision=revision)
            if revision_record.status != "draft":
                raise ValueError("only draft revisions may be published")
            body = RuleRevisionBody(
                name=revision_record.name,
                summary=revision_record.summary,
                stage=revision_record.stage,
                predicate=revision_record.predicate_json,
                expression=revision_record.expression_json,
                effect=revision_record.effect_json,
                tags=list(revision_record.tags_json or []),
                metadata=dict(revision_record.metadata_json or {}),
            )
            self._validate_revision_body(body)
            revision_record.status = "published"
            revision_record.published_at = _utcnow()
        return _revision_document_from_record(revision_record)

    def retire_revision(
        self,
        *,
        organization_id: str,
        rule_id: str,
        revision: int,
        allow_system_scope: bool = False,
    ) -> RuleDefinitionRevisionDocument:
        with self._session_factory.begin() as session:
            definition = self._require_definition(session, organization_id=organization_id, rule_id=rule_id)
            self._require_mutable_scope(
                target_organization_id=definition.organization_id,
                allow_system_scope=allow_system_scope,
            )
            revision_record = self._require_revision(session, rule_id=rule_id, revision=revision)
            if revision_record.status != "published":
                raise ValueError("only published revisions may be retired")
            revision_record.status = "retired"
        return _revision_document_from_record(revision_record)

    def list_libraries(
        self,
        *,
        organization_id: str,
        organization_scope: RulesOrganizationScope = "all",
        visibility: RulesLibraryVisibility | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> list[RuleLibrarySummary]:
        with self._session_factory() as session:
            statement: Select[tuple[RuleLibraryRecord]] = select(RuleLibraryRecord).order_by(
                RuleLibraryRecord.library_id.asc(),
                RuleLibraryRecord.published_at.desc(),
            )
            statement = _apply_library_scope(statement, organization_id=organization_id, organization_scope=organization_scope)
            if visibility is not None:
                statement = statement.where(RuleLibraryRecord.visibility == visibility)
            records = session.execute(statement).scalars().all()
            items: list[RuleLibrarySummary] = []
            normalized_search = (search or "").strip().lower()
            for record in records:
                haystack = " ".join([record.library_id, record.name, record.summary]).lower()
                if normalized_search and normalized_search not in haystack:
                    continue
                items.append(
                    RuleLibrarySummary(
                        library_id=record.library_id,
                        organization_id=record.organization_id,
                        version=record.version,
                        visibility=record.visibility,  # type: ignore[arg-type]
                        name=record.name,
                        summary=record.summary,
                        published_at=record.published_at,
                    )
                )
            return items[:limit]

    def create_library_version(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        payload: RuleLibraryVersionCreate,
        allow_system_scope: bool = False,
    ) -> RuleLibraryVersionDocument:
        target_organization_id = None if payload.organization_scope == "system" else organization_id
        self._require_mutable_scope(
            target_organization_id=target_organization_id,
            allow_system_scope=allow_system_scope,
        )
        if payload.visibility == "system" and target_organization_id is not None:
            raise ValueError("system visibility requires system scope")
        if payload.visibility == "organization" and target_organization_id is None:
            raise ValueError("organization visibility requires organization scope")
        now = _utcnow()
        with self._session_factory.begin() as session:
            library = RuleLibraryRecord(
                library_version_id=str(uuid4()),
                organization_id=target_organization_id,
                library_id=payload.library_id,
                version=payload.version,
                visibility=payload.visibility,
                name=payload.name,
                summary=payload.summary,
                metadata_json=dict(payload.metadata),
                created_at=now,
                created_by_user_id=actor_user_id,
                published_at=now,
            )
            session.add(library)
            entries: list[RuleLibraryEntryRecord] = []
            for entry in payload.entries:
                revision = self._require_revision(session, rule_id=entry.rule_id, revision=entry.revision)
                self._ensure_revision_bindable_to_scope(
                    revision_record=revision,
                    target_organization_id=target_organization_id,
                )
                if revision.status != "published":
                    raise ValueError("library entries must reference published revisions")
                entry_record = RuleLibraryEntryRecord(
                    library_entry_id=str(uuid4()),
                    library_id=payload.library_id,
                    library_version=payload.version,
                    rule_id=entry.rule_id,
                    rule_revision=entry.revision,
                    sort_order=entry.sort_order,
                    notes=entry.notes,
                )
                entries.append(entry_record)
                session.add(entry_record)
        return RuleLibraryVersionDocument(
            organization_id=target_organization_id,
            library_id=payload.library_id,
            version=payload.version,
            visibility=payload.visibility,
            name=payload.name,
            summary=payload.summary,
            metadata=dict(payload.metadata),
            entries=[
                RuleLibraryEntryDocument(
                    library_entry_id=item.library_entry_id,
                    rule_id=item.rule_id,
                    revision=item.rule_revision,
                    sort_order=item.sort_order,
                    notes=item.notes,
                )
                for item in sorted(entries, key=lambda item: (item.sort_order, item.library_entry_id))
            ],
            created_at=now,
            created_by_user_id=actor_user_id,
            published_at=now,
        )

    def get_library_version(
        self,
        *,
        organization_id: str,
        library_id: str,
        version: str,
        organization_scope: RulesOrganizationScope = "all",
    ) -> RuleLibraryVersionDocument | None:
        with self._session_factory() as session:
            statement = select(RuleLibraryRecord).where(
                RuleLibraryRecord.library_id == library_id,
                RuleLibraryRecord.version == version,
            )
            statement = _apply_library_scope(statement, organization_id=organization_id, organization_scope=organization_scope)
            library = session.execute(statement).scalar_one_or_none()
            if library is None:
                return None
            entry_records = session.execute(
                select(RuleLibraryEntryRecord)
                .where(
                    RuleLibraryEntryRecord.library_id == library_id,
                    RuleLibraryEntryRecord.library_version == version,
                )
                .order_by(RuleLibraryEntryRecord.sort_order.asc(), RuleLibraryEntryRecord.library_entry_id.asc())
            ).scalars().all()
            return RuleLibraryVersionDocument(
                organization_id=library.organization_id,
                library_id=library.library_id,
                version=library.version,
                visibility=library.visibility,  # type: ignore[arg-type]
                name=library.name,
                summary=library.summary,
                metadata=dict(library.metadata_json or {}),
                entries=[
                    RuleLibraryEntryDocument(
                        library_entry_id=item.library_entry_id,
                        rule_id=item.rule_id,
                        revision=item.rule_revision,
                        sort_order=item.sort_order,
                        notes=item.notes,
                    )
                    for item in entry_records
                ],
                created_at=library.created_at,
                created_by_user_id=library.created_by_user_id,
                published_at=library.published_at,
            )

    def list_bindings(
        self,
        *,
        organization_id: str,
        organization_scope: RulesOrganizationScope = "all",
        rule_id: str | None = None,
        revision: int | None = None,
        mode: RuleBindingMode | None = None,
        agent_id: str | None = None,
        step_id: str | None = None,
        channel: str | None = None,
        tool_ref: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[RuleBindingDocument]:
        with self._session_factory() as session:
            statement: Select[tuple[RuleBindingRecord]] = select(RuleBindingRecord).order_by(
                RuleBindingRecord.order.asc(),
                RuleBindingRecord.binding_id.asc(),
            )
            statement = _apply_binding_scope(statement, organization_id=organization_id, organization_scope=organization_scope)
            if rule_id is not None:
                statement = statement.where(RuleBindingRecord.rule_id == rule_id)
            if revision is not None:
                statement = statement.where(RuleBindingRecord.rule_revision == revision)
            if mode is not None:
                statement = statement.where(RuleBindingRecord.mode == mode)
            records = session.execute(statement).scalars().all()
            items = [_binding_document_from_record(item) for item in records]
            filtered: list[RuleBindingDocument] = []
            for item in items:
                if agent_id is not None and item.scope.agent_ids and agent_id not in item.scope.agent_ids:
                    continue
                if step_id is not None and item.scope.step_ids and step_id not in item.scope.step_ids:
                    continue
                if channel is not None and item.scope.channels and channel not in item.scope.channels:
                    continue
                if tool_ref is not None and item.scope.tool_refs and tool_ref not in item.scope.tool_refs:
                    continue
                if event_type is not None and item.scope.event_types and event_type not in item.scope.event_types:
                    continue
                filtered.append(item)
            return filtered[:limit]

    def create_binding(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        payload: RuleBindingCreate,
        allow_system_scope: bool = False,
    ) -> RuleBindingDocument:
        self._validate_broad_scope(scope=payload.scope, confirmed=payload.confirm_broad_scope)
        target_organization_id = None if payload.organization_scope == "system" else organization_id
        self._require_mutable_scope(
            target_organization_id=target_organization_id,
            allow_system_scope=allow_system_scope,
        )
        now = _utcnow()
        with self._session_factory.begin() as session:
            revision = self._require_revision(session, rule_id=payload.rule_id, revision=payload.revision)
            if revision.status != "published":
                raise ValueError("bindings must reference published revisions")
            self._ensure_revision_bindable_to_scope(
                revision_record=revision,
                target_organization_id=target_organization_id,
            )
            record = RuleBindingRecord(
                binding_id=payload.binding_id,
                organization_id=target_organization_id,
                rule_id=payload.rule_id,
                rule_revision=payload.revision,
                mode=payload.mode,
                order=payload.order,
                channels=list(payload.scope.channels),
                agent_ids=list(payload.scope.agent_ids),
                step_ids=list(payload.scope.step_ids),
                tool_refs=list(payload.scope.tool_refs),
                event_types=list(payload.scope.event_types),
                scope_fingerprint=_scope_fingerprint(payload.scope),
                metadata_json=dict(payload.metadata),
                created_at=now,
                created_by_user_id=actor_user_id,
                updated_at=now,
                updated_by_user_id=actor_user_id,
            )
            session.add(record)
        return _binding_document_from_record(record)

    def update_binding(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        binding_id: str,
        payload: RuleBindingUpdate,
        allow_system_scope: bool = False,
    ) -> RuleBindingDocument:
        with self._session_factory.begin() as session:
            record = self._require_binding(session, organization_id=organization_id, binding_id=binding_id)
            self._require_mutable_scope(
                target_organization_id=record.organization_id,
                allow_system_scope=allow_system_scope,
            )
            if payload.revision is not None:
                revision = self._require_revision(session, rule_id=record.rule_id, revision=payload.revision)
                if revision.status != "published":
                    raise ValueError("bindings must reference published revisions")
                self._ensure_revision_bindable_to_scope(
                    revision_record=revision,
                    target_organization_id=record.organization_id,
                )
                record.rule_revision = payload.revision
            if payload.mode is not None:
                record.mode = payload.mode
            if payload.order is not None:
                record.order = payload.order
            if payload.scope is not None:
                self._validate_broad_scope(scope=payload.scope, confirmed=payload.confirm_broad_scope)
                record.channels = list(payload.scope.channels)
                record.agent_ids = list(payload.scope.agent_ids)
                record.step_ids = list(payload.scope.step_ids)
                record.tool_refs = list(payload.scope.tool_refs)
                record.event_types = list(payload.scope.event_types)
                record.scope_fingerprint = _scope_fingerprint(payload.scope)
            if payload.metadata is not None:
                record.metadata_json = dict(payload.metadata)
            record.updated_at = _utcnow()
            record.updated_by_user_id = actor_user_id
        return _binding_document_from_record(record)

    def seed_starter_library(self) -> None:
        program = starter_rule_program()
        starter_rules = list(program.library.rules)
        if not starter_rules:
            return

        rule_ids = [rule.rule_id for rule in starter_rules]
        library_id = program.library.library_id
        library_version = program.library.version
        now = _utcnow()

        with self._session_factory.begin() as session:
            existing_definition_ids = {
                item[0]
                for item in session.execute(
                    select(RuleDefinitionRecord.rule_id).where(RuleDefinitionRecord.rule_id.in_(rule_ids))
                ).all()
            }
            existing_revision_keys = {
                (record.rule_id, record.revision)
                for record in session.execute(
                    select(RuleDefinitionRevisionRecord).where(
                        RuleDefinitionRevisionRecord.rule_id.in_(rule_ids),
                        RuleDefinitionRevisionRecord.revision == 1,
                    )
                ).scalars().all()
            }

            for rule in starter_rules:
                if rule.rule_id not in existing_definition_ids:
                    session.add(
                        RuleDefinitionRecord(
                            rule_id=rule.rule_id,
                            organization_id=None,
                            created_at=now,
                            created_by_user_id=None,
                        )
                    )
                if (rule.rule_id, rule.revision) not in existing_revision_keys:
                    body = RuleRevisionBody(
                        name=rule.name,
                        summary=rule.summary,
                        stage=rule.stage,
                        predicate=rule.predicate,
                        effect=rule.effect,
                        tags=list(rule.tags),
                        metadata=dict(rule.metadata),
                    )
                    self._validate_revision_body(body)
                    session.add(
                        RuleDefinitionRevisionRecord(
                            revision_id=str(uuid4()),
                            organization_id=None,
                            rule_id=rule.rule_id,
                            revision=rule.revision,
                            status="published",
                            stage=rule.stage,
                            name=rule.name,
                            summary=rule.summary,
                            predicate_json=body.predicate.model_dump(mode="json"),
                            expression_json=None,
                            effect_json=body.effect.model_dump(mode="json"),
                            tags_json=list(rule.tags),
                            metadata_json=dict(rule.metadata),
                            checksum=_revision_checksum(body),
                            created_at=now,
                            created_by_user_id=None,
                            published_at=now,
                        )
                    )

            library = session.execute(
                select(RuleLibraryRecord).where(
                    RuleLibraryRecord.library_id == library_id,
                    RuleLibraryRecord.version == library_version,
                )
            ).scalar_one_or_none()
            if library is None:
                library = RuleLibraryRecord(
                    library_version_id=str(uuid4()),
                    organization_id=None,
                    library_id=library_id,
                    version=library_version,
                    visibility="system",
                    name=_STARTER_LIBRARY_NAME,
                    summary=_STARTER_LIBRARY_SUMMARY,
                    metadata_json={"seed_source": "starter_rule_program"},
                    created_at=now,
                    created_by_user_id=None,
                    published_at=now,
                )
                session.add(library)

            existing_entry_keys = {
                (record.rule_id, record.rule_revision)
                for record in session.execute(
                    select(RuleLibraryEntryRecord).where(
                        RuleLibraryEntryRecord.library_id == library_id,
                        RuleLibraryEntryRecord.library_version == library_version,
                    )
                ).scalars().all()
            }
            for index, rule in enumerate(starter_rules, start=1):
                entry_key = (rule.rule_id, rule.revision)
                if entry_key in existing_entry_keys:
                    continue
                session.add(
                    RuleLibraryEntryRecord(
                        library_entry_id=str(uuid4()),
                        library_id=library_id,
                        library_version=library_version,
                        rule_id=rule.rule_id,
                        rule_revision=rule.revision,
                        sort_order=index * 10,
                        notes="Seeded starter rule",
                    )
                )

    def _get_definition(self, session: Session, *, rule_id: str) -> RuleDefinitionRecord | None:
        return session.get(RuleDefinitionRecord, rule_id)

    def _require_definition(self, session: Session, *, organization_id: str, rule_id: str) -> RuleDefinitionRecord:
        record = self._get_definition(session, rule_id=rule_id)
        if record is None:
            raise KeyError(rule_id)
        if record.organization_id not in {None, organization_id}:
            raise KeyError(rule_id)
        return record

    def _get_revision(
        self,
        session: Session,
        *,
        rule_id: str,
        revision: int,
    ) -> RuleDefinitionRevisionRecord | None:
        return session.execute(
            select(RuleDefinitionRevisionRecord).where(
                RuleDefinitionRevisionRecord.rule_id == rule_id,
                RuleDefinitionRevisionRecord.revision == revision,
            )
        ).scalar_one_or_none()

    def _require_revision(
        self,
        session: Session,
        *,
        rule_id: str,
        revision: int,
    ) -> RuleDefinitionRevisionRecord:
        record = self._get_revision(session, rule_id=rule_id, revision=revision)
        if record is None:
            raise KeyError(f"{rule_id}@{revision}")
        return record

    def _list_revisions_for_rule(self, session: Session, *, rule_id: str) -> list[RuleDefinitionRevisionRecord]:
        return session.execute(
            select(RuleDefinitionRevisionRecord)
            .where(RuleDefinitionRevisionRecord.rule_id == rule_id)
            .order_by(RuleDefinitionRevisionRecord.revision.asc())
        ).scalars().all()

    def _require_binding(self, session: Session, *, organization_id: str, binding_id: str) -> RuleBindingRecord:
        record = session.get(RuleBindingRecord, binding_id)
        if record is None:
            raise KeyError(binding_id)
        if record.organization_id not in {None, organization_id}:
            raise KeyError(binding_id)
        return record

    @staticmethod
    def _validate_revision_body(body: RuleRevisionBody) -> None:
        definition = body.to_definition(rule_id="rule.validation", revision=1)
        allowed = _ALLOWED_EFFECTS_BY_STAGE.get(definition.stage)
        if allowed is None:
            raise ValueError(f"unsupported rule stage: {definition.stage}")
        if definition.effect.kind not in allowed:
            raise ValueError(
                f"effect {definition.effect.kind} is not allowed at stage {definition.stage}"
            )

    @staticmethod
    def _validate_broad_scope(*, scope: RuleBindingScope, confirmed: bool) -> None:
        if confirmed:
            return
        if (
            not scope.channels
            or not scope.agent_ids
            or not scope.step_ids
            or not scope.tool_refs
            or not scope.event_types
        ):
            raise ValueError("broad scope requires explicit confirmation")

    @staticmethod
    def _ensure_revision_bindable_to_scope(
        *,
        revision_record: RuleDefinitionRevisionRecord,
        target_organization_id: str | None,
    ) -> None:
        if target_organization_id is None and revision_record.organization_id is not None:
            raise ValueError("system-scoped bindings and libraries may reference only system rules")
        if target_organization_id is not None and revision_record.organization_id not in {None, target_organization_id}:
            raise ValueError("organization scope cannot reference another organization's rule")

    @staticmethod
    def _require_mutable_scope(
        *,
        target_organization_id: str | None,
        allow_system_scope: bool,
    ) -> None:
        if target_organization_id is None and not allow_system_scope:
            raise PermissionError("superuser required for system scope")


def build_rules_runtime(session_factory: sessionmaker[Session]) -> RulesRuntime:
    store = SQLAlchemyRulesStore(session_factory)
    store.seed_starter_library()
    resolver = SQLAlchemyRuleProgramResolver(session_factory)
    engine = RuleEngine()
    return RulesRuntime(store=store, resolver=resolver, engine=engine)


def _definition_scope_statement(
    *,
    organization_id: str,
    organization_scope: RulesOrganizationScope,
) -> Select[tuple[RuleDefinitionRecord]]:
    statement: Select[tuple[RuleDefinitionRecord]] = select(RuleDefinitionRecord).where(
        RuleDefinitionRecord.archived_at.is_(None)
    )
    if organization_scope == "system":
        return statement.where(RuleDefinitionRecord.organization_id.is_(None))
    if organization_scope == "organization":
        return statement.where(RuleDefinitionRecord.organization_id == organization_id)
    return statement.where(
        or_(RuleDefinitionRecord.organization_id.is_(None), RuleDefinitionRecord.organization_id == organization_id)
    )


def _apply_library_scope(
    statement: Select,
    *,
    organization_id: str,
    organization_scope: RulesOrganizationScope,
) -> Select:
    if organization_scope == "system":
        return statement.where(RuleLibraryRecord.organization_id.is_(None))
    if organization_scope == "organization":
        return statement.where(RuleLibraryRecord.organization_id == organization_id)
    return statement.where(
        or_(RuleLibraryRecord.organization_id.is_(None), RuleLibraryRecord.organization_id == organization_id)
    )


def _apply_binding_scope(
    statement: Select,
    *,
    organization_id: str,
    organization_scope: RulesOrganizationScope,
) -> Select:
    if organization_scope == "system":
        return statement.where(RuleBindingRecord.organization_id.is_(None))
    if organization_scope == "organization":
        return statement.where(RuleBindingRecord.organization_id == organization_id)
    return statement.where(
        or_(RuleBindingRecord.organization_id.is_(None), RuleBindingRecord.organization_id == organization_id)
    )


def _definition_in_scope(
    record_organization_id: str | None,
    *,
    organization_id: str,
    organization_scope: RulesOrganizationScope,
) -> bool:
    if organization_scope == "system":
        return record_organization_id is None
    if organization_scope == "organization":
        return record_organization_id == organization_id
    return record_organization_id in {None, organization_id}


def _json_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _revision_checksum(body: RuleRevisionBody) -> str:
    payload = {
        "name": body.name,
        "summary": body.summary,
        "stage": body.stage,
        "predicate": _json_payload(body.predicate),
        "effect": _json_payload(body.effect),
        "tags": list(body.tags),
        "metadata": dict(body.metadata),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _scope_fingerprint(scope: RuleBindingScope) -> str:
    payload = {
        "channels": sorted(scope.channels),
        "agent_ids": sorted(scope.agent_ids),
        "step_ids": sorted(scope.step_ids),
        "tool_refs": sorted(scope.tool_refs),
        "event_types": sorted(scope.event_types),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _revision_document_from_record(record: RuleDefinitionRevisionRecord) -> RuleDefinitionRevisionDocument:
    return RuleDefinitionRevisionDocument(
        organization_id=record.organization_id,
        rule_id=record.rule_id,
        revision=record.revision,
        status=record.status,  # type: ignore[arg-type]
        stage=record.stage,
        name=record.name,
        summary=record.summary,
        predicate=record.predicate_json,
        expression=record.expression_json,
        effect=record.effect_json,
        tags=list(record.tags_json or []),
        metadata=dict(record.metadata_json or {}),
        created_at=record.created_at,
        created_by_user_id=record.created_by_user_id,
        published_at=record.published_at,
    )


def _binding_document_from_record(record: RuleBindingRecord) -> RuleBindingDocument:
    return RuleBindingDocument(
        binding_id=record.binding_id,
        organization_id=record.organization_id,
        rule_id=record.rule_id,
        revision=record.rule_revision,
        mode=record.mode,  # type: ignore[arg-type]
        order=record.order,
        scope=RuleBindingScope(
            channels=list(record.channels or []),
            agent_ids=list(record.agent_ids or []),
            step_ids=list(record.step_ids or []),
            tool_refs=list(record.tool_refs or []),
            event_types=list(record.event_types or []),
        ),
        metadata=dict(record.metadata_json or {}),
        created_at=record.created_at,
        created_by_user_id=record.created_by_user_id,
        updated_at=record.updated_at,
        updated_by_user_id=record.updated_by_user_id,
    )


__all__ = [
    "RuleBindingCreate",
    "RuleBindingDocument",
    "RuleBindingUpdate",
    "RuleDefinitionRevisionDocument",
    "RuleDefinitionSummary",
    "RuleLibraryEntryCreate",
    "RuleLibraryEntryDocument",
    "RuleLibrarySummary",
    "RuleLibraryVersionCreate",
    "RuleLibraryVersionDocument",
    "RuleRevisionBody",
    "RulesRuntime",
    "RulesStore",
    "RulesOrganizationScope",
    "SQLAlchemyRulesStore",
    "build_rules_runtime",
]
