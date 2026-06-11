"""Stage 2.5 decision gate evaluator.

Compares a candidate ``BenchmarkReport`` (Gemma 4 E4B) against the reference
baseline (Qwen3-8B). Default outcome: ship the reference. The candidate must
pass **all** criteria to win.

Criteria source: ``docs/pre-fill-intent-classifier-design/01-first-principles.md``
§Stage 2.5 decision gate (criteria).
"""
from __future__ import annotations

from dataclasses import dataclass

from .metrics import BenchmarkReport


@dataclass(slots=True, frozen=True)
class Stage25GateCriteria:
    """Pass thresholds. Defaults match the spec; override for local sandbox runs."""

    macro_f1_max_deficit: float = 0.02
    min_per_intent_f1: float = 0.75
    min_per_intent_support: int = 30
    max_ece: float = 0.10
    p50_latency_max_ms: float = 100.0
    require_p50_le_baseline: bool = True


@dataclass(slots=True, frozen=True)
class GateCheck:
    name: str
    passed: bool
    detail: str


@dataclass(slots=True, frozen=True)
class Stage25GateResult:
    candidate_model: str
    baseline_model: str
    passed: bool
    checks: list[GateCheck]

    def summary(self) -> str:
        verdict = "PASS — ship candidate" if self.passed else "FAIL — ship baseline"
        lines = [
            f"Stage 2.5 gate: {self.candidate_model} vs {self.baseline_model}",
            f"Verdict: {verdict}",
            "",
        ]
        for check in self.checks:
            mark = "✓" if check.passed else "✗"
            lines.append(f"  {mark} {check.name}: {check.detail}")
        return "\n".join(lines)


def evaluate_gate(
    candidate: BenchmarkReport,
    baseline: BenchmarkReport,
    *,
    criteria: Stage25GateCriteria | None = None,
) -> Stage25GateResult:
    c = criteria or Stage25GateCriteria()
    checks = [
        _check_macro_f1(candidate, baseline, c),
        _check_per_intent_floor(candidate, c),
        _check_multilingual(candidate, baseline),
        _check_calibration(candidate, c),
        _check_latency(candidate, baseline, c),
    ]
    return Stage25GateResult(
        candidate_model=candidate.model,
        baseline_model=baseline.model,
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def _check_macro_f1(
    candidate: BenchmarkReport,
    baseline: BenchmarkReport,
    c: Stage25GateCriteria,
) -> GateCheck:
    deficit = baseline.macro_f1 - candidate.macro_f1
    passed = deficit <= c.macro_f1_max_deficit
    return GateCheck(
        name="macro_f1_within_2pts_of_baseline",
        passed=passed,
        detail=(
            f"candidate={candidate.macro_f1:.4f} baseline={baseline.macro_f1:.4f} "
            f"deficit={deficit:+.4f} (max {c.macro_f1_max_deficit:.4f})"
        ),
    )


def _check_per_intent_floor(
    candidate: BenchmarkReport,
    c: Stage25GateCriteria,
) -> GateCheck:
    bad = [
        m
        for m in candidate.per_intent.values()
        if m.support >= c.min_per_intent_support and m.f1 < c.min_per_intent_f1
    ]
    if bad:
        worst = min(bad, key=lambda m: m.f1)
        return GateCheck(
            name="no_intent_f1_below_floor",
            passed=False,
            detail=(
                f"{len(bad)} intent(s) with support>={c.min_per_intent_support} below "
                f"f1={c.min_per_intent_f1}; worst={worst.intent} f1={worst.f1:.3f}"
            ),
        )
    return GateCheck(
        name="no_intent_f1_below_floor",
        passed=True,
        detail=(
            f"all intents with support>={c.min_per_intent_support} above "
            f"f1={c.min_per_intent_f1}"
        ),
    )


def _check_multilingual(
    candidate: BenchmarkReport,
    baseline: BenchmarkReport,
) -> GateCheck:
    shared_langs = sorted(
        lang
        for lang in candidate.per_language
        if lang in baseline.per_language and lang != "unknown"
    )
    if not shared_langs:
        return GateCheck(
            name="multilingual_macro_f1_meets_baseline",
            passed=True,
            detail="no shared non-default languages in eval set; check skipped",
        )
    failing = []
    for lang in shared_langs:
        cand = candidate.per_language[lang].macro_f1
        base = baseline.per_language[lang].macro_f1
        if cand < base:
            failing.append((lang, cand, base))
    if failing:
        worst = min(failing, key=lambda t: t[1] - t[2])
        return GateCheck(
            name="multilingual_macro_f1_meets_baseline",
            passed=False,
            detail=(
                f"{len(failing)} language(s) regress; worst {worst[0]}: "
                f"cand={worst[1]:.3f} base={worst[2]:.3f}"
            ),
        )
    return GateCheck(
        name="multilingual_macro_f1_meets_baseline",
        passed=True,
        detail=f"candidate >= baseline on all {len(shared_langs)} language(s): {shared_langs}",
    )


def _check_calibration(
    candidate: BenchmarkReport,
    c: Stage25GateCriteria,
) -> GateCheck:
    if candidate.calibration is None:
        return GateCheck(
            name="confidence_calibration_ece",
            passed=True,
            detail="no calibration data (all confidences zero); check skipped",
        )
    ece = candidate.calibration.expected_calibration_error
    return GateCheck(
        name="confidence_calibration_ece",
        passed=ece <= c.max_ece,
        detail=f"ece={ece:.4f} (max {c.max_ece:.4f})",
    )


def _check_latency(
    candidate: BenchmarkReport,
    baseline: BenchmarkReport,
    c: Stage25GateCriteria,
) -> GateCheck:
    if candidate.latency is None or baseline.latency is None:
        return GateCheck(
            name="p50_latency",
            passed=True,
            detail="no latency data; check skipped",
        )
    cand_p50 = candidate.latency.p50_ms
    base_p50 = baseline.latency.p50_ms
    under_budget = cand_p50 <= c.p50_latency_max_ms
    if c.require_p50_le_baseline:
        if cand_p50 <= base_p50 or under_budget:
            return GateCheck(
                name="p50_latency",
                passed=True,
                detail=(
                    f"candidate p50={cand_p50:.1f}ms baseline p50={base_p50:.1f}ms "
                    f"(budget {c.p50_latency_max_ms:.0f}ms)"
                ),
            )
        return GateCheck(
            name="p50_latency",
            passed=False,
            detail=(
                f"candidate p50={cand_p50:.1f}ms > baseline p50={base_p50:.1f}ms "
                f"and over budget {c.p50_latency_max_ms:.0f}ms"
            ),
        )
    return GateCheck(
        name="p50_latency",
        passed=under_budget,
        detail=f"candidate p50={cand_p50:.1f}ms (budget {c.p50_latency_max_ms:.0f}ms)",
    )
