"""WI-6.9 — promotion-gate logic for the LoRA registry.

Given an eval report from the training pipeline (running in
``ruhu-ai-training/qwen``) plus the current production baseline, decide
whether the candidate LoRA passes all the gates from
``docs/pre-fill-intent-classifier-design/06-evaluation-spec.md``
§Promotion gate. On pass, the API layer (``promotion_api.py``) flips
``status`` to ``production`` via ``registry.promote_to_production``;
on fail the candidate stays as ``candidate`` and the training pipeline
notifies the engineer (Slack / email).

Two regimes:

- **Steady-state** (prior production exists): macro-F1 lifts ≥3pp,
  unknown rate ≤+1pp, no per-intent F1 regresses ≥5pp on intents with
  ≥30 support, p99 latency ≤1.2× baseline, ECE ≤0.10.

- **Cold-start** (no prior production for the agent): macro-F1 lifts
  ≥5pp over the base-model reference, unknown rate ≤10%, p99 latency
  ≤400ms.

The two regimes share an output shape so the registry / API layer
doesn't care which gate fired.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

GateOutcome = Literal["pass", "fail"]


# ── eval-report payload (matches docs/06-evaluation-spec.md §Output) ───────


class PerIntentMetric(BaseModel):
    intent: str
    support: int = Field(..., ge=0)
    f1: float = Field(..., ge=0.0, le=1.0)


class EvalLatency(BaseModel):
    p50_ms: float = Field(..., ge=0.0)
    p99_ms: float = Field(..., ge=0.0)


class EvalReport(BaseModel):
    """Subset of the spec's eval-report shape used by the promotion gate."""

    macro_f1: float = Field(..., ge=0.0, le=1.0)
    unknown_rate: float = Field(..., ge=0.0, le=1.0)
    per_intent: list[PerIntentMetric] = Field(default_factory=list)
    latency: EvalLatency
    expected_calibration_error: float = Field(..., ge=0.0, le=1.0)


class BaselineReport(BaseModel):
    """Reduced shape carrying just the comparable fields from prior prod."""

    macro_f1: float = Field(..., ge=0.0, le=1.0)
    unknown_rate: float = Field(..., ge=0.0, le=1.0)
    per_intent: list[PerIntentMetric] = Field(default_factory=list)
    latency: EvalLatency


# ── decision shape ─────────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class GateCheck:
    name: str
    outcome: GateOutcome
    detail: str


@dataclass(slots=True, frozen=True)
class PromotionDecision:
    """Result of running every gate. ``promote`` is True iff all checks pass."""

    promote: bool
    regime: Literal["steady_state", "cold_start"]
    checks: list[GateCheck]

    @property
    def failures(self) -> list[GateCheck]:
        return [check for check in self.checks if check.outcome == "fail"]

    def summary(self) -> str:
        verdict = "PROMOTE" if self.promote else "REJECT"
        head = f"Promotion gate ({self.regime}): {verdict}"
        lines = [head]
        for check in self.checks:
            mark = "✓" if check.outcome == "pass" else "✗"
            lines.append(f"  {mark} {check.name}: {check.detail}")
        return "\n".join(lines)


# ── thresholds ─────────────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class PromotionGateThresholds:
    macro_f1_lift_steady: float = 0.03
    unknown_rate_max_increase_steady: float = 0.01
    per_intent_f1_max_drop_steady: float = 0.05
    per_intent_min_support: int = 30
    p99_latency_max_multiplier_steady: float = 1.20
    expected_calibration_error_max: float = 0.10
    macro_f1_lift_cold_start: float = 0.05
    unknown_rate_max_cold_start: float = 0.10
    p99_latency_max_ms_cold_start: float = 400.0


# ── orchestrator ───────────────────────────────────────────────────────────


def evaluate(
    candidate: EvalReport,
    *,
    baseline: BaselineReport | None,
    base_model_macro_f1: float | None = None,
    thresholds: PromotionGateThresholds | None = None,
) -> PromotionDecision:
    """Run the full gate. Returns the decision; doesn't touch the registry."""
    settings = thresholds or PromotionGateThresholds()
    if baseline is not None:
        return _evaluate_steady_state(candidate, baseline, settings)
    if base_model_macro_f1 is not None:
        return _evaluate_cold_start(candidate, base_model_macro_f1, settings)
    raise ValueError(
        "evaluate requires either a baseline (steady-state) or "
        "base_model_macro_f1 (cold-start)"
    )


def _evaluate_steady_state(
    candidate: EvalReport,
    baseline: BaselineReport,
    s: PromotionGateThresholds,
) -> PromotionDecision:
    checks: list[GateCheck] = [
        _check_macro_f1_lift(candidate, baseline, s),
        _check_unknown_rate_no_regression(candidate, baseline, s),
        _check_per_intent_no_regression(candidate, baseline, s),
        _check_p99_within_multiplier(candidate, baseline, s),
        _check_calibration(candidate, s),
    ]
    return PromotionDecision(
        promote=all(c.outcome == "pass" for c in checks),
        regime="steady_state",
        checks=checks,
    )


def _evaluate_cold_start(
    candidate: EvalReport,
    base_model_macro_f1: float,
    s: PromotionGateThresholds,
) -> PromotionDecision:
    checks: list[GateCheck] = [
        _check_macro_f1_cold_start(candidate, base_model_macro_f1, s),
        _check_unknown_rate_cold_start(candidate, s),
        _check_p99_cold_start(candidate, s),
    ]
    return PromotionDecision(
        promote=all(c.outcome == "pass" for c in checks),
        regime="cold_start",
        checks=checks,
    )


