"""Tests for attachment view producers and service view-write integration.

Covers:
  - GeminiFileUploader: successful upload returns URI, HTTP error raises
  - GeminiVisionProducer: describes with file_uri, describes with inline bytes,
    raises on missing input, raises on HTTP error
  - AttachmentService: writes text view after document extraction
  - AttachmentService: writes native_file_uri + vision views for images
  - AttachmentService: producer failures don't block extraction result
  - AttachmentService: no views written when producers are None
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ruhu.attachments.models import AttachmentView
from ruhu.attachments.producers import GeminiFileUploader, GeminiVisionProducer
from ruhu.attachments.service import AttachmentService
from ruhu.attachments.store import InMemoryAttachmentStore


# ══════════════════════════════════════════════════════════════════════════════
# GeminiFileUploader unit tests
# ══════════════════════════════════════════════════════════════════════════════


def _mock_httpx_client(status_code: int = 200, json_body: dict | None = None):
    """Return a context-manager-compatible mock for httpx.Client."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = json_body or {}
    mock_response.text = ""
    mock_client.post.return_value = mock_response
    # Support `with httpx.Client(...) as client:` pattern.
    ctx = MagicMock()
    ctx.__enter__.return_value = mock_client
    ctx.__exit__.return_value = False
    return ctx, mock_client, mock_response


def test_gemini_file_uploader_returns_uri_on_success() -> None:
    ctx, mock_client, _ = _mock_httpx_client(
        status_code=200,
        json_body={"file": {"uri": "https://example.com/files/abc123", "name": "files/abc123"}},
    )
    uploader = GeminiFileUploader(api_key="test-key")

    with patch("ruhu.attachments.producers.httpx") as mock_httpx:
        mock_httpx.Client.return_value = ctx
        uri = uploader.upload(
            filename="cv.pdf",
            content_type="application/pdf",
            content_bytes=b"fake pdf content",
        )

    assert uri == "https://example.com/files/abc123"
    # Verify POST was called with the upload URL.
    call_args = mock_client.post.call_args
    assert "uploadType=multipart" in call_args.args[0]
    assert "test-key" in call_args.args[0]


def test_gemini_file_uploader_raises_on_http_error() -> None:
    ctx, _, mock_response = _mock_httpx_client(status_code=500)
    mock_response.text = "Internal Server Error"
    uploader = GeminiFileUploader(api_key="test-key")

    with patch("ruhu.attachments.producers.httpx") as mock_httpx:
        mock_httpx.Client.return_value = ctx
        with pytest.raises(RuntimeError, match="500"):
            uploader.upload(
                filename="doc.txt",
                content_type="text/plain",
                content_bytes=b"hello",
            )


def test_gemini_file_uploader_raises_on_missing_uri_in_response() -> None:
    # Response 200 but unexpected shape (no 'file' key).
    ctx, _, _ = _mock_httpx_client(status_code=200, json_body={"unexpected": "shape"})
    uploader = GeminiFileUploader(api_key="test-key")

    with patch("ruhu.attachments.producers.httpx") as mock_httpx:
        mock_httpx.Client.return_value = ctx
        with pytest.raises(RuntimeError, match="unexpected response shape"):
            uploader.upload(filename="x.pdf", content_type="application/pdf", content_bytes=b"x")


# ══════════════════════════════════════════════════════════════════════════════
# GeminiVisionProducer unit tests
# ══════════════════════════════════════════════════════════════════════════════


def test_gemini_vision_producer_describes_with_file_uri() -> None:
    ctx, mock_client, _ = _mock_httpx_client(
        status_code=200,
        json_body={
            "candidates": [
                {"content": {"parts": [{"text": "A resume with a photo and text."}]}}
            ]
        },
    )
    producer = GeminiVisionProducer(api_key="test-key")

    with patch("ruhu.attachments.producers.httpx") as mock_httpx:
        mock_httpx.Client.return_value = ctx
        description = producer.describe(
            file_uri="https://example.com/files/abc123",
            content_type="image/jpeg",
        )

    assert description == "A resume with a photo and text."
    # Payload should contain fileData, not inlineData.
    payload = mock_client.post.call_args.kwargs["json"]
    parts = payload["contents"][0]["parts"]
    image_part = parts[0]
    assert "fileData" in image_part
    assert image_part["fileData"]["fileUri"] == "https://example.com/files/abc123"


def test_gemini_vision_producer_describes_with_inline_bytes() -> None:
    ctx, mock_client, _ = _mock_httpx_client(
        status_code=200,
        json_body={
            "candidates": [
                {"content": {"parts": [{"text": "A screenshot of a dashboard."}]}}
            ]
        },
    )
    producer = GeminiVisionProducer(api_key="test-key")

    with patch("ruhu.attachments.producers.httpx") as mock_httpx:
        mock_httpx.Client.return_value = ctx
        description = producer.describe(
            content_bytes=b"\x89PNG\r\n",
            content_type="image/png",
        )

    assert "dashboard" in description
    payload = mock_client.post.call_args.kwargs["json"]
    parts = payload["contents"][0]["parts"]
    image_part = parts[0]
    assert "inlineData" in image_part
    assert image_part["inlineData"]["mimeType"] == "image/png"


def test_gemini_vision_producer_raises_without_input() -> None:
    producer = GeminiVisionProducer(api_key="test-key")
    with pytest.raises(ValueError, match="file_uri or content_bytes"):
        producer.describe()


