"""Tests for src/ruhu/classifier/training/curate.py — WI-6.3."""
from __future__ import annotations

import json
from typing import Sequence

import pytest

from ruhu.classifier.training.curate import (
    CurationReport,
    LabelConsistencyConflict,
    assert_no_conversation_leakage,
    curate,
    label_consistency_filter,
    load_confusion_pairs,
    main as cli_main,
    oversample_confusion_pairs,
    read_teacher_labeled,
    split_train_val,
    write_curation_report,
    write_split,
)
from ruhu.knowledge.embeddings import HashingEmbeddingProvider


def _row(
    *,
    user_text: str,
    labels: list[str],
    conversation_id: str = "conv-1",
    bucket: str = "low_conf",
) -> dict:
    return {
        "context": "PREFIX",
        "input_window": f"User message: {user_text}\nIntent:",
        "labels": list(labels),
        "teacher_confidence": 0.9,
        "_metadata": {
            "bucket": bucket,
            "conversation_id": conversation_id,
            "agent_id": "a1",
            "agent_version_id": "v1",
            "step_id": "entry",
        },
    }


class _ScriptedEmbeddingProvider:
    """Returns vectors from a {text -> vector} dict.

    Lets tests assert exact cosine outcomes without depending on the
    HashingEmbeddingProvider's behaviour for specific phrases.
    """

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    @property
    def model_key(self) -> str:
        return "scripted"

    @property
    def dimensions(self) -> int | None:
        return next(iter(self._vectors.values()), [0]).__len__() if self._vectors else None

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vectors[text]

    def close(self) -> None:
        return None


# ── label_consistency_filter ───────────────────────────────────────────────


def test_label_consistency_filter_drops_both_rows_when_near_duplicates_disagree() -> None:
    rows = [
        _row(user_text="A", labels=["transfer_status"], conversation_id="c1"),
        _row(user_text="B", labels=["kyc_help"], conversation_id="c2"),
        _row(user_text="C", labels=["card_freeze"], conversation_id="c3"),
    ]
    # A and B are near-duplicates with conflicting labels; C is orthogonal.
    provider = _ScriptedEmbeddingProvider(
        {
            rows[0]["input_window"]: [1.0, 0.0],
            rows[1]["input_window"]: [1.0, 0.0],  # cosine 1.0 with A
            rows[2]["input_window"]: [0.0, 1.0],
        }
    )
    kept, conflicts = label_consistency_filter(rows, embedding_provider=provider)
    assert len(kept) == 1
    assert kept[0]["labels"] == ["card_freeze"]
    assert len(conflicts) == 1
    assert conflicts[0].label_a == "transfer_status"
    assert conflicts[0].label_b == "kyc_help"
    assert conflicts[0].cosine == pytest.approx(1.0)


def test_label_consistency_filter_keeps_near_duplicates_with_matching_labels() -> None:
    rows = [
        _row(user_text="hi 1", labels=["transfer_status"]),
        _row(user_text="hi 2", labels=["transfer_status"]),
    ]
    provider = _ScriptedEmbeddingProvider(
        {
            rows[0]["input_window"]: [1.0, 0.0],
            rows[1]["input_window"]: [1.0, 0.0],
        }
    )
    kept, conflicts = label_consistency_filter(rows, embedding_provider=provider)
    assert len(kept) == 2
    assert conflicts == []


def test_label_consistency_filter_treats_empty_labels_as_unknown() -> None:
    """Two near-duplicate rows with empty labels are consistent (both 'unknown')."""
    rows = [
        _row(user_text="x1", labels=[]),
        _row(user_text="x2", labels=[]),
    ]
    provider = _ScriptedEmbeddingProvider(
        {row["input_window"]: [1.0, 0.0] for row in rows}
    )
    kept, conflicts = label_consistency_filter(rows, embedding_provider=provider)
    assert len(kept) == 2
    assert conflicts == []


def test_label_consistency_filter_flags_empty_vs_labeled_as_conflict() -> None:
    """Empty labels (= 'unknown') vs a real label is a conflict."""
    rows = [
        _row(user_text="x1", labels=[]),
        _row(user_text="x2", labels=["transfer_status"]),
    ]
    provider = _ScriptedEmbeddingProvider(
        {row["input_window"]: [1.0, 0.0] for row in rows}
    )
    kept, conflicts = label_consistency_filter(rows, embedding_provider=provider)
    assert kept == []
    assert len(conflicts) == 1
    assert {conflicts[0].label_a, conflicts[0].label_b} == {"unknown", "transfer_status"}


