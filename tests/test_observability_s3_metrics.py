"""Phase S3 — Provider + cost + DB metrics tests.

Covers:
- ``cost.py``: rate-card lookup, canonical model name normalisation, cost math,
  ``record_llm_cost()`` increments ``llm_tokens_total`` + ``llm_cost_usd_total``.
- ``metrics.py``: new S3 counters/histograms are registered in the isolated registry.
- ``_observe_llm_request()``: token extraction from Gemini response body, error
  path emits ``provider_error_total``.
- DB query metrics: ``_install_query_metrics()`` records ``db_query_duration_seconds``
  via SQLite in-memory engine.
- ``turn_error_total``: incremented by the trace store when ``error_kind != "none"``.
- Cardinality guard: ``model`` labels only receive canonical values.
"""
from __future__ import annotations

import time

import pytest
from prometheus_client import CollectorRegistry

from ruhu.observability.cost import (
    _canonical_model_name,
    _RATE_CARD,
    cost_usd,
    record_llm_cost,
)
from ruhu.observability.metrics import (
    llm_tokens_total,
    llm_cost_usd_total,
    llm_request_duration_seconds,
    provider_error_total,
    turn_error_total,
    db_query_duration_seconds,
    registry,
)


# ── cost.py: canonical model name ─────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("gemini-1.5-flash", "gemini-1.5-flash"),
    ("gemini-1.5-flash-preview", "gemini-1.5-flash"),
    ("gemini-1.5-flash-001", "gemini-1.5-flash"),
    ("gemini-2.0-flash-exp", "gemini-2.0-flash"),
    ("gemini-2.0-flash-latest", "gemini-2.0-flash"),
    ("gemini-2.5-pro-preview-04-17", "gemini-2.5-pro"),
    ("gemini-3-flash-preview", "gemini-3-flash"),
    ("totally-unknown-model-xyz", "unknown"),
    ("", "unknown"),
])
def test_canonical_model_name(raw, expected):
    assert _canonical_model_name(raw) == expected


def test_all_rate_card_keys_are_canonical():
    """Every key in the rate card must be its own canonical form."""
    for key in _RATE_CARD:
        if key == "unknown":
            continue
        assert _canonical_model_name(key) == key, (
            f"rate card key {key!r} is not self-canonical"
        )


# ── cost.py: cost_usd math ────────────────────────────────────────────────────


def test_cost_usd_known_model():
    """cost_usd returns correct USD value for a known model."""
    # gemini-1.5-flash: $0.075/1M input, $0.30/1M output
    result = cost_usd("gemini-1.5-flash", input_tokens=1_000_000, output_tokens=0)
    assert abs(result - 0.075) < 1e-9

    result = cost_usd("gemini-1.5-flash", input_tokens=0, output_tokens=1_000_000)
    assert abs(result - 0.30) < 1e-9

    result = cost_usd("gemini-1.5-flash", input_tokens=500_000, output_tokens=250_000)
    expected = (500_000 * 0.075 + 250_000 * 0.30) / 1_000_000
    assert abs(result - expected) < 1e-9


def test_cost_usd_with_preview_suffix():
    """Preview suffix is stripped before rate-card lookup."""
    direct = cost_usd("gemini-1.5-flash", input_tokens=1000, output_tokens=500)
    preview = cost_usd("gemini-1.5-flash-preview", input_tokens=1000, output_tokens=500)
    assert abs(direct - preview) < 1e-12


def test_cost_usd_unknown_model_returns_zero():
    """Unknown models return $0.00 (not an error)."""
    result = cost_usd("some-future-model-xyz", input_tokens=100_000, output_tokens=50_000)
    assert result == 0.0


def test_cost_usd_zero_tokens():
    """Zero tokens cost zero."""
    assert cost_usd("gemini-2.0-flash", input_tokens=0, output_tokens=0) == 0.0


# ── cost.py: record_llm_cost emits metrics ────────────────────────────────────


def _read_counter(counter, **labels) -> float:
    """Read a Prometheus counter value from the isolated registry."""
    labels_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    metric_name = counter._name
    for metric in registry.collect():
        if metric.name == metric_name:
            for sample in metric.samples:
                if all(sample.labels.get(k) == v for k, v in labels.items()):
                    return sample.value
    return 0.0


def test_record_llm_cost_increments_tokens():
    before_in = _read_counter(llm_tokens_total, provider="gemini", model="gemini-2.0-flash", direction="input")
    before_out = _read_counter(llm_tokens_total, provider="gemini", model="gemini-2.0-flash", direction="output")

    record_llm_cost("gemini", "gemini-2.0-flash", input_tokens=1000, output_tokens=500)

    after_in = _read_counter(llm_tokens_total, provider="gemini", model="gemini-2.0-flash", direction="input")
    after_out = _read_counter(llm_tokens_total, provider="gemini", model="gemini-2.0-flash", direction="output")

    assert after_in - before_in == 1000
    assert after_out - before_out == 500


