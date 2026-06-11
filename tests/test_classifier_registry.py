"""Tests for src/ruhu/classifier/registry.py — WI-6.5."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ruhu.classifier.registry import (
    VALID_STATUSES,
    promote_to_production,
    register_candidate,
    resolve_lora,
    retire,
    to_entry,
)
from ruhu.db_models import (
    AgentRecord,
    Base,
    ClassifierLoraRecord,
)


def _db_session() -> Session:
    """In-memory SQLite session limited to the tables this resolver touches."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[AgentRecord.__table__, ClassifierLoraRecord.__table__],
    )
    return Session(engine)


def _seed_agent(session: Session, agent_id: str) -> None:
    now = datetime.now(timezone.utc)
    session.add(
        AgentRecord(
            agent_id=agent_id,
            name=agent_id,
            settings_json={},
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


# ── resolve_lora ───────────────────────────────────────────────────────────


def test_resolve_lora_returns_none_when_no_rows() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    assert resolve_lora(session, agent_id="a1", step_id="entry") is None


def test_resolve_lora_returns_per_step_when_present() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    register_candidate(
        session,
        agent_id="a1",
        step_id="entry",
        lora_name="agent-a1-entry-v1",
        model_uri="gs://bucket/a1-entry-v1.safetensors",
        version="v1",
    )
    record = session.execute(
        ClassifierLoraRecord.__table__.select()
    ).fetchall()
    promote_to_production(session, lora_id=record[0].lora_id)
    assert resolve_lora(session, agent_id="a1", step_id="entry") == "agent-a1-entry-v1"


def test_resolve_lora_falls_back_to_per_agent_when_step_has_no_production() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    agent_wide = register_candidate(
        session,
        agent_id="a1",
        step_id=None,
        lora_name="agent-a1-v3",
        model_uri="gs://bucket/a1-v3.safetensors",
        version="v3",
    )
    promote_to_production(session, lora_id=agent_wide.lora_id)
    # Even when a candidate exists for the step, only production resolves.
    candidate_step = register_candidate(
        session,
        agent_id="a1",
        step_id="entry",
        lora_name="agent-a1-entry-cand",
        model_uri="gs://bucket/a1-entry-cand.safetensors",
        version="v_cand",
    )
    assert candidate_step.status == "candidate"

    resolved = resolve_lora(session, agent_id="a1", step_id="entry")
    assert resolved == "agent-a1-v3"


def test_resolve_lora_prefers_per_step_over_per_agent() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    agent_wide = register_candidate(
        session,
        agent_id="a1",
        step_id=None,
        lora_name="agent-a1-v3",
        model_uri="gs://bucket/a1-v3.safetensors",
        version="v3",
    )
    promote_to_production(session, lora_id=agent_wide.lora_id)
    step_specific = register_candidate(
        session,
        agent_id="a1",
        step_id="entry",
        lora_name="agent-a1-entry-v1",
        model_uri="gs://bucket/a1-entry-v1.safetensors",
        version="v1",
    )
    promote_to_production(session, lora_id=step_specific.lora_id)
    assert resolve_lora(session, agent_id="a1", step_id="entry") == "agent-a1-entry-v1"
    # And calling for a different step still gets the per-agent default
    assert resolve_lora(session, agent_id="a1", step_id="collect") == "agent-a1-v3"


def test_resolve_lora_skips_non_production_statuses() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    candidate = register_candidate(
        session,
        agent_id="a1",
        lora_name="agent-a1-cand",
        model_uri="gs://bucket/cand.safetensors",
        version="v_cand",
    )
    assert candidate.status == "candidate"
    assert resolve_lora(session, agent_id="a1", step_id="entry") is None
    assert resolve_lora(session, agent_id="a1", step_id=None) is None


def test_resolve_lora_scopes_by_organization_id() -> None:
    session = _db_session()
    _seed_agent(session, "shared_agent")

    org_a_lora = register_candidate(
        session,
        agent_id="shared_agent",
        organization_id="org-a",
        lora_name="org-a-lora",
        model_uri="gs://bucket/org-a.safetensors",
        version="v1",
    )
    org_b_lora = register_candidate(
        session,
        agent_id="shared_agent",
        organization_id="org-b",
        lora_name="org-b-lora",
        model_uri="gs://bucket/org-b.safetensors",
        version="v1",
    )
    promote_to_production(session, lora_id=org_a_lora.lora_id)
    promote_to_production(session, lora_id=org_b_lora.lora_id)

    assert (
        resolve_lora(session, agent_id="shared_agent", step_id=None, organization_id="org-a")
        == "org-a-lora"
    )
    assert (
        resolve_lora(session, agent_id="shared_agent", step_id=None, organization_id="org-b")
        == "org-b-lora"
    )
    assert (
        resolve_lora(session, agent_id="shared_agent", step_id=None, organization_id="org-c")
        is None
    )


def test_resolve_lora_treats_null_organization_id_as_distinct_from_set() -> None:
    """An untenanted production LoRA does not resolve for tenanted callers."""
    session = _db_session()
    _seed_agent(session, "a1")
    untenanted = register_candidate(
        session,
        agent_id="a1",
        organization_id=None,
        lora_name="untenanted",
        model_uri="gs://bucket/untenanted.safetensors",
        version="v1",
    )
    promote_to_production(session, lora_id=untenanted.lora_id)
    assert resolve_lora(session, agent_id="a1", step_id=None) == "untenanted"
    assert (
        resolve_lora(session, agent_id="a1", step_id=None, organization_id="org-x")
        is None
    )


def test_resolve_lora_skips_step_lookup_when_step_id_is_none() -> None:
    """resolve_lora(step_id=None) should never return a per-step row."""
    session = _db_session()
    _seed_agent(session, "a1")
    step_lora = register_candidate(
        session,
        agent_id="a1",
        step_id="entry",
        lora_name="step-entry",
        model_uri="gs://bucket/step.safetensors",
        version="v1",
    )
    promote_to_production(session, lora_id=step_lora.lora_id)
    assert resolve_lora(session, agent_id="a1", step_id=None) is None


def test_resolve_lora_picks_most_recent_when_multiple_production_rows_exist() -> None:
    """Defensive: if two rows somehow share scope and both are production
    (e.g. data drift), prefer the most recently-published one."""
    session = _db_session()
    _seed_agent(session, "a1")
    older_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer_ts = datetime(2026, 5, 1, tzinfo=timezone.utc)

    older = register_candidate(
        session,
        agent_id="a1",
        lora_name="older",
        model_uri="gs://bucket/older.safetensors",
        version="v1",
    )
    older.status = "production"
    older.published_at = older_ts
    newer = register_candidate(
        session,
        agent_id="a1",
        lora_name="newer",
        model_uri="gs://bucket/newer.safetensors",
        version="v2",
    )
    newer.status = "production"
    newer.published_at = newer_ts
    session.flush()
    assert resolve_lora(session, agent_id="a1", step_id=None) == "newer"


# ── register_candidate ─────────────────────────────────────────────────────


def test_register_candidate_creates_row_with_status_candidate() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    record = register_candidate(
        session,
        agent_id="a1",
        lora_name="agent-a1-v1",
        model_uri="gs://bucket/a1-v1.safetensors",
        version="v1",
        eval_score={"macro_f1": 0.92},
    )
    assert record.lora_id  # uuid generated
    assert record.status == "candidate"
    assert record.eval_score_json == {"macro_f1": 0.92}
    assert record.published_at is None


def test_register_candidate_unique_lora_name_constraint() -> None:
    """Two rows with the same lora_name must violate the unique constraint."""
    from sqlalchemy.exc import IntegrityError

    session = _db_session()
    _seed_agent(session, "a1")
    register_candidate(
        session,
        agent_id="a1",
        lora_name="agent-a1-v1",
        model_uri="gs://bucket/a1-v1.safetensors",
        version="v1",
    )
    with pytest.raises(IntegrityError):
        register_candidate(
            session,
            agent_id="a1",
            lora_name="agent-a1-v1",
            model_uri="gs://bucket/a1-v1-dup.safetensors",
            version="v1",
        )


# ── promote_to_production ──────────────────────────────────────────────────


def test_promote_to_production_demotes_prior_to_shadow() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    first = register_candidate(
        session,
        agent_id="a1",
        lora_name="agent-a1-v1",
        model_uri="gs://bucket/a1-v1.safetensors",
        version="v1",
    )
    promote_to_production(session, lora_id=first.lora_id)
    second = register_candidate(
        session,
        agent_id="a1",
        lora_name="agent-a1-v2",
        model_uri="gs://bucket/a1-v2.safetensors",
        version="v2",
    )
    promote_to_production(session, lora_id=second.lora_id)

    session.refresh(first)
    session.refresh(second)
    assert first.status == "shadow"
    assert second.status == "production"
    assert second.published_at is not None


def test_promote_to_production_only_demotes_same_scope() -> None:
    """A production LoRA in a different (agent_id, step_id) scope is unaffected."""
    session = _db_session()
    _seed_agent(session, "a1")
    _seed_agent(session, "a2")
    a1_first = register_candidate(
        session, agent_id="a1", lora_name="a1-v1",
        model_uri="gs://bucket/a1-v1.safetensors", version="v1",
    )
    a2_first = register_candidate(
        session, agent_id="a2", lora_name="a2-v1",
        model_uri="gs://bucket/a2-v1.safetensors", version="v1",
    )
    promote_to_production(session, lora_id=a1_first.lora_id)
    promote_to_production(session, lora_id=a2_first.lora_id)

    a1_second = register_candidate(
        session, agent_id="a1", lora_name="a1-v2",
        model_uri="gs://bucket/a1-v2.safetensors", version="v2",
    )
    promote_to_production(session, lora_id=a1_second.lora_id)

    session.refresh(a1_first)
    session.refresh(a2_first)
    session.refresh(a1_second)
    assert a1_first.status == "shadow"
    assert a2_first.status == "production"  # untouched
    assert a1_second.status == "production"


def test_promote_to_production_on_first_production_skips_demotion_step() -> None:
    """First-ever promotion has no prior to demote — should still succeed cleanly."""
    session = _db_session()
    _seed_agent(session, "a1")
    record = register_candidate(
        session, agent_id="a1", lora_name="a1-v1",
        model_uri="gs://bucket/a1-v1.safetensors", version="v1",
    )
    promoted = promote_to_production(session, lora_id=record.lora_id)
    assert promoted.status == "production"
    assert promoted.published_at is not None


def test_promote_to_production_unknown_lora_id_raises() -> None:
    session = _db_session()
    with pytest.raises(ValueError, match="unknown lora_id"):
        promote_to_production(session, lora_id="does-not-exist")


def test_promote_to_production_retired_lora_raises() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    record = register_candidate(
        session, agent_id="a1", lora_name="a1-v1",
        model_uri="gs://bucket/a1-v1.safetensors", version="v1",
    )
    retire(session, lora_id=record.lora_id)
    with pytest.raises(ValueError, match="retired"):
        promote_to_production(session, lora_id=record.lora_id)


def test_promote_to_production_preserves_published_at_on_re_promote() -> None:
    """Re-promoting (e.g. after toggling) should keep the original publish timestamp."""
    session = _db_session()
    _seed_agent(session, "a1")
    record = register_candidate(
        session, agent_id="a1", lora_name="a1-v1",
        model_uri="gs://bucket/a1-v1.safetensors", version="v1",
    )
    first = promote_to_production(session, lora_id=record.lora_id)
    original_published = first.published_at

    # Re-promoting an already-production row is a no-op on published_at.
    second = promote_to_production(
        session,
        lora_id=record.lora_id,
        now=original_published + timedelta(hours=1),
    )
    assert second.published_at == original_published


# ── retire ─────────────────────────────────────────────────────────────────


def test_retire_marks_row_retired() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    record = register_candidate(
        session, agent_id="a1", lora_name="a1-v1",
        model_uri="gs://bucket/a1-v1.safetensors", version="v1",
    )
    retire(session, lora_id=record.lora_id)
    session.refresh(record)
    assert record.status == "retired"


def test_retire_unknown_lora_id_raises() -> None:
    session = _db_session()
    with pytest.raises(ValueError, match="unknown lora_id"):
        retire(session, lora_id="bogus")


def test_retired_row_does_not_resolve() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    record = register_candidate(
        session, agent_id="a1", lora_name="a1-v1",
        model_uri="gs://bucket/a1-v1.safetensors", version="v1",
    )
    promote_to_production(session, lora_id=record.lora_id)
    assert resolve_lora(session, agent_id="a1", step_id=None) == "a1-v1"
    retire(session, lora_id=record.lora_id)
    assert resolve_lora(session, agent_id="a1", step_id=None) is None


# ── to_entry ───────────────────────────────────────────────────────────────


def test_to_entry_projects_columns() -> None:
    session = _db_session()
    _seed_agent(session, "a1")
    record = register_candidate(
        session,
        agent_id="a1",
        step_id="entry",
        organization_id="org-a",
        lora_name="agent-a1-entry-v1",
        model_uri="gs://bucket/a1-entry-v1.safetensors",
        version="v1",
    )
    entry = to_entry(record)
    assert entry.lora_id == record.lora_id
    assert entry.organization_id == "org-a"
    assert entry.agent_id == "a1"
    assert entry.step_id == "entry"
    assert entry.lora_name == "agent-a1-entry-v1"
    assert entry.status == "candidate"
    assert entry.published_at is None


def test_valid_statuses_constant_matches_documented_set() -> None:
    assert VALID_STATUSES == frozenset(
        {"candidate", "production", "shadow", "retired"}
    )