def test_label_consistency_filter_respects_threshold() -> None:
    """Below-threshold pairs are not compared, even if labels disagree."""
    rows = [
        _row(user_text="A", labels=["transfer_status"]),
        _row(user_text="B", labels=["kyc_help"]),
    ]
    provider = _ScriptedEmbeddingProvider(
        {
            rows[0]["input_window"]: [1.0, 0.0],
            rows[1]["input_window"]: [0.0, 1.0],  # cosine 0
        }
    )
    kept, conflicts = label_consistency_filter(
        rows, embedding_provider=provider, threshold=0.5
    )
    assert len(kept) == 2
    assert conflicts == []


def test_label_consistency_filter_handles_empty_input() -> None:
    provider = _ScriptedEmbeddingProvider({})
    kept, conflicts = label_consistency_filter([], embedding_provider=provider)
    assert kept == []
    assert conflicts == []


def test_label_consistency_filter_against_hashing_provider_passes_smoke() -> None:
    """Sanity: identical input_window text under the real HashingEmbeddingProvider
    produces cosine=1.0 and a conflict when labels disagree."""
    rows = [
        _row(user_text="where is my money", labels=["transfer_status"]),
        _row(user_text="where is my money", labels=["kyc_help"]),
    ]
    kept, conflicts = label_consistency_filter(
        rows, embedding_provider=HashingEmbeddingProvider()
    )
    assert kept == []
    assert len(conflicts) == 1


# ── split_train_val ────────────────────────────────────────────────────────


def test_split_train_val_keeps_conversations_intact() -> None:
    rows = []
    for conv_id in ("conv-A", "conv-B", "conv-C"):
        rows.extend(
            _row(user_text=f"{conv_id}-{i}", labels=["x"], conversation_id=conv_id)
            for i in range(5)
        )
    train, val = split_train_val(rows, val_split=0.34, seed=1)
    train_convs = {row["_metadata"]["conversation_id"] for row in train}
    val_convs = {row["_metadata"]["conversation_id"] for row in val}
    assert train_convs.isdisjoint(val_convs)
    # Every original conversation lands in exactly one split.
    assert train_convs | val_convs == {"conv-A", "conv-B", "conv-C"}


def test_split_train_val_is_deterministic_for_same_seed() -> None:
    rows = [
        _row(user_text=str(i), labels=["x"], conversation_id=f"c{i}")
        for i in range(50)
    ]
    a_train, a_val = split_train_val(rows, val_split=0.3, seed=42)
    b_train, b_val = split_train_val(rows, val_split=0.3, seed=42)
    assert [r["input_window"] for r in a_train] == [r["input_window"] for r in b_train]
    assert [r["input_window"] for r in a_val] == [r["input_window"] for r in b_val]


def test_split_train_val_different_seeds_pick_different_partitions() -> None:
    rows = [
        _row(user_text=str(i), labels=["x"], conversation_id=f"c{i}")
        for i in range(100)
    ]
    a_train, _ = split_train_val(rows, val_split=0.2, seed=1)
    b_train, _ = split_train_val(rows, val_split=0.2, seed=2)
    assert {r["_metadata"]["conversation_id"] for r in a_train} != {
        r["_metadata"]["conversation_id"] for r in b_train
    }


def test_split_train_val_zero_split_puts_everything_in_train() -> None:
    rows = [_row(user_text=str(i), labels=["x"], conversation_id=f"c{i}") for i in range(20)]
    train, val = split_train_val(rows, val_split=0.0, seed=0)
    assert len(train) == 20
    assert val == []


def test_split_train_val_full_split_puts_everything_in_val() -> None:
    rows = [_row(user_text=str(i), labels=["x"], conversation_id=f"c{i}") for i in range(20)]
    train, val = split_train_val(rows, val_split=1.0, seed=0)
    assert train == []
    assert len(val) == 20


def test_split_train_val_validates_split_range() -> None:
    with pytest.raises(ValueError):
        split_train_val([], val_split=1.5)


def test_split_train_val_groups_unknown_conversation_ids_individually() -> None:
    """Rows with empty conversation_id get unique synthetic ids — no leakage."""
    rows = [
        _row(user_text=str(i), labels=["x"], conversation_id="")
        for i in range(20)
    ]
    train, val = split_train_val(rows, val_split=0.5, seed=0)
    # All rows split, no leakage to assert (empty conv_ids don't count)
    assert_no_conversation_leakage(train, val)
    assert len(train) + len(val) == 20


def test_assert_no_conversation_leakage_raises_on_overlap() -> None:
    train = [_row(user_text="t", labels=["x"], conversation_id="shared")]
    val = [_row(user_text="v", labels=["x"], conversation_id="shared")]
    with pytest.raises(ValueError, match="leakage"):
        assert_no_conversation_leakage(train, val)


def test_assert_no_conversation_leakage_passes_on_disjoint() -> None:
    train = [_row(user_text="t", labels=["x"], conversation_id="t1")]
    val = [_row(user_text="v", labels=["x"], conversation_id="v1")]
    assert_no_conversation_leakage(train, val)  # no raise


