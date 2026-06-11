"""Tests for the Stage 2.5 decision benchmark (WI-2.5.1).

Covers metrics, the runner, the gate evaluator, the CSV emitter, and the
WI-2.5.1 smoke spec: 100 synthetic rows + one fake backend produces a
well-shaped CSV.
"""
from __future__ import annotations

import csv
import json
import math

import pytest

from ruhu.classifier.benchmark import (
    BenchmarkReport,
    BenchmarkRunner,
    Stage25GateCriteria,
    compute_confusion_matrix,
    compute_ece,
    compute_latency_percentiles,
    compute_per_intent_metrics,
    evaluate_gate,
)
from ruhu.classifier.benchmark._synthetic import (
    StochasticFakeClassifier,
    make_synthetic_eval_set,
)
from ruhu.classifier.benchmark.csv_writer import CSV_COLUMNS, write_report
from ruhu.classifier.benchmark.eval_set import EvalRow, load_eval_set
from ruhu.classifier.benchmark.metrics import (
    PerIntentMetrics,
    compute_macro_f1,
    compute_micro_f1,
    compute_unknown_rate,
)
from ruhu.classifier.benchmark.run_stage_2_5 import main as cli_main
from ruhu.classifier.protocol import (
    ClassificationRequest,
    ClassificationResult,
)


# ── metrics ─────────────────────────────────────────────────────────────────


def test_per_intent_metrics_perfect_classifier() -> None:
    gold = ["a", "a", "b", "c"]
    pred = ["a", "a", "b", "c"]
    per_intent = compute_per_intent_metrics(gold, pred)
    assert {m.intent for m in per_intent.values()} == {"a", "b", "c"}
    for m in per_intent.values():
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
    assert compute_macro_f1(per_intent) == 1.0
    assert compute_micro_f1(gold, pred) == 1.0


def test_per_intent_metrics_handles_unknown_predictions() -> None:
    # gold a appears twice; one was predicted a (TP), one was predicted None (FN).
    # gold b appears twice; one was predicted b (TP), one was predicted a (FP for a, FN for b).
    gold = ["a", "a", "b", "b"]
    pred = ["a", None, "b", "a"]
    per_intent = compute_per_intent_metrics(gold, pred)
    assert per_intent["a"].support == 2
    assert per_intent["a"].recall == 0.5  # 1 TP / (1 TP + 1 FN)
    assert per_intent["a"].precision == 0.5  # 1 TP / (1 TP + 1 FP)
    assert per_intent["b"].support == 2
    assert per_intent["b"].recall == 0.5
    assert per_intent["b"].precision == 1.0  # 1 TP / (1 TP + 0 FP)
    assert compute_unknown_rate(pred) == 0.25


def test_per_intent_metrics_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        compute_per_intent_metrics(["a"], ["a", "b"])


def test_micro_f1_treats_none_as_miss() -> None:
    gold = ["a", "a", "a"]
    pred = [None, None, None]
    assert compute_micro_f1(gold, pred) == 0.0


def test_macro_f1_empty_returns_zero() -> None:
    assert compute_macro_f1({}) == 0.0


def test_compute_ece_perfect_calibration_is_zero() -> None:
    """Confidence 0.95 on always-correct predictions → ECE near 0."""
    predictions = [(0.95, True)] * 100
    report = compute_ece(predictions, n_buckets=10)
    assert report.expected_calibration_error == pytest.approx(0.05, abs=1e-6)


def test_compute_ece_overconfident_classifier_is_high() -> None:
    """Confidence 0.95 but only correct half the time → ECE ≈ 0.45."""
    predictions = [(0.95, True)] * 50 + [(0.95, False)] * 50
    report = compute_ece(predictions, n_buckets=10)
    assert report.expected_calibration_error == pytest.approx(0.45, abs=1e-6)


def test_compute_ece_empty_returns_zero() -> None:
    report = compute_ece([], n_buckets=10)
    assert report.expected_calibration_error == 0.0
    assert report.buckets == []


def test_compute_ece_invalid_buckets_raises() -> None:
    with pytest.raises(ValueError):
        compute_ece([(0.5, True)], n_buckets=0)


