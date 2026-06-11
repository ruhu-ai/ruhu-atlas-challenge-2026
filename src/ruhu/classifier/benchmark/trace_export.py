"""Export production turn traces into Stage 2.5 eval JSONL.

Reads ``turn_traces`` and ``agent_versions`` from the runtime database,
projects each user-message turn into an ``EvalRow``, and emits the
stratified sample as JSONL ready for ``run_stage_2_5.py``.

Three label modes:

- ``silver`` (default): the prod classifier's prediction is used as
  ``gold_chosen_label`` only when ``confidence >= --min-confidence``.
  Skips rows that don't meet the threshold. Useful for regression eval
  against the same model — quality of "silver" labels is upper-bounded
  by the prod classifier itself.

- ``unlabeled``: ``gold_chosen_label=null`` on every row. Use this to
  build a human-review or LLM-as-judge dataset.

- ``predicted``: ``gold_chosen_label`` is always the prod prediction
  (including ``None`` when prod returned unknown). Useful for measuring
  how a candidate model agrees with prod, not absolute quality.

The module avoids importing ``ruhu.agent_document`` so it can run while
that module is being reshaped on the parallel track. Step lookup walks
the raw JSON of ``AgentVersionRecord.agent_document_json``.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

from .eval_set import EvalRow

LabelMode = Literal["silver", "unlabeled", "predicted"]
_TRACE_EXTENSION_KEY = "__trace_extensions__"


@dataclass(slots=True)
class TraceRow:
    """Subset of a ``turn_traces`` row needed to project an eval row."""

    agent_id: str
    agent_version_id: str
    step_id: str
    user_text: str
    classifier_chosen_label: str | None
    classifier_confidence: float | None


def outcome_catalog_for_step_dict(step_dict: dict[str, Any]) -> dict[str, str]:
    """Compute the ``{event: description}`` outcome catalog for a step JSON dict.

    Walks the step's outgoing transitions and selects ``OutcomeCondition``
    entries (``when.kind == "outcome"``), reading the stable ``event``
    token and LLM-evaluated ``description``. The result mirrors what the
    live ``classifier.prompt.outcome_catalog_for_step`` produces from a
    Pydantic ``Step`` object — except this projection only includes
    *authored* outcomes (no universals), since silver-label evals only
    care about labels the classifier could have predicted from authored
    transitions.

    Sorted by event ascending so caller-facing label lists are stable.
    """
    catalog: dict[str, str] = {}
    for transition in step_dict.get("transitions") or []:
        condition = transition.get("when") or {}
        if condition.get("kind") != "outcome":
            continue
        event = condition.get("event")
        if not event:
            continue
        description = condition.get("description") or ""
        catalog[str(event)] = str(description)
    return {event: catalog[event] for event in sorted(catalog)}


def find_step_in_doc(
    document_json: dict[str, Any],
    step_id: str,
) -> dict[str, Any] | None:
    for scenario in document_json.get("scenarios") or []:
        for step in scenario.get("steps") or []:
            if step.get("id") == step_id:
                return step
    return None


def extract_user_text(rules_json: dict[str, Any] | None) -> str | None:
    """Pull ``redacted_text`` from a trace's normalized observation.

    Returns ``None`` when the turn isn't a user-message turn (system
    events, voice timeouts, etc.).
    """
    if not rules_json:
        return None
    extensions = rules_json.get(_TRACE_EXTENSION_KEY) or {}
    obs = extensions.get("normalized_observation") or {}
    if not obs.get("text_present"):
        return None
    text = obs.get("redacted_text")
    if not text:
        return None
    return str(text).strip() or None


def project_eval_row(
    trace: TraceRow,
    agent_document_json: dict[str, Any],
    *,
    label_mode: LabelMode = "silver",
    min_confidence: float = 0.85,
) -> EvalRow | None:
    step = find_step_in_doc(agent_document_json, trace.step_id)
    if step is None:
        return None
    catalog = outcome_catalog_for_step_dict(step)
    if not catalog:
        return None
    gold = _gold_label(trace, label_mode=label_mode, min_confidence=min_confidence)
    if label_mode == "silver" and gold is None:
        return None
    return EvalRow(
        agent_id=trace.agent_id,
        agent_version_id=trace.agent_version_id,
        step_id=trace.step_id,
        step_name=str(step.get("name") or trace.step_id),
        step_summary=str(step.get("description") or step.get("summary") or ""),
        candidate_labels=catalog,
        user_text=trace.user_text,
        gold_chosen_label=gold,
        language="unknown",
    )


def _gold_label(
    trace: TraceRow,
    *,
    label_mode: LabelMode,
    min_confidence: float,
) -> str | None:
    if label_mode == "unlabeled":
        return None
    pred = trace.classifier_chosen_label
    if label_mode == "predicted":
        return pred
    if label_mode == "silver":
        confidence = float(trace.classifier_confidence or 0.0)
        if pred is not None and confidence >= min_confidence:
            return pred
        return None
    raise ValueError(f"unknown label_mode: {label_mode!r}")


def stratified_sample(
    rows: list[EvalRow],
    *,
    rows_per_step: int,
    seed: int = 42,
) -> list[EvalRow]:
    """Cap each ``(agent_id, step_id)`` bucket at ``rows_per_step`` rows."""
    if rows_per_step < 1:
        raise ValueError("rows_per_step must be >= 1")
    rng = random.Random(seed)
    buckets: dict[tuple[str, str], list[EvalRow]] = {}
    for row in rows:
        buckets.setdefault((row.agent_id, row.step_id), []).append(row)
    sampled: list[EvalRow] = []
    for key in sorted(buckets):
        bucket = buckets[key]
        rng.shuffle(bucket)
        sampled.extend(bucket[:rows_per_step])
    return sampled


def write_jsonl(rows: Iterable[EvalRow], path: str | Path) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with p.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(_row_to_json(row) + "\n")
            written += 1
    return written


def _row_to_json(row: EvalRow) -> str:
    return json.dumps(
        {
            "agent_id": row.agent_id,
            "agent_version_id": row.agent_version_id,
            "step_id": row.step_id,
            "step_name": row.step_name,
            "step_summary": row.step_summary,
            "candidate_labels": row.candidate_labels,
            "user_text": row.user_text,
            "gold_chosen_label": row.gold_chosen_label,
            "language": row.language,
        },
        ensure_ascii=False,
    )


# ── DB orchestration ─────────────────────────────────────────────────────────


def export_from_session(
    session: Any,
    *,
    agent_id: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    label_mode: LabelMode = "silver",
    min_confidence: float = 0.85,
    rows_per_step: int = 200,
    seed: int = 42,
) -> list[EvalRow]:
    """Query ``turn_traces``, project to eval rows, return a stratified sample.

    ``session`` is a SQLAlchemy session. Imported lazily so this module
    is unit-testable on machines without the full backend stack.
    """
    from sqlalchemy import select

    from ...db_models import AgentVersionRecord, TurnTraceRecord

    query = select(TurnTraceRecord)
    if agent_id is not None:
        query = query.where(TurnTraceRecord.agent_id == agent_id)
    if start_date is not None:
        query = query.where(TurnTraceRecord.recorded_at >= start_date)
    if end_date is not None:
        query = query.where(TurnTraceRecord.recorded_at < end_date)

    doc_cache: dict[str, dict[str, Any] | None] = {}

    def _resolve_doc(version_id: str) -> dict[str, Any] | None:
        if version_id in doc_cache:
            return doc_cache[version_id]
        record = session.execute(
            select(AgentVersionRecord).where(AgentVersionRecord.version_id == version_id)
        ).scalar_one_or_none()
        doc_cache[version_id] = record.agent_document_json if record else None
        return doc_cache[version_id]

    candidate_rows: list[EvalRow] = []
    for record in session.execute(query).scalars():
        if not record.agent_version_id:
            continue
        document = _resolve_doc(record.agent_version_id)
        if not document:
            continue
        user_text = extract_user_text(record.rules_json)
        if not user_text:
            continue
        classifier = record.classifier_json or {}
        trace = TraceRow(
            agent_id=record.agent_id,
            agent_version_id=record.agent_version_id,
            step_id=record.step_before,
            user_text=user_text,
            classifier_chosen_label=classifier.get("chosen_label"),
            classifier_confidence=classifier.get("confidence"),
        )
        eval_row = project_eval_row(
            trace,
            document,
            label_mode=label_mode,
            min_confidence=min_confidence,
        )
        if eval_row is not None:
            candidate_rows.append(eval_row)

    return stratified_sample(
        candidate_rows,
        rows_per_step=rows_per_step,
        seed=seed,
    )


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = _run(args)
    written = write_jsonl(rows, args.output)
    summary = _summary(rows, args)
    print(summary)
    return 0 if written > 0 else 2


def _run(args: argparse.Namespace) -> list[EvalRow]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(args.database_url)
    with Session(engine) as session:
        return export_from_session(
            session,
            agent_id=args.agent_id,
            start_date=args.start_date,
            end_date=args.end_date,
            label_mode=args.label_mode,
            min_confidence=args.min_confidence,
            rows_per_step=args.rows_per_step,
            seed=args.seed,
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ruhu.classifier.benchmark.trace_export",
        description="Export production turn traces into Stage 2.5 eval JSONL.",
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy URL (e.g. postgresql+psycopg://user@host/ruhu_runtime_dev)",
    )
    parser.add_argument(
        "--agent-id",
        default=None,
        help="Restrict to one agent. Omit to export across all agents.",
    )
    parser.add_argument(
        "--start-date",
        type=_parse_iso,
        default=None,
        help="Inclusive lower bound on recorded_at (ISO 8601).",
    )
    parser.add_argument(
        "--end-date",
        type=_parse_iso,
        default=None,
        help="Exclusive upper bound on recorded_at (ISO 8601).",
    )
    parser.add_argument(
        "--label-mode",
        choices=("silver", "unlabeled", "predicted"),
        default="silver",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.85,
        help="Minimum classifier confidence for silver labels.",
    )
    parser.add_argument(
        "--rows-per-step",
        type=int,
        default=200,
        help="Cap per (agent_id, step_id) bucket.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", required=True, help="Output JSONL path.")
    return parser.parse_args(argv)


def _parse_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected ISO 8601 date/datetime, got {value!r}: {exc}"
        ) from exc


def _summary(rows: list[EvalRow], args: argparse.Namespace) -> str:
    by_step: dict[tuple[str, str], int] = {}
    labeled = 0
    for row in rows:
        by_step[(row.agent_id, row.step_id)] = by_step.get((row.agent_id, row.step_id), 0) + 1
        if row.gold_chosen_label is not None:
            labeled += 1
    lines = [
        f"trace_export wrote {len(rows)} rows to {args.output}",
        f"  label_mode={args.label_mode}  labeled={labeled}/{len(rows)}",
        f"  buckets={len(by_step)}  rows_per_step_cap={args.rows_per_step}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
