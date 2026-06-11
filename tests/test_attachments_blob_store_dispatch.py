"""Tests for the BlobStore dispatch logic in AttachmentService.

The service supports two storage paths:

1. **Legacy DB-bytes path** — when ``blob_store`` is None, uploads are
   written via ``store.save_blob`` into ``AttachmentBlobRecord``.
2. **BlobStore-backed path** — when ``blob_store`` is configured, bytes
   are written to the BlobStore and the row records ``blob_uri``.

Reads dispatch on whether ``blob_uri`` is set, so existing rows keep
working forever even after the service starts using a BlobStore.
"""
from __future__ import annotations

import pytest

from ruhu.attachments.service import (
    AttachmentService,
    _build_blob_key,
    _parse_blob_key,
)
from ruhu.attachments.store import InMemoryAttachmentStore
from ruhu.blob_store import (
    BlobNotFoundError,
    BlobStoreError,
    InMemoryBlobStore,
)


# ── Key + URI helpers ─────────────────────────────────────────────────


def test_build_blob_key_uses_org_first_layout() -> None:
    key = _build_blob_key(
        organization_id="org_42",
        conversation_id="conv_1",
        attachment_id="att_abc",
        filename="photo.jpg",
    )
    assert key == "org_42/conv_1/att_abc/photo.jpg"


def test_build_blob_key_falls_back_to_anon_for_unauth_uploads() -> None:
    key = _build_blob_key(
        organization_id=None,
        conversation_id="conv_1",
        attachment_id="att_abc",
        filename="photo.jpg",
    )
    assert key.startswith("_anon/")


def test_build_blob_key_treats_blank_org_as_anon() -> None:
    key = _build_blob_key(
        organization_id="   ",
        conversation_id="conv_1",
        attachment_id="att_abc",
        filename="photo.jpg",
    )
    assert key.startswith("_anon/")


def test_parse_blob_key_extracts_key_from_full_uri() -> None:
    assert _parse_blob_key("s3://bucket/org_1/conv_1/att/x.bin") == "org_1/conv_1/att/x.bin"
    assert _parse_blob_key("gcs://b/o/c/a/y.png") == "o/c/a/y.png"
    assert _parse_blob_key("in_memory://in-memory/key") == "key"


def test_parse_blob_key_handles_keys_with_slashes() -> None:
    assert _parse_blob_key("s3://bucket/a/b/c/d/e") == "a/b/c/d/e"


def test_parse_blob_key_returns_none_for_invalid_uri() -> None:
    assert _parse_blob_key("") is None
    assert _parse_blob_key("not-a-uri") is None
    assert _parse_blob_key("s3://bucket-with-no-key") is None


# ── Upload path: BlobStore configured ────────────────────────────────


def test_upload_with_blob_store_writes_bytes_to_blob_store_and_records_uri() -> None:
    blob_store = InMemoryBlobStore(bucket="ruhu-attachments")
    service = AttachmentService(
        store=InMemoryAttachmentStore(),
        blob_store=blob_store,
    )

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_42",
        channel="whatsapp",
        filename="photo.jpg",
        content_type="image/jpeg",
        content_bytes=b"jpeg-bytes-here",
        source="meta_whatsapp",
    )

    # blob_uri is recorded on the row.
    assert attachment.blob_uri is not None
    assert attachment.blob_uri.startswith("in_memory://ruhu-attachments/org_42/conv_1/")
    assert attachment.blob_uri.endswith("photo.jpg")

    # Bytes live in the BlobStore.
    key = _parse_blob_key(attachment.blob_uri)
    assert key is not None
    assert blob_store.get_blob(key=key) == b"jpeg-bytes-here"


def test_upload_with_blob_store_does_not_write_db_bytes_row() -> None:
    """Don't double-store: when BlobStore is in use, AttachmentBlobRecord
    must NOT also be populated. That would double our storage bill."""
    in_mem_store = InMemoryAttachmentStore()
    service = AttachmentService(
        store=in_mem_store,
        blob_store=InMemoryBlobStore(),
    )

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="whatsapp",
        filename="audio.ogg",
        content_type="audio/ogg",
        content_bytes=b"ogg-bytes",
    )

    # Legacy DB-bytes path is empty for this attachment.
    assert in_mem_store.get_blob(attachment.attachment_id) is None


def test_upload_with_blob_store_failure_aborts_upload_no_row_persisted() -> None:
    """If the BlobStore put fails, the attachment row must NOT be saved.
    A row with no recoverable bytes is worse than no row at all."""
    in_mem_store = InMemoryAttachmentStore()

    class _BrokenBlobStore(InMemoryBlobStore):
        def put_blob(self, **kwargs):
            raise BlobStoreError("simulated S3 outage")

    service = AttachmentService(
        store=in_mem_store,
        blob_store=_BrokenBlobStore(),
    )

    with pytest.raises(BlobStoreError):
        service.upload_attachment(
            conversation_id="conv_1",
            organization_id="org_1",
            channel="whatsapp",
            filename="x.jpg",
            content_type="image/jpeg",
            content_bytes=b"x",
        )

    # No attachment row was saved.
    assert in_mem_store.list_attachments("conv_1") == []


# ── Upload path: legacy (no BlobStore) ───────────────────────────────


