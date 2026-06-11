"""Tests for trace_export — production-trace → Stage 2.5 eval JSONL.

Edge-owned outcomes shape: each step's outcome catalog comes from its
``OutcomeCondition`` transitions (``when.kind == "outcome"``), and the
trace's ``classifier_json`` carries ``chosen_label`` rather than the
legacy ``intent_name``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ruhu.classifier.benchmark.eval_set import EvalRow, load_eval_set
from ruhu.classifier.benchmark.trace_export import (
    TraceRow,
    export_from_session,
    extract_user_text,
    find_step_in_doc,
    main as cli_main,
    outcome_catalog_for_step_dict,
    project_eval_row,
    stratified_sample,
    write_jsonl,
)


# ── outcome_catalog_for_step_dict ───────────────────────────────────────────


def _outcome(event: str, description: str, *, to: str = "next", tid: str | None = None) -> dict:
    return {
        "id": tid or f"t_{event}",
        "to_step_id": to,
        "when": {"kind": "outcome", "event": event, "description": description},
    }


def test_outcome_catalog_reads_authored_outcome_transitions() -> None:
    step = {
        "transitions": [
            _outcome("transfer_status", "User asks about a transfer."),
            _outcome("kyc_help", "User has a KYC question.", tid="t2"),
        ]
    }
    catalog = outcome_catalog_for_step_dict(step)
    assert catalog == {
        "kyc_help": "User has a KYC question.",
        "transfer_status": "User asks about a transfer.",
    }


def test_outcome_catalog_skips_non_outcome_conditions() -> None:
    step = {
        "transitions": [
            {"id": "t1", "to_step_id": "next", "when": {"kind": "fact_present", "fact_name": "email"}},
            {"id": "t2", "to_step_id": "exit", "when": {"kind": "otherwise"}},
            _outcome("transfer_status", "User asks about a transfer."),
        ]
    }
    assert outcome_catalog_for_step_dict(step) == {
        "transfer_status": "User asks about a transfer.",
    }


def test_outcome_catalog_handles_step_with_no_outcome_transitions() -> None:
    step = {"transitions": [{"id": "t1", "to_step_id": "exit", "when": {"kind": "otherwise"}}]}
    assert outcome_catalog_for_step_dict(step) == {}


def test_outcome_catalog_handles_step_with_no_transitions_field() -> None:
    assert outcome_catalog_for_step_dict({}) == {}


def test_outcome_catalog_skips_outcome_with_missing_event() -> None:
    step = {"transitions": [{"id": "t1", "to_step_id": "next", "when": {"kind": "outcome"}}]}
    assert outcome_catalog_for_step_dict(step) == {}


def test_outcome_catalog_uses_empty_string_when_description_missing() -> None:
    step = {
        "transitions": [
            {"id": "t1", "to_step_id": "next", "when": {"kind": "outcome", "event": "foo"}}
        ]
    }
    assert outcome_catalog_for_step_dict(step) == {"foo": ""}


# ── find_step_in_doc / extract_user_text ────────────────────────────────────


def test_find_step_in_doc_walks_scenarios() -> None:
    doc = {
        "scenarios": [
            {"id": "s1", "steps": [{"id": "a"}, {"id": "b"}]},
            {"id": "s2", "steps": [{"id": "c"}]},
        ]
    }
    assert find_step_in_doc(doc, "b")["id"] == "b"
    assert find_step_in_doc(doc, "c")["id"] == "c"
    assert find_step_in_doc(doc, "missing") is None


def test_extract_user_text_pulls_redacted_text() -> None:
    rules = {
        "__trace_extensions__": {
            "normalized_observation": {
                "text_present": True,
                "redacted_text": "where is my money?",
            }
        }
    }
    assert extract_user_text(rules) == "where is my money?"


def test_extract_user_text_returns_none_when_text_absent() -> None:
    assert extract_user_text({}) is None
    assert extract_user_text({"__trace_extensions__": {}}) is None
    assert (
        extract_user_text(
            {"__trace_extensions__": {"normalized_observation": {"text_present": False}}}
        )
        is None
    )
    assert (
        extract_user_text(
            {
                "__trace_extensions__": {
                    "normalized_observation": {"text_present": True, "redacted_text": ""}
                }
            }
        )
        is None
    )


# ── project_eval_row ────────────────────────────────────────────────────────


def _doc_with_step(step: dict) -> dict:
    return {
        "scenarios": [
            {"id": "main", "steps": [step]},
        ]
    }


def _step_with_outcome(
    event: str = "transfer_status",
    description: str = "User asks about a transfer.",
    *,
    id: str = "entry",
    name: str | None = None,
    summary: str | None = None,
    description_field: str | None = None,
) -> dict:
    step: dict = {"id": id, "transitions": [_outcome(event, description)]}
    if name is not None:
        step["name"] = name
    if summary is not None:
        step["summary"] = summary
    if description_field is not None:
        step["description"] = description_field
    return step


def _trace(
    *,
    chosen_label: str | None,
    confidence: float | None,
    user_text: str = "hi",
    step_id: str = "entry",
) -> TraceRow:
    return TraceRow(
        agent_id="agent_a",
        agent_version_id="v1",
        step_id=step_id,
        user_text=user_text,
        classifier_chosen_label=chosen_label,
        classifier_confidence=confidence,
    )


def test_project_eval_row_silver_keeps_high_confidence() -> None:
    doc = _doc_with_step(_step_with_outcome(name="Entry"))
    trace = _trace(chosen_label="transfer_status", confidence=0.95)
    row = project_eval_row(trace, doc, label_mode="silver", min_confidence=0.85)
    assert row is not None
    assert row.gold_chosen_label == "transfer_status"
    assert row.candidate_labels == {"transfer_status": "User asks about a transfer."}


def test_project_eval_row_silver_drops_low_confidence() -> None:
    doc = _doc_with_step(_step_with_outcome())
    trace = _trace(chosen_label="transfer_status", confidence=0.4)
    assert project_eval_row(trace, doc, label_mode="silver", min_confidence=0.85) is None


def test_project_eval_row_unlabeled_keeps_row_with_null_gold() -> None:
    doc = _doc_with_step(_step_with_outcome())
    trace = _trace(chosen_label="transfer_status", confidence=0.4)
    row = project_eval_row(trace, doc, label_mode="unlabeled")
    assert row is not None
    assert row.gold_chosen_label is None


def test_project_eval_row_predicted_passes_unknown_through() -> None:
    doc = _doc_with_step(_step_with_outcome())
    trace = _trace(chosen_label=None, confidence=0.0)
    row = project_eval_row(trace, doc, label_mode="predicted")
    assert row is not None
    assert row.gold_chosen_label is None


def test_project_eval_row_skips_when_step_missing() -> None:
    doc = _doc_with_step(_step_with_outcome(id="other"))
    trace = _trace(chosen_label="transfer_status", confidence=0.95)
    assert project_eval_row(trace, doc, label_mode="silver") is None


def test_project_eval_row_skips_when_step_has_no_outcomes() -> None:
    doc = _doc_with_step({"id": "entry", "name": "Entry"})
    trace = _trace(chosen_label="anything", confidence=0.95)
    assert project_eval_row(trace, doc, label_mode="silver") is None


def test_project_eval_row_uses_step_description_for_summary() -> None:
    doc = _doc_with_step(
        _step_with_outcome(
            name="Entry",
            description_field="Triage the user's reason for contacting.",
        )
    )
    trace = _trace(chosen_label="transfer_status", confidence=0.95)
    row = project_eval_row(trace, doc, label_mode="silver")
    assert row is not None
    assert row.step_summary == "Triage the user's reason for contacting."


# ── stratified_sample ───────────────────────────────────────────────────────


def _row_factory(step_id: str, idx: int, agent_id: str = "a") -> EvalRow:
    return EvalRow(
        agent_id=agent_id,
        agent_version_id="v",
        step_id=step_id,
        step_name=step_id,
        step_summary="",
        candidate_labels={"x": "x"},
        user_text=f"text-{idx}",
        gold_chosen_label="x",
    )


def test_stratified_sample_caps_per_step_bucket() -> None:
    rows = [_row_factory("entry", i) for i in range(20)] + [
        _row_factory("collect", i) for i in range(20)
    ]
    sample = stratified_sample(rows, rows_per_step=5, seed=0)
    assert len(sample) == 10
    assert sum(1 for r in sample if r.step_id == "entry") == 5
    assert sum(1 for r in sample if r.step_id == "collect") == 5


def test_stratified_sample_keeps_smaller_buckets_intact() -> None:
    rows = [_row_factory("entry", i) for i in range(3)] + [
        _row_factory("collect", i) for i in range(8)
    ]
    sample = stratified_sample(rows, rows_per_step=5, seed=0)
    assert sum(1 for r in sample if r.step_id == "entry") == 3
    assert sum(1 for r in sample if r.step_id == "collect") == 5


def test_stratified_sample_is_deterministic_for_same_seed() -> None:
    rows = [_row_factory("entry", i) for i in range(50)]
    a = stratified_sample(rows, rows_per_step=10, seed=42)
    b = stratified_sample(rows, rows_per_step=10, seed=42)
    assert [r.user_text for r in a] == [r.user_text for r in b]


def test_stratified_sample_different_seeds_pick_different_rows() -> None:
    rows = [_row_factory("entry", i) for i in range(50)]
    a = stratified_sample(rows, rows_per_step=10, seed=1)
    b = stratified_sample(rows, rows_per_step=10, seed=2)
    assert [r.user_text for r in a] != [r.user_text for r in b]


def test_stratified_sample_partitions_by_agent() -> None:
    rows = (
        [_row_factory("entry", i, agent_id="a1") for i in range(8)]
        + [_row_factory("entry", i, agent_id="a2") for i in range(8)]
    )
    sample = stratified_sample(rows, rows_per_step=5, seed=0)
    assert sum(1 for r in sample if r.agent_id == "a1") == 5
    assert sum(1 for r in sample if r.agent_id == "a2") == 5


def test_stratified_sample_invalid_cap_raises() -> None:
    with pytest.raises(ValueError):
        stratified_sample([], rows_per_step=0)


# ── write_jsonl + EvalRow round-trip ────────────────────────────────────────


def test_write_jsonl_round_trips_through_load_eval_set(tmp_path) -> None:
    rows = [
        EvalRow(
            agent_id="a",
            agent_version_id="v",
            step_id="s",
            step_name="S",
            step_summary="",
            candidate_labels={"x": "x"},
            user_text="hello",
            gold_chosen_label="x",
            language="en",
        )
    ]
    path = tmp_path / "eval.jsonl"
    written = write_jsonl(rows, path)
    assert written == 1
    loaded = load_eval_set(path)
    assert len(loaded) == 1
    assert loaded[0].user_text == "hello"
    assert loaded[0].gold_chosen_label == "x"


def test_write_jsonl_creates_parent_dir(tmp_path) -> None:
    target = tmp_path / "nested" / "eval.jsonl"
    rows = [
        EvalRow(
            agent_id="a",
            agent_version_id="v",
            step_id="s",
            step_name="S",
            step_summary="",
            candidate_labels={"x": "x"},
            user_text="hi",
            gold_chosen_label="x",
        )
    ]
    write_jsonl(rows, target)
    assert target.exists()


# ── DB end-to-end with SQLite ───────────────────────────────────────────────


def _db_session() -> Session:
    """In-memory SQLite session with only the tables this exporter touches.

    The rest of the schema includes Postgres ARRAY columns SQLite can't
    render, so we narrow ``create_all`` to the three tables we query.
    """
    from ruhu.db_models import (
        AgentRecord,
        AgentVersionRecord,
        Base,
        TurnTraceRecord,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            AgentRecord.__table__,
            AgentVersionRecord.__table__,
            TurnTraceRecord.__table__,
        ],
    )
    return Session(engine)


def _insert_agent_version(
    session: Session,
    *,
    version_id: str,
    agent_id: str,
    document: dict,
) -> None:
    from ruhu.db_models import AgentRecord, AgentVersionRecord

    now = datetime.now(timezone.utc)
    if not session.get(AgentRecord, agent_id):
        session.add(
            AgentRecord(
                agent_id=agent_id,
                name=agent_id,
                settings_json={},
                created_at=now,
                updated_at=now,
            )
        )
    session.add(
        AgentVersionRecord(
            version_id=version_id,
            agent_id=agent_id,
            status="published",
            version_number=1,
            agent_document_json=document,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


def _insert_trace(
    session: Session,
    *,
    agent_id: str,
    agent_version_id: str,
    step_id: str,
    user_text: str,
    chosen_label: str | None,
    confidence: float | None,
    recorded_at: datetime | None = None,
) -> None:
    from ruhu.db_models import TurnTraceRecord

    session.add(
        TurnTraceRecord(
            trace_id=str(uuid.uuid4()),
            conversation_id="conv-1",
            turn_id=str(uuid.uuid4()),
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            step_before=step_id,
            step_after=step_id,
            semantic_events_json=[],
            fact_updates_json=[],
            chosen_action_json={},
            emitted_messages_json=[],
            tool_calls_json=[],
            rules_json={
                "__trace_extensions__": {
                    "normalized_observation": {
                        "text_present": True,
                        "redacted_text": user_text,
                    }
                }
            },
            latency_breakdown_ms_json={},
            classifier_json={"chosen_label": chosen_label, "confidence": confidence},
            recorded_at=recorded_at or datetime.now(timezone.utc),
        )
    )
    session.flush()


def test_export_from_session_silver_mode_filters_by_confidence() -> None:
    session = _db_session()
    doc = _doc_with_step(_step_with_outcome(name="Entry"))
    _insert_agent_version(session, version_id="v1", agent_id="a1", document=doc)
    _insert_trace(
        session, agent_id="a1", agent_version_id="v1", step_id="entry",
        user_text="where is my money", chosen_label="transfer_status", confidence=0.95,
    )
    _insert_trace(
        session, agent_id="a1", agent_version_id="v1", step_id="entry",
        user_text="hmm", chosen_label="transfer_status", confidence=0.4,
    )

    rows = export_from_session(session, label_mode="silver", min_confidence=0.85)
    assert len(rows) == 1
    assert rows[0].user_text == "where is my money"
    assert rows[0].gold_chosen_label == "transfer_status"


def test_export_from_session_unlabeled_keeps_low_confidence_rows() -> None:
    session = _db_session()
    doc = _doc_with_step(_step_with_outcome())
    _insert_agent_version(session, version_id="v1", agent_id="a1", document=doc)
    _insert_trace(
        session, agent_id="a1", agent_version_id="v1", step_id="entry",
        user_text="hmm", chosen_label="transfer_status", confidence=0.4,
    )
    rows = export_from_session(session, label_mode="unlabeled")
    assert len(rows) == 1
    assert rows[0].gold_chosen_label is None


def test_export_from_session_skips_traces_without_text() -> None:
    session = _db_session()
    doc = _doc_with_step(_step_with_outcome())
    _insert_agent_version(session, version_id="v1", agent_id="a1", document=doc)

    from ruhu.db_models import TurnTraceRecord
    session.add(
        TurnTraceRecord(
            trace_id="t1",
            conversation_id="c1",
            turn_id="x",
            agent_id="a1",
            agent_version_id="v1",
            step_before="entry",
            step_after="entry",
            semantic_events_json=[],
            fact_updates_json=[],
            chosen_action_json={},
            emitted_messages_json=[],
            tool_calls_json=[],
            rules_json={},  # No __trace_extensions__ key.
            latency_breakdown_ms_json={},
            classifier_json={"chosen_label": "transfer_status", "confidence": 0.95},
            recorded_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    assert export_from_session(session, label_mode="silver") == []


def test_export_from_session_filters_by_date_range() -> None:
    session = _db_session()
    doc = _doc_with_step(_step_with_outcome("x", "X description."))
    _insert_agent_version(session, version_id="v1", agent_id="a1", document=doc)
    now = datetime.now(timezone.utc)
    _insert_trace(
        session, agent_id="a1", agent_version_id="v1", step_id="entry",
        user_text="early", chosen_label="x", confidence=0.95,
        recorded_at=now - timedelta(days=10),
    )
    _insert_trace(
        session, agent_id="a1", agent_version_id="v1", step_id="entry",
        user_text="recent", chosen_label="x", confidence=0.95,
        recorded_at=now - timedelta(hours=1),
    )
    rows = export_from_session(
        session,
        label_mode="silver",
        start_date=now - timedelta(days=2),
    )
    assert [r.user_text for r in rows] == ["recent"]


def test_export_from_session_filters_by_agent_id() -> None:
    session = _db_session()
    doc = _doc_with_step(_step_with_outcome("x", "X description."))
    _insert_agent_version(session, version_id="v1", agent_id="a1", document=doc)
    _insert_agent_version(session, version_id="v2", agent_id="a2", document=doc)
    _insert_trace(
        session, agent_id="a1", agent_version_id="v1", step_id="entry",
        user_text="hi-1", chosen_label="x", confidence=0.95,
    )
    _insert_trace(
        session, agent_id="a2", agent_version_id="v2", step_id="entry",
        user_text="hi-2", chosen_label="x", confidence=0.95,
    )
    rows = export_from_session(session, agent_id="a1", label_mode="silver")
    assert [r.user_text for r in rows] == ["hi-1"]


def test_export_from_session_stratified_caps_per_step() -> None:
    session = _db_session()
    doc = {
        "scenarios": [
            {
                "id": "main",
                "steps": [
                    _step_with_outcome("x", "X description.", id="entry"),
                    _step_with_outcome("x", "X description.", id="collect"),
                ],
            }
        ]
    }
    _insert_agent_version(session, version_id="v1", agent_id="a1", document=doc)
    for i in range(20):
        _insert_trace(
            session, agent_id="a1", agent_version_id="v1", step_id="entry",
            user_text=f"entry-{i}", chosen_label="x", confidence=0.95,
        )
    for i in range(15):
        _insert_trace(
            session, agent_id="a1", agent_version_id="v1", step_id="collect",
            user_text=f"collect-{i}", chosen_label="x", confidence=0.95,
        )
    rows = export_from_session(session, label_mode="silver", rows_per_step=5)
    by_step = {"entry": 0, "collect": 0}
    for r in rows:
        by_step[r.step_id] += 1
    assert by_step == {"entry": 5, "collect": 5}


def test_cli_main_writes_jsonl(tmp_path) -> None:
    """CLI: end-to-end smoke against an in-memory SQLite + monkey-patched engine."""
    import ruhu.classifier.benchmark.trace_export as te

    session = _db_session()
    doc = _doc_with_step(_step_with_outcome("x", "X description."))
    _insert_agent_version(session, version_id="v1", agent_id="a1", document=doc)
    _insert_trace(
        session, agent_id="a1", agent_version_id="v1", step_id="entry",
        user_text="hi there", chosen_label="x", confidence=0.95,
    )

    captured: dict = {}

    def _fake_run(args):
        captured["called"] = True
        return export_from_session(
            session,
            agent_id=args.agent_id,
            label_mode=args.label_mode,
            min_confidence=args.min_confidence,
            rows_per_step=args.rows_per_step,
            seed=args.seed,
        )

    original = te._run
    te._run = _fake_run
    try:
        out = tmp_path / "out.jsonl"
        rc = cli_main(
            [
                "--database-url", "sqlite:///:memory:",
                "--label-mode", "silver",
                "--rows-per-step", "10",
                "--output", str(out),
            ]
        )
    finally:
        te._run = original

    assert rc == 0
    assert captured["called"] is True
    rows_text = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows_text) == 1
    parsed = json.loads(rows_text[0])
    assert parsed["user_text"] == "hi there"
    assert parsed["gold_chosen_label"] == "x"
