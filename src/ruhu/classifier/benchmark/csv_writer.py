"""CSV emitter for cross-config Stage 2.5 comparison.

One row per ``(agent_id, model, gpu_class, lora_name)``. The Stage 2.5
analysis spreadsheet diffs rows on shared ``(agent_id, step_id)`` keys.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .metrics import BenchmarkReport

CSV_COLUMNS = (
    "agent_id",
    "scope",
    "scope_id",
    "model",
    "gpu_class",
    "lora_name",
    "row_count",
    "macro_f1",
    "micro_f1",
    "unknown_rate",
    "p50_ms",
    "p90_ms",
    "p99_ms",
    "ece",
    "lang_breakdown",
)


def write_report(
    path: str | Path,
    report: BenchmarkReport,
    *,
    append: bool = False,
) -> None:
    """Write one ``BenchmarkReport`` (overall + per-step rows) to CSV.

    ``append=True`` opens for append and only writes the header on a fresh
    file. Use this to combine runs across ``(model, gpu_class)`` configs into
    a single comparison sheet.
    """
    p = Path(path)
    write_header = not (append and p.exists() and p.stat().st_size > 0)
    mode = "a" if append else "w"
    with p.open(mode, encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(_row_for("overall", "all", report))
        for step_id, step_report in sorted(report.per_step.items()):
            writer.writerow(_row_for("step", step_id, step_report))


def _row_for(
    scope: str,
    scope_id: str,
    report: BenchmarkReport,
) -> dict[str, str | float | int]:
    latency = report.latency
    ece = (
        report.calibration.expected_calibration_error if report.calibration else 0.0
    )
    return {
        "agent_id": report.agent_id,
        "scope": scope,
        "scope_id": scope_id,
        "model": report.model,
        "gpu_class": report.gpu_class,
        "lora_name": report.lora_name or "",
        "row_count": report.row_count,
        "macro_f1": round(report.macro_f1, 4),
        "micro_f1": round(report.micro_f1, 4),
        "unknown_rate": round(report.unknown_rate, 4),
        "p50_ms": round(latency.p50_ms, 1) if latency else 0.0,
        "p90_ms": round(latency.p90_ms, 1) if latency else 0.0,
        "p99_ms": round(latency.p99_ms, 1) if latency else 0.0,
        "ece": round(ece, 4),
        "lang_breakdown": _format_lang_breakdown(report),
    }


def _format_lang_breakdown(report: BenchmarkReport) -> str:
    if not report.per_language:
        return ""
    parts = [
        f"{lang}:{slice_.macro_f1:.3f}"
        for lang, slice_ in sorted(report.per_language.items())
    ]
    return "|".join(parts)
