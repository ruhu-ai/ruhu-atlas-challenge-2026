from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..blob_store import BlobNotFoundError, BlobStore, BlobStoreError
from ..knowledge.extractors import detect_file_kind, extract_knowledge_file
from .models import (
    Artifact,
    AttachmentExtraction,
    AttachmentKind,
    AttachmentProjection,
    AttachmentRef,
    AttachmentUpload,
    AttachmentView,
    utc_now,
)
from .store import AttachmentStore

if TYPE_CHECKING:
    from .producers import GeminiFileUploader, GeminiVisionProducer

logger = logging.getLogger(__name__)


_FILENAME_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9._ -]+")
_ALLOWED_CONTENT_TYPES_BY_KIND: dict[AttachmentKind, set[str]] = {
    "text": {"text/plain"},
    "markdown": {"text/markdown", "text/plain"},
    "json": {"application/json", "text/json"},
    "yaml": {"application/yaml", "text/yaml", "text/plain"},
    "csv": {"text/csv", "application/csv", "text/plain"},
    "html": {"text/html"},
    "xml": {"application/xml", "text/xml"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "pdf": {"application/pdf"},
    "image": {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"},
    "audio": {
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
        "audio/aac",
        "audio/flac",
    },
    "binary": set(),
}


def _kind_from_upload(*, filename: str, content_type: str) -> AttachmentKind:
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("audio/"):
        return "audio"
    detected = detect_file_kind(filename)
    return detected if detected != "binary" else "binary"


def _sanitize_filename(filename: str) -> str:
    candidate = Path(filename.strip()).name
    candidate = _FILENAME_SANITIZE_PATTERN.sub("_", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .")
    if not candidate:
        raise ValueError("filename is invalid")
    if len(candidate) > 255:
        suffix = Path(candidate).suffix[:32]
        stem = Path(candidate).stem[: max(1, 255 - len(suffix))]
        candidate = f"{stem}{suffix}"
    return candidate


def _normalize_content_type(content_type: str, *, filename: str) -> str:
    raw = (content_type or "").split(";", 1)[0].strip().lower()
    if raw:
        return raw
    guessed, _ = mimetypes.guess_type(filename)
    return (guessed or "application/octet-stream").lower()


def _assert_allowed_content_type(*, kind: AttachmentKind, content_type: str, filename: str) -> None:
    if kind == "binary":
        raise ValueError("unsupported attachment type")
    allowed = _ALLOWED_CONTENT_TYPES_BY_KIND.get(kind, set())
    if content_type in allowed or content_type == "application/octet-stream":
        return
    guessed = _normalize_content_type("", filename=filename)
    if guessed in allowed:
        return
    raise ValueError(f"unsupported content type {content_type} for {kind} attachment")


def _kind_policy(*, kind: AttachmentKind, extraction_status: str) -> dict[str, object]:
    placeholder_only = kind in {"image", "audio"}
    content_available = extraction_status == "ready" and not placeholder_only
    return {
        "download_allowed": True,
        "runtime_ready": extraction_status in {"ready", "skipped"},
        "content_available": content_available,
        "placeholder_only": placeholder_only,
        "modality": "visual" if kind == "image" else "audio" if kind == "audio" else "document",
        "ocr_ready": kind == "image" and content_available,
        "transcription_ready": kind == "audio" and content_available,
    }


@dataclass(slots=True)
class AttachmentService:
    store: AttachmentStore
    max_file_bytes: int = 10 * 1024 * 1024
    event_emitter: Callable[..., None] | None = field(default=None)
    # Optional producers for image views.  When set, process_attachment()
    # writes native_file_uri and vision views alongside the existing
    # placeholder extraction.  All view writes are best-effort — failures
    # are logged but do not block the caller.
    file_uploader: "GeminiFileUploader | None" = field(default=None)
    vision_producer: "GeminiVisionProducer | None" = field(default=None)
    # Default retention policy.  When set, upload_attachment() sets
    # retention_expires_at = now + default_retention_days unless the caller
    # provides an explicit retention_expires_at.
    default_retention_days: int | None = field(default=None)
    # Optional object-storage backend. When set, attachment bytes are
    # written to the BlobStore and the attachment row records a
    # ``blob_uri`` of the form ``<backend>://<bucket>/<key>``.  When None,
    # bytes fall through to the legacy ``AttachmentBlobRecord`` path
    # (DB ``LargeBinary`` column).  Reads dispatch on whether ``blob_uri``
    # is populated, so existing rows uploaded before this wiring landed
    # remain readable indefinitely.
    blob_store: BlobStore | None = field(default=None)

    # ── Internal view-write helpers ───────────────────────────────────────────

    def _maybe_write_text_view(
        self,
        attachment: AttachmentUpload,
        text_content: str | None,
    ) -> None:
        """Write a text view for an extracted document.  Best-effort."""
        if not text_content:
            return
        now = utc_now()
        view = AttachmentView(
            attachment_id=attachment.attachment_id,
            conversation_id=attachment.conversation_id,
            organization_id=attachment.organization_id,
            kind="text",
            status="ready",
            content_text=text_content,
            provider="knowledge.extractors",
            created_at=now,
            updated_at=now,
        )
        try:
            self.store.save_view(view)
        except Exception:
            logger.warning(
                "attachment service: text view write failed for %s",
                attachment.attachment_id,
                exc_info=True,
            )

    def _emit_event(
        self,
        *,
        attachment: AttachmentUpload,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        if self.event_emitter is None:
            return
        try:
            self.event_emitter(
                conversation_id=attachment.conversation_id,
                organization_id=attachment.organization_id,
                name=name,
                payload=dict(payload),
            )
        except Exception:
            logger.warning(
                "attachment service: event emitter failed for %s",
                attachment.attachment_id,
                exc_info=True,
            )

    def _maybe_produce_image_views(
        self,
        *,
        attachment: AttachmentUpload,
        blob: bytes,
    ) -> None:
        """Produce native_file_uri and vision views for image attachments.

        Runs the file uploader first (gives a URI for the vision call), then
        the vision producer.  Both are best-effort: a failure in either step
        is logged but does not raise.
        """
        file_uri: str | None = None

        if self.file_uploader is not None:
            now = utc_now()
            uri_view = AttachmentView(
                attachment_id=attachment.attachment_id,
                conversation_id=attachment.conversation_id,
                organization_id=attachment.organization_id,
                kind="native_file_uri",
                status="processing",
                created_at=now,
                updated_at=now,
            )
            try:
                file_uri = self.file_uploader.upload(
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    content_bytes=blob,
                )
                uri_view = uri_view.model_copy(
                    update={"status": "ready", "content_text": file_uri, "updated_at": utc_now()}
                )
            except Exception:
                uri_view = uri_view.model_copy(
                    update={
                        "status": "failed",
                        "error_code": "upload_error",
                        "updated_at": utc_now(),
                    }
                )
                logger.warning(
                    "attachment service: native_file_uri upload failed for %s",
                    attachment.attachment_id,
                    exc_info=True,
                )
            try:
                self.store.save_view(uri_view)
            except Exception:
                logger.warning(
                    "attachment service: native_file_uri view write failed for %s",
                    attachment.attachment_id,
                    exc_info=True,
                )

        if self.vision_producer is not None:
            now = utc_now()
            vision_view = AttachmentView(
                attachment_id=attachment.attachment_id,
                conversation_id=attachment.conversation_id,
                organization_id=attachment.organization_id,
                kind="vision",
                status="processing",
                created_at=now,
                updated_at=now,
            )
            try:
                description = self.vision_producer.describe(
                    file_uri=file_uri,
                    content_bytes=blob if file_uri is None else None,
                    content_type=attachment.content_type,
                )
                vision_view = vision_view.model_copy(
                    update={"status": "ready", "content_text": description, "updated_at": utc_now()}
                )
            except Exception:
                vision_view = vision_view.model_copy(
                    update={
                        "status": "failed",
                        "error_code": "vision_error",
                        "updated_at": utc_now(),
                    }
                )
                logger.warning(
                    "attachment service: vision description failed for %s",
                    attachment.attachment_id,
                    exc_info=True,
                )
            try:
                self.store.save_view(vision_view)
            except Exception:
                logger.warning(
                    "attachment service: vision view write failed for %s",
                    attachment.attachment_id,
                    exc_info=True,
                )

    def upload_attachment(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        channel: str,
        filename: str,
        content_type: str,
        content_bytes: bytes,
        source: str = "public_widget",
        metadata: dict[str, object] | None = None,
        retention_expires_at: datetime | None = None,
    ) -> AttachmentUpload:
        payload = bytes(content_bytes)
        if not filename.strip():
            raise ValueError("filename is required")
        if not payload:
            raise ValueError("attachment content is empty")
        if len(payload) > self.max_file_bytes:
            raise ValueError(f"attachment exceeds limit of {self.max_file_bytes} bytes")
        sanitized_filename = _sanitize_filename(filename)
        normalized_content_type = _normalize_content_type(content_type, filename=sanitized_filename)
        kind = _kind_from_upload(filename=sanitized_filename, content_type=normalized_content_type)
        _assert_allowed_content_type(kind=kind, content_type=normalized_content_type, filename=sanitized_filename)
        now = utc_now()
        # Apply default retention if no explicit value was provided.
        if retention_expires_at is None and self.default_retention_days is not None:
            retention_expires_at = now + timedelta(days=self.default_retention_days)
        attachment_id = AttachmentUpload(
            conversation_id=conversation_id,
            filename=sanitized_filename,
            content_type=normalized_content_type,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        ).attachment_id

        # Decide storage path BEFORE we save any DB rows. If the BlobStore
        # is configured, write bytes there first; on success we then save
        # the attachment row with ``blob_uri`` pointing at the stored
        # object. If BlobStore put fails, raise — we don't want a row
        # claiming "this attachment exists" with no recoverable bytes.
        blob_uri: str | None = None
        if self.blob_store is not None:
            blob_key = _build_blob_key(
                organization_id=organization_id,
                conversation_id=conversation_id,
                attachment_id=attachment_id,
                filename=sanitized_filename,
            )
            try:
                ref = self.blob_store.put_blob(
                    key=blob_key,
                    content=payload,
                    content_type=normalized_content_type,
                    metadata={
                        "attachment_id": attachment_id,
                        "conversation_id": conversation_id,
                        "organization_id": organization_id or "",
                        "channel": channel,
                        "source": source,
                    },
                )
            except BlobStoreError:
                logger.exception(
                    "attachment blob_store put failed; aborting upload",
                    extra={
                        "attachment_id": attachment_id,
                        "conversation_id": conversation_id,
                        "size_bytes": len(payload),
                    },
                )
                raise
            blob_uri = ref.uri()

        attachment = AttachmentUpload(
            attachment_id=attachment_id,
            organization_id=organization_id,
            conversation_id=conversation_id,
            channel=channel,  # type: ignore[arg-type]
            source=source,
            filename=sanitized_filename,
            content_type=normalized_content_type,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            kind=kind,
            retention_expires_at=retention_expires_at,
            metadata={
                **dict(metadata or {}),
                "original_filename": filename,
                "sanitized_filename": sanitized_filename,
            },
            blob_uri=blob_uri,
            created_at=now,
            updated_at=now,
        )
        stored = self.store.save_attachment(attachment)
        # Only write the legacy DB-bytes row when the BlobStore is NOT
        # in use. Otherwise we'd duplicate the bytes (one in S3, one in
        # Postgres) and double our storage bill.
        if blob_uri is None:
            self.store.save_blob(stored.attachment_id, payload)
        return stored

    def load_attachment_bytes(
        self,
        *,
        attachment_id: str,
        organization_id: str | None = None,
    ) -> bytes | None:
        """Load the raw bytes for an attachment.

        Dispatches on ``blob_uri``: when set, reads from the BlobStore;
        otherwise falls back to the legacy ``AttachmentBlobRecord``
        DB-bytes path. Returns None when the attachment row doesn't
        exist OR when the BlobStore says the object is gone.

        Tenant isolation is enforced when ``organization_id`` is given.
        """
        attachment = self.store.get_attachment(
            attachment_id, organization_id=organization_id
        )
        if attachment is None:
            return None
        if attachment.blob_uri:
            if self.blob_store is None:
                # Row claims BlobStore-backed but service has no client to
                # read from — surface as missing rather than corrupt.
                logger.warning(
                    "attachment blob_uri set but no blob_store configured",
                    extra={
                        "attachment_id": attachment_id,
                        "blob_uri": attachment.blob_uri,
                    },
                )
                return None
            key = _parse_blob_key(attachment.blob_uri)
            if key is None:
                logger.warning(
                    "attachment blob_uri could not be parsed",
                    extra={
                        "attachment_id": attachment_id,
                        "blob_uri": attachment.blob_uri,
                    },
                )
                return None
            try:
                return self.blob_store.get_blob(key=key)
            except BlobNotFoundError:
                return None
        return self.store.get_blob(attachment_id)

    def list_conversation_attachments(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
    ) -> list[AttachmentProjection]:
        attachments = self.store.list_attachments(conversation_id, organization_id=organization_id)
        return [
            AttachmentProjection(
                attachment=attachment,
                extraction=self.store.get_extraction(
                    attachment.attachment_id,
                    organization_id=organization_id,
                ),
            )
            for attachment in attachments
        ]

    def get_projection(
        self,
        *,
        attachment_id: str,
        organization_id: str | None,
    ) -> AttachmentProjection | None:
        attachment = self.store.get_attachment(attachment_id, organization_id=organization_id)
        if attachment is None:
            return None
        return AttachmentProjection(
            attachment=attachment,
            extraction=self.store.get_extraction(attachment_id, organization_id=organization_id),
        )

    def get_attachment_bytes(
        self,
        *,
        attachment_id: str,
        organization_id: str | None,
    ) -> tuple[AttachmentUpload, bytes] | None:
        attachment = self.store.get_attachment(attachment_id, organization_id=organization_id)
        if attachment is None:
            return None
        blob = self.load_attachment_bytes(
            attachment_id=attachment_id,
            organization_id=organization_id,
        )
        if blob is None:
            return None
        return attachment, blob

    def get_artifact_bytes(
        self,
        *,
        artifact_id: str,
        organization_id: str | None,
    ) -> tuple[Artifact, bytes] | None:
        artifact = self.store.get_artifact(artifact_id, organization_id=organization_id)
        if artifact is None:
            return None
        blob = self.store.get_artifact_blob(artifact_id)
        if blob is None:
            return None
        return artifact, blob

    def process_attachment(
        self,
        *,
        attachment_id: str,
        organization_id: str | None = None,
    ) -> AttachmentProjection:
        attachment = self.store.get_attachment(attachment_id, organization_id=organization_id)
        if attachment is None:
            raise KeyError(attachment_id)
        blob = self.load_attachment_bytes(
            attachment_id=attachment_id,
            organization_id=organization_id,
        )
        if blob is None:
            raise ValueError("attachment blob is missing")

        now = utc_now()
        attachment = attachment.model_copy(
            update={
                "scan_status": "passed",
                "updated_at": now,
                "message": None,
            }
        )

        if attachment.kind == "image":
            self._emit_event(
                attachment=attachment,
                name="scan_passed",
                payload={
                    "attachment_id": attachment.attachment_id,
                    "kind": attachment.kind,
                    "trust_tier": attachment.trust_tier,
                },
            )
            extraction = AttachmentExtraction(
                attachment_id=attachment.attachment_id,
                organization_id=attachment.organization_id,
                conversation_id=attachment.conversation_id,
                text_content=None,
                summary="Image accepted. OCR and vision extraction are not enabled yet.",
                structured_data={
                    "placeholder_only": True,
                    "modality": "image",
                    "ocr_ready": False,
                },
                metadata={"placeholder": "image_ocr_pending"},
            )
            stored_extraction = self.store.save_extraction(extraction)
            attachment = attachment.model_copy(
                update={
                    "extraction_status": "ready",
                    "updated_at": utc_now(),
                    "message": "Image accepted. OCR and vision extraction are not enabled yet.",
                }
            )
            stored = self.store.save_attachment(attachment)
            # Produce native_file_uri + vision views when producers are configured.
            self._maybe_produce_image_views(attachment=stored, blob=blob)
            self._emit_event(
                attachment=stored,
                name="view_skipped",
                payload={
                    "attachment_id": stored.attachment_id,
                    "view_kind": "text",
                    "reason": "OCR or vision extraction is not enabled yet.",
                },
            )
            return AttachmentProjection(attachment=stored, extraction=stored_extraction)

        if attachment.kind == "audio":
            self._emit_event(
                attachment=attachment,
                name="scan_passed",
                payload={
                    "attachment_id": attachment.attachment_id,
                    "kind": attachment.kind,
                    "trust_tier": attachment.trust_tier,
                },
            )
            extraction = AttachmentExtraction(
                attachment_id=attachment.attachment_id,
                organization_id=attachment.organization_id,
                conversation_id=attachment.conversation_id,
                text_content=None,
                summary="Audio accepted. Automatic transcription is not enabled yet.",
                structured_data={
                    "placeholder_only": True,
                    "modality": "audio",
                    "transcription_ready": False,
                },
                metadata={"placeholder": "audio_transcription_pending"},
            )
            stored_extraction = self.store.save_extraction(extraction)
            attachment = attachment.model_copy(
                update={
                    "extraction_status": "ready",
                    "updated_at": utc_now(),
                    "message": "Audio accepted. Automatic transcription is not enabled yet.",
                }
            )
            stored = self.store.save_attachment(attachment)
            self._emit_event(
                attachment=stored,
                name="view_skipped",
                payload={
                    "attachment_id": stored.attachment_id,
                    "view_kind": "transcript",
                    "reason": "Automatic transcription is not enabled yet.",
                },
            )
            return AttachmentProjection(attachment=stored, extraction=stored_extraction)

        if attachment.kind == "binary":
            attachment = attachment.model_copy(
                update={
                    "extraction_status": "skipped",
                    "updated_at": utc_now(),
                    "message": "Attachment accepted but not runtime-readable yet.",
                }
            )
            stored = self.store.save_attachment(attachment)
            return AttachmentProjection(attachment=stored, extraction=None)

        try:
            extracted = extract_knowledge_file(filename=attachment.filename, file_bytes=blob)
        except Exception as exc:
            attachment = attachment.model_copy(
                update={
                    "extraction_status": "failed",
                    "updated_at": utc_now(),
                    "message": str(exc),
                }
            )
            stored = self.store.save_attachment(attachment)
            self._emit_event(
                attachment=stored,
                name="view_failed",
                payload={
                    "attachment_id": stored.attachment_id,
                    "view_kind": "text",
                    "error_code": "extraction_failed",
                    "error_detail": str(exc),
                },
            )
            return AttachmentProjection(attachment=stored, extraction=None)

        self._emit_event(
            attachment=attachment,
            name="scan_passed",
            payload={
                "attachment_id": attachment.attachment_id,
                "kind": attachment.kind,
                "trust_tier": attachment.trust_tier,
            },
        )
        extraction = AttachmentExtraction(
            attachment_id=attachment.attachment_id,
            organization_id=attachment.organization_id,
            conversation_id=attachment.conversation_id,
            text_content=extracted.content,
            summary=extracted.summary,
            structured_data={},
            metadata=dict(extracted.metadata),
        )
        stored_extraction = self.store.save_extraction(extraction)
        attachment = attachment.model_copy(
            update={
                "extraction_status": "ready",
                "updated_at": utc_now(),
                "message": "Attachment processed.",
            }
        )
        stored = self.store.save_attachment(attachment)
        # Write a text view so the view-ready worker can dispatch it to the kernel.
        self._maybe_write_text_view(stored, stored_extraction.text_content)
        self._emit_event(
            attachment=stored,
            name="view_ready",
            payload={
                "attachment_id": stored.attachment_id,
                "view_kind": "text",
                "provider": "knowledge.extractors",
                "content_length": len(stored_extraction.text_content or ""),
            },
        )
        return AttachmentProjection(attachment=stored, extraction=stored_extraction)

    def materialize_ref(
        self,
        *,
        attachment_id: str,
        organization_id: str | None,
    ) -> AttachmentRef | None:
        projection = self.get_projection(attachment_id=attachment_id, organization_id=organization_id)
        if projection is None:
            return None
        extraction = projection.extraction
        return AttachmentRef(
            attachment_id=projection.attachment.attachment_id,
            kind=projection.attachment.kind,
            source=projection.attachment.source,
            filename=projection.attachment.filename,
            content_type=projection.attachment.content_type,
            scan_status=projection.attachment.scan_status,
            extraction_status=projection.attachment.extraction_status,
            extracted_text=None if extraction is None else extraction.text_content,
            structured_data={} if extraction is None else dict(extraction.structured_data),
            metadata=dict(projection.attachment.metadata),
            policy={
                **_kind_policy(
                    kind=projection.attachment.kind,
                    extraction_status=projection.attachment.extraction_status,
                ),
                "download_allowed": projection.attachment.scan_status == "passed",
                "runtime_ready": projection.attachment.scan_status == "passed"
                and projection.attachment.extraction_status in {"ready", "skipped"},
            },
        )

    def create_artifact(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        filename: str,
        content_type: str,
        content_bytes: bytes,
        kind: str = "other",
        task_id: str | None = None,
        source_attachment_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Artifact:
        payload = bytes(content_bytes)
        if not filename.strip():
            raise ValueError("filename is required")
        if not payload:
            raise ValueError("artifact content is empty")
        if len(payload) > self.max_file_bytes:
            raise ValueError(f"artifact exceeds limit of {self.max_file_bytes} bytes")
        sanitized_filename = _sanitize_filename(filename)
        normalized_content_type = _normalize_content_type(content_type, filename=sanitized_filename)
        artifact = Artifact(
            organization_id=organization_id,
            conversation_id=conversation_id,
            source_attachment_id=source_attachment_id,
            task_id=task_id,
            kind=kind,  # type: ignore[arg-type]
            filename=sanitized_filename,
            content_type=normalized_content_type,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            metadata={
                **dict(metadata or {}),
                "original_filename": filename,
                "sanitized_filename": sanitized_filename,
            },
        )
        stored = self.store.save_artifact(artifact)
        self.store.save_artifact_blob(stored.artifact_id, payload)
        if self.event_emitter is not None:
            try:
                self.event_emitter(
                    conversation_id=stored.conversation_id,
                    organization_id=stored.organization_id,
                    name="artifact.ready",
                    payload={
                        "artifact_id": stored.artifact_id,
                        "kind": stored.kind,
                        "filename": stored.filename,
                    },
                )
            except Exception:
                logger.warning(
                    "attachment service: event emitter failed for artifact %s",
                    stored.artifact_id,
                    exc_info=True,
                )
        return stored


# ── BlobStore key + URI helpers ──────────────────────────────────────


def _build_blob_key(
    *,
    organization_id: str | None,
    conversation_id: str,
    attachment_id: str,
    filename: str,
) -> str:
    """Build the BlobStore key for an attachment.

    Layout: ``<org_id>/<conv_id>/<attachment_id>/<filename>``. Org first
    because S3 + GCS list operations are prefix-scoped — an org-prefix
    is the right boundary for retention, lifecycle, and access policy.
    Anonymous uploads (no org_id) bucket under ``_anon`` so they're
    visibly distinct in audits.
    """
    safe_org = (organization_id or "_anon").strip() or "_anon"
    return f"{safe_org}/{conversation_id}/{attachment_id}/{filename}"


def _parse_blob_key(blob_uri: str) -> str | None:
    """Parse ``<backend>://<bucket>/<key>`` and return ``key`` only.

    The configured BlobStore is the source of truth for backend + bucket
    identity at read time; the URI exists for auditability and to make
    cross-backend mismatches loud at read time. We don't validate the
    backend matches here — let the BlobStore raise BlobNotFoundError
    naturally if it does.
    """
    if not blob_uri:
        return None
    scheme_idx = blob_uri.find("://")
    if scheme_idx < 0:
        return None
    rest = blob_uri[scheme_idx + 3 :]
    bucket_sep = rest.find("/")
    if bucket_sep < 0:
        return None
    return rest[bucket_sep + 1 :] or None
