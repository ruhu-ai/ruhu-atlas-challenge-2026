"""Tests for src/ruhu/classifier/promotion.py — WI-6.9 gate logic."""
from __future__ import annotations

import pytest

from ruhu.classifier.promotion import (
    BaselineReport,
    EvalLatency,
    EvalReport,
    PerIntentMetric,
    PromotionGateThresholds,
    evaluate,
)


def _eval(
    *,
    macro_f1: float = 0.90,
    unknown_rate: float = 0.05,
    p99_ms: float = 200.0,
    p50_ms: float = 80.0,
    ece: float = 0.05,
    per_intent: list[PerIntentMetric] | None = None,
) -> EvalReport:
    return EvalReport(
        macro_f1=macro_f1,
        unknown_rate=unknown_rate,
        per_intent=per_intent or [],
        latency=EvalLatency(p50_ms=p50_ms, p99_ms=p99_ms),
        expected_calibration_error=ece,
    )


def _baseline(
    *,
    macro_f1: float = 0.85,
    unknown_rate: float = 0.06,
    p99_ms: float = 180.0,
    p50_ms: float = 75.0,
    per_intent: list[PerIntentMetric] | None = None,
) -> BaselineReport:
    return BaselineReport(
        macro_f1=macro_f1,
        unknown_rate=unknown_rate,
        per_intent=per_intent or [],
        latency=EvalLatency(p50_ms=p50_ms, p99_ms=p99_ms),
    )


# ── steady-state happy path ───────────────────────────────────────────────


def test_steady_state_passes_when_all_gates_met() -> None:
    decision = evaluate(
        _eval(macro_f1=0.90, unknown_rate=0.05, p99_ms=200.0, ece=0.05),
        baseline=_baseline(macro_f1=0.85, unknown_rate=0.06, p99_ms=180.0),
    )
    assert decision.regime == "steady_state"
    assert decision.promote is True
    assert all(c.outcome == "pass" for c in decision.checks)


# ── steady-state failure modes ────────────────────────────────────────────


def test_steady_state_fails_when_macro_f1_lift_under_three_pp() -> None:
    decision = evaluate(
        _eval(macro_f1=0.86),
        baseline=_baseline(macro_f1=0.85),
    )
    assert decision.promote is False
    assert any(
        c.outcome == "fail" and c.name == "macro_f1_lift" for c in decision.failures
    )


def test_steady_state_fails_when_unknown_rate_regresses_more_than_one_pp() -> None:
    decision = evaluate(
        _eval(unknown_rate=0.08),
        baseline=_baseline(unknown_rate=0.05),
    )
    assert decision.promote is False
    assert any(c.name == "unknown_rate_no_regression" for c in decision.failures)


def test_steady_state_fails_when_per_intent_drops_more_than_five_pp() -> None:
    cand_intent = [
        PerIntentMetric(intent="transfer_status", support=100, f1=0.70),
    ]
    base_intent = [
        PerIntentMetric(intent="transfer_status", support=100, f1=0.80),
    ]
    decision = evaluate(
        _eval(per_intent=cand_intent),
        baseline=_baseline(per_intent=base_intent),
    )
    assert decision.promote is False
    assert any(c.name == "per_intent_f1_no_regression" for c in decision.failures)


def test_steady_state_ignores_low_support_intents_in_per_intent_check() -> None:
    """A 0.5pp drop on a 10-support intent doesn't trigger the gate."""
    cand_intent = [PerIntentMetric(intent="rare", support=10, f1=0.30)]
    base_intent = [PerIntentMetric(intent="rare", support=10, f1=0.85)]
    decision = evaluate(
        _eval(per_intent=cand_intent),
        baseline=_baseline(per_intent=base_intent),
    )
    # Only the low-support drop exists; no regression check fires
    per_intent_check = next(
        c for c in decision.checks if c.name == "per_intent_f1_no_regression"
    )
    assert per_intent_check.outcome == "pass"


def test_steady_state_fails_on_p99_over_one_point_two_baseline() -> None:
    decision = evaluate(
        _eval(p99_ms=250.0),  # 250 / 180 ≈ 1.39× — too slow
        baseline=_baseline(p99_ms=180.0),
    )
    assert decision.promote is False
    assert any(c.name == "p99_latency" for c in decision.failures)


def test_steady_state_passes_on_p99_at_the_ceiling() -> None:
    decision = evaluate(
        _eval(p99_ms=216.0),  # exactly 1.20× of 180
        baseline=_baseline(p99_ms=180.0),
    )
    p99_check = next(c for c in decision.checks if c.name == "p99_latency")
    assert p99_check.outcome == "pass"


def test_steady_state_fails_on_high_ece() -> None:
    decision = evaluate(
        _eval(ece=0.15),
        baseline=_baseline(),
    )
    assert decision.promote is False
    assert any(c.name == "calibration_ece" for c in decision.failures)


# ── cold-start regime ─────────────────────────────────────────────────────


def test_cold_start_passes_when_all_three_gates_met() -> None:
    decision = evaluate(
        _eval(macro_f1=0.85, unknown_rate=0.05, p99_ms=300.0),
        baseline=None,
        base_model_macro_f1=0.78,  # lift = 0.07 ≥ 0.05
    )
    assert decision.regime == "cold_start"
    assert decision.promote is True
    assert all(c.outcome == "pass" for c in decision.checks)


def test_cold_start_fails_when_macro_f1_lift_under_five_pp_over_base() -> None:
    decision = evaluate(
        _eval(macro_f1=0.80),
        baseline=None,
        base_model_macro_f1=0.78,  # lift only 0.02
    )
    assert decision.promote is False
    assert any(c.name == "macro_f1_lift_over_base" for c in decision.failures)


def test_cold_start_fails_when_unknown_rate_above_ten_percent() -> None:
    decision = evaluate(
        _eval(unknown_rate=0.15),
        baseline=None,
        base_model_macro_f1=0.50,
    )
    assert decision.promote is False
    assert any(c.name == "unknown_rate_cold_start" for c in decision.failures)


def test_cold_start_fails_on_p99_over_400ms() -> None:
    decision = evaluate(
        _eval(p99_ms=500.0),
        baseline=None,
        base_model_macro_f1=0.50,
    )
    assert decision.promote is False
    assert any(c.name == "p99_latency_cold_start" for c in decision.failures)


def test_cold_start_passes_at_the_ceilings() -> None:
    decision = evaluate(
        _eval(macro_f1=0.55, unknown_rate=0.10, p99_ms=400.0),
        baseline=None,
        base_model_macro_f1=0.50,
    )
    assert decision.promote is True


# ── invariants ────────────────────────────────────────────────────────────


def test_evaluate_requires_either_baseline_or_base_model_macro_f1() -> None:
    with pytest.raises(ValueError):
        evaluate(_eval(), baseline=None)


def test_decision_summary_renders_marks_for_each_check() -> None:
    decision = evaluate(_eval(), baseline=_baseline())
    text = decision.summary()
    assert "PROMOTE" in text or "REJECT" in text
    assert "macro_f1_lift" in text


def test_thresholds_are_overridable() -> None:
    """A stricter gate should reject what default would accept."""
    relaxed = PromotionGateThresholds(macro_f1_lift_steady=0.05)  # 5pp instead of 3
    decision = evaluate(
        _eval(macro_f1=0.88),  # 3pp lift over 0.85 baseline — would normally pass
        baseline=_baseline(macro_f1=0.85),
        thresholds=relaxed,
    )
    assert any(c.name == "macro_f1_lift" and c.outcome == "fail" for c in decision.checks)
