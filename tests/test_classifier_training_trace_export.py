"""Tests for src/ruhu/classifier/training/trace_export.py — WI-6.1.

Pure-function tests + a SQLite end-to-end exercise of the
``export_from_session`` query path. Mirrors the structure of
``test_classifier_trace_export.py`` (the benchmark exporter).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepCompletion,
    StepTransition,
)
from ruhu.classifier.prompt import (
    build_classifier_prefix,
    build_classifier_suffix,
    reset_prefix_cache,
)
from ruhu.classifier.training.trace_export import (
    CANCELLATION_WINDOW,
    Bucket,
    TrainingRow,
    assign_bucket,
    detect_cancellation_patterns,
    export_from_session,
    extract_degraded_mode,
    extract_event_type,
    extract_outcome_labels,
    extract_user_text,
    load_confusion_pairs,
    main as cli_main,
    project_training_row,
    row_to_jsonl,
    write_training_jsonl,
)
from ruhu.schemas import OutcomeCondition


@pytest.fixture(autouse=True)
def _clear_prefix_cache():
    reset_prefix_cache()
    yield
    reset_prefix_cache()


def _entry_step(
    *,
    id: str = "entry",
    name: str = "Entry",
    description: str = "Triage the user.",
    outcomes: dict[str, str] | None = None,
) -> Step:
    """Build an entry step whose outcome catalog comes from authored
    ``OutcomeCondition`` transitions (the new edge-owned-outcomes shape)."""
    catalog = outcomes or {"transfer_status": "User asks about a transfer."}
    # Self-loop transitions keep the step graph valid (``to_step_id`` must
    # reference a known step) while still letting the prompt assembler
    # surface the outcome catalog.
    transitions = [
        StepTransition(
            id=f"t_{event}",
            to_step_id=id,
            when=OutcomeCondition(event=event, description=desc),
        )
        for event, desc in catalog.items()
    ]
    return Step(
        id=id,
        name=name,
        description=description,
        transitions=transitions,
        completion=StepCompletion(disposition="resolved"),
    )


def _doc(*, version: str = "v1", step: Step | None = None) -> AgentDocument:
    step = step or _entry_step()
    return AgentDocument(
        version=version,
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Main",
                start_step_id=step.id,
                steps=[step],
            )
        ],
    )


# ── pure helpers ────────────────────────────────────────────────────────────


def test_extract_event_type_pulls_from_extensions() -> None:
    rules = {"__trace_extensions__": {"event_type": "user_message"}}
    assert extract_event_type(rules) == "user_message"


def test_extract_event_type_returns_empty_when_absent() -> None:
    assert extract_event_type(None) == ""
    assert extract_event_type({}) == ""
    assert extract_event_type({"__trace_extensions__": {}}) == ""


def test_extract_degraded_mode_walks_decision_observability() -> None:
    rules = {
        "__trace_extensions__": {
            "decision_observability": {"degraded_mode": "classifier_unavailable"}
        }
    }
    assert extract_degraded_mode(rules) == "classifier_unavailable"


def test_extract_degraded_mode_returns_none_when_absent() -> None:
    assert extract_degraded_mode(None) is None
    assert extract_degraded_mode({"__trace_extensions__": {}}) is None
    assert extract_degraded_mode({"__trace_extensions__": {"decision_observability": {}}}) is None


def test_extract_user_text_handles_missing_fields() -> None:
    assert extract_user_text(None) is None
    assert extract_user_text({"__trace_extensions__": {}}) is None
    assert extract_user_text(
        {"__trace_extensions__": {"normalized_observation": {"text_present": False}}}
    ) is None


def test_extract_user_text_strips_and_returns() -> None:
    assert extract_user_text(
        {
            "__trace_extensions__": {
                "normalized_observation": {
                    "text_present": True,
                    "redacted_text": "  hi there  ",
                }
            }
        }
    ) == "hi there"


def test_extract_outcome_labels_dedupes_and_only_keeps_outcome_resolved() -> None:
    events = [
        {"family": "routing", "name": "outcome_resolved", "payload": {"event": "transfer_status"}},
        {"family": "routing", "name": "outcome_resolved", "payload": {"event": "transfer_status"}},
        {"family": "routing", "name": "outcome_resolved", "payload": {"event": "kyc_help"}},
        {"family": "routing", "name": "classifier_unavailable", "payload": {"reason": "x"}},
        {"family": "fact_extracted", "name": "email"},
        {"family": "system", "name": "noop"},
        # Analytics intent_detected events do NOT contribute to workflow
        # training labels — only routing.outcome_resolved does.
        {"family": "intent_detected", "name": "transfer_status"},
    ]
    assert extract_outcome_labels(events) == ["transfer_status", "kyc_help"]


def test_extract_outcome_labels_handles_empty_and_malformed() -> None:
    assert extract_outcome_labels(None) == []
    assert extract_outcome_labels([]) == []
    assert extract_outcome_labels([{"not_a_real_event": True}, "string"]) == []
    # outcome_resolved with no payload.event → skipped.
    assert extract_outcome_labels([{"family": "routing", "name": "outcome_resolved"}]) == []
    assert extract_outcome_labels(
        [{"family": "routing", "name": "outcome_resolved", "payload": {}}]
    ) == []


# ── assign_bucket ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "confidence, completed, expected",
    [
        (0.95, True, ("high_conf_completion", 1.0)),
        (0.95, False, ("other", 1.0)),
        (0.5, False, ("low_conf", 2.0)),
        (0.5, True, ("low_conf", 2.0)),
        (0.85, True, ("other", 1.0)),
        (0.2, True, ("other", 1.0)),
        (None, True, ("other", 1.0)),
    ],
)
def test_assign_bucket_thresholds(confidence, completed, expected) -> None:
    assert assign_bucket(confidence=confidence, is_completed=completed) == expected


def test_assign_bucket_confusion_pair_overrides_other_buckets() -> None:
    """Confusion pair takes precedence: weight=3 even on a high-confidence completion."""
    bucket, weight = assign_bucket(
        confidence=0.95, is_completed=True, confusion_pair=True
    )
    assert bucket == "confusion_pair"
    assert weight == 3.0


# ── project_training_row ───────────────────────────────────────────────────


def test_project_training_row_uses_canonical_prompt_assembler() -> None:
    step = _entry_step()
    document = _doc(step=step)
    row = project_training_row(
        agent_id="a",
        agent_version_id="v1",
        step=step,
        document=document,
        user_text="where is my money?",
        semantic_events_json=[
            {
                "family": "routing",
                "name": "outcome_resolved",
                "payload": {"event": "transfer_status"},
            }
        ],
        confidence=0.95,
        is_completed=True,
        conversation_id="conv-1",
        turn_recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert row.context == build_classifier_prefix(document, step)
    assert row.input_window == build_classifier_suffix("where is my money?")
    assert row.labels == ["transfer_status"]
    assert row.bucket == "high_conf_completion"
    assert row.weight == 1.0
    assert row.needs_teacher_relabel is False


def test_project_training_row_low_conf_marks_for_teacher_relabel() -> None:
    step = _entry_step()
    document = _doc(step=step)
    row = project_training_row(
        agent_id="a",
        agent_version_id="v1",
        step=step,
        document=document,
        user_text="hmm",
        semantic_events_json=[],
        confidence=0.5,
        is_completed=False,
        conversation_id="conv-1",
        turn_recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert row.bucket == "low_conf"
    assert row.weight == 2.0
    assert row.needs_teacher_relabel is True


def test_project_training_row_cancellation_marks_for_teacher_relabel() -> None:
    step = _entry_step()
    document = _doc(step=step)
    row = project_training_row(
        agent_id="a",
        agent_version_id="v1",
        step=step,
        document=document,
        user_text="cancel",
        semantic_events_json=[],
        confidence=0.95,
        is_completed=True,
        conversation_id="conv-1",
        turn_recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        cancellation_pattern=True,
    )
    assert row.cancellation_pattern is True
    assert row.needs_teacher_relabel is True
    # bucket-1 weight unchanged — cancellation is a flag, not a re-bucket
    assert row.bucket == "high_conf_completion"
    assert row.weight == 1.0


def test_project_training_row_confusion_pair_overrides_bucket() -> None:
    step = _entry_step()
    document = _doc(step=step)
    pairs = {frozenset({"transfer_status", "kyc_help"})}
    row = project_training_row(
        agent_id="a",
        agent_version_id="v1",
        step=step,
        document=document,
        user_text="x",
        semantic_events_json=[
            {
                "family": "routing",
                "name": "outcome_resolved",
                "payload": {"event": "transfer_status"},
            }
        ],
        confidence=0.95,
        is_completed=True,
        conversation_id="conv-1",
        turn_recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        confusion_pairs=pairs,
    )
    assert row.bucket == "confusion_pair"
    assert row.weight == 3.0
    assert row.needs_teacher_relabel is True


# ── detect_cancellation_patterns ───────────────────────────────────────────


def _ts(seconds: int = 0) -> datetime:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seconds)


def test_cancellation_detector_flags_close_followups_with_different_label() -> None:
    summaries = {
        "conv-1": [
            (_ts(0), "transfer_status"),
            (_ts(5), "kyc_help"),
        ]
    }
    flagged = detect_cancellation_patterns(summaries)
    assert flagged == {("conv-1", _ts(0))}


def test_cancellation_detector_ignores_followups_outside_window() -> None:
    summaries = {
        "conv-1": [
            (_ts(0), "transfer_status"),
            (_ts(int(CANCELLATION_WINDOW.total_seconds()) + 5), "kyc_help"),
        ]
    }
    assert detect_cancellation_patterns(summaries) == set()


def test_cancellation_detector_ignores_same_label_followups() -> None:
    summaries = {
        "conv-1": [
            (_ts(0), "transfer_status"),
            (_ts(3), "transfer_status"),
        ]
    }
    assert detect_cancellation_patterns(summaries) == set()


def test_cancellation_detector_handles_multiple_conversations_independently() -> None:
    summaries = {
        "conv-1": [(_ts(0), "a"), (_ts(2), "b")],
        "conv-2": [(_ts(100), "x"), (_ts(102), "x")],
        "conv-3": [(_ts(200), "p"), (_ts(220), "q")],  # outside window
    }
    assert detect_cancellation_patterns(summaries) == {("conv-1", _ts(0))}


# ── load_confusion_pairs ───────────────────────────────────────────────────


def test_load_confusion_pairs_returns_none_for_no_path() -> None:
    assert load_confusion_pairs(None) is None


def test_load_confusion_pairs_parses_pairs(tmp_path) -> None:
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([["a", "b"], ["c", "d"], ["a", "a"]]))
    pairs = load_confusion_pairs(p)
    assert pairs == {frozenset({"a", "b"}), frozenset({"c", "d"})}


def test_load_confusion_pairs_rejects_malformed(tmp_path) -> None:
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([["a"]]))
    with pytest.raises(ValueError):
        load_confusion_pairs(p)


# ── row_to_jsonl + write_training_jsonl ────────────────────────────────────


def test_row_to_jsonl_has_training_contract_at_top_level() -> None:
    row = TrainingRow(
        context="<prefix>",
        input_window="<suffix>",
        labels=["transfer_status"],
        bucket="high_conf_completion",
        agent_id="a",
        confidence=0.95,
    )
    parsed = json.loads(row_to_jsonl(row))
    assert set(parsed.keys()) == {"context", "input_window", "labels", "_metadata"}
    assert parsed["context"] == "<prefix>"
    assert parsed["input_window"] == "<suffix>"
    assert parsed["labels"] == ["transfer_status"]
    assert parsed["_metadata"]["bucket"] == "high_conf_completion"
    assert parsed["_metadata"]["confidence"] == 0.95


def test_row_to_jsonl_serialises_recorded_at_as_iso_string() -> None:
    row = TrainingRow(
        context="x",
        input_window="y",
        labels=[],
        turn_recorded_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    parsed = json.loads(row_to_jsonl(row))
    assert parsed["_metadata"]["turn_recorded_at"] == "2026-01-01T12:00:00+00:00"


def test_row_to_jsonl_handles_missing_recorded_at() -> None:
    row = TrainingRow(context="x", input_window="y", labels=[])
    parsed = json.loads(row_to_jsonl(row))
    assert parsed["_metadata"]["turn_recorded_at"] is None


def test_write_training_jsonl_lays_out_per_agent_file(tmp_path) -> None:
    rows = [
        TrainingRow(context=f"c{i}", input_window=f"i{i}", labels=[f"l{i}"])
        for i in range(3)
    ]
    out_path = write_training_jsonl(rows, output_dir=tmp_path, agent_id="agent_42")
    assert out_path == tmp_path / "agents" / "agent_42" / "raw_traces.jsonl"
    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["context"] == "c0"


# ── DB end-to-end with SQLite ──────────────────────────────────────────────


def _db_session() -> Session:
    """In-memory SQLite session restricted to the four tables we touch."""
    from ruhu.db_models import (
        AgentRecord,
        AgentVersionRecord,
        Base,
        ConversationRecord,
        TurnTraceRecord,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            AgentRecord.__table__,
            AgentVersionRecord.__table__,
            ConversationRecord.__table__,
            TurnTraceRecord.__table__,
        ],
    )
    return Session(engine)


def _insert_agent(session: Session, *, agent_id: str, version_id: str) -> None:
    from ruhu.db_models import AgentRecord, AgentVersionRecord

    now = datetime.now(timezone.utc)
    session.add(
        AgentRecord(
            agent_id=agent_id,
            name=agent_id,
            settings_json={},
            current_published_version_id=version_id,
            created_at=now,
            updated_at=now,
        )
    )
    document = _doc(version=version_id)
    session.add(
        AgentVersionRecord(
            version_id=version_id,
            agent_id=agent_id,
            status="published",
            version_number=1,
            agent_document_json=document.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


def _insert_conversation(
    session: Session,
    *,
    conversation_id: str,
    agent_id: str,
    agent_version_id: str,
    mode: str = "live",
    status: str = "active",
    outcome: str | None = None,
    created_at: datetime | None = None,
) -> None:
    from ruhu.db_models import ConversationRecord

    now = created_at or datetime.now(timezone.utc)
    session.add(
        ConversationRecord(
            conversation_id=conversation_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            mode=mode,
            status=status,
            outcome=outcome,
            step_id="entry",
            facts_json={},
            metadata_json={},
            processed_dedupe_keys_json=[],
            control_state_json={},
            last_event_sequence=0,
            started_at=now,
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
    conversation_id: str,
    user_text: str,
    intent: str | None,
    confidence: float | None,
    event_type: str = "user_message",
    degraded_mode: str | None = None,
    recorded_at: datetime | None = None,
    semantic_events: list | None = None,
) -> None:
    from ruhu.db_models import TurnTraceRecord

    rules = {
        "__trace_extensions__": {
            "event_type": event_type,
            "normalized_observation": {
                "text_present": True,
                "redacted_text": user_text,
            },
            "decision_observability": {"degraded_mode": degraded_mode},
        }
    }
    default_events: list = []
    if intent:
        default_events = [
            {
                "family": "routing",
                "name": "outcome_resolved",
                "payload": {"event": intent},
            }
        ]
    session.add(
        TurnTraceRecord(
            trace_id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            turn_id=str(uuid.uuid4()),
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            step_before="entry",
            step_after="entry",
            semantic_events_json=semantic_events if semantic_events is not None else default_events,
            fact_updates_json=[],
            chosen_action_json={},
            emitted_messages_json=[],
            tool_calls_json=[],
            rules_json=rules,
            latency_breakdown_ms_json={},
            classifier_json={"chosen_label": intent, "confidence": confidence},
            recorded_at=recorded_at or datetime.now(timezone.utc),
        )
    )
    session.flush()


def test_export_returns_empty_when_no_published_version() -> None:
    session = _db_session()
    from ruhu.db_models import AgentRecord

    now = datetime.now(timezone.utc)
    session.add(
        AgentRecord(
            agent_id="a",
            name="a",
            settings_json={},
            current_published_version_id=None,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
    assert export_from_session(session, agent_id="a") == []


def test_export_filters_event_type() -> None:
    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(session, conversation_id="c1", agent_id="a", agent_version_id="v1")
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="kept", intent="transfer_status", confidence=0.95,
        event_type="user_message",
    )
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="dropped", intent="transfer_status", confidence=0.95,
        event_type="system_event",
    )
    rows = export_from_session(session, agent_id="a")
    assert len(rows) == 1
    assert "kept" in rows[0].input_window


def test_export_drops_classifier_unavailable_turns() -> None:
    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(session, conversation_id="c1", agent_id="a", agent_version_id="v1")
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="kept", intent="transfer_status", confidence=0.95,
    )
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="degraded", intent="transfer_status", confidence=0.95,
        degraded_mode="classifier_unavailable",
    )
    rows = export_from_session(session, agent_id="a")
    assert [row.input_window for row in rows] == [
        build_classifier_suffix("kept")
    ]


def test_export_drops_test_mode_conversations() -> None:
    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(
        session,
        conversation_id="c-test",
        agent_id="a",
        agent_version_id="v1",
        mode="simulator",
    )
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c-test",
        user_text="dev noise", intent="transfer_status", confidence=0.95,
    )
    assert export_from_session(session, agent_id="a") == []


def test_export_drops_unclassified_turns() -> None:
    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(session, conversation_id="c1", agent_id="a", agent_version_id="v1")
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="no classifier", intent=None, confidence=None,
    )
    assert export_from_session(session, agent_id="a") == []


def test_export_skips_steps_not_in_published_version() -> None:
    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(session, conversation_id="c1", agent_id="a", agent_version_id="v1")
    from ruhu.db_models import TurnTraceRecord

    session.add(
        TurnTraceRecord(
            trace_id=str(uuid.uuid4()),
            conversation_id="c1",
            turn_id=str(uuid.uuid4()),
            agent_id="a",
            agent_version_id="v1",
            step_before="ghost_step",  # not in the published doc
            step_after="ghost_step",
            semantic_events_json=[
                {
                    "family": "routing",
                    "name": "outcome_resolved",
                    "payload": {"event": "transfer_status"},
                }
            ],
            fact_updates_json=[],
            chosen_action_json={},
            emitted_messages_json=[],
            tool_calls_json=[],
            rules_json={
                "__trace_extensions__": {
                    "event_type": "user_message",
                    "normalized_observation": {
                        "text_present": True,
                        "redacted_text": "hi",
                    },
                }
            },
            latency_breakdown_ms_json={},
            classifier_json={"chosen_label": "transfer_status", "confidence": 0.95},
            recorded_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    assert export_from_session(session, agent_id="a") == []


def test_export_assigns_high_conf_completion_when_outcome_resolved() -> None:
    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(
        session,
        conversation_id="c1",
        agent_id="a",
        agent_version_id="v1",
        outcome="resolved",
    )
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="thanks", intent="transfer_status", confidence=0.95,
    )
    rows = export_from_session(session, agent_id="a")
    assert rows[0].bucket == "high_conf_completion"
    assert rows[0].weight == 1.0


def test_export_assigns_low_conf_when_in_window() -> None:
    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(session, conversation_id="c1", agent_id="a", agent_version_id="v1")
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="hmm", intent="transfer_status", confidence=0.55,
    )
    rows = export_from_session(session, agent_id="a")
    assert rows[0].bucket == "low_conf"
    assert rows[0].weight == 2.0
    assert rows[0].needs_teacher_relabel is True


def test_export_flags_cancellation_for_close_relabel() -> None:
    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(session, conversation_id="c1", agent_id="a", agent_version_id="v1")
    # Use "now" so the lookback filter doesn't drop these rows.
    base = datetime.now(timezone.utc) - timedelta(minutes=5)
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="first attempt", intent="transfer_status", confidence=0.95,
        recorded_at=base,
    )
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="re-stated", intent="kyc_help", confidence=0.95,
        recorded_at=base + timedelta(seconds=4),
    )
    rows = sorted(export_from_session(session, agent_id="a"), key=lambda r: r.turn_recorded_at)
    # The earlier turn is the cancellation; the later one is the user's correction.
    assert rows[0].cancellation_pattern is True
    assert rows[1].cancellation_pattern is False


def test_export_filters_by_lookback_window() -> None:
    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(
        session,
        conversation_id="old",
        agent_id="a",
        agent_version_id="v1",
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    _insert_conversation(session, conversation_id="recent", agent_id="a", agent_version_id="v1")
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="old",
        user_text="ancient", intent="transfer_status", confidence=0.95,
        recorded_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="recent",
        user_text="now", intent="transfer_status", confidence=0.95,
    )
    rows = export_from_session(session, agent_id="a", lookback_days=30)
    assert {r.conversation_id for r in rows} == {"recent"}


def test_cli_main_writes_jsonl_to_per_agent_path(tmp_path) -> None:
    """CLI smoke: monkey-patch _run to inject a SQLite-backed session."""
    import ruhu.classifier.training.trace_export as te

    session = _db_session()
    _insert_agent(session, agent_id="a", version_id="v1")
    _insert_conversation(
        session, conversation_id="c1", agent_id="a", agent_version_id="v1",
        outcome="resolved",
    )
    _insert_trace(
        session, agent_id="a", agent_version_id="v1", conversation_id="c1",
        user_text="thanks", intent="transfer_status", confidence=0.95,
    )

    original = te._run
    te._run = lambda args: export_from_session(
        session,
        agent_id=args.agent_id,
        lookback_days=args.lookback_days,
    )
    try:
        rc = cli_main(
            [
                "--database-url", "sqlite:///:memory:",
                "--agent-id", "a",
                "--lookback-days", "30",
                "--output-dir", str(tmp_path),
            ]
        )
    finally:
        te._run = original

    assert rc == 0
    out_path = tmp_path / "agents" / "a" / "raw_traces.jsonl"
    assert out_path.exists()
    line = out_path.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["labels"] == ["transfer_status"]
    assert parsed["_metadata"]["bucket"] == "high_conf_completion"
