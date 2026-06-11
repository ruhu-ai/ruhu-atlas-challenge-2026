"""WI-6.1 — Export turn traces into classifier training JSONL.

Reads ``turn_traces`` + ``conversations`` + ``agent_versions`` from the
runtime database, applies the filter rules from
``docs/pre-fill-intent-classifier-design/05-training-pipeline.md``
§Trace export rules, assigns each surviving turn to one of three weight
buckets, and writes JSONL of the shape:

```
{
  "context": "<deterministic prefix from prompt.py>",
  "input_window": "User message: <text>\\nOutcome:",
  "labels": ["<outcome_event>", ...],
  "_metadata": { ... bucket / weight / source-of-truth fields ... }
}
```

The wire shape (``context``, ``input_window``, ``labels``) is the compact
classifier ingestion contract. ``_metadata`` is a Ruhu-specific extension that
downstream pipeline stages (``teacher_relabel``, ``curate``,
``train_lora``) read to drive bucket-weighted training and teacher
relabeling. The training script is free to ignore ``_metadata`` — the
core training fields are sufficient on their own.

Bucket assignment per spec §What we *prioritize*:

| Bucket                       | Condition                                       | Weight |
|------------------------------|-------------------------------------------------|--------|
| ``high_conf_completion``     | ``confidence ≥ 0.9`` AND conversation completed | 1.0    |
| ``low_conf``                 | ``0.3 ≤ confidence < 0.7``                      | 2.0    |
| ``confusion_pair``           | label ∈ a configured confusion pair             | 3.0    |
| ``other``                    | everything else                                 | 1.0    |

``confusion_pair`` requires Bucket-3 input (a list of ``[intent_a,
intent_b]`` pairs from the latest eval). When no confusion-pairs file is
provided, no rows enter that bucket — they're rebucketed naturally when
WI-6.8 (eval harness) starts producing confusion data.

Cancellation pattern: when a user posts a follow-up turn within 8s of
the prior turn AND the predicted label changes, the *first* turn is
flagged ``cancellation_pattern: true`` in metadata. Per spec these
aren't filtered, only flagged for teacher relabel.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from ...agent_document import AgentDocument, Step
from ..prompt import build_classifier_prefix, build_classifier_suffix

Bucket = Literal["high_conf_completion", "low_conf", "confusion_pair", "other"]

USER_MESSAGE_EVENT_TYPES = frozenset({"user_message", "user_final_transcript"})
DEGRADED_CLASSIFIER_UNAVAILABLE = "classifier_unavailable"
CANCELLATION_WINDOW = timedelta(seconds=8)
COMPLETION_OUTCOMES = frozenset({"resolved", "completed", "success"})

DEFAULT_LOOKBACK_DAYS = 30
HIGH_CONF_THRESHOLD = 0.9
LOW_CONF_LOWER = 0.3
LOW_CONF_UPPER = 0.7

_TRACE_EXTENSION_KEY = "__trace_extensions__"


@dataclass(slots=True, frozen=True)
class TrainingRow:
    context: str
    input_window: str
    labels: list[str]
    weight: float = 1.0
    bucket: Bucket = "other"
    needs_teacher_relabel: bool = False
    cancellation_pattern: bool = False
    agent_id: str = ""
    agent_version_id: str = ""
    step_id: str = ""
    confidence: float | None = None
    conversation_id: str = ""
    turn_recorded_at: datetime | None = None


# ── pure projection helpers (no DB) ──────────────────────────────────────────


def extract_event_type(rules_json: dict | None) -> str:
    if not rules_json:
        return ""
    return str((rules_json.get(_TRACE_EXTENSION_KEY) or {}).get("event_type") or "")


def extract_degraded_mode(rules_json: dict | None) -> str | None:
    if not rules_json:
        return None
    obs = (rules_json.get(_TRACE_EXTENSION_KEY) or {}).get("decision_observability") or {}
    value = obs.get("degraded_mode")
    return str(value) if value is not None else None


def extract_user_text(rules_json: dict | None) -> str | None:
    """Pull ``redacted_text`` from the trace's normalized observation.

    Mirrors ``benchmark.trace_export.extract_user_text`` — see comment
    there about the source-of-truth helper. Duplicated here (5 lines) to
    avoid an import cycle while we have two trace exporters.
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


