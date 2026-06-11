from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ruhu.db import build_session_factory
from ruhu.rules import starter_rule_program
from ruhu.rules_store import RuleRevisionBody, SQLAlchemyRulesStore
from ruhu.rules_sqlalchemy_models import (
    RuleBindingRecord,
    RuleDefinitionRecord,
    RuleDefinitionRevisionRecord,
    RuleLibraryEntryRecord,
    RuleLibraryRecord,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def test_rules_sqlalchemy_tables_round_trip_and_enforce_key_constraints(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    now = _utcnow()

    with session_factory.begin() as session:
        session.add(
            RuleDefinitionRecord(
                rule_id="rule.tool.high_value_transaction_confirmation",
                organization_id=None,
                created_at=now,
            )
        )
        session.add(
            RuleDefinitionRevisionRecord(
                revision_id="rule-revision-1",
                organization_id=None,
                rule_id="rule.tool.high_value_transaction_confirmation",
                revision=1,
                status="published",
                stage="before_tool",
                name="Require confirmation for high-value transactions",
                summary="Require explicit confirmation before large payments.",
                predicate_json={
                    "kind": "match",
                    "path": "tool.args.amount",
                    "operator": "gt",
                    "value": 10000,
                },
                effect_json={
                    "kind": "require_confirmation",
                    "code": "high_value_transaction_requires_confirmation",
                    "message": "This transaction requires explicit approval before execution.",
                },
                tags_json=["starter", "approval"],
                metadata_json={},
                checksum="checksum-a",
                created_at=now,
                published_at=now,
            )
        )
        session.add(
            RuleLibraryRecord(
                library_version_id="library-version-1",
                organization_id=None,
                library_id="ruhu.starter.rules",
                version="2026-04-11",
                visibility="system",
                name="Ruhu Starter Rules",
                summary="Starter compliance and operations guardrails.",
                metadata_json={},
                created_at=now,
                published_at=now,
            )
        )

    with session_factory.begin() as session:
        session.add(
            RuleLibraryEntryRecord(
                library_entry_id="library-entry-1",
                library_id="ruhu.starter.rules",
                library_version="2026-04-11",
                rule_id="rule.tool.high_value_transaction_confirmation",
                rule_revision=1,
                sort_order=10,
                notes="Starter approval rule",
            )
        )
        session.add(
            RuleBindingRecord(
                binding_id="bind.tool.high_value_transaction_confirmation.default",
                organization_id=None,
                rule_id="rule.tool.high_value_transaction_confirmation",
                rule_revision=1,
                mode="enforce",
                order=50,
                channels=[],
                agent_ids=[],
                step_ids=[],
                tool_refs=["process_transaction"],
                event_types=[],
                scope_fingerprint="tool:process_transaction",
                metadata_json={},
                created_at=now,
                updated_at=now,
            )
        )

    with session_factory() as session:
        binding = session.get(RuleBindingRecord, "bind.tool.high_value_transaction_confirmation.default")
        assert binding is not None
        assert binding.rule_id == "rule.tool.high_value_transaction_confirmation"
        assert binding.tool_refs == ["process_transaction"]

        library = session.get(RuleLibraryRecord, "library-version-1")
        assert library is not None
        assert library.library_id == "ruhu.starter.rules"

    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(
                RuleDefinitionRevisionRecord(
                    revision_id="rule-revision-draft-1",
                    organization_id=None,
                    rule_id="rule.tool.high_value_transaction_confirmation",
                    revision=2,
                    status="draft",
                    stage="before_tool",
                    name="Draft one",
                    summary="First draft revision.",
                    predicate_json={"kind": "match", "path": "tool.args.amount", "operator": "gt", "value": 15000},
                    effect_json={
                        "kind": "require_confirmation",
                        "code": "needs_confirmation",
                        "message": "Confirm it.",
                    },
                    tags_json=[],
                    metadata_json={},
                    checksum="checksum-draft-1",
                    created_at=_utcnow(),
                    published_at=None,
                )
            )
            session.add(
                RuleDefinitionRevisionRecord(
                    revision_id="rule-revision-draft-2",
                    organization_id=None,
                    rule_id="rule.tool.high_value_transaction_confirmation",
                    revision=3,
                    status="draft",
                    stage="before_tool",
                    name="Draft two",
                    summary="Second draft revision.",
                    predicate_json={"kind": "match", "path": "tool.args.amount", "operator": "gt", "value": 20000},
                    effect_json={
                        "kind": "require_confirmation",
                        "code": "needs_confirmation",
                        "message": "Confirm it.",
                    },
                    tags_json=[],
                    metadata_json={},
                    checksum="checksum-draft-2",
                    created_at=_utcnow(),
                    published_at=None,
                )
            )

    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(
                RuleBindingRecord(
                    binding_id="bind.tool.high_value_transaction_confirmation.duplicate",
                    organization_id=None,
                    rule_id="rule.tool.high_value_transaction_confirmation",
                    rule_revision=1,
                    mode="enforce",
                    order=51,
                    channels=[],
                    agent_ids=[],
                    step_ids=[],
                    tool_refs=["process_transaction"],
                    event_types=[],
                    scope_fingerprint="tool:process_transaction",
                    metadata_json={},
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
            )


def test_rules_store_seeds_starter_library_idempotently(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyRulesStore(session_factory)
    starter_program = starter_rule_program()

    store.seed_starter_library()
    store.seed_starter_library()

    with session_factory() as session:
        starter_library = session.execute(
            select(RuleLibraryRecord).where(
                RuleLibraryRecord.library_id == starter_program.library.library_id,
                RuleLibraryRecord.version == starter_program.library.version,
            )
        ).scalar_one_or_none()
        assert starter_library is not None
        assert starter_library.visibility == "system"

        starter_entries = session.execute(
            select(RuleLibraryEntryRecord).where(
                RuleLibraryEntryRecord.library_id == starter_program.library.library_id,
                RuleLibraryEntryRecord.library_version == starter_program.library.version,
            )
        ).scalars().all()
        assert len(starter_entries) == len(starter_program.library.rules)

        starter_revisions = session.execute(
            select(RuleDefinitionRevisionRecord).where(
                RuleDefinitionRevisionRecord.rule_id.in_([rule.rule_id for rule in starter_program.library.rules])
            )
        ).scalars().all()
        assert len(starter_revisions) == len(starter_program.library.rules)
        assert {record.status for record in starter_revisions} == {"published"}

        bindings = session.execute(select(RuleBindingRecord)).scalars().all()
        assert bindings == []


def test_rules_store_can_retire_published_revision(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyRulesStore(session_factory)

    created = store.create_definition(
        organization_id="org-1",
        actor_user_id="user-admin",
        body=RuleRevisionBody(
            name="Retire Me",
            summary="Retire coverage",
            stage="turn_ingress",
            predicate={
                "kind": "match",
                "path": "turn.text",
                "operator": "contains",
                "value": "hello",
            },
            effect={
                "kind": "trace",
                "code": "trace_hello",
            },
        ),
        rule_id="rule.retire.coverage",
        organization_scope="organization",
        allow_system_scope=False,
    )
    assert created.status == "draft"

    published = store.publish_revision(
        organization_id="org-1",
        rule_id="rule.retire.coverage",
        revision=1,
        allow_system_scope=False,
    )
    assert published.status == "published"

    retired = store.retire_revision(
        organization_id="org-1",
        rule_id="rule.retire.coverage",
        revision=1,
        allow_system_scope=False,
    )
    assert retired.status == "retired"

    listed = store.list_definitions(
        organization_id="org-1",
        organization_scope="organization",
        status="retired",
    )
    assert [item.rule_id for item in listed] == ["rule.retire.coverage"]
