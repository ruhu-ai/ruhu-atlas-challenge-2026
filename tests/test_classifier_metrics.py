"""Tests for classifier Prometheus instrumentation.

Verifies that ``_emit_classifier_metrics`` writes the right counters and
histograms with the right label sets, *without* needing real Gemma weights.
"""
from __future__ import annotations

from typing import Any

from ruhu.classifier.protocol import ClassificationRequest, ClassificationResult
from ruhu.classifier.transformers_backend import _emit_classifier_metrics
from ruhu.observability import metrics as m


def _request(**overrides: Any) -> ClassificationRequest:
    base = dict(
        agent_id="agent_test",
        agent_version_id="3.0",
        step_id="entry",
        step_name="Entry",
        step_summary="",
        user_text="hello",
        candidate_labels={"product_question": "x"},
    )
    base.update(overrides)
    return ClassificationRequest(**base)


def _label_count(metric: Any, **labels: str) -> float:
    """Read the current value of a Prometheus metric for given label values."""
    samples = list(metric.collect())[0].samples
    for sample in samples:
        if sample.labels == labels and sample.name.endswith(("_total", "_count")):
            return sample.value
    return 0.0


def _histogram_count(metric: Any, **labels: str) -> float:
    """Total observation count for a histogram given label values."""
    samples = list(metric.collect())[0].samples
    for sample in samples:
        if sample.labels == labels and sample.name.endswith("_count"):
            return sample.value
    return 0.0


def test_happy_path_increments_decisions_and_records_duration() -> None:
    req = _request()
    result = ClassificationResult(
        chosen_label="product_question",
        confidence=0.91,
        prefill_tokens=42,
        decode_tokens=2,
        backend="transformers",
        elapsed_ms=87,
    )

    before_decisions = _label_count(
        m.classifier_decisions_total,
        agent_id="agent_test",
        step_id="entry",
        chosen_label="product_question",
        backend="transformers",
        lora="base",
    )
    before_duration_count = _histogram_count(
        m.classifier_request_duration_seconds,
        agent_id="agent_test",
        step_id="entry",
        backend="transformers",
        lora="base",
        cache_hit="false",
    )

    _emit_classifier_metrics(req, result)

    after_decisions = _label_count(
        m.classifier_decisions_total,
        agent_id="agent_test",
        step_id="entry",
        chosen_label="product_question",
        backend="transformers",
        lora="base",
    )
    assert after_decisions == before_decisions + 1

    after_duration_count = _histogram_count(
        m.classifier_request_duration_seconds,
        agent_id="agent_test",
        step_id="entry",
        backend="transformers",
        lora="base",
        cache_hit="false",
    )
    assert after_duration_count == before_duration_count + 1


def test_unknown_intent_increments_unknown_counter() -> None:
    req = _request()
    result = ClassificationResult(
        chosen_label=None,
        confidence=0.0,
        backend="transformers",
        error="unknown_label",
    )

    before = _label_count(
        m.classifier_unknown_total,
        agent_id="agent_test",
        step_id="entry",
        backend="transformers",
    )

    _emit_classifier_metrics(req, result)

    after = _label_count(
        m.classifier_unknown_total,
        agent_id="agent_test",
        step_id="entry",
        backend="transformers",
    )
    assert after == before + 1


def test_error_increments_errors_counter() -> None:
    req = _request()
    result = ClassificationResult(
        chosen_label=None,
        confidence=0.0,
        backend="transformers",
        error="generate_failed: CUDA OOM",
    )

    before = _label_count(
        m.classifier_errors_total,
        error_kind="generate_failed",
        backend="transformers",
    )

    _emit_classifier_metrics(req, result)

    after = _label_count(
        m.classifier_errors_total,
        error_kind="generate_failed",
        backend="transformers",
    )
    assert after == before + 1


def test_prefill_tokens_records_with_cache_hit_label() -> None:
    req = _request()
    result = ClassificationResult(
        chosen_label="product_question",
        confidence=0.95,
        prefill_tokens=128,
        cache_hit=True,
        backend="vllm",
        elapsed_ms=42,
    )

    before = _label_count(
        m.classifier_prefill_tokens_total,
        agent_id="agent_test",
        step_id="entry",
        cache_hit="true",
    )

    _emit_classifier_metrics(req, result)

    after = _label_count(
        m.classifier_prefill_tokens_total,
        agent_id="agent_test",
        step_id="entry",
        cache_hit="true",
    )
    assert after == before + 128


def test_lora_name_propagates_to_labels() -> None:
    req = _request()
    result = ClassificationResult(
        chosen_label="product_question",
        confidence=0.9,
        prefill_tokens=12,
        decode_tokens=1,
        backend="vllm",
        lora_name="agent-melonpay_support_demo",
        elapsed_ms=50,
    )

    before = _label_count(
        m.classifier_decisions_total,
        agent_id="agent_test",
        step_id="entry",
        chosen_label="product_question",
        backend="vllm",
        lora="agent-melonpay_support_demo",
    )

    _emit_classifier_metrics(req, result)

    after = _label_count(
        m.classifier_decisions_total,
        agent_id="agent_test",
        step_id="entry",
        chosen_label="product_question",
        backend="vllm",
        lora="agent-melonpay_support_demo",
    )
    assert after == before + 1


def test_zero_confidence_is_not_observed_in_histogram() -> None:
    """0.0 confidence on classifier failure should not pollute the
    confidence histogram (would skew per-agent calibration analysis)."""
    req = _request()
    result = ClassificationResult(
        chosen_label=None,
        confidence=0.0,
        backend="transformers",
        error="empty_request",
    )

    before = _histogram_count(
        m.classifier_confidence,
        agent_id="agent_test",
        step_id="entry",
    )

    _emit_classifier_metrics(req, result)

    after = _histogram_count(
        m.classifier_confidence,
        agent_id="agent_test",
        step_id="entry",
    )
    assert after == before  # not incremented


def test_emit_metrics_swallows_unexpected_exceptions() -> None:
    """Instrumentation must never break the classifier request path. If
    metrics.collect throws for any reason, _emit_classifier_metrics must
    not propagate the exception."""
    req = _request()
    # Construct a result whose attribute access raises — simulates a
    # partially-built ClassificationResult or a future schema drift.
    class BrokenResult:
        backend = "transformers"
        lora_name = None
        cache_hit = False
        elapsed_ms = 0
        chosen_label = None
        confidence = 0.0
        prefill_tokens = 0
        @property
        def error(self) -> str:
            raise RuntimeError("simulated metric build failure")

    # Should not raise.
    _emit_classifier_metrics(req, BrokenResult())  # type: ignore[arg-type]