# ── individual gate checks ─────────────────────────────────────────────────


def _check_macro_f1_lift(
    candidate: EvalReport,
    baseline: BaselineReport,
    s: PromotionGateThresholds,
) -> GateCheck:
    lift = candidate.macro_f1 - baseline.macro_f1
    passed = lift >= s.macro_f1_lift_steady
    return GateCheck(
        name="macro_f1_lift",
        outcome="pass" if passed else "fail",
        detail=(
            f"candidate={candidate.macro_f1:.4f} baseline={baseline.macro_f1:.4f} "
            f"lift={lift:+.4f} (need ≥ {s.macro_f1_lift_steady:.4f})"
        ),
    )


def _check_unknown_rate_no_regression(
    candidate: EvalReport,
    baseline: BaselineReport,
    s: PromotionGateThresholds,
) -> GateCheck:
    delta = candidate.unknown_rate - baseline.unknown_rate
    passed = delta <= s.unknown_rate_max_increase_steady
    return GateCheck(
        name="unknown_rate_no_regression",
        outcome="pass" if passed else "fail",
        detail=(
            f"candidate={candidate.unknown_rate:.4f} baseline={baseline.unknown_rate:.4f} "
            f"delta={delta:+.4f} (max {s.unknown_rate_max_increase_steady:.4f})"
        ),
    )


def _check_per_intent_no_regression(
    candidate: EvalReport,
    baseline: BaselineReport,
    s: PromotionGateThresholds,
) -> GateCheck:
    baseline_by_intent = {m.intent: m for m in baseline.per_intent}
    bad: list[str] = []
    for cand in candidate.per_intent:
        if cand.support < s.per_intent_min_support:
            continue
        prior = baseline_by_intent.get(cand.intent)
        if prior is None:
            continue
        drop = prior.f1 - cand.f1
        if drop > s.per_intent_f1_max_drop_steady:
            bad.append(
                f"{cand.intent}: {prior.f1:.3f} → {cand.f1:.3f} ({drop:+.3f})"
            )
    if bad:
        return GateCheck(
            name="per_intent_f1_no_regression",
            outcome="fail",
            detail=(
                f"{len(bad)} intent(s) regress (>{s.per_intent_f1_max_drop_steady} drop "
                f"at support≥{s.per_intent_min_support}); first: {bad[0]}"
            ),
        )
    return GateCheck(
        name="per_intent_f1_no_regression",
        outcome="pass",
        detail=(
            f"no intent regresses >{s.per_intent_f1_max_drop_steady} "
            f"at support≥{s.per_intent_min_support}"
        ),
    )


def _check_p99_within_multiplier(
    candidate: EvalReport,
    baseline: BaselineReport,
    s: PromotionGateThresholds,
) -> GateCheck:
    ceiling = baseline.latency.p99_ms * s.p99_latency_max_multiplier_steady
    passed = candidate.latency.p99_ms <= ceiling
    return GateCheck(
        name="p99_latency",
        outcome="pass" if passed else "fail",
        detail=(
            f"candidate p99={candidate.latency.p99_ms:.0f}ms "
            f"baseline p99={baseline.latency.p99_ms:.0f}ms "
            f"(ceiling {ceiling:.0f}ms = {s.p99_latency_max_multiplier_steady}× baseline)"
        ),
    )


def _check_calibration(
    candidate: EvalReport,
    s: PromotionGateThresholds,
) -> GateCheck:
    passed = candidate.expected_calibration_error <= s.expected_calibration_error_max
    return GateCheck(
        name="calibration_ece",
        outcome="pass" if passed else "fail",
        detail=(
            f"ece={candidate.expected_calibration_error:.4f} "
            f"(max {s.expected_calibration_error_max:.4f})"
        ),
    )


def _check_macro_f1_cold_start(
    candidate: EvalReport,
    base_model_macro_f1: float,
    s: PromotionGateThresholds,
) -> GateCheck:
    lift = candidate.macro_f1 - base_model_macro_f1
    passed = lift >= s.macro_f1_lift_cold_start
    return GateCheck(
        name="macro_f1_lift_over_base",
        outcome="pass" if passed else "fail",
        detail=(
            f"candidate={candidate.macro_f1:.4f} base_model={base_model_macro_f1:.4f} "
            f"lift={lift:+.4f} (need ≥ {s.macro_f1_lift_cold_start:.4f})"
        ),
    )


def _check_unknown_rate_cold_start(
    candidate: EvalReport,
    s: PromotionGateThresholds,
) -> GateCheck:
    passed = candidate.unknown_rate <= s.unknown_rate_max_cold_start
    return GateCheck(
        name="unknown_rate_cold_start",
        outcome="pass" if passed else "fail",
        detail=(
            f"unknown_rate={candidate.unknown_rate:.4f} "
            f"(max {s.unknown_rate_max_cold_start:.4f})"
        ),
    )


def _check_p99_cold_start(
    candidate: EvalReport,
    s: PromotionGateThresholds,
) -> GateCheck:
    passed = candidate.latency.p99_ms <= s.p99_latency_max_ms_cold_start
    return GateCheck(
        name="p99_latency_cold_start",
        outcome="pass" if passed else "fail",
        detail=(
            f"p99={candidate.latency.p99_ms:.0f}ms "
            f"(max {s.p99_latency_max_ms_cold_start:.0f}ms)"
        ),
    )


__all__ = [
    "BaselineReport",
    "EvalLatency",
    "EvalReport",
    "GateCheck",
    "GateOutcome",
    "PerIntentMetric",
    "PromotionDecision",
    "PromotionGateThresholds",
    "evaluate",
]
