"""Blob storage abstraction for Ruhu.

Why this exists: today, attachments either live as raw bytes inside
Postgres (workable for tiny files, terrible for media), or as
Gemini-Files-API URIs (vision-specific, not general-purpose). Inbound
WhatsApp media, exported reports, and any other "binary content the
runtime needs to keep around" don't yet have a clean home.

This module defines a small ``BlobStore`` Protocol and four backends:

* ``InMemoryBlobStore`` — for tests; dict-backed.
* ``LocalFilesystemBlobStore`` — for dev; files under a configured root.
* ``S3BlobStore`` — production AWS path; lazy-imports ``boto3``.
* ``GCSBlobStore`` — production GCP path; lazy-imports
  ``google.cloud.storage``.

Operators install only the cloud SDK they need via the
``storage-s3`` / ``storage-gcs`` optional extras in ``pyproject.toml``.
The factory ``build_blob_store(settings)`` picks one backend based on
``RuntimeSettings.blob_store_backend`` and corresponding env vars.

Design contracts:

* All methods are **synchronous** to match the dominant pattern in the
  rest of the codebase (Stripe, Resend, etc. all use sync httpx).
  Routes that need to call the store from async contexts can wrap the
  call in ``starlette.concurrency.run_in_threadpool``.
* Errors from the cloud SDK are translated into ``BlobStoreError``
  (transient / not found / permission denied) so callers don't need to
  know the underlying client's exception types.
* ``BlobRef`` is the durable handle to a stored object; it can be
  serialized into DB columns or events without losing backend identity.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

BlobBackend = Literal["s3", "gcs", "local", "in_memory"]


# ── Public types ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BlobRef:
    """Durable reference to a stored blob.

    Carries enough identity to round-trip through DB rows + events:
    ``backend`` + ``bucket`` + ``key`` is the natural primary key.
    """

    backend: BlobBackend
    bucket: str
    key: str
    content_type: str | None = None
    size_bytes: int | None = None
    etag: str | None = None

    def uri(self) -> str:
        """Stable string representation for log lines + DB columns.

        Format: ``<backend>://<bucket>/<key>``. Not a URL the cloud SDK
        consumes directly — for that, use ``presigned_get_url``.
        """
        return f"{self.backend}://{self.bucket}/{self.key}"


class BlobStoreError(Exception):
    """Base error class for blob store operations."""


class BlobNotFoundError(BlobStoreError):
    """The requested blob does not exist."""


class BlobPermissionDeniedError(BlobStoreError):
    """The store rejected the call due to credentials / IAM."""


class BlobStoreUnavailableError(BlobStoreError):
    """Transient backend failure. Caller may retry."""


class BlobStore(Protocol):
    backend: BlobBackend
    bucket: str

    def put_blob(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> BlobRef: ...

    def get_blob(self, *, key: str) -> bytes: ...

    def delete_blob(self, *, key: str) -> bool: ...

    def presigned_get_url(self, *, key: str, expires_in_seconds: int = 3600) -> str | None: ...

    def presigned_put_url(
        self,
        *,
        key: str,
        content_type: str | None = None,
        expires_in_seconds: int = 3600,
    ) -> str | None: ...


# ── In-memory backend (tests) ────────────────────────────────────────


@dataclass
class _InMemoryEntry:
    content: bytes
    content_type: str | None
    metadata: dict[str, str]
    etag: str


class InMemoryBlobStore:
    """Dict-backed store for tests. Not thread-safe — fine for single-process tests."""

    backend: BlobBackend = "in_memory"

    def __init__(self, *, bucket: str = "in-memory") -> None:
        self.bucket = bucket
        self._entries: dict[str, _InMemoryEntry] = {}

    def put_blob(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> BlobRef:
        etag = hashlib.md5(content, usedforsecurity=False).hexdigest()
        self._entries[key] = _InMemoryEntry(
            content=bytes(content),
            content_type=content_type,
            metadata=dict(metadata or {}),
            etag=etag,
        )
        return BlobRef(
            backend=self.backend,
            bucket=self.bucket,
            key=key,
            content_type=content_type,
            size_bytes=len(content),
            etag=etag,
        )

    def get_blob(self, *, key: str) -> bytes:
        entry = self._entries.get(key)
        if entry is None:
            raise BlobNotFoundError(f"blob not found: {self.uri_for(key)}")
        return entry.content

    def delete_blob(self, *, key: str) -> bool:
        return self._entries.pop(key, None) is not None

    def presigned_get_url(self, *, key: str, expires_in_seconds: int = 3600) -> str | None:
        # In-memory backend has no real URL surface; tests don't need one.
        return None

    def presigned_put_url(
        self,
        *,
        key: str,
        content_type: str | None = None,
        expires_in_seconds: int = 3600,
    ) -> str | None:
        return None

    def uri_for(self, key: str) -> str:
        return f"{self.backend}://{self.bucket}/{key}"


# ── Local filesystem backend (dev) ───────────────────────────────────


class LocalFilesystemBlobStore:
    """Stores blobs as files under a configured root directory.

    Layout: ``<root>/<bucket>/<key>``. Subdirectories are created as
    needed. Useful for ``make dev`` / smoke tests where you want
    persistence across process restarts but no cloud auth.

    Path-traversal guard: any ``key`` that would resolve outside
    ``<root>/<bucket>`` is rejected. Matters because ``key`` may be
    derived from user-supplied filenames.
    """

    backend: BlobBackend = "local"

    def __init__(self, *, bucket: str, root: str | Path) -> None:
        self.bucket = bucket
        self._root = Path(root).resolve()
        self._bucket_root = (self._root / bucket).resolve()
        self._bucket_root.mkdir(parents=True, exist_ok=True)

    def _resolve_key_path(self, key: str) -> Path:
        candidate = (self._bucket_root / key).resolve()
        try:
            candidate.relative_to(self._bucket_root)
        except ValueError as exc:
            raise BlobStoreError(f"local blob key escapes bucket root: {key!r}") from exc
        return candidate

    def put_blob(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> BlobRef:
        path = self._resolve_key_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to tmp then rename. Prevents readers from
        # seeing a half-written file under concurrent put_blob.
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
        etag = hashlib.md5(content, usedforsecurity=False).hexdigest()
        return BlobRef(
            backend=self.backend,
            bucket=self.bucket,
            key=key,
            content_type=content_type,
            size_bytes=len(content),
            etag=etag,
        )

    def get_blob(self, *, key: str) -> bytes:
        path = self._resolve_key_path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise BlobNotFoundError(f"blob not found: local://{self.bucket}/{key}") from exc

    def delete_blob(self, *, key: str) -> bool:
        path = self._resolve_key_path(key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def presigned_get_url(self, *, key: str, expires_in_seconds: int = 3600) -> str | None:
        return None

    def presigned_put_url(
        self,
        *,
        key: str,
        content_type: str | None = None,
        expires_in_seconds: int = 3600,
    ) -> str | None:
        return None


# ── S3 backend (production AWS) ──────────────────────────────────────


class S3BlobStore:
    """boto3-backed S3 blob store.

    Lazy-imports ``boto3`` so the project can run without it when the
    operator selects a different backend. Errors from the AWS SDK are
    translated to the abstract ``BlobStoreError`` hierarchy so callers
    don't have to know about ``ClientError`` / ``BotoCoreError``.

    Constructor accepts an optional ``client`` to inject a mock for
    tests. In production, the client is built lazily on first use.
    """

    backend: BlobBackend = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        region_name: str | None = None,
        client=None,  # type: ignore[no-untyped-def]
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3BlobStore bucket is required")
        self.bucket = bucket
        self._region_name = region_name
        self._client = client

    def _get_client(self):  # type: ignore[no-untyped-def]
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise BlobStoreError(
                "boto3 is required for the S3 blob store backend; install with `pip install ruhu[storage-s3]`"
            ) from exc
        kwargs: dict[str, str] = {}
        if self._region_name:
            kwargs["region_name"] = self._region_name
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def put_blob(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> BlobRef:
        client = self._get_client()
        kwargs: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": content,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        if metadata:
            kwargs["Metadata"] = dict(metadata)
        try:
            response = client.put_object(**kwargs)
        except Exception as exc:
            raise self._translate_error(exc, op="put", key=key) from exc
        etag = (response.get("ETag") or "").strip('"') or None
        return BlobRef(
            backend=self.backend,
            bucket=self.bucket,
            key=key,
            content_type=content_type,
            size_bytes=len(content),
            etag=etag,
        )

    def get_blob(self, *, key: str) -> bytes:
        client = self._get_client()
        try:
            response = client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise self._translate_error(exc, op="get", key=key) from exc
        body = response.get("Body")
        if body is None:
            raise BlobStoreError(f"S3 get_object returned no body for {key}")
        return body.read()

    def delete_blob(self, *, key: str) -> bool:
        client = self._get_client()
        try:
            client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            translated = self._translate_error(exc, op="delete", key=key)
            if isinstance(translated, BlobNotFoundError):
                return False
            raise translated from exc

    def presigned_get_url(self, *, key: str, expires_in_seconds: int = 3600) -> str | None:
        client = self._get_client()
        try:
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in_seconds,
            )
        except Exception as exc:
            logger.warning("s3_presigned_get_url_failed key=%s error=%s", key, exc)
            return None

    def presigned_put_url(
        self,
        *,
        key: str,
        content_type: str | None = None,
        expires_in_seconds: int = 3600,
    ) -> str | None:
        client = self._get_client()
        params: dict[str, object] = {"Bucket": self.bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        try:
            return client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=expires_in_seconds,
            )
        except Exception as exc:
            logger.warning("s3_presigned_put_url_failed key=%s error=%s", key, exc)
            return None

    @staticmethod
    def _translate_error(exc: Exception, *, op: str, key: str) -> BlobStoreError:
        message = f"S3 {op} failed for {key}: {exc}"
        # boto3 raises botocore.exceptions.ClientError with a Code in
        # response['Error']['Code']. We avoid importing botocore here to
        # keep the module lazy — duck-type via .response.
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            error = response.get("Error") or {}
            code = str(error.get("Code") or "")
            if code in {"NoSuchKey", "404", "NotFound"}:
                return BlobNotFoundError(message)
            if code in {"AccessDenied", "Forbidden", "403"}:
                return BlobPermissionDeniedError(message)
            if code in {"SlowDown", "ServiceUnavailable", "InternalError", "503", "500"}:
                return BlobStoreUnavailableError(message)
        return BlobStoreError(message)


# ── GCS backend (production GCP) ─────────────────────────────────────


class GCSBlobStore:
    """google-cloud-storage backed GCP blob store.

    Lazy-imports the SDK. Errors are translated to the abstract
    ``BlobStoreError`` hierarchy.
    """

    backend: BlobBackend = "gcs"

    def __init__(
        self,
        *,
        bucket: str,
        project: str | None = None,
        client=None,  # type: ignore[no-untyped-def]
    ) -> None:
        if not bucket.strip():
            raise ValueError("GCSBlobStore bucket is required")
        self.bucket = bucket
        self._project = project
        self._client = client

    def _get_client(self):  # type: ignore[no-untyped-def]
        if self._client is not None:
            return self._client
        try:
            from google.cloud import storage  # type: ignore[import-not-found]
        except ImportError as exc:
            raise BlobStoreError(
                "google-cloud-storage is required for the GCS blob store backend; "
                "install with `pip install ruhu[storage-gcs]`"
            ) from exc
        kwargs: dict[str, str] = {}
        if self._project:
            kwargs["project"] = self._project
        self._client = storage.Client(**kwargs)
        return self._client

    def _bucket(self):  # type: ignore[no-untyped-def]
        return self._get_client().bucket(self.bucket)

    def put_blob(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> BlobRef:
        try:
            blob = self._bucket().blob(key)
            if metadata:
                blob.metadata = dict(metadata)
            blob.upload_from_string(content, content_type=content_type or "application/octet-stream")
        except Exception as exc:
            raise self._translate_error(exc, op="put", key=key) from exc
        etag = getattr(blob, "etag", None) or getattr(blob, "md5_hash", None)
        return BlobRef(
            backend=self.backend,
            bucket=self.bucket,
            key=key,
            content_type=content_type,
            size_bytes=len(content),
            etag=etag,
        )

    def get_blob(self, *, key: str) -> bytes:
        try:
            blob = self._bucket().blob(key)
            return blob.download_as_bytes()
        except Exception as exc:
            raise self._translate_error(exc, op="get", key=key) from exc

    def delete_blob(self, *, key: str) -> bool:
        try:
            blob = self._bucket().blob(key)
            blob.delete()
            return True
        except Exception as exc:
            translated = self._translate_error(exc, op="delete", key=key)
            if isinstance(translated, BlobNotFoundError):
                return False
            raise translated from exc

    def presigned_get_url(self, *, key: str, expires_in_seconds: int = 3600) -> str | None:
        try:
            from datetime import timedelta

            blob = self._bucket().blob(key)
            return blob.generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=expires_in_seconds),
                method="GET",
            )
        except Exception as exc:
            logger.warning("gcs_presigned_get_url_failed key=%s error=%s", key, exc)
            return None

    def presigned_put_url(
        self,
        *,
        key: str,
        content_type: str | None = None,
        expires_in_seconds: int = 3600,
    ) -> str | None:
        try:
            from datetime import timedelta

            blob = self._bucket().blob(key)
            return blob.generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=expires_in_seconds),
                method="PUT",
                content_type=content_type,
            )
        except Exception as exc:
            logger.warning("gcs_presigned_put_url_failed key=%s error=%s", key, exc)
            return None

    @staticmethod
    def _translate_error(exc: Exception, *, op: str, key: str) -> BlobStoreError:
        message = f"GCS {op} failed for {key}: {exc}"
        # google-cloud-storage raises google.api_core.exceptions.* with
        # a known hierarchy. Duck-type via the class name to avoid
        # importing google.api_core at module top level.
        cls_name = type(exc).__name__
        if cls_name in {"NotFound"}:
            return BlobNotFoundError(message)
        if cls_name in {"Forbidden", "Unauthorized"}:
            return BlobPermissionDeniedError(message)
        if cls_name in {"ServiceUnavailable", "InternalServerError", "GatewayTimeout"}:
            return BlobStoreUnavailableError(message)
        return BlobStoreError(message)


# ── Cleanup helper for tests / dev ───────────────────────────────────


def cleanup_local_root(root: str | Path) -> None:
    """Recursively remove a local-filesystem blob root.

    Provided as a public helper so tests can clean up between cases
    without reaching into private state.
    """
    target = Path(root)
    if target.exists():
        shutil.rmtree(target)


# ── Factory ─────────────────────────────────────────────────────────


def build_blob_store(
    *,
    backend: BlobBackend,
    bucket: str | None = None,
    region_name: str | None = None,
    project: str | None = None,
    local_root: str | Path | None = None,
) -> BlobStore:
    """Construct a blob store from explicit arguments.

    The caller is expected to read the relevant fields off
    ``RuntimeSettings`` (or equivalent config) and pass them in. This
    keeps the module free of coupling to the ``RuntimeSettings`` shape.

    Selection rules:
      * ``in_memory`` → no external state required
      * ``local`` → requires ``local_root``; ``bucket`` defaults to ``"default"``
      * ``s3`` → requires ``bucket``; ``region_name`` optional
      * ``gcs`` → requires ``bucket``; ``project`` optional
    """
    if backend == "in_memory":
        return InMemoryBlobStore(bucket=bucket or "in-memory")
    if backend == "local":
        if local_root is None:
            raise ValueError("local backend requires local_root")
        return LocalFilesystemBlobStore(bucket=bucket or "default", root=local_root)
    if backend == "s3":
        if not bucket:
            raise ValueError("s3 backend requires bucket")
        return S3BlobStore(bucket=bucket, region_name=region_name)
    if backend == "gcs":
        if not bucket:
            raise ValueError("gcs backend requires bucket")
        return GCSBlobStore(bucket=bucket, project=project)
    raise ValueError(f"unknown blob backend: {backend!r}")


def build_blob_store_from_settings(settings) -> BlobStore:  # type: ignore[no-untyped-def]
    """Construct a blob store from ``RuntimeSettings``.

    Reads ``settings.blob_store_*`` fields and dispatches to
    ``build_blob_store``. Kept as a separate helper so the core module
    has no import dependency on ``runtime_config``.
    """
    backend_raw = (settings.blob_store_backend or "in_memory").strip().lower()
    if backend_raw not in {"s3", "gcs", "local", "in_memory"}:
        raise ValueError(
            f"RUHU_BLOB_STORE_BACKEND must be one of: s3, gcs, local, in_memory "
            f"(got {settings.blob_store_backend!r})"
        )
    return build_blob_store(
        backend=backend_raw,  # type: ignore[arg-type]
        bucket=settings.blob_store_bucket,
        region_name=settings.blob_store_s3_region,
        project=settings.blob_store_gcs_project,
        local_root=settings.blob_store_local_root,
    )