def test_gemini_vision_producer_raises_on_http_error() -> None:
    ctx, _, mock_response = _mock_httpx_client(status_code=429)
    mock_response.text = "Rate limit exceeded"
    producer = GeminiVisionProducer(api_key="test-key")

    with patch("ruhu.attachments.producers.httpx") as mock_httpx:
        mock_httpx.Client.return_value = ctx
        with pytest.raises(RuntimeError, match="429"):
            producer.describe(content_bytes=b"\xff\xd8\xff", content_type="image/jpeg")


# ══════════════════════════════════════════════════════════════════════════════
# AttachmentService integration tests (text view)
# ══════════════════════════════════════════════════════════════════════════════


def test_service_writes_text_view_after_document_extraction() -> None:
    """After processing a text attachment, a 'text' view is written to the store."""
    store = InMemoryAttachmentStore()
    service = AttachmentService(store=store)

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"Hello from Ruhu. alice@example.com",
    )
    service.process_attachment(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )

    views = store.list_views(attachment.attachment_id, organization_id="org_1")
    assert len(views) == 1
    assert views[0].kind == "text"
    assert views[0].status == "ready"
    assert "Ruhu" in (views[0].content_text or "")


def test_service_no_text_view_on_extraction_failure() -> None:
    """If text extraction fails, no text view is written."""
    store = InMemoryAttachmentStore()
    service = AttachmentService(store=store)

    # Binary attachment — extraction is skipped, no text view.
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="photo.png",
        content_type="image/png",
        content_bytes=b"\x89PNG\r\n\x1a\nbinary",
    )
    service.process_attachment(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )

    # Image produces no text view (only potential vision/native_file_uri when producers are set).
    text_views = [v for v in store.list_views(attachment.attachment_id) if v.kind == "text"]
    assert len(text_views) == 0


# ══════════════════════════════════════════════════════════════════════════════
# AttachmentService integration tests (image views)
# ══════════════════════════════════════════════════════════════════════════════


def test_service_writes_native_file_uri_and_vision_views_for_image() -> None:
    """process_attachment() writes both native_file_uri and vision views when producers are set."""
    store = InMemoryAttachmentStore()
    mock_uploader = MagicMock()
    mock_uploader.upload.return_value = "https://example.com/files/img1"
    mock_vision = MagicMock()
    mock_vision.describe.return_value = "A photo ID with the name John Doe."

    service = AttachmentService(
        store=store,
        file_uploader=mock_uploader,
        vision_producer=mock_vision,
    )

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="id.jpg",
        content_type="image/jpeg",
        content_bytes=b"\xff\xd8\xffsome jpeg bytes",
    )
    service.process_attachment(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )

    views = {v.kind: v for v in store.list_views(attachment.attachment_id, organization_id="org_1")}
    assert "native_file_uri" in views
    assert views["native_file_uri"].status == "ready"
    assert views["native_file_uri"].content_text == "https://example.com/files/img1"

    assert "vision" in views
    assert views["vision"].status == "ready"
    assert "John Doe" in (views["vision"].content_text or "")

    # Vision producer should have been called with the file URI (not inline bytes).
    mock_vision.describe.assert_called_once()
    call_kwargs = mock_vision.describe.call_args.kwargs
    assert call_kwargs["file_uri"] == "https://example.com/files/img1"
    assert call_kwargs.get("content_bytes") is None


def test_service_vision_failure_does_not_block_extraction() -> None:
    """If the vision producer raises, the image still gets its placeholder extraction returned."""
    store = InMemoryAttachmentStore()
    mock_uploader = MagicMock()
    mock_uploader.upload.side_effect = RuntimeError("Upload failed")
    mock_vision = MagicMock()
    mock_vision.describe.side_effect = RuntimeError("Vision failed")

    service = AttachmentService(
        store=store,
        file_uploader=mock_uploader,
        vision_producer=mock_vision,
    )

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="photo.jpg",
        content_type="image/jpeg",
        content_bytes=b"\xff\xd8\xffbytes",
    )
    projection = service.process_attachment(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )

    # Main extraction result is intact.
    assert projection.attachment.extraction_status == "ready"
    assert projection.extraction is not None

    # Failed views are written with status="failed".
    views = {v.kind: v for v in store.list_views(attachment.attachment_id)}
    assert views["native_file_uri"].status == "failed"
    assert views["vision"].status == "failed"


def test_service_no_views_when_producers_are_none() -> None:
    """Without producers, only existing extraction happens — no view rows."""
    store = InMemoryAttachmentStore()
    service = AttachmentService(store=store)  # No file_uploader, no vision_producer.

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="scan.png",
        content_type="image/png",
        content_bytes=b"\x89PNG\r\n\x1a\nbinary",
    )
    service.process_attachment(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )

    views = store.list_views(attachment.attachment_id)
    assert views == []


def test_service_vision_producer_uses_inline_bytes_when_no_uploader() -> None:
    """If no file_uploader but vision_producer is set, content_bytes are used inline."""
    store = InMemoryAttachmentStore()
    mock_vision = MagicMock()
    mock_vision.describe.return_value = "A scanned document."

    service = AttachmentService(
        store=store,
        file_uploader=None,  # No uploader
        vision_producer=mock_vision,
    )

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="doc.jpg",
        content_type="image/jpeg",
        content_bytes=b"\xff\xd8\xffimage data",
    )
    service.process_attachment(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )

    # Vision was called with inline bytes, not file_uri.
    call_kwargs = mock_vision.describe.call_args.kwargs
    assert call_kwargs["file_uri"] is None
    assert call_kwargs["content_bytes"] is not None

    views = {v.kind: v for v in store.list_views(attachment.attachment_id)}
    assert "vision" in views
    assert views["vision"].content_text == "A scanned document."
