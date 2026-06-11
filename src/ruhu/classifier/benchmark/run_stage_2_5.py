"""Stage 2.5 decision-benchmark CLI.

Drives a chosen backend through an eval set and emits a CSV row plus a
structured JSON report. Intended invocation across the four Stage 2.5
configs (see WI-2.5.2):

```
python -m ruhu.classifier.benchmark.run_stage_2_5 \\
    --eval-set ./eval/melonpay.jsonl \\
    --backend transformers --model gemma-4-e4b --gpu-class L4 \\
    --csv-out ./out/stage_2_5.csv --report-out ./out/gemma-l4.json
```

Backends:

- ``synthetic``: stochastic fixture (for the WI-2.5.1 smoke test, no GPU).
- ``transformers``: in-process HF Gemma via the existing
  ``GemmaLocalInterpreter`` weight-loading path. Triggers weight load.
- ``vllm``: HTTP backend (Stage 3+); not yet wired here.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from ..protocol import PrefillClassifier
from . import csv_writer
from ._synthetic import StochasticFakeClassifier, make_synthetic_eval_set
from .eval_set import EvalRow, load_eval_set
from .runner import BenchmarkRunner


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = _load_rows(args)
    classifier = _build_classifier(args)

    runner = BenchmarkRunner(
        classifier,
        model=args.model,
        gpu_class=args.gpu_class,
        lora_name=args.lora_name,
    )
    report = runner.run(rows)

    csv_writer.write_report(args.csv_out, report, append=args.csv_append)
    if args.report_out:
        Path(args.report_out).write_text(
            json.dumps(_serialize(report), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(_summary(report, args))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ruhu.classifier.benchmark.run_stage_2_5",
        description="Stage 2.5 decision-benchmark runner (WI-2.5.1).",
    )
    parser.add_argument(
        "--eval-set",
        help="Path to a JSONL eval set. Omit when --backend=synthetic to use a fixture.",
    )
    parser.add_argument(
        "--backend",
        choices=("synthetic", "transformers"),
        default="synthetic",
        help="Classifier backend. 'synthetic' runs without GPU.",
    )
    parser.add_argument("--model", required=True, help="Logical model name (e.g. gemma-4-e4b)")
    parser.add_argument("--gpu-class", required=True, help="GPU tier label (L4, L40S, H100, ...)")
    parser.add_argument("--lora-name", default=None, help="Optional LoRA adapter id")
    parser.add_argument("--csv-out", required=True, help="CSV file path; append mode safe")
    parser.add_argument(
        "--csv-append",
        action="store_true",
        help="Append to --csv-out instead of overwriting (use across configs).",
    )
    parser.add_argument(
        "--report-out",
        default=None,
        help="Optional JSON report path with full per-step / per-intent detail.",
    )
    parser.add_argument(
        "--synthetic-rows",
        type=int,
        default=100,
        help="Row count for the synthetic eval set (only when --eval-set is omitted).",
    )
    parser.add_argument(
        "--synthetic-fidelity",
        type=float,
        default=0.85,
        help="Fake classifier fidelity for the synthetic backend (0..1).",
    )
    return parser.parse_args(argv)


def _load_rows(args: argparse.Namespace) -> list[EvalRow]:
    if args.eval_set:
        return load_eval_set(args.eval_set)
    if args.backend != "synthetic":
        raise SystemExit("--eval-set is required for non-synthetic backends")
    return make_synthetic_eval_set(n_rows=args.synthetic_rows)


def _build_classifier(args: argparse.Namespace) -> PrefillClassifier:
    if args.backend == "synthetic":
        return StochasticFakeClassifier(fidelity=args.synthetic_fidelity)
    if args.backend == "transformers":
        return _build_transformers_classifier()
    raise SystemExit(f"unsupported backend: {args.backend}")


def _build_transformers_classifier() -> PrefillClassifier:
    try:
        from ...gemma_local import TransformersGemmaBackend
        from ..transformers_backend import TransformersClassifierBackend
    except ImportError as exc:
        raise SystemExit(
            "transformers backend requires torch + transformers + the gemma_local "
            f"weights module to be importable: {exc}"
        ) from exc
    gemma = TransformersGemmaBackend()
    return TransformersClassifierBackend.from_gemma_backend(gemma)


def _summary(report: Any, args: argparse.Namespace) -> str:
    lines = [
        f"Stage 2.5 benchmark: agent={report.agent_id} model={args.model} gpu={args.gpu_class}",
        f"  rows={report.row_count}  macro_f1={report.macro_f1:.4f}  "
        f"micro_f1={report.micro_f1:.4f}  unknown_rate={report.unknown_rate:.4f}",
    ]
    if report.latency:
        lines.append(
            f"  latency p50={report.latency.p50_ms:.1f}ms p90={report.latency.p90_ms:.1f}ms "
            f"p99={report.latency.p99_ms:.1f}ms"
        )
    if report.calibration:
        lines.append(
            f"  ECE={report.calibration.expected_calibration_error:.4f}"
        )
    return "\n".join(lines)


def _serialize(obj: Any) -> Any:
    if is_dataclass(obj):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj


if __name__ == "__main__":
    sys.exit(main())