# ── oversample_confusion_pairs ─────────────────────────────────────────────


def test_oversample_replicates_rows_in_pair() -> None:
    rows = [
        _row(user_text="a", labels=["transfer_status"]),
        _row(user_text="b", labels=["unrelated"]),
        _row(user_text="c", labels=["kyc_help"]),
    ]
    pairs = {frozenset({"transfer_status", "kyc_help"})}
    out, added = oversample_confusion_pairs(rows, pairs=pairs, factor=3)
    assert added == 4  # rows a and c each get 2 extra copies
    assert len(out) == 3 + 4
    # Exactly one occurrence of the unrelated row
    unrelated = [row for row in out if row["labels"] == ["unrelated"]]
    assert len(unrelated) == 1
    # Three occurrences of transfer_status
    transfer = [row for row in out if "transfer_status" in row["labels"]]
    assert len(transfer) == 3


def test_oversample_no_pairs_passes_through_unchanged() -> None:
    rows = [_row(user_text="a", labels=["transfer_status"])]
    out, added = oversample_confusion_pairs(rows, pairs=None, factor=3)
    assert added == 0
    assert out == rows


def test_oversample_factor_one_is_a_no_op() -> None:
    rows = [_row(user_text="a", labels=["transfer_status"])]
    pairs = {frozenset({"transfer_status", "kyc_help"})}
    out, added = oversample_confusion_pairs(rows, pairs=pairs, factor=1)
    assert added == 0
    assert len(out) == 1


def test_oversample_skips_rows_with_no_labels() -> None:
    rows = [_row(user_text="a", labels=[])]
    pairs = {frozenset({"transfer_status", "kyc_help"})}
    out, added = oversample_confusion_pairs(rows, pairs=pairs, factor=3)
    assert added == 0
    assert out == rows


def test_oversample_handles_empty_rows() -> None:
    out, added = oversample_confusion_pairs([], pairs={frozenset({"a", "b"})}, factor=3)
    assert out == []
    assert added == 0


# ── orchestrator ───────────────────────────────────────────────────────────


def test_curate_end_to_end_drops_conflicts_splits_and_oversamples() -> None:
    rows = []
    # 8 conflict-free conversations across two labels — should split cleanly
    for i in range(8):
        rows.append(
            _row(
                user_text=f"transfer-{i}",
                labels=["transfer_status"],
                conversation_id=f"transfer-conv-{i}",
            )
        )
    for i in range(8):
        rows.append(
            _row(
                user_text=f"kyc-{i}",
                labels=["kyc_help"],
                conversation_id=f"kyc-conv-{i}",
            )
        )
    # One conflicting near-duplicate pair (cosine forced to 1.0 via scripted provider)
    conflict_rows = [
        _row(
            user_text="conflict-A",
            labels=["transfer_status"],
            conversation_id="conflict-A",
        ),
        _row(
            user_text="conflict-B",
            labels=["card_freeze"],
            conversation_id="conflict-B",
        ),
    ]
    rows.extend(conflict_rows)

    vectors: dict[str, list[float]] = {}
    for row in rows:
        text = row["input_window"]
        labels = row["labels"]
        # Default basis vector based on label so unrelated rows have cosine 0
        if labels == ["transfer_status"]:
            vectors[text] = [1.0, 0.0, 0.0, 0.0]
        elif labels == ["kyc_help"]:
            vectors[text] = [0.0, 1.0, 0.0, 0.0]
        else:
            vectors[text] = [0.0, 0.0, 1.0, 0.0]
    # Force the conflict pair to be near-duplicates (cosine 1.0) on a fourth axis
    vectors[conflict_rows[0]["input_window"]] = [0.0, 0.0, 0.0, 1.0]
    vectors[conflict_rows[1]["input_window"]] = [0.0, 0.0, 0.0, 1.0]
    provider = _ScriptedEmbeddingProvider(vectors)

    pairs = {frozenset({"transfer_status", "kyc_help"})}
    train, val, report = curate(
        rows,
        embedding_provider=provider,
        confusion_pairs=pairs,
        val_split=0.25,
        seed=7,
    )

    assert report.rows_in == len(rows)
    assert report.rows_dropped_for_label_conflict == 2
    assert len(report.label_conflicts) == 1

    train_convs = {row["_metadata"]["conversation_id"] for row in train}
    val_convs = {row["_metadata"]["conversation_id"] for row in val}
    assert train_convs.isdisjoint(val_convs)

    # Every train row has a label in the confusion pair → oversampled 3×
    transfer_or_kyc_train = [
        row
        for row in train
        if any(label in {"transfer_status", "kyc_help"} for label in row["labels"])
    ]
    assert report.train_rows_oversampled > 0
    assert len(transfer_or_kyc_train) > 0