def test_confusion_matrix_uses_unknown_for_none() -> None:
    gold = ["a", "a", None]
    pred = [None, "b", "a"]
    matrix = compute_confusion_matrix(gold, pred)
    assert matrix["a"]["_unknown_"] == 1
    assert matrix["a"]["b"] == 1
    assert matrix["_unknown_"]["a"] == 1


def test_compute_latency_percentiles_known_ordering() -> None:
    values = list(range(1, 101))
    p = compute_latency_percentiles(values)
    assert p.count == 100
    assert 49.0 <= p.p50_ms <= 51.0
    assert 89.0 <= p.p90_ms <= 91.0
    assert 98.0 <= p.p99_ms <= 100.0


def test_compute_latency_percentiles_empty() -> None:
    p = compute_latency_percentiles([])
    assert p.count == 0
    assert p.p50_ms == p.p90_ms == p.p99_ms == 0.0


# ── eval_set ────────────────────────────────────────────────────────────────


def test_load_eval_set_parses_minimal_fields(tmp_path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(
        json.dumps(
            {
                "agent_id": "a1",
                "agent_version_id": "v1",
                "step_id": "entry",
                "candidate_labels": {"x": "intent x"},
                "user_text": "hi",
                "gold_chosen_label": "x",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = load_eval_set(path)
    assert len(rows) == 1
    assert rows[0].step_name == "entry"  # falls back to step_id
    assert rows[0].language == "unknown"


def test_load_eval_set_skips_blank_lines(tmp_path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(
        "\n"
        + json.dumps(
            {
                "agent_id": "a",
                "agent_version_id": "v",
                "step_id": "s",
                "candidate_labels": {"x": "x"},
                "user_text": "u",
                "gold_chosen_label": "x",
            }
        )
        + "\n\n",
        encoding="utf-8",
    )
    assert len(load_eval_set(path)) == 1


def test_load_eval_set_invalid_json_raises(tmp_path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_eval_set(path)


def test_load_eval_set_missing_field_raises(tmp_path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(
        json.dumps({"agent_id": "a"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required field"):
        load_eval_set(path)


def test_eval_row_to_classification_request_propagates_lora() -> None:
    row = EvalRow(
        agent_id="a",
        agent_version_id="v",
        step_id="s",
        step_name="S",
        step_summary="",
        candidate_labels={"x": "x"},
        user_text="hi",
        gold_chosen_label="x",
    )
    req = row.to_classification_request(lora_name="lora-1")
    assert isinstance(req, ClassificationRequest)
    assert req.lora_name == "lora-1"


# ── runner ──────────────────────────────────────────────────────────────────


class _ScriptedClassifier:
    """Returns predictions from a (gold→pred) script."""

    def __init__(self, script: dict[str, str | None], confidence: float = 0.9) -> None:
        self.script = script
        self.confidence = confidence

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        gold = next(iter(self.script.keys()))
        # Use the user_text as a sentinel hint so we can map per-row.
        pred = self.script.get(request.user_text, None)
        return ClassificationResult(
            chosen_label=pred,
            confidence=self.confidence if pred is not None else 0.0,
            backend="synthetic",
            elapsed_ms=10,
        )


def _row(user_text: str, gold: str, language: str = "en") -> EvalRow:
    return EvalRow(
        agent_id="agent_a",
        agent_version_id="v1",
        step_id="entry",
        step_name="Entry",
        step_summary="",
        candidate_labels={"x": "x", "y": "y"},
        user_text=user_text,
        gold_chosen_label=gold,
        language=language,
    )


def test_runner_produces_overall_per_step_per_language() -> None:
    rows = [
        _row("u1", "x", language="en"),
        _row("u2", "y", language="en"),
        _row("u3", "x", language="sw"),
        _row("u4", "y", language="sw"),
    ]
    classifier = _ScriptedClassifier({"u1": "x", "u2": "y", "u3": "y", "u4": "y"})
    runner = BenchmarkRunner(classifier, model="m", gpu_class="L4")
    report = runner.run(rows)
    assert report.row_count == 4
    assert "entry" in report.per_step
    assert set(report.per_language.keys()) == {"en", "sw"}
    # Three of four correct (u1=x✓, u2=y✓, u3=x→y✗, u4=y✓).
    assert report.micro_f1 == pytest.approx(0.75)
    # 'en' slice (u1, u2) is 2-for-2; 'sw' slice (u3, u4) is 1-for-2.
    assert report.per_language["en"].micro_f1 == 1.0
    assert report.per_language["sw"].micro_f1 == pytest.approx(0.5)


def test_runner_empty_rows_raises() -> None:
    classifier = _ScriptedClassifier({})
    runner = BenchmarkRunner(classifier, model="m", gpu_class="L4")
    with pytest.raises(ValueError, match="empty"):
        runner.run([])


def test_runner_records_classifier_elapsed_ms() -> None:
    rows = [_row("u1", "x")]
    classifier = _ScriptedClassifier({"u1": "x"}, confidence=0.95)
    runner = BenchmarkRunner(classifier, model="m", gpu_class="L4")
    report = runner.run(rows)
    assert report.latency is not None
    assert report.latency.p50_ms == 10.0


# ── gate ────────────────────────────────────────────────────────────────────


def _stub_report(
    *,
    model: str,
    macro_f1: float,
    p50_ms: float,
    ece: float,
    per_intent: dict[str, PerIntentMetrics] | None = None,
    per_language: dict[str, BenchmarkReport] | None = None,
) -> BenchmarkReport:
    from ruhu.classifier.benchmark.metrics import (
        CalibrationReport,
        LatencyPercentiles,
    )

    return BenchmarkReport(
        agent_id="a",
        model=model,
        gpu_class="L4",
        lora_name=None,
        row_count=200,
        micro_f1=macro_f1,
        macro_f1=macro_f1,
        unknown_rate=0.05,
        per_intent=per_intent or {},
        per_step={},
        per_language=per_language or {},
        confusion_matrix={},
        latency=LatencyPercentiles(p50_ms=p50_ms, p90_ms=p50_ms * 2, p99_ms=p50_ms * 3, count=200),
        calibration=CalibrationReport(buckets=[], expected_calibration_error=ece),
    )


def test_gate_passes_when_candidate_meets_all_criteria() -> None:
    baseline = _stub_report(model="qwen3-8b", macro_f1=0.90, p50_ms=80.0, ece=0.05)
    candidate = _stub_report(model="gemma-4-e4b", macro_f1=0.89, p50_ms=70.0, ece=0.05)
    result = evaluate_gate(candidate, baseline)
    assert result.passed
    assert all(check.passed for check in result.checks)


def test_gate_fails_on_macro_f1_deficit_over_2pts() -> None:
    baseline = _stub_report(model="qwen3-8b", macro_f1=0.90, p50_ms=80.0, ece=0.05)
    candidate = _stub_report(model="gemma-4-e4b", macro_f1=0.86, p50_ms=70.0, ece=0.05)
    result = evaluate_gate(candidate, baseline)
    assert not result.passed
    failed = [c for c in result.checks if not c.passed]
    assert any(c.name == "macro_f1_within_2pts_of_baseline" for c in failed)


def test_gate_fails_on_per_intent_floor_when_support_high_enough() -> None:
    bad_intent = PerIntentMetrics(intent="rare", support=40, precision=0.7, recall=0.7, f1=0.70)
    candidate = _stub_report(
        model="gemma-4-e4b",
        macro_f1=0.89,
        p50_ms=70.0,
        ece=0.05,
        per_intent={"rare": bad_intent},
    )
    baseline = _stub_report(model="qwen3-8b", macro_f1=0.90, p50_ms=80.0, ece=0.05)
    result = evaluate_gate(candidate, baseline)
    failed = [c.name for c in result.checks if not c.passed]
    assert "no_intent_f1_below_floor" in failed


def test_gate_ignores_low_support_intents_for_floor() -> None:
    """An f1=0.5 intent with support=10 doesn't fail the gate."""
    weak_low_support = PerIntentMetrics(
        intent="rare", support=10, precision=0.5, recall=0.5, f1=0.50
    )
    candidate = _stub_report(
        model="gemma-4-e4b",
        macro_f1=0.89,
        p50_ms=70.0,
        ece=0.05,
        per_intent={"rare": weak_low_support},
    )
    baseline = _stub_report(model="qwen3-8b", macro_f1=0.90, p50_ms=80.0, ece=0.05)
    result = evaluate_gate(candidate, baseline)
    floor = next(c for c in result.checks if c.name == "no_intent_f1_below_floor")
    assert floor.passed


def test_gate_fails_on_high_ece() -> None:
    baseline = _stub_report(model="qwen3-8b", macro_f1=0.90, p50_ms=80.0, ece=0.05)
    candidate = _stub_report(model="gemma-4-e4b", macro_f1=0.89, p50_ms=70.0, ece=0.20)
    result = evaluate_gate(candidate, baseline)
    failed = [c.name for c in result.checks if not c.passed]
    assert "confidence_calibration_ece" in failed


def test_gate_passes_latency_when_under_budget_even_if_slower() -> None:
    """Per spec: candidate p50 may exceed baseline if it's still under budget."""
    baseline = _stub_report(model="qwen3-8b", macro_f1=0.90, p50_ms=40.0, ece=0.05)
    candidate = _stub_report(model="gemma-4-e4b", macro_f1=0.89, p50_ms=80.0, ece=0.05)
    result = evaluate_gate(candidate, baseline, criteria=Stage25GateCriteria(p50_latency_max_ms=100.0))
    latency = next(c for c in result.checks if c.name == "p50_latency")
    assert latency.passed


def test_gate_fails_latency_when_over_budget_and_slower_than_baseline() -> None:
    baseline = _stub_report(model="qwen3-8b", macro_f1=0.90, p50_ms=80.0, ece=0.05)
    candidate = _stub_report(model="gemma-4-e4b", macro_f1=0.89, p50_ms=200.0, ece=0.05)
    result = evaluate_gate(candidate, baseline, criteria=Stage25GateCriteria(p50_latency_max_ms=100.0))
    latency = next(c for c in result.checks if c.name == "p50_latency")
    assert not latency.passed


def test_gate_multilingual_check_skips_when_no_shared_languages() -> None:
    baseline = _stub_report(model="qwen3-8b", macro_f1=0.90, p50_ms=80.0, ece=0.05)
    candidate = _stub_report(model="gemma-4-e4b", macro_f1=0.89, p50_ms=70.0, ece=0.05)
    result = evaluate_gate(candidate, baseline)
    multi = next(c for c in result.checks if c.name == "multilingual_macro_f1_meets_baseline")
    assert multi.passed


def test_gate_summary_renders_pass_and_fail_marks() -> None:
    baseline = _stub_report(model="qwen3-8b", macro_f1=0.90, p50_ms=80.0, ece=0.05)
    candidate = _stub_report(model="gemma-4-e4b", macro_f1=0.89, p50_ms=70.0, ece=0.05)
    summary = evaluate_gate(candidate, baseline).summary()
    assert "PASS — ship candidate" in summary
    assert "✓" in summary


# ── csv_writer ──────────────────────────────────────────────────────────────


def test_csv_writer_writes_header_and_overall_plus_per_step(tmp_path) -> None:
    rows = [_row("u1", "x"), _row("u2", "y")]
    classifier = _ScriptedClassifier({"u1": "x", "u2": "y"})
    runner = BenchmarkRunner(classifier, model="qwen3-8b", gpu_class="L4")
    report = runner.run(rows)

    csv_path = tmp_path / "out.csv"
    write_report(csv_path, report)

    rows_out = list(csv.DictReader(csv_path.open()))
    assert list(rows_out[0].keys()) == list(CSV_COLUMNS)
    assert {r["scope"] for r in rows_out} == {"overall", "step"}
    overall = next(r for r in rows_out if r["scope"] == "overall")
    assert overall["model"] == "qwen3-8b"
    assert overall["gpu_class"] == "L4"
    assert float(overall["macro_f1"]) == pytest.approx(1.0)


def test_csv_writer_append_writes_header_only_once(tmp_path) -> None:
    rows = [_row("u1", "x")]
    classifier = _ScriptedClassifier({"u1": "x"})
    csv_path = tmp_path / "out.csv"

    write_report(csv_path, BenchmarkRunner(classifier, model="m1", gpu_class="L4").run(rows), append=True)
    write_report(csv_path, BenchmarkRunner(classifier, model="m2", gpu_class="L4").run(rows), append=True)

    text = csv_path.read_text()
    assert text.count(",".join(CSV_COLUMNS)) == 1
    rows_out = list(csv.DictReader(csv_path.open()))
    models = {r["model"] for r in rows_out}
    assert models == {"m1", "m2"}


# ── synthetic + WI-2.5.1 smoke spec ─────────────────────────────────────────


def test_synthetic_eval_set_is_deterministic() -> None:
    a = make_synthetic_eval_set(n_rows=100, seed=42)
    b = make_synthetic_eval_set(n_rows=100, seed=42)
    assert [(r.user_text, r.gold_chosen_label, r.language) for r in a] == [
        (r.user_text, r.gold_chosen_label, r.language) for r in b
    ]
    assert len(a) == 100
    assert {r.language for r in a} == {"en", "sw"}


def test_wi_2_5_1_smoke_100_rows_synthetic_one_backend(tmp_path) -> None:
    """WI-2.5.1 smoke spec: 100-row synthetic + one backend produces a CSV with the right shape."""
    rows = make_synthetic_eval_set(n_rows=100, seed=42)
    classifier = StochasticFakeClassifier(fidelity=0.85, seed=0)
    runner = BenchmarkRunner(classifier, model="gemma-4-e4b", gpu_class="L4", lora_name="agent-test")
    report = runner.run(rows)

    assert report.row_count == 100
    assert report.latency is not None and report.latency.count == 100
    assert report.calibration is not None
    assert 0.0 <= report.calibration.expected_calibration_error <= 1.0
    assert "entry" in report.per_step
    assert set(report.per_language.keys()) == {"en", "sw"}
    # Stochastic fidelity=0.85 should land us in [0.6, 0.95] macro-F1.
    assert 0.5 <= report.macro_f1 <= 1.0

    csv_path = tmp_path / "stage_2_5.csv"
    write_report(csv_path, report)
    rows_out = list(csv.DictReader(csv_path.open()))
    assert list(rows_out[0].keys()) == list(CSV_COLUMNS)
    overall = next(r for r in rows_out if r["scope"] == "overall")
    assert overall["lora_name"] == "agent-test"
    assert "en:" in overall["lang_breakdown"] and "sw:" in overall["lang_breakdown"]


def test_cli_main_smoke_with_synthetic_backend(tmp_path, capsys) -> None:
    csv_path = tmp_path / "cli.csv"
    report_path = tmp_path / "cli.json"
    rc = cli_main(
        [
            "--backend", "synthetic",
            "--model", "gemma-4-e4b",
            "--gpu-class", "L4",
            "--csv-out", str(csv_path),
            "--report-out", str(report_path),
            "--synthetic-rows", "60",
            "--synthetic-fidelity", "0.9",
        ]
    )
    assert rc == 0
    assert csv_path.exists()
    rows_out = list(csv.DictReader(csv_path.open()))
    assert any(r["scope"] == "overall" for r in rows_out)
    report_data = json.loads(report_path.read_text())
    assert report_data["row_count"] == 60
    assert report_data["model"] == "gemma-4-e4b"
    captured = capsys.readouterr()
    assert "macro_f1=" in captured.out


def test_stochastic_fake_classifier_extreme_fidelities_bound_macro_f1() -> None:
    rows = make_synthetic_eval_set(n_rows=200, seed=42)
    high = BenchmarkRunner(
        StochasticFakeClassifier(fidelity=0.99, seed=0),
        model="m",
        gpu_class="L4",
    ).run(rows)
    low = BenchmarkRunner(
        StochasticFakeClassifier(fidelity=0.05, seed=1),
        model="m",
        gpu_class="L4",
    ).run(rows)
    assert high.macro_f1 > 0.85
    assert low.macro_f1 < 0.4


# Sanity import — keeps Pyflakes happy and surfaces any lazy-loading issues.
def test_serialize_is_json_safe() -> None:
    """The CLI's _serialize must not produce NaN/Infinity."""
    from ruhu.classifier.benchmark.run_stage_2_5 import _serialize

    rows = make_synthetic_eval_set(n_rows=20, seed=0)
    runner = BenchmarkRunner(
        StochasticFakeClassifier(seed=0),
        model="m",
        gpu_class="L4",
    )
    report = runner.run(rows)
    data = _serialize(report)
    encoded = json.dumps(data, allow_nan=False)
    assert "NaN" not in encoded and "Infinity" not in encoded
    assert math.isfinite(data["latency"]["p50_ms"])
