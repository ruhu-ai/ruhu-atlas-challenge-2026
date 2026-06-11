"""Unit tests for the attachment-aware condition kinds.

Exercises the kernel's evaluation of ``attachment_present`` and
``view_ready`` transitions per the kernel-behavior note.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ruhu.attachments.models import AttachmentRef
from ruhu.kernel import ConversationKernel
from ruhu.schemas import AttachmentPresentCondition, Condition, RuntimeTurn, ViewReadyCondition


def _make_kernel() -> ConversationKernel:
    """Minimal kernel for condition-evaluation tests.

    We only exercise ``_condition_matches`` and its helpers, which don't
    touch stores.  Pass None for the required deps; they are unused.
    """
    return ConversationKernel.__new__(ConversationKernel)


def _make_turn(
    *,
    event_type: str = "user_message",
    attachments: list[AttachmentRef] | None = None,
    metadata: dict | None = None,
) -> RuntimeTurn:
    return RuntimeTurn(
        turn_id="t1",
        dedupe_key="t1",
        channel="web_widget",
        modality="text",
        event_type=event_type,  # type: ignore[arg-type]
        text="hi",
        attachments=attachments or [],
        metadata=metadata or {},
        received_at=datetime.now(timezone.utc),
    )


def _make_ref(kind: str = "pdf") -> AttachmentRef:
    return AttachmentRef(
        attachment_id=f"att_{kind}",
        kind=kind,  # type: ignore[arg-type]
        source="widget",
        filename=f"f.{kind}",
        content_type="application/octet-stream",
        trust_tier="anonymous",
    )


# ── attachment_present ───────────────────────────────────────────────────────


def test_attachment_present_fires_for_any_attachment_when_unfiltered() -> None:
    kernel = _make_kernel()
    cond = AttachmentPresentCondition()
    turn = _make_turn(attachments=[_make_ref("pdf")])
    assert kernel._condition_matches(cond, set(), {}, turn) is True


def test_attachment_present_does_not_fire_with_empty_attachments() -> None:
    kernel = _make_kernel()
    cond = AttachmentPresentCondition()
    turn = _make_turn(attachments=[])
    assert kernel._condition_matches(cond, set(), {}, turn) is False


def test_attachment_present_does_not_fire_without_turn() -> None:
    kernel = _make_kernel()
    cond = AttachmentPresentCondition()
    assert kernel._condition_matches(cond, set(), {}, None) is False


def test_attachment_present_any_of_kinds_or_semantics() -> None:
    kernel = _make_kernel()
    cond = AttachmentPresentCondition(any_of_kinds=["pdf", "docx"])
    assert kernel._condition_matches(cond, set(), {}, _make_turn(attachments=[_make_ref("pdf")])) is True
    assert kernel._condition_matches(cond, set(), {}, _make_turn(attachments=[_make_ref("image")])) is False
    # Mixed batch where at least one matches.
    assert (
        kernel._condition_matches(
            cond, set(), {}, _make_turn(attachments=[_make_ref("image"), _make_ref("docx")])
        )
        is True
    )


def test_attachment_present_all_of_kinds_and_semantics() -> None:
    kernel = _make_kernel()
    cond = AttachmentPresentCondition(all_of_kinds=["pdf", "image"])
    # Only pdf → doesn't cover image.
    assert kernel._condition_matches(cond, set(), {}, _make_turn(attachments=[_make_ref("pdf")])) is False
    # Both kinds present.
    assert (
        kernel._condition_matches(
            cond, set(), {}, _make_turn(attachments=[_make_ref("pdf"), _make_ref("image")])
        )
        is True
    )
    # Extra kind present alongside required ones still matches.
    assert (
        kernel._condition_matches(
            cond,
            set(),
            {},
            _make_turn(attachments=[_make_ref("pdf"), _make_ref("image"), _make_ref("audio")]),
        )
        is True
    )


def test_attachment_present_combined_filters_both_must_match() -> None:
    kernel = _make_kernel()
    cond = AttachmentPresentCondition(
        any_of_kinds=["pdf", "docx"],
        all_of_kinds=["image"],
    )
    # any_of matches (pdf) but all_of misses (no image).
    assert kernel._condition_matches(cond, set(), {}, _make_turn(attachments=[_make_ref("pdf")])) is False
    # Both match.
    assert (
        kernel._condition_matches(
            cond, set(), {}, _make_turn(attachments=[_make_ref("pdf"), _make_ref("image")])
        )
        is True
    )


def test_attachment_present_ignores_historical_attachments() -> None:
    """Only current-turn attachments count (matching-scope rule)."""
    kernel = _make_kernel()
    cond = AttachmentPresentCondition()
    # A text-only turn with empty attachments — prior turn uploads are not
    # part of this RuntimeTurn.
    turn = _make_turn(attachments=[])
    assert kernel._condition_matches(cond, set(), {}, turn) is False


# ── view_ready ───────────────────────────────────────────────────────────────


def test_view_ready_fires_on_matching_system_event() -> None:
    kernel = _make_kernel()
    cond = ViewReadyCondition(view_kind="text")
    turn = _make_turn(
        event_type="system_event",
        attachments=[_make_ref("pdf")],
        metadata={
            "system_event_kind": "view_ready",
            "view_kind": "text",
            "attachment_id": "att_pdf",
        },
    )
    assert kernel._condition_matches(cond, set(), {}, turn) is True


def test_view_ready_does_not_fire_on_non_system_event() -> None:
    kernel = _make_kernel()
    cond = ViewReadyCondition(view_kind="text")
    turn = _make_turn(
        event_type="user_message",  # wrong event type
        metadata={"system_event_kind": "view_ready", "view_kind": "text"},
    )
    assert kernel._condition_matches(cond, set(), {}, turn) is False


def test_view_ready_requires_matching_view_kind() -> None:
    kernel = _make_kernel()
    cond = ViewReadyCondition(view_kind="text")
    turn = _make_turn(
        event_type="system_event",
        metadata={"system_event_kind": "view_ready", "view_kind": "vision"},
    )
    assert kernel._condition_matches(cond, set(), {}, turn) is False


def test_view_ready_requires_view_ready_system_event_kind() -> None:
    kernel = _make_kernel()
    cond = ViewReadyCondition(view_kind="text")
    # system_event with a different system_event_kind.
    turn = _make_turn(
        event_type="system_event",
        metadata={"system_event_kind": "heartbeat", "view_kind": "text"},
    )
    assert kernel._condition_matches(cond, set(), {}, turn) is False


def test_view_ready_respects_any_of_kinds_when_specified() -> None:
    kernel = _make_kernel()
    cond = ViewReadyCondition(view_kind="text", any_of_kinds=["pdf", "docx"])
    # Attachment kind matches filter.
    good = _make_turn(
        event_type="system_event",
        attachments=[_make_ref("pdf")],
        metadata={"system_event_kind": "view_ready", "view_kind": "text"},
    )
    assert kernel._condition_matches(cond, set(), {}, good) is True
    # Attachment kind doesn't match filter.
    bad = _make_turn(
        event_type="system_event",
        attachments=[_make_ref("image")],
        metadata={"system_event_kind": "view_ready", "view_kind": "text"},
    )
    assert kernel._condition_matches(cond, set(), {}, bad) is False


# ── schema validators ────────────────────────────────────────────────────────


def test_view_ready_condition_requires_view_kind_at_schema_level() -> None:
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ViewReadyCondition()  # type: ignore[call-arg]


def test_runtime_turn_rejects_upload_success_with_empty_attachments() -> None:
    """Spec §5: turn-acceptance schema validation — upload_success must
    carry at least one attachment."""
    import pytest
    with pytest.raises(ValueError, match="upload_success.*must carry at least one attachment"):
        RuntimeTurn(
            turn_id="t1",
            dedupe_key="t1",
            channel="web_widget",
            modality="text",
            event_type="upload_success",
            attachments=[],
            received_at=datetime.now(timezone.utc),
        )


def test_runtime_turn_accepts_upload_failed_with_empty_attachments() -> None:
    """upload_failed may legitimately carry no attachments (ingestion itself
    failed) — not rejected by schema."""
    turn = RuntimeTurn(
        turn_id="t1",
        dedupe_key="t1",
        channel="web_widget",
        modality="text",
        event_type="upload_failed",
        attachments=[],
        received_at=datetime.now(timezone.utc),
    )
    assert turn.event_type == "upload_failed"
