"""Tests for the BlobStore abstraction and its four backends.

* InMemoryBlobStore — direct roundtrip
* LocalFilesystemBlobStore — tmp_path roundtrip; path-traversal guard
* S3BlobStore — boto3 client mocked; verify exact call args + error mapping
* GCSBlobStore — google-cloud-storage mocked; verify call shape + error mapping
* Factory + build_blob_store_from_settings dispatch
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ruhu.blob_store import (
    BlobNotFoundError,
    BlobPermissionDeniedError,
    BlobRef,
    BlobStoreError,
    BlobStoreUnavailableError,
    GCSBlobStore,
    InMemoryBlobStore,
    LocalFilesystemBlobStore,
    S3BlobStore,
    build_blob_store,
    build_blob_store_from_settings,
)
from ruhu.runtime_config import RuntimeSettings


# ── BlobRef ──────────────────────────────────────────────────────────


def test_blob_ref_uri_format() -> None:
    ref = BlobRef(backend="s3", bucket="acme", key="files/x.bin")
    assert ref.uri() == "s3://acme/files/x.bin"


def test_blob_ref_is_immutable() -> None:
    ref = BlobRef(backend="s3", bucket="acme", key="x")
    with pytest.raises(Exception):
        ref.key = "y"  # type: ignore[misc]


# ── InMemoryBlobStore ────────────────────────────────────────────────


def test_in_memory_put_then_get_roundtrips_bytes_and_metadata() -> None:
    store = InMemoryBlobStore(bucket="test-bucket")
    ref = store.put_blob(
        key="docs/hello.txt",
        content=b"hello world",
        content_type="text/plain",
        metadata={"owner": "u1"},
    )
    assert ref.backend == "in_memory"
    assert ref.bucket == "test-bucket"
    assert ref.key == "docs/hello.txt"
    assert ref.size_bytes == 11
    assert ref.content_type == "text/plain"
    assert ref.etag is not None  # md5 hex of content
    assert store.get_blob(key="docs/hello.txt") == b"hello world"


def test_in_memory_get_missing_raises_not_found() -> None:
    store = InMemoryBlobStore()
    with pytest.raises(BlobNotFoundError):
        store.get_blob(key="missing")


def test_in_memory_delete_returns_true_when_exists_false_otherwise() -> None:
    store = InMemoryBlobStore()
    store.put_blob(key="a", content=b"x")
    assert store.delete_blob(key="a") is True
    assert store.delete_blob(key="a") is False  # second delete is idempotent


def test_in_memory_presigned_urls_are_none() -> None:
    store = InMemoryBlobStore()
    assert store.presigned_get_url(key="x") is None
    assert store.presigned_put_url(key="x") is None


# ── LocalFilesystemBlobStore ─────────────────────────────────────────


def test_local_put_then_get_roundtrips_bytes(tmp_path: Path) -> None:
    store = LocalFilesystemBlobStore(bucket="b1", root=tmp_path)
    ref = store.put_blob(key="folder/file.bin", content=b"\x00\x01\x02")
    assert ref.size_bytes == 3
    assert ref.etag is not None
    assert store.get_blob(key="folder/file.bin") == b"\x00\x01\x02"


def test_local_put_creates_parent_directories(tmp_path: Path) -> None:
    store = LocalFilesystemBlobStore(bucket="b1", root=tmp_path)
    store.put_blob(key="deep/nested/dir/file.txt", content=b"x")
    assert (tmp_path / "b1" / "deep" / "nested" / "dir" / "file.txt").exists()


def test_local_get_missing_raises_not_found(tmp_path: Path) -> None:
    store = LocalFilesystemBlobStore(bucket="b1", root=tmp_path)
    with pytest.raises(BlobNotFoundError):
        store.get_blob(key="not-there")


def test_local_path_traversal_attempt_is_rejected(tmp_path: Path) -> None:
    """A key like ``../../etc/passwd`` must not escape the bucket root."""
    store = LocalFilesystemBlobStore(bucket="b1", root=tmp_path)
    with pytest.raises(BlobStoreError, match="escapes bucket root"):
        store.put_blob(key="../../etc/passwd", content=b"x")


def test_local_delete_idempotent(tmp_path: Path) -> None:
    store = LocalFilesystemBlobStore(bucket="b1", root=tmp_path)
    store.put_blob(key="x", content=b"y")
    assert store.delete_blob(key="x") is True
    assert store.delete_blob(key="x") is False


def test_local_atomic_write_does_not_leave_partial_files(tmp_path: Path) -> None:
    """Atomic-write contract: put_blob writes to a tmp file then renames,
    so concurrent readers see either the old or new content, never half."""
    store = LocalFilesystemBlobStore(bucket="b1", root=tmp_path)
    store.put_blob(key="x", content=b"v1")
    store.put_blob(key="x", content=b"v2 longer content")
    assert store.get_blob(key="x") == b"v2 longer content"
    # Only one file should exist under that key path.
    bucket_root = tmp_path / "b1"
    files = list(bucket_root.rglob("*"))
    files = [f for f in files if f.is_file()]
    assert len(files) == 1


# ── S3BlobStore ──────────────────────────────────────────────────────


def _make_boto_client_error(code: str) -> Exception:
    """Construct a duck-typed boto3 ClientError without importing botocore."""
    exc = Exception(f"boto-error-{code}")
    exc.response = {"Error": {"Code": code, "Message": f"simulated {code}"}}  # type: ignore[attr-defined]
    return exc


def test_s3_put_blob_calls_put_object_with_expected_args() -> None:
    client = MagicMock()
    client.put_object.return_value = {"ETag": '"abc123"'}
    store = S3BlobStore(bucket="acme-bucket", client=client)

    ref = store.put_blob(
        key="reports/2026/q1.pdf",
        content=b"pdf-bytes",
        content_type="application/pdf",
        metadata={"owner": "u1"},
    )

    client.put_object.assert_called_once_with(
        Bucket="acme-bucket",
        Key="reports/2026/q1.pdf",
        Body=b"pdf-bytes",
        ContentType="application/pdf",
        Metadata={"owner": "u1"},
    )
    assert ref.backend == "s3"
    assert ref.bucket == "acme-bucket"
    assert ref.size_bytes == 9
    assert ref.etag == "abc123"  # quotes stripped


def test_s3_get_blob_returns_body_bytes() -> None:
    body = MagicMock()
    body.read.return_value = b"the bytes"
    client = MagicMock()
    client.get_object.return_value = {"Body": body}
    store = S3BlobStore(bucket="b", client=client)

    assert store.get_blob(key="x") == b"the bytes"
    client.get_object.assert_called_once_with(Bucket="b", Key="x")


def test_s3_get_no_such_key_raises_blob_not_found() -> None:
    client = MagicMock()
    client.get_object.side_effect = _make_boto_client_error("NoSuchKey")
    store = S3BlobStore(bucket="b", client=client)
    with pytest.raises(BlobNotFoundError):
        store.get_blob(key="missing")


def test_s3_access_denied_raises_permission_denied() -> None:
    client = MagicMock()
    client.put_object.side_effect = _make_boto_client_error("AccessDenied")
    store = S3BlobStore(bucket="b", client=client)
    with pytest.raises(BlobPermissionDeniedError):
        store.put_blob(key="x", content=b"y")


def test_s3_5xx_raises_unavailable() -> None:
    client = MagicMock()
    client.put_object.side_effect = _make_boto_client_error("ServiceUnavailable")
    store = S3BlobStore(bucket="b", client=client)
    with pytest.raises(BlobStoreUnavailableError):
        store.put_blob(key="x", content=b"y")


def test_s3_unknown_error_raises_generic_blob_store_error() -> None:
    client = MagicMock()
    client.put_object.side_effect = _make_boto_client_error("WeirdError")
    store = S3BlobStore(bucket="b", client=client)
    with pytest.raises(BlobStoreError) as exc_info:
        store.put_blob(key="x", content=b"y")
    # NOT one of the specific subclasses
    assert not isinstance(exc_info.value, BlobNotFoundError)
    assert not isinstance(exc_info.value, BlobPermissionDeniedError)
    assert not isinstance(exc_info.value, BlobStoreUnavailableError)


def test_s3_delete_returns_false_on_no_such_key() -> None:
    client = MagicMock()
    client.delete_object.side_effect = _make_boto_client_error("NoSuchKey")
    store = S3BlobStore(bucket="b", client=client)
    assert store.delete_blob(key="x") is False


def test_s3_presigned_get_url_delegates_to_client() -> None:
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://signed.example.com/...."
    store = S3BlobStore(bucket="b", client=client)
    url = store.presigned_get_url(key="x", expires_in_seconds=600)
    assert url == "https://signed.example.com/...."
    client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "b", "Key": "x"},
        ExpiresIn=600,
    )


def test_s3_presigned_put_url_includes_content_type_when_given() -> None:
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://x"
    store = S3BlobStore(bucket="b", client=client)
    store.presigned_put_url(key="x", content_type="image/jpeg", expires_in_seconds=900)
    client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={"Bucket": "b", "Key": "x", "ContentType": "image/jpeg"},
        ExpiresIn=900,
    )


def test_s3_presigned_url_returns_none_on_client_failure() -> None:
    client = MagicMock()
    client.generate_presigned_url.side_effect = RuntimeError("boom")
    store = S3BlobStore(bucket="b", client=client)
    assert store.presigned_get_url(key="x") is None
    assert store.presigned_put_url(key="x") is None


def test_s3_bucket_required() -> None:
    with pytest.raises(ValueError):
        S3BlobStore(bucket="   ")


# ── GCSBlobStore ─────────────────────────────────────────────────────


def _make_gcs_blob_mock() -> tuple[MagicMock, MagicMock]:
    """Build a (client, blob) pair where client.bucket(name).blob(key) → blob."""
    blob = MagicMock()
    bucket_obj = MagicMock()
    bucket_obj.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket_obj
    return client, blob


def test_gcs_put_blob_uploads_via_blob_object() -> None:
    client, blob = _make_gcs_blob_mock()
    blob.etag = "gcs-etag-1"
    store = GCSBlobStore(bucket="acme", client=client)

    ref = store.put_blob(
        key="r/2026.pdf",
        content=b"pdf",
        content_type="application/pdf",
        metadata={"owner": "u1"},
    )

    client.bucket.assert_called_with("acme")
    blob.upload_from_string.assert_called_once_with(b"pdf", content_type="application/pdf")
    assert blob.metadata == {"owner": "u1"}
    assert ref.backend == "gcs"
    assert ref.size_bytes == 3
    assert ref.etag == "gcs-etag-1"


def test_gcs_get_blob_downloads_bytes() -> None:
    client, blob = _make_gcs_blob_mock()
    blob.download_as_bytes.return_value = b"contents"
    store = GCSBlobStore(bucket="acme", client=client)
    assert store.get_blob(key="x") == b"contents"


def test_gcs_not_found_raises_blob_not_found() -> None:
    client, blob = _make_gcs_blob_mock()

    class NotFound(Exception):
        pass

    blob.download_as_bytes.side_effect = NotFound("not there")
    store = GCSBlobStore(bucket="acme", client=client)
    with pytest.raises(BlobNotFoundError):
        store.get_blob(key="x")


def test_gcs_forbidden_raises_permission_denied() -> None:
    client, blob = _make_gcs_blob_mock()

    class Forbidden(Exception):
        pass

    blob.upload_from_string.side_effect = Forbidden("nope")
    store = GCSBlobStore(bucket="acme", client=client)
    with pytest.raises(BlobPermissionDeniedError):
        store.put_blob(key="x", content=b"y")


def test_gcs_service_unavailable_raises_unavailable() -> None:
    client, blob = _make_gcs_blob_mock()

    class ServiceUnavailable(Exception):
        pass

    blob.upload_from_string.side_effect = ServiceUnavailable("upstream down")
    store = GCSBlobStore(bucket="acme", client=client)
    with pytest.raises(BlobStoreUnavailableError):
        store.put_blob(key="x", content=b"y")


def test_gcs_delete_returns_false_on_not_found() -> None:
    client, blob = _make_gcs_blob_mock()

    class NotFound(Exception):
        pass

    blob.delete.side_effect = NotFound("missing")
    store = GCSBlobStore(bucket="acme", client=client)
    assert store.delete_blob(key="x") is False


# ── Factory ──────────────────────────────────────────────────────────


def test_factory_in_memory_default() -> None:
    store = build_blob_store(backend="in_memory")
    assert isinstance(store, InMemoryBlobStore)


def test_factory_local_requires_root() -> None:
    with pytest.raises(ValueError):
        build_blob_store(backend="local")


def test_factory_local_builds_filesystem_backend(tmp_path: Path) -> None:
    store = build_blob_store(backend="local", bucket="b1", local_root=tmp_path)
    assert isinstance(store, LocalFilesystemBlobStore)


def test_factory_s3_requires_bucket() -> None:
    with pytest.raises(ValueError):
        build_blob_store(backend="s3")


def test_factory_gcs_requires_bucket() -> None:
    with pytest.raises(ValueError):
        build_blob_store(backend="gcs")


def test_factory_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="unknown blob backend"):
        build_blob_store(backend="dropbox")  # type: ignore[arg-type]


def test_build_from_settings_defaults_to_in_memory() -> None:
    store = build_blob_store_from_settings(RuntimeSettings())
    assert isinstance(store, InMemoryBlobStore)


def test_build_from_settings_dispatches_to_local(tmp_path: Path) -> None:
    settings = RuntimeSettings(
        blob_store_backend="local",
        blob_store_bucket="b1",
        blob_store_local_root=str(tmp_path),
    )
    store = build_blob_store_from_settings(settings)
    assert isinstance(store, LocalFilesystemBlobStore)


def test_build_from_settings_normalizes_case() -> None:
    """Setting ``RUHU_BLOB_STORE_BACKEND=S3`` (uppercase) should still work."""
    settings = RuntimeSettings(blob_store_backend="S3", blob_store_bucket="b")
    store = build_blob_store_from_settings(settings)
    assert isinstance(store, S3BlobStore)


def test_build_from_settings_rejects_unknown_backend() -> None:
    settings = RuntimeSettings(blob_store_backend="pinata")
    with pytest.raises(ValueError, match="must be one of"):
        build_blob_store_from_settings(settings)


# ── S3 lazy-import guard (no boto3 installed) ────────────────────────


def test_s3_get_client_raises_clear_error_when_boto3_missing(monkeypatch) -> None:
    """When boto3 isn't installed, a put/get/delete attempt must surface
    a clear actionable BlobStoreError rather than ImportError."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "boto3" or name.startswith("boto3."):
            raise ImportError("simulated missing boto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    store = S3BlobStore(bucket="b")  # no client injected
    with pytest.raises(BlobStoreError, match="boto3 is required"):
        store.put_blob(key="x", content=b"y")


def test_gcs_get_client_raises_clear_error_when_sdk_missing(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "google.cloud" or name.startswith("google.cloud"):
            raise ImportError("simulated missing google-cloud-storage")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    store = GCSBlobStore(bucket="b")  # no client injected
    with pytest.raises(BlobStoreError, match="google-cloud-storage is required"):
        store.put_blob(key="x", content=b"y")