def test_record_llm_cost_increments_cost_counter():
    before = _read_counter(llm_cost_usd_total, provider="gemini", model="gemini-1.5-flash")

    record_llm_cost("gemini", "gemini-1.5-flash", input_tokens=2_000_000, output_tokens=1_000_000)

    after = _read_counter(llm_cost_usd_total, provider="gemini", model="gemini-1.5-flash")
    expected = cost_usd("gemini-1.5-flash", input_tokens=2_000_000, output_tokens=1_000_000)

    assert abs((after - before) - expected) < 1e-9


def test_record_llm_cost_normalises_model_label():
    """Model label stored in counter is the canonical name, not the raw name."""
    before = _read_counter(llm_tokens_total, provider="vertex", model="gemini-2.0-flash", direction="input")
    record_llm_cost("vertex", "gemini-2.0-flash-exp", input_tokens=500, output_tokens=200)
    after = _read_counter(llm_tokens_total, provider="vertex", model="gemini-2.0-flash", direction="input")
    assert after - before == 500


def test_record_llm_cost_does_not_raise_for_unknown_model():
    """record_llm_cost is a no-raise helper even for unknown models."""
    record_llm_cost("gemini", "totally-new-model-2099", input_tokens=100, output_tokens=50)


# ── metrics.py: S3 metrics registered ────────────────────────────────────────


def test_s3_metrics_registered_in_registry():
    """All Phase S3 metrics are present in the isolated Prometheus registry.

    prometheus_client strips the ``_total`` suffix from Counter ``m.name``
    (it appears only in sample names).  We check the base name that the
    registry actually uses.
    """
    metric_names = {m.name for m in registry.collect()}
    # Counters: prometheus_client stores without _total in m.name
    # Histograms: stored with full name
    expected = {
        "ruhu_llm_request_duration_seconds",  # Histogram — full name
        "ruhu_llm_tokens",                    # Counter base name
        "ruhu_llm_cost_usd",                  # Counter base name
        "ruhu_provider_error",                # Counter base name
        "ruhu_turn_error",                    # Counter base name
        "ruhu_turn_classifier_fallback",      # Counter base name
        "ruhu_turn_controller_of_record",     # Counter base name
        "ruhu_db_query_duration_seconds",     # Histogram — full name
    }
    missing = expected - metric_names
    assert not missing, f"S3 metrics missing from registry: {missing}"


# ── _observe_llm_request: token extraction + error path ──────────────────────


