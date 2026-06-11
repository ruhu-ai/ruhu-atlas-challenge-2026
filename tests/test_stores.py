from __future__ import annotations

from datetime import datetime, timezone
import json

from ruhu.db_models import TurnTraceRecord
from ruhu.db import build_session_factory
from ruhu.rules import RuleMatch, RuleStageDecision, RuleTrace, RuntimeRulesTrace, WarnEffect
from ruhu.schemas import (
    ActionRecord,
    ClassifierTraceRecord,
    ConversationState,
    SemanticEventRecord,
    TurnTrace,
)
from ruhu.stores import (
    SQLAlchemyConversationStore,
    SQLAlchemyTraceStore,
    _project_classifier_trace,
    _record_to_trace,
)


def test_sqlalchemy_conversation_store_roundtrip(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyConversationStore(session_factory)
    state = ConversationState(
        conversation_id="conv_1",
        agent_id="sales",
        agent_version_id="version_sales",
        step_id="product_qa",
        facts={"email": "test@example.com"},
        processed_dedupe_keys=["turn_1"],
        updated_at=datetime.now(timezone.utc),
    )

    store.save(state)

    loaded = store.load("conv_1")
    assert loaded is not None
    assert loaded.agent_id == "sales"
    assert loaded.facts["email"] == "test@example.com"
    assert store.list_conversations()[0].conversation_id == "conv_1"


def test_sqlalchemy_conversation_store_merges_concurrent_fact_snapshots(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyConversationStore(session_factory)
    base = ConversationState(
        conversation_id="conv_race",
        agent_id="sales",
        agent_version_id="version_sales",
        step_id="collect_callback_details",
        facts={"customer_name": "Ada"},
        metadata={
            "__ruhu_fact_metadata__": {
                "customer_name": {"captured_at": "2026-05-29T08:00:00+00:00"},
            },
            "__ruhu_step_missing_facts__": ["phone_number", "preferred_branch"],
            "__ruhu_current_step_id__": "collect_callback_details",
            "__ruhu_cursor_revision__": 2,
        },
        updated_at=datetime(2026, 5, 29, 8, 0, tzinfo=timezone.utc),
    )
    branch_turn = base.model_copy(deep=True)
    branch_turn.facts["preferred_branch"] = "Abuja"
    branch_turn.metadata["__ruhu_fact_metadata__"]["preferred_branch"] = {
        "captured_at": "2026-05-29T08:01:00+00:00",
    }
    branch_turn.metadata["__ruhu_step_missing_facts__"] = ["phone_number"]
    branch_turn.metadata["__ruhu_current_step_id__"] = "create_callback_ticket"
    branch_turn.metadata["__ruhu_cursor_revision__"] = 3
    branch_turn.step_id = "create_callback_ticket"
    stale_turn = base.model_copy(deep=True)
    stale_turn.updated_at = datetime(2026, 5, 29, 8, 2, tzinfo=timezone.utc)

    store.save(base)
    store.save(branch_turn)
    store.save(stale_turn)

    loaded = store.load("conv_race")
    assert loaded is not None
    assert loaded.facts["customer_name"] == "Ada"
    assert loaded.facts["preferred_branch"] == "Abuja"
    assert loaded.step_id == "create_callback_ticket"
    assert loaded.metadata["__ruhu_current_step_id__"] == "create_callback_ticket"
    assert loaded.metadata["__ruhu_cursor_revision__"] == 3
    assert "preferred_branch" not in loaded.metadata["__ruhu_step_missing_facts__"]


def test_sqlalchemy_trace_store_roundtrip(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyTraceStore(session_factory)
    recorded_at = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
    trace = TurnTrace(
        trace_id="trace_1",
        conversation_id="conv_1",
        turn_id="turn_1",
        agent_id="sales",
        step_before="discover",
        step_after="product_qa",
        chosen_action=ActionRecord(type="transition", reason="transition_to:product_qa"),
        rules=RuntimeRulesTrace(
            evaluations=[
                RuleStageDecision(
                    stage="turn_ingress",
                    traces=[
                        RuleTrace(
                            binding_id="bind.turn.warn",
                            rule_id="rule.turn.warn",
                            revision=1,
                            outcome="matched",
                            mode="enforce",
                            effect_kind="warn",
                        )
                    ],
                    matched_rules=[
                        RuleMatch(
                            binding_id="bind.turn.warn",
                            rule_id="rule.turn.warn",
                            revision=1,
                            rule_name="Warn on refund keyword",
                            mode="enforce",
                            effect=WarnEffect(code="refund_warning", message="Refund keyword detected."),
                        )
                    ],
                    terminal_effect=WarnEffect(
                        code="refund_warning",
                        message="Refund keyword detected.",
                    ),
                )
            ]
        ),
        latency_breakdown_ms={"total": 12},
        recorded_at=recorded_at,
    )

    store.append(trace)

    loaded = store.by_conversation("conv_1")
    assert len(loaded) == 1
    assert loaded[0].trace_id == "trace_1"
    assert loaded[0].rules.evaluations[0].stage == "turn_ingress"
    assert loaded[0].rules.evaluations[0].terminal_effect is not None
    assert loaded[0].rules.evaluations[0].terminal_effect.kind == "warn"
    assert loaded[0].rules.evaluations[0].matched_rules[0].effect.code == "refund_warning"
    assert store.all()[0].step_after == "product_qa"
    assert loaded[0].recorded_at == recorded_at


def test_record_to_trace_accepts_legacy_stringified_json_payloads() -> None:
    recorded_at = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
    record = TurnTraceRecord(
        trace_id="trace_legacy",
        conversation_id="conv_legacy",
        organization_id="public",
        turn_id="turn_legacy",
        agent_id="sales",
        agent_version_id="version_sales",
        step_before="discover",
        step_after="product_qa",
        semantic_events_json=json.dumps([]),
        fact_updates_json=json.dumps([]),
        chosen_action_json=json.dumps({"type": "transition", "reason": "transition_to:product_qa"}),
        emitted_messages_json=json.dumps([]),
        tool_calls_json=json.dumps([]),
        rules_json=json.dumps({"evaluations": []}),
        latency_breakdown_ms_json=json.dumps({"total": 12}),
        recorded_at=recorded_at,
    )

    trace = _record_to_trace(record)

    assert trace.trace_id == "trace_legacy"
    assert trace.chosen_action.type == "transition"
    assert trace.rules.evaluations == []
    assert trace.latency_breakdown_ms["total"] == 12
    # Legacy records (pre-Stage-1 cascade era) have no classifier_json.
    assert trace.classifier is None


def _make_minimal_trace(**overrides) -> TurnTrace:
    base = dict(
        trace_id="trace_test",
        conversation_id="conv_test",
        turn_id="turn_test",
        agent_id="test_agent",
        step_before="entry",
        step_after="entry",
        chosen_action=ActionRecord(type="stay", reason="test"),
        recorded_at=datetime(2026, 4, 30, 15, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return TurnTrace(**base)


def test_project_classifier_trace_returns_none_when_no_classifier_data() -> None:
    """A turn with no classifier events should leave classifier_json NULL."""
    trace = _make_minimal_trace()
    assert _project_classifier_trace(trace) is None


def test_project_classifier_trace_prefers_explicit_classifier_field() -> None:
    """When trace.classifier is set (Stage 3+ dispatcher path), use it directly."""
    trace = _make_minimal_trace(
        classifier=ClassifierTraceRecord(
            backend="vllm",
            model="Qwen/Qwen3-8B",
            chosen_label="product_question",
            confidence=0.94,
            cache_hit=True,
        )
    )
    projected = _project_classifier_trace(trace)
    assert projected is not None
    assert projected["backend"] == "vllm"
    assert projected["model"] == "Qwen/Qwen3-8B"
    assert projected["chosen_label"] == "product_question"
    assert projected["confidence"] == 0.94
    assert projected["cache_hit"] is True


def test_project_classifier_trace_falls_back_to_event_payload() -> None:
    """Stage 1–2 path: GemmaLocalInterpreter stashes ClassificationResult fields
    in SemanticEventRecord.payload. Projection picks them up."""
    trace = _make_minimal_trace(
        semantic_events=[
            SemanticEventRecord(
                family="routing",
                name="outcome_resolved",
                source="classifier",
                confidence=0.91,
                payload={
                    "event": "product_question",
                    "transition_id": "t_product",
                    "classifier_trace": {
                        "backend": "transformers",
                        "model": "gemma-4-E4B-it",
                        "chosen_label": "product_question",
                        "confidence": 0.91,
                        "decode_logprobs": {"product_question": -0.1},
                        "cache_hit": False,
                        "prefill_tokens": 42,
                        "decode_tokens": 2,
                        "elapsed_ms": 87,
                    },
                },
            )
        ]
    )
    projected = _project_classifier_trace(trace)
    assert projected is not None
    assert projected["backend"] == "transformers"
    assert projected["model"] == "gemma-4-E4B-it"
    assert projected["confidence"] == 0.91
    assert projected["prefill_tokens"] == 42
    assert projected["elapsed_ms"] == 87


def test_project_classifier_trace_ignores_non_classifier_events() -> None:
    """Deterministic / tool / system events should never produce classifier metadata."""
    trace = _make_minimal_trace(
        semantic_events=[
            SemanticEventRecord(
                family="fact_updated",
                name="email",
                source="deterministic",
                payload={"classifier_trace": {"backend": "transformers"}},  # impostor
            ),
            SemanticEventRecord(
                family="tool_outcome",
                name="action_code_success",
                source="tool",
                payload={"classifier_trace": {"backend": "vllm"}},  # impostor
            ),
        ]
    )
    assert _project_classifier_trace(trace) is None


def test_project_classifier_trace_skips_classifier_events_without_payload() -> None:
    """Classifier events without classifier_trace metadata are skipped, not errors."""
    trace = _make_minimal_trace(
        semantic_events=[
            SemanticEventRecord(
                family="intent_detected",
                name="product_question",
                source="classifier",
                confidence=0.5,
                payload={},  # no classifier_trace key
            )
        ]
    )
    assert _project_classifier_trace(trace) is None


def test_sqlalchemy_conversation_store_list_pushes_limit_offset_into_sql(
    postgres_database_url_factory,
) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyConversationStore(session_factory)
    for index in range(5):
        store.save(
            ConversationState(
                conversation_id=f"conv_page_{index}",
                agent_id="sales",
                agent_version_id="version_sales",
                step_id="discover",
                updated_at=datetime(2026, 6, 11, 10, index, tzinfo=timezone.utc),
            )
        )

    window = store.list_conversations(limit=2, offset=1)
    assert [state.conversation_id for state in window] == ["conv_page_1", "conv_page_2"]

    tail = store.list_conversations(limit=10, offset=4)
    assert [state.conversation_id for state in tail] == ["conv_page_4"]

    everything = store.list_conversations()
    assert len(everything) == 5


def test_sqlalchemy_conversation_store_filters_by_agent_and_version(
    postgres_database_url_factory,
) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyConversationStore(session_factory)
    for agent_id, version_id, conversation_id in [
        ("agent_a", "version_a_1", "conv_filter_1"),
        ("agent_a", "version_a_2", "conv_filter_2"),
        ("agent_b", "version_b_1", "conv_filter_3"),
    ]:
        store.save(
            ConversationState(
                conversation_id=conversation_id,
                agent_id=agent_id,
                agent_version_id=version_id,
                step_id="discover",
                updated_at=datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc),
            )
        )

    agent_window = store.list_conversations(agent_id="agent_a")
    assert [state.conversation_id for state in agent_window] == ["conv_filter_1", "conv_filter_2"]

    version_window = store.list_conversations(agent_id="agent_a", agent_version_id="version_a_2")
    assert [state.conversation_id for state in version_window] == ["conv_filter_2"]


def test_sqlalchemy_trace_store_by_conversation_pushes_limit_offset_into_sql(
    postgres_database_url_factory,
) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyTraceStore(session_factory)
    for index in range(4):
        store.append(
            TurnTrace(
                trace_id=f"trace_page_{index}",
                conversation_id="conv_paged",
                turn_id=f"turn_{index}",
                agent_id="sales",
                step_before="discover",
                step_after="product_qa",
                chosen_action=ActionRecord(type="transition", reason="transition_to:product_qa"),
                recorded_at=datetime(2026, 6, 11, 11, index, tzinfo=timezone.utc),
            )
        )

    window = store.by_conversation("conv_paged", limit=2, offset=1)
    assert [trace.trace_id for trace in window] == ["trace_page_1", "trace_page_2"]
    assert len(store.by_conversation("conv_paged")) == 4


def test_sqlalchemy_trace_store_filters_by_agent_and_version(
    postgres_database_url_factory,
) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyTraceStore(session_factory)
    for agent_id, version_id, trace_id in [
        ("agent_a", "version_a_1", "trace_filter_1"),
        ("agent_a", "version_a_2", "trace_filter_2"),
        ("agent_b", "version_b_1", "trace_filter_3"),
    ]:
        store.append(
            TurnTrace(
                trace_id=trace_id,
                conversation_id=f"conv_{trace_id}",
                turn_id=f"turn_{trace_id}",
                agent_id=agent_id,
                agent_version_id=version_id,
                step_before="discover",
                step_after="product_qa",
                chosen_action=ActionRecord(type="transition", reason="transition_to:product_qa"),
                recorded_at=datetime(2026, 6, 11, 11, 0, tzinfo=timezone.utc),
            )
        )

    agent_traces = store.all(agent_id="agent_a")
    assert [trace.trace_id for trace in agent_traces] == ["trace_filter_1", "trace_filter_2"]

    version_traces = store.all(agent_id="agent_a", agent_version_id="version_a_2")
    assert [trace.trace_id for trace in version_traces] == ["trace_filter_2"]


def test_in_memory_stores_honour_limit_offset() -> None:
    from ruhu.stores import InMemoryConversationStore, InMemoryTraceStore

    conversation_store = InMemoryConversationStore()
    for index in range(4):
        conversation_store.save(
            ConversationState(
                conversation_id=f"conv_mem_{index}",
                agent_id="sales",
                agent_version_id="version_sales",
                step_id="discover",
                updated_at=datetime(2026, 6, 11, 9, index, tzinfo=timezone.utc),
            )
        )
    window = conversation_store.list_conversations(limit=2, offset=1)
    assert [state.conversation_id for state in window] == ["conv_mem_1", "conv_mem_2"]

    trace_store = InMemoryTraceStore()
    for index in range(4):
        trace_store.append(
            TurnTrace(
                trace_id=f"trace_mem_{index}",
                conversation_id="conv_mem",
                turn_id=f"turn_{index}",
                agent_id="sales",
                step_before="discover",
                step_after="product_qa",
                chosen_action=ActionRecord(type="transition", reason="transition_to:product_qa"),
                recorded_at=datetime(2026, 6, 11, 9, index, tzinfo=timezone.utc),
            )
        )
    window_traces = trace_store.by_conversation("conv_mem", limit=2, offset=1)
    assert [trace.trace_id for trace in window_traces] == ["trace_mem_1", "trace_mem_2"]
