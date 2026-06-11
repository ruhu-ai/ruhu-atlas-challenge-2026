"""Tests for attachment lifecycle projection events (canonical spec §12).

The service emits named events through its optional ``event_emitter``
callback.  Widget/UTI clients subscribe to these via the realtime
projection outbox so attachment chips update live instead of polling.

These tests use an in-process collector emitter rather than the real
control plane — that's enough to verify the event family, name, and
payload shape.  The realtime adapter shape is exercised by a small unit
test of ``build_realtime_attachment_event_emitter``.
"""

from __future__ import annotations

from typing import Any

from ruhu.attachments.models import AttachmentView
from ruhu.attachments.runtime import build_realtime_attachment_event_emitter
from ruhu.attachments.service import AttachmentService
from ruhu.attachments.store import InMemoryAttachmentStore


class _CollectingEmitter:
    """Test double that records everything the service emits."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        self.events.append(
            {
                "conversation_id": conversation_id,
                "organization_id": organization_id,
                "name": name,
                "payload": dict(payload),
            }
        )


def _make_service(emitter: _CollectingEmitter | None = None) -> AttachmentService:
    return AttachmentService(
        InMemoryAttachmentStore(),
        max_file_bytes=1024 * 1024,
        event_emitter=emitter,
    )


# ── service-level event emission ─────────────────────────────────────────────


def test_scan_passed_event_is_emitted_during_processing() -> None:
    emitter = _CollectingEmitter()
    service = _make_service(emitter)
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"hello",
    )
    service.process_attachment(attachment_id=attachment.attachment_id, organization_id="org_1")

    scan_events = [e for e in emitter.events if e["name"] == "scan_passed"]
    assert len(scan_events) == 1
    event = scan_events[0]
    assert event["conversation_id"] == "conv_1"
    assert event["organization_id"] == "org_1"
    assert event["payload"]["attachment_id"] == attachment.attachment_id
    assert event["payload"]["kind"] == "text"
    assert event["payload"]["trust_tier"] == "anonymous"


def test_view_ready_event_is_emitted_when_text_extraction_succeeds() -> None:
    emitter = _CollectingEmitter()
    service = _make_service(emitter)
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"The quick brown fox.",
    )
    service.process_attachment(attachment_id=attachment.attachment_id, organization_id="org_1")

    view_events = [e for e in emitter.events if e["name"] == "view_ready"]
    assert len(view_events) == 1
    event = view_events[0]
    assert event["payload"]["attachment_id"] == attachment.attachment_id
    assert event["payload"]["view_kind"] == "text"
    assert event["payload"]["provider"] == "knowledge.extractors"
    assert event["payload"]["content_length"] > 0


def test_view_skipped_event_emitted_for_images() -> None:
    emitter = _CollectingEmitter()
    service = _make_service(emitter)
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="photo.png",
        content_type="image/png",
        content_bytes=b"\x89PNG\r\n\x1a\nbinary",
    )
    service.process_attachment(attachment_id=attachment.attachment_id, organization_id="org_1")

    skipped_events = [e for e in emitter.events if e["name"] == "view_skipped"]
    assert len(skipped_events) == 1
    event = skipped_events[0]
    assert event["payload"]["view_kind"] == "text"
    assert "OCR" in event["payload"]["reason"] or "vision" in event["payload"]["reason"]


def test_view_skipped_event_emitted_for_audio_transcript() -> None:
    emitter = _CollectingEmitter()
    service = _make_service(emitter)
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="call.wav",
        content_type="audio/wav",
        content_bytes=b"RIFFfake",
    )
    service.process_attachment(attachment_id=attachment.attachment_id, organization_id="org_1")

    skipped_events = [e for e in emitter.events if e["name"] == "view_skipped"]
    assert len(skipped_events) == 1
    assert skipped_events[0]["payload"]["view_kind"] == "transcript"


def test_view_failed_event_emitted_when_extraction_raises() -> None:
    """A text view that fails extraction must produce a ``view_failed``
    event with error code + detail.  We force the failure by handing the
    text extractor bytes it can't decode as UTF-8 for a format (JSON) that
    the extractor tries to parse."""
    emitter = _CollectingEmitter()
    service = _make_service(emitter)
    # Invalid JSON content → extractor raises JSONDecodeError.
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="broken.json",
        content_type="application/json",
        content_bytes=b"\x80\x81\x82 not valid utf-8",
    )
    service.process_attachment(attachment_id=attachment.attachment_id, organization_id="org_1")

    failed_events = [e for e in emitter.events if e["name"] == "view_failed"]
    assert len(failed_events) == 1
    event = failed_events[0]
    assert event["payload"]["view_kind"] == "text"
    assert event["payload"]["error_code"] == "extraction_failed"
    assert event["payload"]["error_detail"]


def test_artifact_ready_event_emitted_on_create_artifact() -> None:
    emitter = _CollectingEmitter()
    service = _make_service(emitter)
    artifact = service.create_artifact(
        conversation_id="conv_1",
        organization_id="org_1",
        filename="report.txt",
        content_type="text/plain",
        content_bytes=b"summary",
        kind="result_bundle",
    )

    ready_events = [e for e in emitter.events if e["name"] == "artifact.ready"]
    assert len(ready_events) == 1
    event = ready_events[0]
    assert event["payload"]["artifact_id"] == artifact.artifact_id
    assert event["payload"]["kind"] == "result_bundle"
    assert event["payload"]["filename"] == "report.txt"


def test_service_without_emitter_does_not_raise() -> None:
    """Services built without an emitter should run normally; the emit
    helper is a no-op."""
    service = _make_service(emitter=None)
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"hi",
    )
    service.process_attachment(attachment_id=attachment.attachment_id, organization_id="org_1")
    # Got here without raising.


def test_emitter_failure_does_not_break_processing() -> None:
    """Emitter exceptions must never fail the upload/processing path."""

    def _broken_emitter(**_: Any) -> None:
        raise RuntimeError("projection sink offline")

    service = AttachmentService(
        InMemoryAttachmentStore(),
        event_emitter=_broken_emitter,
    )
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"hello",
    )
    # Must not raise despite emitter crashing at every event.
    projection = service.process_attachment(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )
    assert projection.attachment.scan_status == "passed"
    assert projection.attachment.extraction_status == "ready"
    assert projection.extraction is not None


# ── realtime adapter shape ───────────────────────────────────────────────────


def test_realtime_adapter_routes_attachment_events_under_attachment_family() -> None:
    """``build_realtime_attachment_event_emitter`` wraps the control plane
    so ``scan_passed`` lands under the ``attachment`` family and
    ``artifact.ready`` under the ``artifact`` family (spec §12)."""

    captured: list[dict[str, Any]] = []

    class _FakeEvents:
        def append(self, **kwargs: Any) -> None:
            captured.append(dict(kwargs))

    class _FakeControlPlane:
        def __init__(self) -> None:
            self.events = _FakeEvents()

    emitter = build_realtime_attachment_event_emitter(_FakeControlPlane())

    emitter(
        conversation_id="conv_1",
        organization_id="org_1",
        name="scan_passed",
        payload={"attachment_id": "att_1"},
    )
    emitter(
        conversation_id="conv_1",
        organization_id="org_1",
        name="artifact.ready",
        payload={"artifact_id": "art_1"},
    )

    assert len(captured) == 2
    # Attachment event
    assert captured[0]["family"] == "attachment"
    assert captured[0]["name"] == "scan_passed"
    assert captured[0]["payload"] == {"attachment_id": "att_1"}
    assert captured[0]["outbox_topic"] == "conversation_projection"
    assert captured[0]["actor_type"] == "system"
    # Artifact event — ``artifact.`` prefix stripped, family set
    assert captured[1]["family"] == "artifact"
    assert captured[1]["name"] == "ready"
    assert captured[1]["payload"] == {"artifact_id": "art_1"}


def test_realtime_adapter_swallows_sink_failures() -> None:
    """If the control plane's ``events.append`` raises, the emitter must
    log and return — never propagate — so upload handlers stay healthy."""

    class _CrashingEvents:
        def append(self, **_: Any) -> None:
            raise RuntimeError("db offline")

    class _FakeControlPlane:
        def __init__(self) -> None:
            self.events = _CrashingEvents()

    emitter = build_realtime_attachment_event_emitter(_FakeControlPlane())

    # Must not raise.
    emitter(
        conversation_id="conv_1",
        organization_id="org_1",
        name="scan_passed",
        payload={},
    )
