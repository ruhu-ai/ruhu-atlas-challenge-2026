from __future__ import annotations

import hashlib
import json
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .rules import RuleBinding, RuleBindingScope, RuleDefinition, RuleLibrary, RuleProgram
from .rules_sqlalchemy_models import RuleBindingRecord, RuleDefinitionRevisionRecord
from .schemas import Channel


class RuleProgramResolver(Protocol):
    def resolve(
        self,
        *,
        organization_id: str | None,
        agent_id: str | None = None,
        step_id: str | None = None,
        channel: Channel | None = None,
        event_type: str | None = None,
        tool_ref: str | None = None,
    ) -> RuleProgram: ...


class RuleProgramResolutionInput(BaseModel):
    organization_id: str | None = None
    agent_id: str | None = None
    step_id: str | None = None
    channel: Channel | None = None
    event_type: str | None = None
    tool_ref: str | None = None


class SQLAlchemyRuleProgramResolver:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def resolve(
        self,
        *,
        organization_id: str | None,
        agent_id: str | None = None,
        step_id: str | None = None,
        channel: Channel | None = None,
        event_type: str | None = None,
        tool_ref: str | None = None,
    ) -> RuleProgram:
        request = RuleProgramResolutionInput(
            organization_id=organization_id,
            agent_id=agent_id,
            step_id=step_id,
            channel=channel,
            event_type=event_type,
            tool_ref=tool_ref,
        )

        with self._session_factory() as session:
            rows = session.execute(_build_resolution_statement(request)).all()

        bindings: list[RuleBinding] = []
        rules_by_key: dict[tuple[str, int], RuleDefinition] = {}
        for binding_record, revision_record in rows:
            binding = _binding_from_record(binding_record)
            if not _scope_matches_resolution(binding.scope, request):
                continue

            key = (binding.rule_id, binding.revision)
            if key not in rules_by_key:
                rules_by_key[key] = _rule_from_revision_record(revision_record)
            bindings.append(binding)

        ordered_bindings = sorted(bindings, key=lambda item: (item.order, item.binding_id))
        ordered_rules = sorted(rules_by_key.values(), key=lambda item: (item.rule_id, item.revision))
        return RuleProgram(
            library=RuleLibrary(
                library_id=_resolved_library_id(request),
                version=_resolved_library_version(request, ordered_bindings, ordered_rules),
                rules=ordered_rules,
            ),
            bindings=ordered_bindings,
        )


def _build_resolution_statement(request: RuleProgramResolutionInput):
    organization_filter = RuleBindingRecord.organization_id.is_(None)
    if request.organization_id is not None:
        organization_filter = or_(
            RuleBindingRecord.organization_id.is_(None),
            RuleBindingRecord.organization_id == request.organization_id,
        )

    revision_visibility_filter = or_(
        RuleDefinitionRevisionRecord.organization_id.is_(None),
        and_(
            RuleBindingRecord.organization_id.is_not(None),
            RuleDefinitionRevisionRecord.organization_id == RuleBindingRecord.organization_id,
        ),
    )

    return (
        select(RuleBindingRecord, RuleDefinitionRevisionRecord)
        .join(
            RuleDefinitionRevisionRecord,
            and_(
                RuleBindingRecord.rule_id == RuleDefinitionRevisionRecord.rule_id,
                RuleBindingRecord.rule_revision == RuleDefinitionRevisionRecord.revision,
            ),
        )
        .where(
            organization_filter,
            RuleDefinitionRevisionRecord.status == "published",
            revision_visibility_filter,
        )
        .order_by(RuleBindingRecord.order.asc(), RuleBindingRecord.binding_id.asc())
    )


def _binding_from_record(record: RuleBindingRecord) -> RuleBinding:
    return RuleBinding(
        binding_id=record.binding_id,
        rule_id=record.rule_id,
        revision=record.rule_revision,
        mode=record.mode,
        order=record.order,
        scope=RuleBindingScope(
            channels=list(record.channels or []),
            agent_ids=list(record.agent_ids or []),
            step_ids=list(record.step_ids or []),
            tool_refs=list(record.tool_refs or []),
            event_types=list(record.event_types or []),
        ),
        metadata=dict(record.metadata_json or {}),
    )


def _rule_from_revision_record(record: RuleDefinitionRevisionRecord) -> RuleDefinition:
    return RuleDefinition(
        rule_id=record.rule_id,
        revision=record.revision,
        name=record.name,
        summary=record.summary,
        stage=record.stage,
        predicate=record.predicate_json,
        effect=record.effect_json,
        tags=list(record.tags_json or []),
        metadata=dict(record.metadata_json or {}),
    )


def _scope_matches_resolution(
    scope: RuleBindingScope,
    request: RuleProgramResolutionInput,
) -> bool:
    if request.channel is not None and scope.channels and request.channel not in scope.channels:
        return False
    if request.agent_id is not None and scope.agent_ids and request.agent_id not in scope.agent_ids:
        return False
    if request.step_id is not None and scope.step_ids and request.step_id not in scope.step_ids:
        return False
    if request.tool_ref is not None and scope.tool_refs and request.tool_ref not in scope.tool_refs:
        return False
    if request.event_type is not None and scope.event_types and request.event_type not in scope.event_types:
        return False
    return True


def _resolved_library_id(request: RuleProgramResolutionInput) -> str:
    if request.organization_id is None:
        return "ruhu.runtime.rules.system"
    return f"ruhu.runtime.rules.org.{request.organization_id}"


def _resolved_library_version(
    request: RuleProgramResolutionInput,
    bindings: list[RuleBinding],
    rules: list[RuleDefinition],
) -> str:
    payload = {
        "request": request.model_dump(mode="json"),
        "bindings": [
            {
                "binding_id": binding.binding_id,
                "rule_id": binding.rule_id,
                "revision": binding.revision,
                "mode": binding.mode,
                "order": binding.order,
                "scope": binding.scope.model_dump(mode="json"),
            }
            for binding in bindings
        ],
        "rules": [
            {
                "rule_id": rule.rule_id,
                "revision": rule.revision,
            }
            for rule in rules
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"resolved:{digest}"