def extract_outcome_labels(semantic_events_json: list | None) -> list[str]:
    """Return the workflow-routing outcome events fired this turn.

    Walks ``semantic_events_json`` for ``family="routing", name="outcome_resolved"``
    records (emitted by ``classifier_strategy.result_to_routing_events``)
    and returns each event's ``payload["event"]`` — the stable
    ``OutcomeCondition.event`` token that became the training label.
    """
    if not semantic_events_json:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for event in semantic_events_json:
        if not isinstance(event, dict):
            continue
        if event.get("family") != "routing" or event.get("name") != "outcome_resolved":
            continue
        payload = event.get("payload") or {}
        label = payload.get("event")
        if not label or label in seen:
            continue
        seen.add(str(label))
        out.append(str(label))
    return out


def assign_bucket(
    *,
    confidence: float | None,
    is_completed: bool,
    confusion_pair: bool = False,
) -> tuple[Bucket, float]:
    if confusion_pair:
        return "confusion_pair", 3.0
    if confidence is not None:
        if confidence >= HIGH_CONF_THRESHOLD and is_completed:
            return "high_conf_completion", 1.0
        if LOW_CONF_LOWER <= confidence < LOW_CONF_UPPER:
            return "low_conf", 2.0
    return "other", 1.0


def _confusion_pairs_match(labels: list[str], pairs: set[frozenset[str]] | None) -> bool:
    if not pairs or not labels:
        return False
    label_set = set(labels)
    return any(pair.issubset(label_set) or pair & label_set for pair in pairs)


def project_training_row(
    *,
    agent_id: str,
    agent_version_id: str,
    step: Step,
    document: AgentDocument,
    user_text: str,
    semantic_events_json: list | None,
    confidence: float | None,
    is_completed: bool,
    conversation_id: str,
    turn_recorded_at: datetime,
    confusion_pairs: set[frozenset[str]] | None = None,
    cancellation_pattern: bool = False,
) -> TrainingRow:
    labels = extract_outcome_labels(semantic_events_json)
    is_confusion = _confusion_pairs_match(labels, confusion_pairs)
    bucket, weight = assign_bucket(
        confidence=confidence,
        is_completed=is_completed,
        confusion_pair=is_confusion,
    )
    needs_teacher = bucket in {"low_conf", "confusion_pair"} or cancellation_pattern
    return TrainingRow(
        context=build_classifier_prefix(document, step),
        input_window=build_classifier_suffix(user_text),
        labels=labels,
        weight=weight,
        bucket=bucket,
        needs_teacher_relabel=needs_teacher,
        cancellation_pattern=cancellation_pattern,
        agent_id=agent_id,
        agent_version_id=agent_version_id,
        step_id=step.id,
        confidence=confidence,
        conversation_id=conversation_id,
        turn_recorded_at=turn_recorded_at,
    )


def detect_cancellation_patterns(
    turn_summaries: dict[str, list[tuple[datetime, str | None]]],
) -> set[tuple[str, datetime]]:
    """Return ``(conversation_id, recorded_at)`` keys flagged as cancellations.

    A turn is flagged when the *next* turn in the same conversation arrives
    within ``CANCELLATION_WINDOW`` and has a different predicted outcome
    label. The user noticed the wrong classification and re-stated.
    """
    flagged: set[tuple[str, datetime]] = set()
    for conv_id, turns in turn_summaries.items():
        sorted_turns = sorted(turns, key=lambda t: t[0])
        for current, follow_up in zip(sorted_turns, sorted_turns[1:]):
            t0, label0 = current
            t1, label1 = follow_up
            if t1 - t0 > CANCELLATION_WINDOW:
                continue
            if label0 == label1:
                continue
            flagged.add((conv_id, t0))
    return flagged


