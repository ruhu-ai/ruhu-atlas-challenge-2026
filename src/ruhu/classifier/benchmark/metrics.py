"""Pure-Python classifier metrics.

Computes per-intent precision/recall/F1, macro-F1, micro-F1, expected
calibration error, confusion matrices, and latency percentiles. No torch,
no sklearn — Stage 2.5 runs anywhere a Python interpreter does.

All inputs accept ``None`` predictions (the classifier returned ``unknown``
or failed). ``None`` predictions count toward unknown-rate and against
recall on the gold class, but contribute nothing to precision on real
labels.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean


@dataclass(slots=True, frozen=True)
class PerIntentMetrics:
    intent: str
    support: int
    precision: float
    recall: float
    f1: float


@dataclass(slots=True, frozen=True)
class LatencyPercentiles:
    p50_ms: float
    p90_ms: float
    p99_ms: float
    count: int


@dataclass(slots=True, frozen=True)
class CalibrationBucket:
    confidence_min: float
    confidence_max: float
    predictions: int
    accuracy: float


@dataclass(slots=True, frozen=True)
class CalibrationReport:
    buckets: list[CalibrationBucket]
    expected_calibration_error: float


@dataclass(slots=True)
class BenchmarkReport:
    """Full per-run report. One per ``(model, gpu_class, lora_name)`` config."""

    agent_id: str
    model: str
    gpu_class: str
    lora_name: str | None
    row_count: int
    micro_f1: float
    macro_f1: float
    unknown_rate: float
    per_intent: dict[str, PerIntentMetrics] = field(default_factory=dict)
    per_step: dict[str, "BenchmarkReport"] = field(default_factory=dict)
    per_language: dict[str, "BenchmarkReport"] = field(default_factory=dict)
    confusion_matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    latency: LatencyPercentiles | None = None
    calibration: CalibrationReport | None = None


def compute_per_intent_metrics(
    gold: list[str | None],
    pred: list[str | None],
) -> dict[str, PerIntentMetrics]:
    """Per-intent precision / recall / F1.

    Computed only over intents that appear in ``gold`` (we don't fabricate
    metrics for labels that no eval row exercises). ``None`` predictions
    count as misses against recall but never against precision.
    """
    if len(gold) != len(pred):
        raise ValueError(f"length mismatch: gold={len(gold)} pred={len(pred)}")

    intents = sorted({g for g in gold if g is not None})
    out: dict[str, PerIntentMetrics] = {}
    for intent in intents:
        tp = sum(1 for g, p in zip(gold, pred) if g == intent and p == intent)
        fp = sum(1 for g, p in zip(gold, pred) if g != intent and p == intent)
        fn = sum(1 for g, p in zip(gold, pred) if g == intent and p != intent)
        support = tp + fn
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        out[intent] = PerIntentMetrics(
            intent=intent,
            support=support,
            precision=precision,
            recall=recall,
            f1=f1,
        )
    return out


def compute_macro_f1(per_intent: dict[str, PerIntentMetrics]) -> float:
    if not per_intent:
        return 0.0
    return mean(m.f1 for m in per_intent.values())


def compute_micro_f1(
    gold: list[str | None],
    pred: list[str | None],
) -> float:
    """Micro-F1 over real-label predictions only.

    ``None`` predictions don't count as a TP for any class, but the
    corresponding gold rows still count toward the gold-row total. This
    matches the classifier evaluation definition.
    """
    if len(gold) != len(pred):
        raise ValueError(f"length mismatch: gold={len(gold)} pred={len(pred)}")
    tp = sum(1 for g, p in zip(gold, pred) if g is not None and g == p)
    pred_real = sum(1 for p in pred if p is not None)
    gold_real = sum(1 for g in gold if g is not None)
    if pred_real == 0 and gold_real == 0:
        return 0.0
    precision = tp / pred_real if pred_real > 0 else 0.0
    recall = tp / gold_real if gold_real > 0 else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def compute_unknown_rate(pred: list[str | None]) -> float:
    if not pred:
        return 0.0
    return sum(1 for p in pred if p is None) / len(pred)


def compute_confusion_matrix(
    gold: list[str | None],
    pred: list[str | None],
) -> dict[str, dict[str, int]]:
    if len(gold) != len(pred):
        raise ValueError(f"length mismatch: gold={len(gold)} pred={len(pred)}")
    matrix: dict[str, dict[str, int]] = {}
    for g, p in zip(gold, pred):
        gold_key = g if g is not None else "_unknown_"
        pred_key = p if p is not None else "_unknown_"
        matrix.setdefault(gold_key, {})
        matrix[gold_key][pred_key] = matrix[gold_key].get(pred_key, 0) + 1
    return matrix


def compute_latency_percentiles(values_ms: list[int | float]) -> LatencyPercentiles:
    if not values_ms:
        return LatencyPercentiles(p50_ms=0.0, p90_ms=0.0, p99_ms=0.0, count=0)
    sorted_values = sorted(float(v) for v in values_ms)
    return LatencyPercentiles(
        p50_ms=_percentile(sorted_values, 0.50),
        p90_ms=_percentile(sorted_values, 0.90),
        p99_ms=_percentile(sorted_values, 0.99),
        count=len(sorted_values),
    )


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def compute_ece(
    predictions: list[tuple[float, bool]],
    n_buckets: int = 10,
) -> CalibrationReport:
    """Expected Calibration Error with equal-width buckets.

    Each ``predictions`` entry is ``(confidence, correct)``. Confidence is in
    [0, 1]; correct is whether the prediction matched the gold label. Returns
    the per-bucket reliability data plus the weighted ECE.
    """
    if n_buckets < 1:
        raise ValueError("n_buckets must be >= 1")
    buckets: list[CalibrationBucket] = []
    if not predictions:
        return CalibrationReport(buckets=buckets, expected_calibration_error=0.0)

    edges = [i / n_buckets for i in range(n_buckets + 1)]
    total = len(predictions)
    weighted_gap = 0.0
    for i in range(n_buckets):
        lo, hi = edges[i], edges[i + 1]
        in_bucket = [
            (c, k) for c, k in predictions if (lo <= c < hi or (i == n_buckets - 1 and c == hi))
        ]
        if not in_bucket:
            buckets.append(
                CalibrationBucket(
                    confidence_min=lo,
                    confidence_max=hi,
                    predictions=0,
                    accuracy=0.0,
                )
            )
            continue
        avg_conf = mean(c for c, _ in in_bucket)
        accuracy = sum(1 for _, k in in_bucket if k) / len(in_bucket)
        weighted_gap += abs(avg_conf - accuracy) * (len(in_bucket) / total)
        buckets.append(
            CalibrationBucket(
                confidence_min=lo,
                confidence_max=hi,
                predictions=len(in_bucket),
                accuracy=accuracy,
            )
        )
    return CalibrationReport(
        buckets=buckets,
        expected_calibration_error=weighted_gap,
    )
