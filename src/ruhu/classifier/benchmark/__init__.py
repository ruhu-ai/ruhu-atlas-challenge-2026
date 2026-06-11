"""Stage 2.5 decision benchmark for the prefill-first classifier.

Drives a ``PrefillClassifier`` through an eval set, computes per-intent /
per-step / per-language metrics, and emits a CSV comparable across
``(model, gpu_class)`` configurations. The Stage 2.5 gate evaluator decides
whether the cost-latency candidate (Gemma 4 E4B) wins over the reference
baseline (Qwen3-8B).

See ``docs/pre-fill-intent-classifier-design/01-first-principles.md``
§Stage 2.5 decision gate (criteria) and §07-work-items.md WI-2.5.
"""
from .eval_set import EvalRow, load_eval_set
from .gate import Stage25GateCriteria, Stage25GateResult, evaluate_gate
from .metrics import (
    BenchmarkReport,
    LatencyPercentiles,
    PerIntentMetrics,
    compute_confusion_matrix,
    compute_ece,
    compute_latency_percentiles,
    compute_per_intent_metrics,
)
from .runner import BenchmarkRunner

__all__ = [
    "BenchmarkReport",
    "BenchmarkRunner",
    "EvalRow",
    "LatencyPercentiles",
    "PerIntentMetrics",
    "Stage25GateCriteria",
    "Stage25GateResult",
    "compute_confusion_matrix",
    "compute_ece",
    "compute_latency_percentiles",
    "compute_per_intent_metrics",
    "evaluate_gate",
    "load_eval_set",
]