def row_to_jsonl(row: TrainingRow) -> str:
    return json.dumps(
        {
            "context": row.context,
            "input_window": row.input_window,
            "labels": list(row.labels),
            "_metadata": {
                "weight": row.weight,
                "bucket": row.bucket,
                "needs_teacher_relabel": row.needs_teacher_relabel,
                "cancellation_pattern": row.cancellation_pattern,
                "agent_id": row.agent_id,
                "agent_version_id": row.agent_version_id,
                "step_id": row.step_id,
                "confidence": row.confidence,
                "conversation_id": row.conversation_id,
                "turn_recorded_at": (
                    row.turn_recorded_at.isoformat()
                    if row.turn_recorded_at is not None
                    else None
                ),
            },
        },
        ensure_ascii=False,
    )


def write_training_jsonl(
    rows: Iterable[TrainingRow],
    *,
    output_dir: str | Path,
    agent_id: str,
) -> Path:
    """Write ``data/agents/{agent_id}/raw_traces.jsonl`` per spec §Output."""
    out_dir = Path(output_dir) / "agents" / agent_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "raw_traces.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(row_to_jsonl(row) + "\n")
    return out_path


def load_confusion_pairs(path: str | Path | None) -> set[frozenset[str]] | None:
    """Load the confusion-pair file used for bucket 3 weighting.

    Format: JSON array of two-element arrays — ``[[intent_a, intent_b], ...]``.
    Returns ``None`` when no path is given (bucket 3 stays empty until
    WI-6.8 starts producing confusion data).
    """
    if path is None:
        return None
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    pairs: set[frozenset[str]] = set()
    for entry in raw:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError(f"confusion pair must be [a, b]; got {entry!r}")
        a, b = entry
        if a == b:
            continue
        pairs.add(frozenset({str(a), str(b)}))
    return pairs


# ── DB orchestration ─────────────────────────────────────────────────────────


def export_from_session(
    session: Any,
    *,
    agent_id: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    confusion_pairs: set[frozenset[str]] | None = None,
    now: datetime | None = None,
) -> list[TrainingRow]:
    """Apply filter rules, bucket-assign, and project to TrainingRows.

    ``session`` is a SQLAlchemy session. Imported lazily so the module is
    unit-testable on machines without the full backend stack.
    """
    from sqlalchemy import select

    from ...db_models import (
        AgentRecord,
        AgentVersionRecord,
        ConversationRecord,
        TurnTraceRecord,
    )

    agent = session.get(AgentRecord, agent_id)
    if agent is None or agent.current_published_version_id is None:
        return []
    version_record = session.execute(
        select(AgentVersionRecord).where(
            AgentVersionRecord.version_id == agent.current_published_version_id
        )
    ).scalar_one_or_none()
    if version_record is None:
        return []
    document = AgentDocument.model_validate(version_record.agent_document_json)
    step_index: dict[str, Step] = {step.id: step for step in document.steps}
    if not step_index:
        return []

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=lookback_days)
    conversations = session.execute(
        select(ConversationRecord).where(
            ConversationRecord.agent_id == agent_id,
            ConversationRecord.created_at >= cutoff,
            ConversationRecord.mode == "live",
        )
    ).scalars().all()
    conv_index = {c.conversation_id: c for c in conversations}
    if not conv_index:
        return []

    traces = (
        session.execute(
            select(TurnTraceRecord).where(
                TurnTraceRecord.agent_id == agent_id,
                TurnTraceRecord.conversation_id.in_(list(conv_index.keys())),
                TurnTraceRecord.recorded_at >= cutoff,
            )
        )
        .scalars()
        .all()
    )

    cancellation_flags = _build_cancellation_flags(traces)

    rows: list[TrainingRow] = []
    for trace in traces:
        if trace.step_before not in step_index:
            continue
        if extract_event_type(trace.rules_json) not in USER_MESSAGE_EVENT_TYPES:
            continue
        if extract_degraded_mode(trace.rules_json) == DEGRADED_CLASSIFIER_UNAVAILABLE:
            continue
        classifier = trace.classifier_json or {}
        if classifier.get("chosen_label") is None:
            continue
        user_text = extract_user_text(trace.rules_json)
        if not user_text:
            continue

        conversation = conv_index[trace.conversation_id]
        is_completed = (
            conversation.outcome in COMPLETION_OUTCOMES
            or conversation.status in COMPLETION_OUTCOMES
        )
        cancellation = (trace.conversation_id, trace.recorded_at) in cancellation_flags

        rows.append(
            project_training_row(
                agent_id=trace.agent_id,
                agent_version_id=trace.agent_version_id or "",
                step=step_index[trace.step_before],
                document=document,
                user_text=user_text,
                semantic_events_json=trace.semantic_events_json,
                confidence=classifier.get("confidence"),
                is_completed=is_completed,
                conversation_id=trace.conversation_id,
                turn_recorded_at=trace.recorded_at,
                confusion_pairs=confusion_pairs,
                cancellation_pattern=cancellation,
            )
        )
    return rows