def test_upload_without_blob_store_falls_through_to_db_bytes_path() -> None:
    in_mem_store = InMemoryAttachmentStore()
    service = AttachmentService(store=in_mem_store, blob_store=None)

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="whatsapp",
        filename="photo.jpg",
        content_type="image/jpeg",
        content_bytes=b"jpeg-bytes",
    )

    assert attachment.blob_uri is None
    assert in_mem_store.get_blob(attachment.attachment_id) == b"jpeg-bytes"


# ── Read path: load_attachment_bytes dispatch ────────────────────────


def test_load_bytes_reads_from_blob_store_when_blob_uri_set() -> None:
    blob_store = InMemoryBlobStore()
    service = AttachmentService(
        store=InMemoryAttachmentStore(),
        blob_store=blob_store,
    )
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="whatsapp",
        filename="x.jpg",
        content_type="image/jpeg",
        content_bytes=b"original-bytes",
    )

    assert (
        service.load_attachment_bytes(
            attachment_id=attachment.attachment_id, organization_id="org_1"
        )
        == b"original-bytes"
    )


def test_load_bytes_falls_back_to_db_bytes_when_blob_uri_unset() -> None:
    """A row uploaded BEFORE the BlobStore was wired in has blob_uri=None.
    The service must still return its bytes via the legacy path."""
    in_mem_store = InMemoryAttachmentStore()
    service = AttachmentService(store=in_mem_store, blob_store=InMemoryBlobStore())

    # Simulate a legacy upload: upload via a service WITHOUT blob_store,
    # then read via a service WITH blob_store.
    legacy_service = AttachmentService(store=in_mem_store, blob_store=None)
    attachment = legacy_service.upload_attachment(
        conversation_id="conv_legacy",
        organization_id="org_1",
        channel="web_widget",
        filename="old.jpg",
        content_type="image/jpeg",
        content_bytes=b"legacy-bytes",
    )

    # Now read via the BlobStore-aware service. It must fall back to
    # AttachmentBlobRecord because blob_uri is None on this row.
    assert (
        service.load_attachment_bytes(
            attachment_id=attachment.attachment_id, organization_id="org_1"
        )
        == b"legacy-bytes"
    )


def test_load_bytes_returns_none_for_unknown_attachment() -> None:
    service = AttachmentService(store=InMemoryAttachmentStore(), blob_store=InMemoryBlobStore())
    assert service.load_attachment_bytes(attachment_id="att_missing") is None


def test_load_bytes_returns_none_when_blob_store_says_object_gone() -> None:
    """If the row claims the blob is at <uri> but the BlobStore returns
    NotFound (e.g. lifecycle policy expired the object), surface as
    None rather than raising — matches legacy semantics."""
    in_mem_store = InMemoryAttachmentStore()
    blob_store = InMemoryBlobStore()
    service = AttachmentService(store=in_mem_store, blob_store=blob_store)

    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="whatsapp",
        filename="x.jpg",
        content_type="image/jpeg",
        content_bytes=b"x",
    )
    # Object disappears from the BlobStore (e.g. lifecycle expiry).
    key = _parse_blob_key(attachment.blob_uri or "")
    assert key is not None
    blob_store.delete_blob(key=key)

    assert (
        service.load_attachment_bytes(
            attachment_id=attachment.attachment_id, organization_id="org_1"
        )
        is None
    )


def test_load_bytes_returns_none_when_blob_uri_set_but_no_blob_store_configured() -> None:
    """Defensive case: row claims BlobStore-backed but service has no
    BlobStore configured. Return None (missing) rather than corrupt or raise."""
    in_mem_store = InMemoryAttachmentStore()
    blob_store = InMemoryBlobStore()
    upload_service = AttachmentService(store=in_mem_store, blob_store=blob_store)
    attachment = upload_service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="whatsapp",
        filename="x.jpg",
        content_type="image/jpeg",
        content_bytes=b"x",
    )

    # Now read via a service that has NO blob_store configured.
    misconfigured_service = AttachmentService(store=in_mem_store, blob_store=None)
    result = misconfigured_service.load_attachment_bytes(
        attachment_id=attachment.attachment_id, organization_id="org_1"
    )
    assert result is None


def test_load_bytes_enforces_org_scope() -> None:
    service = AttachmentService(store=InMemoryAttachmentStore(), blob_store=InMemoryBlobStore())
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_owner",
        channel="whatsapp",
        filename="x.jpg",
        content_type="image/jpeg",
        content_bytes=b"x",
    )
    # Wrong org cannot read.
    assert (
        service.load_attachment_bytes(
            attachment_id=attachment.attachment_id, organization_id="org_attacker"
        )
        is None
    )
    # Right org can read.
    assert (
        service.load_attachment_bytes(
            attachment_id=attachment.attachment_id, organization_id="org_owner"
        )
        == b"x"
    )


# ── Round-trip: blob_uri persists through save/get_attachment ────────


def test_blob_uri_round_trips_through_attachment_record() -> None:
    """The domain model + record converters must preserve blob_uri so a
    subsequent get_attachment() sees the same URI we just saved."""
    in_mem_store = InMemoryAttachmentStore()
    service = AttachmentService(store=in_mem_store, blob_store=InMemoryBlobStore())

    saved = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="whatsapp",
        filename="x.jpg",
        content_type="image/jpeg",
        content_bytes=b"x",
    )
    fetched = in_mem_store.get_attachment(saved.attachment_id, organization_id="org_1")

    assert fetched is not None
    assert fetched.blob_uri == saved.blob_uri
    assert fetched.blob_uri is not None