def test_curate_drops_rows_with_empty_input_window() -> None:
    rows = [
        _row(user_text="ok", labels=["x"]),
        {"input_window": "", "labels": ["x"], "_metadata": {"conversation_id": "c"}},
    ]
    train, val, report = curate(
        rows, embedding_provider=HashingEmbeddingProvider(), val_split=0.0
    )
    assert report.rows_dropped_for_empty_text == 1
    assert report.train_count + report.val_count == 1


def test_curate_no_confusion_pairs_skips_oversampling() -> None:
    rows = [
        _row(
            user_text=f"text-{i}",
            labels=["transfer_status"],
            conversation_id=f"c{i}",
        )
        for i in range(10)
    ]
    train, val, report = curate(
        rows,
        embedding_provider=HashingEmbeddingProvider(),
        confusion_pairs=None,
        val_split=0.2,
        seed=0,
    )
    assert report.train_rows_oversampled == 0
    assert len(train) + len(val) == 10


# ── load_confusion_pairs ──────────────────────────────────────────────────


def test_load_confusion_pairs_returns_none_for_no_path() -> None:
    assert load_confusion_pairs(None) is None


def test_load_confusion_pairs_parses(tmp_path) -> None:
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([["a", "b"], ["c", "d"]]))
    pairs = load_confusion_pairs(p)
    assert pairs == {frozenset({"a", "b"}), frozenset({"c", "d"})}


def test_load_confusion_pairs_rejects_malformed(tmp_path) -> None:
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([["a"]]))
    with pytest.raises(ValueError):
        load_confusion_pairs(p)


# ── JSONL i/o ──────────────────────────────────────────────────────────────


def test_read_teacher_labeled_skips_blank_lines(tmp_path) -> None:
    p = tmp_path / "in.jsonl"
    p.write_text(
        "\n"
        + json.dumps({"input_window": "x", "labels": [], "_metadata": {}})
        + "\n\n"
    )
    assert len(read_teacher_labeled(p)) == 1


def test_read_teacher_labeled_invalid_json_raises(tmp_path) -> None:
    p = tmp_path / "in.jsonl"
    p.write_text("not json\n")
    with pytest.raises(ValueError, match="invalid JSON"):
        read_teacher_labeled(p)


def test_write_split_creates_parent_dir_and_returns_count(tmp_path) -> None:
    target = tmp_path / "nested" / "train.jsonl"
    written = write_split([{"a": 1}, {"a": 2}], target)
    assert written == 2
    assert target.exists()


def test_write_curation_report_serialises_conflicts(tmp_path) -> None:
    report = CurationReport(
        rows_in=5,
        rows_dropped_for_label_conflict=2,
        rows_dropped_for_empty_text=0,
        label_conflicts=[
            LabelConsistencyConflict(
                text_a="A",
                text_b="B",
                label_a="x",
                label_b="y",
                cosine=0.97,
                conversation_id_a="ca",
                conversation_id_b="cb",
            )
        ],
        train_count=2,
        val_count=1,
        train_rows_oversampled=0,
        train_unique_conversations=2,
        val_unique_conversations=1,
    )
    out_path = write_curation_report(report, tmp_path / "report.json")
    payload = json.loads(out_path.read_text())
    assert payload["rows_in"] == 5
    assert payload["label_conflicts"][0]["cosine"] == 0.97
    assert payload["label_conflicts"][0]["conversation_id_a"] == "ca"


# ── CLI ────────────────────────────────────────────────────────────────────


def test_cli_main_writes_train_val_and_report(tmp_path) -> None:
    input_path = tmp_path / "teacher_labeled.jsonl"
    rows = [
        _row(
            user_text=f"text-{i}",
            labels=["transfer_status"],
            conversation_id=f"c{i}",
        )
        for i in range(10)
    ]
    input_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )

    rc = cli_main(
        [
            "--input", str(input_path),
            "--agent-id", "agent_a",
            "--output-dir", str(tmp_path / "out"),
            "--val-split", "0.3",
            "--seed", "7",
        ]
    )
    assert rc == 0
    train_path = tmp_path / "out" / "agents" / "agent_a" / "train.jsonl"
    val_path = tmp_path / "out" / "agents" / "agent_a" / "val.jsonl"
    report_path = tmp_path / "out" / "agents" / "agent_a" / "curation_report.json"
    assert train_path.exists()
    assert val_path.exists()
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["rows_in"] == 10
    assert report["train_count"] + report["val_count"] == 10


def test_cli_main_returns_2_when_no_rows_survive(tmp_path) -> None:
    """Exit non-zero when curation drops every row — surfaces in CI."""
    input_path = tmp_path / "teacher_labeled.jsonl"
    input_path.write_text("", encoding="utf-8")
    rc = cli_main(
        [
            "--input", str(input_path),
            "--agent-id", "agent_a",
            "--output-dir", str(tmp_path / "out"),
        ]
    )
    assert rc == 2
