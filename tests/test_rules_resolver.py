from __future__ import annotations

from datetime import datetime, timezone

from ruhu.db import build_session_factory
from ruhu.rules_resolver import SQLAlchemyRuleProgramResolver
from ruhu.rules_sqlalchemy_models import (
    RuleBindingRecord,
    RuleDefinitionRecord,
    RuleDefinitionRevisionRecord,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _add_definition(
    session,
    *,
    rule_id: str,
    organization_id: str | None,
) -> None:
    session.add(
        RuleDefinitionRecord(
            rule_id=rule_id,
            organization_id=organization_id,
            created_at=_utcnow(),
        )
    )


def _add_revision(
    session,
    *,
    revision_id: str,
    rule_id: str,
    revision: int,
    organization_id: str | None,
    status: str,
    stage: str = "before_tool",
    predicate_json: dict | None = None,
    effect_json: dict | None = None,
) -> None:
    session.add(
        RuleDefinitionRevisionRecord(
            revision_id=revision_id,
            organization_id=organization_id,
            rule_id=rule_id,
            revision=revision,
            status=status,
            stage=stage,
            name=f"{rule_id} rev {revision}",
            summary=f"{rule_id} summary",
            predicate_json=predicate_json
            or {
                "kind": "match",
                "path": "turn.text",
                "operator": "contains",
                "value": "refund",
            },
            effect_json=effect_json
            or {
                "kind": "trace",
                "code": f"{rule_id}.trace",
                "message": f"{rule_id} matched",
            },
            tags_json=["test"],
            metadata_json={},
            checksum=f"checksum:{rule_id}:{revision}:{status}",
            created_at=_utcnow(),
            published_at=_utcnow() if status == "published" else None,
        )
    )


def _add_binding(
    session,
    *,
    binding_id: str,
    rule_id: str,
    revision: int,
    organization_id: str | None,
    mode: str = "enforce",
    order: int = 100,
    channels: list[str] | None = None,
    agent_ids: list[str] | None = None,
    step_ids: list[str] | None = None,
    tool_refs: list[str] | None = None,
    event_types: list[str] | None = None,
    scope_fingerprint: str,
) -> None:
    session.add(
        RuleBindingRecord(
            binding_id=binding_id,
            organization_id=organization_id,
            rule_id=rule_id,
            rule_revision=revision,
            mode=mode,
            order=order,
            channels=list(channels or []),
            agent_ids=list(agent_ids or []),
            step_ids=list(step_ids or []),
            tool_refs=list(tool_refs or []),
            event_types=list(event_types or []),
            scope_fingerprint=scope_fingerprint,
            metadata_json={},
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
    )


def test_resolver_returns_system_and_org_rules_in_binding_order(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())

    with session_factory.begin() as session:
        _add_definition(session, rule_id="rule.system.turn.warn", organization_id=None)
        _add_definition(session, rule_id="rule.org.tool.confirm", organization_id="org-acme")
        _add_definition(session, rule_id="rule.org.tool.disabled", organization_id="org-acme")
        _add_definition(session, rule_id="rule.other.tool.block", organization_id="org-other")

        _add_revision(session, revision_id="rev-system-turn-1", rule_id="rule.system.turn.warn", revision=1, organization_id=None, status="published", stage="turn_ingress")
        _add_revision(
            session,
            revision_id="rev-org-tool-1",
            rule_id="rule.org.tool.confirm",
            revision=1,
            organization_id="org-acme",
            status="published",
            stage="before_tool",
            predicate_json={"kind": "match", "path": "tool.args.amount", "operator": "gt", "value": 1000},
            effect_json={
                "kind": "require_confirmation",
                "code": "confirm_large_payment",
                "message": "Confirm large payment.",
            },
        )
        _add_revision(
            session,
            revision_id="rev-org-tool-disabled-1",
            rule_id="rule.org.tool.disabled",
            revision=1,
            organization_id="org-acme",
            status="published",
            stage="before_tool",
        )
        _add_revision(
            session,
            revision_id="rev-other-org-1",
            rule_id="rule.other.tool.block",
            revision=1,
            organization_id="org-other",
            status="published",
            stage="before_tool",
        )
        session.flush()

        _add_binding(
            session,
            binding_id="bind.system.turn.warn",
            organization_id=None,
            rule_id="rule.system.turn.warn",
            revision=1,
            order=10,
            channels=["web_widget"],
            scope_fingerprint="channel:web_widget",
        )
        _add_binding(
            session,
            binding_id="bind.org.tool.confirm",
            organization_id="org-acme",
            rule_id="rule.org.tool.confirm",
            revision=1,
            order=20,
            agent_ids=["billing_support"],
            tool_refs=["process_transaction"],
            event_types=["user_message"],
            scope_fingerprint="agent:billing_support|tool:process_transaction|event:user_message",
        )
        _add_binding(
            session,
            binding_id="bind.org.tool.disabled",
            organization_id="org-acme",
            rule_id="rule.org.tool.disabled",
            revision=1,
            mode="disabled",
            order=30,
            tool_refs=["process_transaction"],
            scope_fingerprint="tool:process_transaction",
        )
        _add_binding(
            session,
            binding_id="bind.other.tool.block",
            organization_id="org-other",
            rule_id="rule.other.tool.block",
            revision=1,
            order=15,
            tool_refs=["process_transaction"],
            scope_fingerprint="tool:process_transaction",
        )

    resolver = SQLAlchemyRuleProgramResolver(session_factory)
    program = resolver.resolve(
        organization_id="org-acme",
        agent_id="billing_support",
        channel="web_widget",
        event_type="user_message",
        tool_ref="process_transaction",
    )

    assert [binding.binding_id for binding in program.bindings] == [
        "bind.system.turn.warn",
        "bind.org.tool.confirm",
        "bind.org.tool.disabled",
    ]
    assert [binding.mode for binding in program.bindings] == ["enforce", "enforce", "disabled"]
    assert {(rule.rule_id, rule.revision) for rule in program.library.rules} == {
        ("rule.system.turn.warn", 1),
        ("rule.org.tool.confirm", 1),
        ("rule.org.tool.disabled", 1),
    }
    assert program.library.library_id == "ruhu.runtime.rules.org.org-acme"
    assert program.library.version.startswith("resolved:")


def test_resolver_excludes_draft_rules_and_narrows_when_scope_values_are_provided(
    postgres_database_url_factory,
) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())

    with session_factory.begin() as session:
        _add_definition(session, rule_id="rule.system.tool.specific", organization_id=None)
        _add_definition(session, rule_id="rule.system.tool.general", organization_id=None)
        _add_definition(session, rule_id="rule.org.tool.draft_only", organization_id="org-acme")

        _add_revision(
            session,
            revision_id="rev-system-specific-1",
            rule_id="rule.system.tool.specific",
            revision=1,
            organization_id=None,
            status="published",
        )
        _add_revision(
            session,
            revision_id="rev-system-general-1",
            rule_id="rule.system.tool.general",
            revision=1,
            organization_id=None,
            status="published",
        )
        _add_revision(
            session,
            revision_id="rev-org-draft-1",
            rule_id="rule.org.tool.draft_only",
            revision=1,
            organization_id="org-acme",
            status="draft",
        )
        session.flush()

        _add_binding(
            session,
            binding_id="bind.system.tool.specific",
            organization_id=None,
            rule_id="rule.system.tool.specific",
            revision=1,
            order=10,
            tool_refs=["process_transaction"],
            scope_fingerprint="tool:process_transaction",
        )
        _add_binding(
            session,
            binding_id="bind.system.tool.general",
            organization_id=None,
            rule_id="rule.system.tool.general",
            revision=1,
            order=20,
            scope_fingerprint="all",
        )
        _add_binding(
            session,
            binding_id="bind.org.tool.draft_only",
            organization_id="org-acme",
            rule_id="rule.org.tool.draft_only",
            revision=1,
            order=30,
            tool_refs=["process_transaction"],
            scope_fingerprint="tool:process_transaction",
        )

    resolver = SQLAlchemyRuleProgramResolver(session_factory)

    unresolved_tool_program = resolver.resolve(
        organization_id="org-acme",
        channel="web_widget",
    )
    assert [binding.binding_id for binding in unresolved_tool_program.bindings] == [
        "bind.system.tool.specific",
        "bind.system.tool.general",
    ]

    narrowed_program = resolver.resolve(
        organization_id="org-acme",
        channel="web_widget",
        tool_ref="refund_transaction",
    )
    assert [binding.binding_id for binding in narrowed_program.bindings] == ["bind.system.tool.general"]
    assert {(rule.rule_id, rule.revision) for rule in narrowed_program.library.rules} == {
        ("rule.system.tool.general", 1),
    }


def test_resolver_without_organization_id_only_returns_system_bindings(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())

    with session_factory.begin() as session:
        _add_definition(session, rule_id="rule.system.only", organization_id=None)
        _add_definition(session, rule_id="rule.org.only", organization_id="org-acme")

        _add_revision(
            session,
            revision_id="rev-system-only-1",
            rule_id="rule.system.only",
            revision=1,
            organization_id=None,
            status="published",
        )
        _add_revision(
            session,
            revision_id="rev-org-only-1",
            rule_id="rule.org.only",
            revision=1,
            organization_id="org-acme",
            status="published",
        )
        session.flush()

        _add_binding(
            session,
            binding_id="bind.system.only",
            organization_id=None,
            rule_id="rule.system.only",
            revision=1,
            order=10,
            scope_fingerprint="all",
        )
        _add_binding(
            session,
            binding_id="bind.org.only",
            organization_id="org-acme",
            rule_id="rule.org.only",
            revision=1,
            order=20,
            scope_fingerprint="all",
        )

    resolver = SQLAlchemyRuleProgramResolver(session_factory)
    program = resolver.resolve(organization_id=None, channel="web_widget")

    assert [binding.binding_id for binding in program.bindings] == ["bind.system.only"]
    assert {(rule.rule_id, rule.revision) for rule in program.library.rules} == {("rule.system.only", 1)}
    assert program.library.library_id == "ruhu.runtime.rules.system"
