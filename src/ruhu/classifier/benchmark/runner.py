"""Benchmark runner.

Drives a ``PrefillClassifier`` through an iterable of ``EvalRow``s, gathers
predictions and timings, and emits a fully-populated ``BenchmarkReport``
with overall, per-step, and per-language slices. The runner is
backend-agnostic — pass any object with a ``classify()`` method.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from ..protocol import ClassificationResult, PrefillClassifier
from .eval_set import EvalRow
from .metrics import (
    BenchmarkReport,
    compute_confusion_matrix,
    compute_ece,
    compute_latency_percentiles,
    compute_macro_f1,
    compute_micro_f1,
    compute_per_intent_metrics,
    compute_unknown_rate,
)


@dataclass(slots=True, frozen=True)
class _Sample:
    row: EvalRow
    prediction: str | None
    confidence: float
    correct: bool
    elapsed_ms: int


class BenchmarkRunner:
    def __init__(
        self,
        classifier: PrefillClassifier,
        *,
        model: str,
        gpu_class: str,
        lora_name: str | None = None,
    ) -> None:
        self._classifier = classifier
        self._model = model
        self._gpu_class = gpu_class
        self._lora_name = lora_name

    def run(self, rows: Iterable[EvalRow]) -> BenchmarkReport:
        samples = [self._classify_row(row) for row in rows]
        if not samples:
            raise ValueError("eval set is empty")
        agent_id = samples[0].row.agent_id
        return self._build_report(agent_id, samples)

    def _classify_row(self, row: EvalRow) -> _Sample:
        request = row.to_classification_request(lora_name=self._lora_name)
        started = time.perf_counter()
        result: ClassificationResult = self._classifier.classify(request)
        elapsed_ms = result.elapsed_ms or int((time.perf_counter() - started) * 1000)
        prediction = result.chosen_label
        correct = prediction is not None and prediction == row.gold_chosen_label
        return _Sample(
            row=row,
            prediction=prediction,
            confidence=float(result.confidence or 0.0),
            correct=correct,
            elapsed_ms=int(elapsed_ms),
        )

    def _build_report(self, agent_id: str, samples: list[_Sample]) -> BenchmarkReport:
        report = self._compute_slice(agent_id, samples)
        report.per_step = {
            step_id: self._compute_slice(agent_id, step_samples)
            for step_id, step_samples in _group_by(samples, key=lambda s: s.row.step_id).items()
        }
        report.per_language = {
            lang: self._compute_slice(agent_id, lang_samples)
            for lang, lang_samples in _group_by(samples, key=lambda s: s.row.language).items()
        }
        return report

    def _compute_slice(self, agent_id: str, samples: list[_Sample]) -> BenchmarkReport:
        gold = [s.row.gold_chosen_label for s in samples]
        pred = [s.prediction for s in samples]
        per_intent = compute_per_intent_metrics(gold, pred)
        latency = compute_latency_percentiles([s.elapsed_ms for s in samples])
        calibration = compute_ece(
            [(s.confidence, s.correct) for s in samples if s.confidence > 0.0]
        )
        return BenchmarkReport(
            agent_id=agent_id,
            model=self._model,
            gpu_class=self._gpu_class,
            lora_name=self._lora_name,
            row_count=len(samples),
            micro_f1=compute_micro_f1(gold, pred),
            macro_f1=compute_macro_f1(per_intent),
            unknown_rate=compute_unknown_rate(pred),
            per_intent=per_intent,
            confusion_matrix=compute_confusion_matrix(gold, pred),
            latency=latency,
            calibration=calibration,
        )


def _group_by(samples: list[_Sample], *, key) -> dict[str, list[_Sample]]:
    out: dict[str, list[_Sample]] = {}
    for s in samples:
        out.setdefault(key(s), []).append(s)
    return out
