from __future__ import annotations

from ruhu.attachments.service import AttachmentService
from ruhu.attachments.store import InMemoryAttachmentStore


def test_attachment_service_uploads_and_extracts_text() -> None:
    service = AttachmentService(InMemoryAttachmentStore(), max_file_bytes=1024 * 1024)

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"Hello from Ruhu attachments.",
    )
    projection = service.process_attachment(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )

    assert projection.attachment.scan_status == "passed"
    assert projection.attachment.extraction_status == "ready"
    assert projection.extraction is not None
    assert "Ruhu attachments" in (projection.extraction.text_content or "")
    assert projection.attachment.filename == "notes.txt"
    materialized = service.materialize_ref(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )
    assert materialized is not None
    assert materialized.policy["content_available"] is True


def test_attachment_service_creates_image_and_audio_placeholders_and_artifact_bytes() -> None:
    service = AttachmentService(InMemoryAttachmentStore(), max_file_bytes=1024 * 1024)

    image_attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="../photo.png",
        content_type="image/png",
        content_bytes=b"\x89PNG\r\n\x1a\nbinary",
    )
    image_projection = service.process_attachment(
        attachment_id=image_attachment.attachment_id,
        organization_id="org_1",
    )
    assert image_projection.attachment.filename == "photo.png"
    assert image_projection.attachment.extraction_status == "ready"
    assert image_projection.extraction is not None
    assert image_projection.extraction.structured_data["placeholder_only"] is True

    audio_attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="call.wav",
        content_type="audio/wav",
        content_bytes=b"RIFFfakeWAVEdata",
    )
    audio_projection = service.process_attachment(
        attachment_id=audio_attachment.attachment_id,
        organization_id="org_1",
    )
    assert audio_projection.attachment.extraction_status == "ready"
    assert audio_projection.extraction is not None
    assert audio_projection.extraction.structured_data["transcription_ready"] is False

    artifact = service.create_artifact(
        conversation_id="conv_1",
        organization_id="org_1",
        filename="../artifact.txt",
        content_type="text/plain",
        content_bytes=b"artifact body",
        kind="result_bundle",
    )
    artifact_payload = service.get_artifact_bytes(
        artifact_id=artifact.artifact_id,
        organization_id="org_1",
    )
    assert artifact_payload is not None
    stored_artifact, content = artifact_payload
    assert stored_artifact.filename == "artifact.txt"
    assert content == b"artifact body"

    materialized = service.materialize_ref(
        attachment_id=image_attachment.attachment_id,
        organization_id="org_1",
    )
    assert materialized is not None
    assert materialized.policy["placeholder_only"] is True
    assert materialized.policy["content_available"] is False


def test_attachment_service_rejects_unsupported_binary_uploads() -> None:
    service = AttachmentService(InMemoryAttachmentStore(), max_file_bytes=1024 * 1024)

    try:
        service.upload_attachment(
            conversation_id="conv_1",
            organization_id="org_1",
            channel="web_widget",
            filename="payload.exe",
            content_type="application/x-msdownload",
            content_bytes=b"MZfake",
        )
    except ValueError as exc:
        assert "unsupported attachment type" in str(exc) or "unsupported content type" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unsupported binary upload should fail")