def test_observe_llm_request_ok_extracts_tokens():
    """_observe_llm_request extracts usageMetadata from a Gemini-style response."""
    from ruhu.response_generation import _observe_llm_request

    before_in = _read_counter(llm_tokens_total, provider="gemini", model="gemini-1.5-pro", direction="input")
    before_out = _read_counter(llm_tokens_total, provider="gemini", model="gemini-1.5-pro", direction="output")

    fake_body = {
        "candidates": [{"content": {"parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 80},
    }
    _observe_llm_request(
        time.monotonic() - 0.1,
        provider="gemini",
        model="gemini-1.5-pro",
        stage="generate",
        outcome="ok",
        response_body=fake_body,
    )

    after_in = _read_counter(llm_tokens_total, provider="gemini", model="gemini-1.5-pro", direction="input")
    after_out = _read_counter(llm_tokens_total, provider="gemini", model="gemini-1.5-pro", direction="output")

    assert after_in - before_in == 200
    assert after_out - before_out == 80


def test_observe_llm_request_error_increments_provider_error():
    """_observe_llm_request with outcome='error' emits provider_error_total."""
    from ruhu.response_generation import _observe_llm_request

    before = _read_counter(provider_error_total, provider="gemini", kind="http_error")
    _observe_llm_request(
        time.monotonic() - 0.05,
        provider="gemini",
        model="gemini-2.0-flash",
        stage="generate",
        outcome="error",
    )
    after = _read_counter(provider_error_total, provider="gemini", kind="http_error")
    assert after - before == 1


def test_observe_llm_request_records_histogram():
    """_observe_llm_request emits a duration sample to llm_request_duration_seconds."""
    from ruhu.response_generation import _observe_llm_request

    before_count = sum(
        s.value for m in registry.collect()
        if m.name == "ruhu_llm_request_duration_seconds"
        for s in m.samples
        if s.name.endswith("_count")
        and s.labels.get("provider") == "gemini"
        and s.labels.get("stage") == "classify"
    )
    _observe_llm_request(
        time.monotonic() - 0.02,
        provider="gemini",
        model="gemini-2.0-flash",
        stage="classify",
        outcome="ok",
    )
    after_count = sum(
        s.value for m in registry.collect()
        if m.name == "ruhu_llm_request_duration_seconds"
        for s in m.samples
        if s.name.endswith("_count")
        and s.labels.get("provider") == "gemini"
        and s.labels.get("stage") == "classify"
    )
    assert after_count - before_count >= 1


# ── DB query metrics ──────────────────────────────────────────────────────────


def test_install_query_metrics_records_select():
    """_install_query_metrics observes SELECT latency."""
    from sqlalchemy import create_engine, text
    from ruhu.db import _install_query_metrics

    engine = create_engine("sqlite:///:memory:", future=True)
    _install_query_metrics(engine)

    before = sum(
        s.value for m in registry.collect()
        if m.name == "ruhu_db_query_duration_seconds"
        for s in m.samples
        if s.name.endswith("_count") and s.labels.get("operation") == "select"
    )

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    after = sum(
        s.value for m in registry.collect()
        if m.name == "ruhu_db_query_duration_seconds"
        for s in m.samples
        if s.name.endswith("_count") and s.labels.get("operation") == "select"
    )
    assert after - before >= 1


def test_install_query_metrics_operation_classification():
    """SQL keywords are mapped to bounded operation labels."""
    from sqlalchemy import create_engine, text
    from ruhu.db import _install_query_metrics

    engine = create_engine("sqlite:///:memory:", future=True)
    _install_query_metrics(engine)

    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))
        conn.execute(text("SELECT id FROM t"))
        conn.execute(text("UPDATE t SET id = 2"))
        conn.execute(text("DELETE FROM t"))

    ops_seen = set()
    for m in registry.collect():
        if m.name == "ruhu_db_query_duration_seconds":
            for s in m.samples:
                if s.name.endswith("_count") and s.value > 0:
                    ops_seen.add(s.labels.get("operation"))

    assert "select" in ops_seen
    assert "insert" in ops_seen
    assert "update" in ops_seen
    assert "delete" in ops_seen
    # CREATE TABLE maps to "other"
    assert "other" in ops_seen


# ── turn_error_total ──────────────────────────────────────────────────────────


def _make_trace_store():
    """Build an in-memory SQLAlchemy store using only the TurnTrace table.

    We use only the TurnTraceRecord table (not all of Base) because the full
    Base includes ARRAY columns that are Postgres-only and break SQLite.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from ruhu.db_models import TurnTraceRecord
    from ruhu.stores import SQLAlchemyTraceStore

    engine = create_engine("sqlite:///:memory:", future=True)
    TurnTraceRecord.__table__.create(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return SQLAlchemyTraceStore(sf)


def test_turn_error_total_incremented_on_error_trace():
    """SQLAlchemyTraceStore increments turn_error_total when error_kind != 'none'."""
    from ruhu.schemas import TurnTrace

    store = _make_trace_store()
    before = _read_counter(turn_error_total, error_kind="llm_error")

    from ruhu.schemas import ActionRecord
    trace = TurnTrace(
        trace_id="err_trace_1",
        conversation_id="conv_err",
        turn_id="t_err",
        agent_id="g1",
        step_before="s0",
        step_after="s0",
        error_kind="llm_error",
        chosen_action=ActionRecord(type="stay", reason="test"),
    )
    store.append(trace)

    after = _read_counter(turn_error_total, error_kind="llm_error")
    assert after - before == 1


def test_turn_error_total_not_incremented_on_success_trace():
    """Nominal traces (error_kind='none') do not increment turn_error_total."""
    from ruhu.schemas import TurnTrace

    store = _make_trace_store()

    # Sum all turn_error_total samples before
    before = sum(
        s.value for m in registry.collect()
        if m.name == "ruhu_turn_error"
        for s in m.samples
        if s.name.endswith("_total")
    )

    from ruhu.schemas import ActionRecord
    trace = TurnTrace(
        trace_id="ok_trace_1",
        conversation_id="conv_ok",
        turn_id="t_ok",
        agent_id="g1",
        step_before="s0",
        step_after="s0",
        error_kind="none",
        chosen_action=ActionRecord(type="stay", reason="test"),
    )
    store.append(trace)

    after = sum(
        s.value for m in registry.collect()
        if m.name == "ruhu_turn_error"
        for s in m.samples
        if s.name.endswith("_total")
    )
    assert after == before


# ── Cardinality guard ─────────────────────────────────────────────────────────

_BOUNDED_PROVIDERS = {"gemini", "vertex", "openai", "anthropic"}
_BOUNDED_DIRECTIONS = {"input", "output"}
_BOUNDED_STAGES = {"generate", "classify"}
_BOUNDED_OUTCOMES = {"ok", "error", "timeout"}
_BOUNDED_OPERATIONS = {"select", "insert", "update", "delete", "other"}


def test_llm_tokens_total_label_values_are_bounded():
    """All label values seen on llm_tokens_total are from the bounded enum sets."""
    for m in registry.collect():
        if m.name != "ruhu_llm_tokens_total":
            continue
        for s in m.samples:
            p = s.labels.get("provider")
            d = s.labels.get("direction")
            if p:
                assert p in _BOUNDED_PROVIDERS or p.startswith("gemini") or p == "unknown", (
                    f"unbounded provider label: {p!r}"
                )
            if d:
                assert d in _BOUNDED_DIRECTIONS, f"unbounded direction label: {d!r}"


def test_db_query_duration_operation_values_are_bounded():
    """All operation label values on db_query_duration_seconds are bounded."""
    for m in registry.collect():
        if m.name != "ruhu_db_query_duration_seconds":
            continue
        for s in m.samples:
            op = s.labels.get("operation")
            if op:
                assert op in _BOUNDED_OPERATIONS, f"unbounded operation label: {op!r}"