def _build_cancellation_flags(traces: Iterable[Any]) -> set[tuple[str, datetime]]:
    summaries: dict[str, list[tuple[datetime, str | None]]] = {}
    for trace in traces:
        classifier = trace.classifier_json or {}
        summaries.setdefault(trace.conversation_id, []).append(
            (trace.recorded_at, classifier.get("chosen_label"))
        )
    return detect_cancellation_patterns(summaries)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = _run(args)
    out_path = write_training_jsonl(
        rows, output_dir=args.output_dir, agent_id=args.agent_id
    )
    print(_summary(rows, args, out_path))
    return 0 if rows else 2


def _run(args: argparse.Namespace) -> list[TrainingRow]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    confusion_pairs = load_confusion_pairs(args.confusion_pairs)
    engine = create_engine(args.database_url)
    with Session(engine) as session:
        return export_from_session(
            session,
            agent_id=args.agent_id,
            lookback_days=args.lookback_days,
            confusion_pairs=confusion_pairs,
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ruhu.classifier.training.trace_export",
        description="WI-6.1: export turn traces into classifier training JSONL.",
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="Cutoff window per spec §What we include (default: 30).",
    )
    parser.add_argument(
        "--confusion-pairs",
        default=None,
        help=(
            "Optional path to a JSON file: list of [intent_a, intent_b] pairs from "
            "the latest eval (drives bucket 3 weighting). When omitted, bucket 3 "
            "is empty — fill in once WI-6.8 starts producing confusion data."
        ),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Root directory; output goes to {output-dir}/agents/{agent-id}/raw_traces.jsonl.",
    )
    return parser.parse_args(argv)


def _summary(
    rows: list[TrainingRow],
    args: argparse.Namespace,
    out_path: Path,
) -> str:
    by_bucket: dict[str, int] = {}
    teacher_count = 0
    cancellation_count = 0
    for row in rows:
        by_bucket[row.bucket] = by_bucket.get(row.bucket, 0) + 1
        if row.needs_teacher_relabel:
            teacher_count += 1
        if row.cancellation_pattern:
            cancellation_count += 1
    bucket_lines = "  ".join(
        f"{name}={count}" for name, count in sorted(by_bucket.items())
    )
    return (
        f"trace_export wrote {len(rows)} rows to {out_path}\n"
        f"  agent={args.agent_id}  lookback={args.lookback_days}d\n"
        f"  buckets: {bucket_lines or 'none'}\n"
        f"  teacher_relabel_queue={teacher_count}  cancellation_pattern={cancellation_count}"
    )


if __name__ == "__main__":
    sys.exit(main())
