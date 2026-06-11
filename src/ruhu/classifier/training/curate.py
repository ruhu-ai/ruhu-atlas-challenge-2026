"""WI-6.3 — curation gates for the LoRA training pipeline.

Reads ``teacher_labeled.jsonl`` from WI-6.2, applies the classifier
quality gates, and writes the train / val splits ready for
``train_lora.py`` (WI-6.4).

Spec source: ``docs/pre-fill-intent-classifier-design/05-training-pipeline.md``
§Curation.

The three gates:

1. **Label consistency for near-duplicates.** For every pair of rows
   whose ``input_window`` cosine similarity exceeds ``--cosine-threshold``
   (default 0.95), assert the labels agree. Conflicts are dropped and
   surfaced in the ``CurationReport`` for human review.

2. **Train/val leakage scan.** Split rows into train / val by
   ``conversation_id`` — every turn in a given conversation lands in
   the same split, so the model never sees a held-out turn from a
   conversation it was trained on.

3. **Confusion-pair oversampling.** For rows whose label is in any
   configured confusion pair (from the latest eval), replicate the row
   ``--oversample-factor`` times in the train set. Hard negatives and
   frequently-confused pairs are more useful than raw volume.

The pairwise comparison in gate 1 is O(N²). For per-agent eval sets
(typically a few thousand rows) that's fine; for larger corpora a
follow-up should bucket-prefilter or use approximate nearest neighbors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ...knowledge.embeddings import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    cosine_similarity,
)


DEFAULT_VAL_SPLIT = 0.2
DEFAULT_COSINE_THRESHOLD = 0.95
DEFAULT_OVERSAMPLE_FACTOR = 3


# ── public dataclasses ──────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class LabelConsistencyConflict:
    """Two near-duplicate rows that disagree on label."""

    text_a: str
    text_b: str
    label_a: str
    label_b: str
    cosine: float
    conversation_id_a: str = ""
    conversation_id_b: str = ""


@dataclass(slots=True)
class CurationReport:
    """Per-run statistics — written next to the JSONL output."""

    rows_in: int = 0
    rows_dropped_for_label_conflict: int = 0
    rows_dropped_for_empty_text: int = 0
    label_conflicts: list[LabelConsistencyConflict] = field(default_factory=list)
    train_count: int = 0
    val_count: int = 0
    train_rows_oversampled: int = 0
    train_unique_conversations: int = 0
    val_unique_conversations: int = 0


# ── gate 1: label consistency ──────────────────────────────────────────────


def label_consistency_filter(
    rows: list[dict],
    *,
    embedding_provider: EmbeddingProvider,
    threshold: float = DEFAULT_COSINE_THRESHOLD,
) -> tuple[list[dict], list[LabelConsistencyConflict]]:
    """Drop rows that conflict with a near-duplicate (cosine >= threshold).

    Returns ``(kept_rows, conflicts)``. When two rows conflict, *both*
    are dropped — we don't know which label is correct without human
    review.
    """
    if not rows:
        return [], []
    texts = [str(row.get("input_window") or "").strip() for row in rows]
    vectors = embedding_provider.embed_documents(texts)

    conflicts: list[LabelConsistencyConflict] = []
    drop: set[int] = set()
    for i in range(len(rows)):
        if i in drop:
            continue
        for j in range(i + 1, len(rows)):
            if j in drop:
                continue
            similarity = cosine_similarity(vectors[i], vectors[j])
            if similarity < threshold:
                continue
            label_key_i = _label_key(rows[i])
            label_key_j = _label_key(rows[j])
            if label_key_i == label_key_j:
                continue
            conflicts.append(
                LabelConsistencyConflict(
                    text_a=texts[i],
                    text_b=texts[j],
                    label_a=label_key_i,
                    label_b=label_key_j,
                    cosine=similarity,
                    conversation_id_a=_conversation_id(rows[i]),
                    conversation_id_b=_conversation_id(rows[j]),
                )
            )
            drop.add(i)
            drop.add(j)
            break  # row i is gone; move on
    kept = [row for idx, row in enumerate(rows) if idx not in drop]
    return kept, conflicts


def _label_key(row: dict) -> str:
    """Deterministic comparable key for a row's label set.

    Empty labels collapse to ``unknown`` so two near-duplicate
    "teacher said unknown" rows are treated as consistent.
    """
    labels = row.get("labels") or []
    if not labels:
        return "unknown"
    return "|".join(sorted(str(label) for label in labels))


def _conversation_id(row: dict) -> str:
    return str((row.get("_metadata") or {}).get("conversation_id") or "")


# ── gate 2: train/val leakage scan ─────────────────────────────────────────


def split_train_val(
    rows: list[dict],
    *,
    val_split: float = DEFAULT_VAL_SPLIT,
    seed: int = 42,
) -> tuple[list[dict], list[dict]]:
    """Split by ``conversation_id`` so no conversation crosses splits.

    Deterministic by seed: same ``(rows, val_split, seed)`` always
    produces the same partition. Conversations with empty
    ``conversation_id`` are bucketed into a single synthetic group
    keyed on the row's index, so they're never confused with each
    other but stay in the same split.
    """
    if not 0.0 <= val_split <= 1.0:
        raise ValueError("val_split must be in [0, 1]")
    if not rows:
        return [], []

    by_conversation: dict[str, list[dict]] = {}
    for idx, row in enumerate(rows):
        conv_id = _conversation_id(row) or f"__synthetic_{idx}__"
        by_conversation.setdefault(conv_id, []).append(row)

    seed_bytes = str(seed).encode("utf-8")
    val_conversations: set[str] = set()
    for conv_id in sorted(by_conversation):
        digest = hashlib.sha256(seed_bytes + conv_id.encode("utf-8")).digest()
        # Use the first 8 bytes as a uniform float in [0, 1)
        bucket = int.from_bytes(digest[:8], "big") / 2**64
        if bucket < val_split:
            val_conversations.add(conv_id)

    train_rows: list[dict] = []
    val_rows: list[dict] = []
    for conv_id, conv_rows in by_conversation.items():
        target = val_rows if conv_id in val_conversations else train_rows
        target.extend(conv_rows)
    return train_rows, val_rows


def assert_no_conversation_leakage(train: list[dict], val: list[dict]) -> None:
    """Raise ``ValueError`` if any ``conversation_id`` appears in both splits."""
    train_ids = {_conversation_id(row) for row in train if _conversation_id(row)}
    val_ids = {_conversation_id(row) for row in val if _conversation_id(row)}
    overlap = train_ids & val_ids
    if overlap:
        raise ValueError(
            f"train/val leakage: {len(overlap)} conversation(s) in both splits "
            f"(first: {next(iter(sorted(overlap)))!r})"
        )


# ── gate 3: confusion-pair oversampling ────────────────────────────────────


def oversample_confusion_pairs(
    rows: list[dict],
    *,
    pairs: set[frozenset[str]] | None,
    factor: int = DEFAULT_OVERSAMPLE_FACTOR,
) -> tuple[list[dict], int]:
    """Replicate rows whose label appears in any confusion pair.

    A row's label is "in a pair" when any element of ``row['labels']``
    appears in any element of ``pairs``. Replication factor of 3 means
    the row appears 3 times total (one original + 2 copies). Returns
    ``(rows_with_oversampling, n_replicas_added)``.
    """
    if not pairs or factor <= 1 or not rows:
        return list(rows), 0
    flat: set[str] = set()
    for pair in pairs:
        flat.update(pair)
    out: list[dict] = []
    added = 0
    for row in rows:
        out.append(row)
        labels = row.get("labels") or []
        if any(str(label) in flat for label in labels):
            for _ in range(factor - 1):
                out.append(dict(row))
                added += 1
    return out, added


# ── orchestrator ───────────────────────────────────────────────────────────


def curate(
    rows: list[dict],
    *,
    embedding_provider: EmbeddingProvider,
    confusion_pairs: set[frozenset[str]] | None = None,
    val_split: float = DEFAULT_VAL_SPLIT,
    cosine_threshold: float = DEFAULT_COSINE_THRESHOLD,
    oversample_factor: int = DEFAULT_OVERSAMPLE_FACTOR,
    seed: int = 42,
) -> tuple[list[dict], list[dict], CurationReport]:
    """Apply all three gates and return ``(train, val, report)``."""
    report = CurationReport(rows_in=len(rows))

    # Drop rows with empty input_window outright — they can't be embedded
    # nor used for training.
    cleaned: list[dict] = []
    for row in rows:
        if str(row.get("input_window") or "").strip():
            cleaned.append(row)
        else:
            report.rows_dropped_for_empty_text += 1

    kept, conflicts = label_consistency_filter(
        cleaned, embedding_provider=embedding_provider, threshold=cosine_threshold
    )
    report.label_conflicts = conflicts
    report.rows_dropped_for_label_conflict = len(cleaned) - len(kept)

    train_raw, val_rows = split_train_val(kept, val_split=val_split, seed=seed)
    assert_no_conversation_leakage(train_raw, val_rows)

    train_rows, oversampled = oversample_confusion_pairs(
        train_raw, pairs=confusion_pairs, factor=oversample_factor
    )

    report.train_count = len(train_rows)
    report.val_count = len(val_rows)
    report.train_rows_oversampled = oversampled
    report.train_unique_conversations = len(
        {_conversation_id(row) for row in train_raw if _conversation_id(row)}
    )
    report.val_unique_conversations = len(
        {_conversation_id(row) for row in val_rows if _conversation_id(row)}
    )

    return train_rows, val_rows, report


# ── JSONL i/o ──────────────────────────────────────────────────────────────


def read_teacher_labeled(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"teacher_labeled.jsonl line {line_no}: invalid JSON: {exc}"
                ) from exc
    return rows


def write_split(rows: Iterable[dict], path: str | Path) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with p.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    return written


def write_curation_report(report: CurationReport, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "rows_in": report.rows_in,
        "rows_dropped_for_label_conflict": report.rows_dropped_for_label_conflict,
        "rows_dropped_for_empty_text": report.rows_dropped_for_empty_text,
        "train_count": report.train_count,
        "val_count": report.val_count,
        "train_rows_oversampled": report.train_rows_oversampled,
        "train_unique_conversations": report.train_unique_conversations,
        "val_unique_conversations": report.val_unique_conversations,
        "label_conflicts": [
            {
                "text_a": conflict.text_a,
                "text_b": conflict.text_b,
                "label_a": conflict.label_a,
                "label_b": conflict.label_b,
                "cosine": conflict.cosine,
                "conversation_id_a": conflict.conversation_id_a,
                "conversation_id_b": conflict.conversation_id_b,
            }
            for conflict in report.label_conflicts
        ],
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_confusion_pairs(path: str | Path | None) -> set[frozenset[str]] | None:
    """Mirror ``trace_export.load_confusion_pairs`` shape."""
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


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = read_teacher_labeled(args.input)
    provider = _build_embedding_provider(args)
    confusion_pairs = load_confusion_pairs(args.confusion_pairs)

    train, val, report = curate(
        rows,
        embedding_provider=provider,
        confusion_pairs=confusion_pairs,
        val_split=args.val_split,
        cosine_threshold=args.cosine_threshold,
        oversample_factor=args.oversample_factor,
        seed=args.seed,
    )

    out_dir = Path(args.output_dir) / "agents" / args.agent_id
    train_path = write_split(train, out_dir / "train.jsonl") and (out_dir / "train.jsonl")
    val_path = write_split(val, out_dir / "val.jsonl") and (out_dir / "val.jsonl")
    report_path = write_curation_report(report, out_dir / "curation_report.json")
    print(_summary(report, train_path, val_path, report_path))
    return 0 if report.train_count > 0 or report.val_count > 0 else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ruhu.classifier.training.curate",
        description="WI-6.3: apply classifier quality gates to teacher_labeled.jsonl.",
    )
    parser.add_argument("--input", required=True, help="Path to teacher_labeled.jsonl")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Root dir; output goes to {output-dir}/agents/{agent-id}/{train,val}.jsonl",
    )
    parser.add_argument("--val-split", type=float, default=DEFAULT_VAL_SPLIT)
    parser.add_argument("--cosine-threshold", type=float, default=DEFAULT_COSINE_THRESHOLD)
    parser.add_argument("--oversample-factor", type=int, default=DEFAULT_OVERSAMPLE_FACTOR)
    parser.add_argument(
        "--confusion-pairs",
        default=None,
        help="Optional JSON file of [intent_a, intent_b] pairs from the latest eval.",
    )
    parser.add_argument(
        "--embedding-provider",
        choices=("hashing",),
        default="hashing",
        help=(
            "Embedding backend for the label-consistency check. Only the local "
            "deterministic 'hashing' provider is wired now; vertex / hosted "
            "providers slot in via the Protocol when needed for production."
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


def _build_embedding_provider(args: argparse.Namespace) -> EmbeddingProvider:
    if args.embedding_provider == "hashing":
        return HashingEmbeddingProvider()
    raise SystemExit(f"unsupported embedding provider: {args.embedding_provider}")


def _summary(
    report: CurationReport,
    train_path: Path | bool,
    val_path: Path | bool,
    report_path: Path,
) -> str:
    return (
        f"curate finished\n"
        f"  in={report.rows_in}  "
        f"dropped_empty={report.rows_dropped_for_empty_text}  "
        f"dropped_label_conflict={report.rows_dropped_for_label_conflict}\n"
        f"  train={report.train_count} ({report.train_unique_conversations} convs, "
        f"oversampled +{report.train_rows_oversampled})\n"
        f"  val={report.val_count} ({report.val_unique_conversations} convs)\n"
        f"  output: {train_path}, {val_path}\n"
        f"  report: {report_path}"
    )


if __name__ == "__main__":
    sys.exit(main())
