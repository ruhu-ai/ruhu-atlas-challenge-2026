from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .models import Artifact, AttachmentExtraction, AttachmentUpload, AttachmentView
from .sqlalchemy_models import (
    ArtifactBlobRecord,
    ArtifactRecord,
    AttachmentBlobRecord,
    AttachmentExtractionRecord,
    AttachmentRecord,
    AttachmentViewRecord,
)


class AttachmentStore(Protocol):
    def save_attachment(self, attachment: AttachmentUpload) -> AttachmentUpload: ...

    def get_attachment(
        self,
        attachment_id: str,
        *,
        organization_id: str | None = None,
    ) -> AttachmentUpload | None: ...

    def list_attachments(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AttachmentUpload]: ...

    def save_blob(self, attachment_id: str, content_bytes: bytes) -> None: ...

    def get_blob(self, attachment_id: str) -> bytes | None: ...

    def save_extraction(self, extraction: AttachmentExtraction) -> AttachmentExtraction: ...

    def get_extraction(
        self,
        attachment_id: str,
        *,
        organization_id: str | None = None,
    ) -> AttachmentExtraction | None: ...

    def save_view(self, view: AttachmentView) -> AttachmentView: ...

    def get_view(
        self,
        attachment_id: str,
        kind: str,
        *,
        organization_id: str | None = None,
    ) -> AttachmentView | None: ...

    def list_views(
        self,
        attachment_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AttachmentView]: ...

    def save_artifact(self, artifact: Artifact) -> Artifact: ...

    def get_artifact(
        self,
        artifact_id: str,
        *,
        organization_id: str | None = None,
    ) -> Artifact | None: ...

    def save_artifact_blob(self, artifact_id: str, content_bytes: bytes) -> None: ...

    def get_artifact_blob(self, artifact_id: str) -> bytes | None: ...

    def list_artifacts(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[Artifact]: ...


class InMemoryAttachmentStore:
    def __init__(self) -> None:
        self._attachments: dict[str, AttachmentUpload] = {}
        self._attachment_ids_by_conversation: dict[str, list[str]] = {}
        self._blobs: dict[str, bytes] = {}
        self._extractions: dict[str, AttachmentExtraction] = {}
        # Keyed by (attachment_id, kind) — at most one view per kind per attachment.
        self._views: dict[tuple[str, str], AttachmentView] = {}
        self._artifacts: dict[str, Artifact] = {}
        self._artifact_ids_by_conversation: dict[str, list[str]] = {}
        self._artifact_blobs: dict[str, bytes] = {}

    def save_attachment(self, attachment: AttachmentUpload) -> AttachmentUpload:
        stored = attachment.model_copy(deep=True)
        existing = self._attachments.get(stored.attachment_id)
        self._attachments[stored.attachment_id] = stored
        if existing is None:
            self._attachment_ids_by_conversation.setdefault(stored.conversation_id, []).append(stored.attachment_id)
        return stored.model_copy(deep=True)

    def get_attachment(
        self,
        attachment_id: str,
        *,
        organization_id: str | None = None,
    ) -> AttachmentUpload | None:
        attachment = self._attachments.get(attachment_id)
        if attachment is None:
            return None
        if attachment.deleted_at is not None:
            return None
        if organization_id is not None and attachment.organization_id != organization_id:
            return None
        return attachment.model_copy(deep=True)

    def list_attachments(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AttachmentUpload]:
        result: list[AttachmentUpload] = []
        for attachment_id in self._attachment_ids_by_conversation.get(conversation_id, []):
            attachment = self._attachments[attachment_id]
            if attachment.deleted_at is not None:
                continue
            if organization_id is not None and attachment.organization_id != organization_id:
                continue
            result.append(attachment.model_copy(deep=True))
        result.sort(key=lambda item: (item.created_at, item.attachment_id))
        return result

    def save_blob(self, attachment_id: str, content_bytes: bytes) -> None:
        self._blobs[attachment_id] = bytes(content_bytes)

    def get_blob(self, attachment_id: str) -> bytes | None:
        # Respect soft-delete: return None if the attachment itself is soft-deleted.
        attachment = self._attachments.get(attachment_id)
        if attachment is not None and attachment.deleted_at is not None:
            return None
        content = self._blobs.get(attachment_id)
        return None if content is None else bytes(content)

    def save_extraction(self, extraction: AttachmentExtraction) -> AttachmentExtraction:
        stored = extraction.model_copy(deep=True)
        self._extractions[stored.attachment_id] = stored
        return stored.model_copy(deep=True)

    def get_extraction(
        self,
        attachment_id: str,
        *,
        organization_id: str | None = None,
    ) -> AttachmentExtraction | None:
        extraction = self._extractions.get(attachment_id)
        if extraction is None:
            return None
        if organization_id is not None and extraction.organization_id != organization_id:
            return None
        return extraction.model_copy(deep=True)

    def save_view(self, view: AttachmentView) -> AttachmentView:
        stored = view.model_copy(deep=True)
        self._views[(stored.attachment_id, stored.kind)] = stored
        return stored.model_copy(deep=True)

    def get_view(
        self,
        attachment_id: str,
        kind: str,
        *,
        organization_id: str | None = None,
    ) -> AttachmentView | None:
        view = self._views.get((attachment_id, kind))
        if view is None:
            return None
        if organization_id is not None and view.organization_id != organization_id:
            return None
        return view.model_copy(deep=True)

    def list_views(
        self,
        attachment_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AttachmentView]:
        result: list[AttachmentView] = []
        for (att_id, _kind), view in self._views.items():
            if att_id != attachment_id:
                continue
            if organization_id is not None and view.organization_id != organization_id:
                continue
            result.append(view.model_copy(deep=True))
        return sorted(result, key=lambda v: (v.created_at, v.view_id))

    def save_artifact(self, artifact: Artifact) -> Artifact:
        stored = artifact.model_copy(deep=True)
        existing = self._artifacts.get(stored.artifact_id)
        self._artifacts[stored.artifact_id] = stored
        if existing is None:
            self._artifact_ids_by_conversation.setdefault(stored.conversation_id, []).append(stored.artifact_id)
        return stored.model_copy(deep=True)

    def get_artifact(
        self,
        artifact_id: str,
        *,
        organization_id: str | None = None,
    ) -> Artifact | None:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            return None
        if organization_id is not None and artifact.organization_id != organization_id:
            return None
        return artifact.model_copy(deep=True)

    def save_artifact_blob(self, artifact_id: str, content_bytes: bytes) -> None:
        self._artifact_blobs[artifact_id] = bytes(content_bytes)

    def get_artifact_blob(self, artifact_id: str) -> bytes | None:
        content = self._artifact_blobs.get(artifact_id)
        return None if content is None else bytes(content)

    def list_artifacts(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[Artifact]:
        result: list[Artifact] = []
        for artifact_id in self._artifact_ids_by_conversation.get(conversation_id, []):
            artifact = self._artifacts[artifact_id]
            if organization_id is not None and artifact.organization_id != organization_id:
                continue
            result.append(artifact.model_copy(deep=True))
        result.sort(key=lambda item: (item.created_at, item.artifact_id))
        return result


class SQLAlchemyAttachmentStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_attachment(self, attachment: AttachmentUpload) -> AttachmentUpload:
        with self._session_factory() as session:
            record = session.get(AttachmentRecord, attachment.attachment_id)
            if record is None:
                session.add(_attachment_to_record(attachment))
            else:
                record.organization_id = attachment.organization_id
                record.conversation_id = attachment.conversation_id
                record.channel = attachment.channel
                record.source = attachment.source
                record.filename = attachment.filename
                record.content_type = attachment.content_type
                record.size_bytes = attachment.size_bytes
                record.sha256 = attachment.sha256
                record.kind = attachment.kind
                record.scan_status = attachment.scan_status
                record.extraction_status = attachment.extraction_status
                record.trust_tier = attachment.trust_tier
                record.retention_expires_at = attachment.retention_expires_at
                record.deleted_at = attachment.deleted_at
                record.message = attachment.message
                record.metadata_json = dict(attachment.metadata)
                record.blob_uri = attachment.blob_uri
                record.created_at = attachment.created_at
                record.updated_at = attachment.updated_at
            session.commit()
        return attachment.model_copy(deep=True)

    def get_attachment(
        self,
        attachment_id: str,
        *,
        organization_id: str | None = None,
    ) -> AttachmentUpload | None:
        with self._session_factory() as session:
            record = session.get(AttachmentRecord, attachment_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_attachment(record)

    def list_attachments(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AttachmentUpload]:
        statement = (
            select(AttachmentRecord)
            .where(AttachmentRecord.conversation_id == conversation_id)
            .order_by(AttachmentRecord.created_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(AttachmentRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_attachment(record) for record in records]

    def save_blob(self, attachment_id: str, content_bytes: bytes) -> None:
        with self._session_factory() as session:
            record = session.get(AttachmentBlobRecord, attachment_id)
            if record is None:
                session.add(AttachmentBlobRecord(attachment_id=attachment_id, content_bytes=content_bytes))
            else:
                record.content_bytes = content_bytes
            session.commit()

    def get_blob(self, attachment_id: str) -> bytes | None:
        with self._session_factory() as session:
            record = session.get(AttachmentBlobRecord, attachment_id)
            return None if record is None else bytes(record.content_bytes)

    def save_extraction(self, extraction: AttachmentExtraction) -> AttachmentExtraction:
        with self._session_factory() as session:
            statement = select(AttachmentExtractionRecord).where(
                AttachmentExtractionRecord.attachment_id == extraction.attachment_id
            )
            record = session.execute(statement).scalar_one_or_none()
            if record is None:
                session.add(_extraction_to_record(extraction))
            else:
                record.organization_id = extraction.organization_id
                record.conversation_id = extraction.conversation_id
                record.text_content = extraction.text_content
                record.summary = extraction.summary
                record.structured_data_json = dict(extraction.structured_data)
                record.metadata_json = dict(extraction.metadata)
                record.created_at = extraction.created_at
                record.updated_at = extraction.updated_at
            session.commit()
        return extraction.model_copy(deep=True)

    def get_extraction(
        self,
        attachment_id: str,
        *,
        organization_id: str | None = None,
    ) -> AttachmentExtraction | None:
        statement = select(AttachmentExtractionRecord).where(AttachmentExtractionRecord.attachment_id == attachment_id)
        if organization_id is not None:
            statement = statement.where(AttachmentExtractionRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
        return None if record is None else _record_to_extraction(record)

    def save_view(self, view: AttachmentView) -> AttachmentView:
        with self._session_factory() as session:
            stmt = select(AttachmentViewRecord).where(
                AttachmentViewRecord.attachment_id == view.attachment_id,
                AttachmentViewRecord.kind == view.kind,
            )
            record = session.execute(stmt).scalar_one_or_none()
            if record is None:
                session.add(_view_to_record(view))
            else:
                record.status = view.status
                record.content_text = view.content_text
                record.content_json = dict(view.content_json)
                record.metadata_json = dict(view.metadata)
                record.provider = view.provider
                record.error_code = view.error_code
                record.error_detail = view.error_detail
                record.updated_at = view.updated_at
            session.commit()
        return view.model_copy(deep=True)

    def get_view(
        self,
        attachment_id: str,
        kind: str,
        *,
        organization_id: str | None = None,
    ) -> AttachmentView | None:
        stmt = select(AttachmentViewRecord).where(
            AttachmentViewRecord.attachment_id == attachment_id,
            AttachmentViewRecord.kind == kind,
        )
        if organization_id is not None:
            stmt = stmt.where(AttachmentViewRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(stmt).scalar_one_or_none()
        return None if record is None else _record_to_view(record)

    def list_views(
        self,
        attachment_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AttachmentView]:
        stmt = (
            select(AttachmentViewRecord)
            .where(AttachmentViewRecord.attachment_id == attachment_id)
            .order_by(AttachmentViewRecord.created_at.asc())
        )
        if organization_id is not None:
            stmt = stmt.where(AttachmentViewRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(stmt).scalars().all()
        return [_record_to_view(r) for r in records]

    def save_artifact(self, artifact: Artifact) -> Artifact:
        with self._session_factory() as session:
            record = session.get(ArtifactRecord, artifact.artifact_id)
            if record is None:
                session.add(_artifact_to_record(artifact))
            else:
                record.organization_id = artifact.organization_id
                record.conversation_id = artifact.conversation_id
                record.source_attachment_id = artifact.source_attachment_id
                record.task_id = artifact.task_id
                record.kind = artifact.kind
                record.filename = artifact.filename
                record.content_type = artifact.content_type
                record.size_bytes = artifact.size_bytes
                record.sha256 = artifact.sha256
                record.metadata_json = dict(artifact.metadata)
                record.created_at = artifact.created_at
            session.commit()
        return artifact.model_copy(deep=True)

    def get_artifact(
        self,
        artifact_id: str,
        *,
        organization_id: str | None = None,
    ) -> Artifact | None:
        with self._session_factory() as session:
            record = session.get(ArtifactRecord, artifact_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_artifact(record)

    def save_artifact_blob(self, artifact_id: str, content_bytes: bytes) -> None:
        with self._session_factory() as session:
            record = session.get(ArtifactBlobRecord, artifact_id)
            if record is None:
                session.add(ArtifactBlobRecord(artifact_id=artifact_id, content_bytes=content_bytes))
            else:
                record.content_bytes = content_bytes
            session.commit()

    def get_artifact_blob(self, artifact_id: str) -> bytes | None:
        with self._session_factory() as session:
            record = session.get(ArtifactBlobRecord, artifact_id)
            return None if record is None else bytes(record.content_bytes)

    def list_artifacts(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[Artifact]:
        statement = (
            select(ArtifactRecord)
            .where(ArtifactRecord.conversation_id == conversation_id)
            .order_by(ArtifactRecord.created_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(ArtifactRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_artifact(record) for record in records]


def _attachment_to_record(attachment: AttachmentUpload) -> AttachmentRecord:
    return AttachmentRecord(
        attachment_id=attachment.attachment_id,
        organization_id=attachment.organization_id,
        conversation_id=attachment.conversation_id,
        channel=attachment.channel,
        source=attachment.source,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        sha256=attachment.sha256,
        kind=attachment.kind,
        scan_status=attachment.scan_status,
        extraction_status=attachment.extraction_status,
        trust_tier=attachment.trust_tier,
        retention_expires_at=attachment.retention_expires_at,
        deleted_at=attachment.deleted_at,
        message=attachment.message,
        metadata_json=dict(attachment.metadata),
        blob_uri=attachment.blob_uri,
        created_at=attachment.created_at,
        updated_at=attachment.updated_at,
    )


def _record_to_attachment(record: AttachmentRecord) -> AttachmentUpload:
    return AttachmentUpload.model_validate(
        {
            "attachment_id": record.attachment_id,
            "organization_id": record.organization_id,
            "conversation_id": record.conversation_id,
            "channel": record.channel,
            "source": record.source,
            "filename": record.filename,
            "content_type": record.content_type,
            "size_bytes": record.size_bytes,
            "sha256": record.sha256,
            "kind": record.kind,
            "scan_status": record.scan_status,
            "extraction_status": record.extraction_status,
            "trust_tier": record.trust_tier,
            "retention_expires_at": record.retention_expires_at,
            "deleted_at": record.deleted_at,
            "message": record.message,
            "blob_uri": record.blob_uri,
            "metadata": dict(record.metadata_json or {}),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


def _extraction_to_record(extraction: AttachmentExtraction) -> AttachmentExtractionRecord:
    return AttachmentExtractionRecord(
        extraction_id=extraction.extraction_id,
        attachment_id=extraction.attachment_id,
        organization_id=extraction.organization_id,
        conversation_id=extraction.conversation_id,
        text_content=extraction.text_content,
        summary=extraction.summary,
        structured_data_json=dict(extraction.structured_data),
        metadata_json=dict(extraction.metadata),
        created_at=extraction.created_at,
        updated_at=extraction.updated_at,
    )


def _record_to_extraction(record: AttachmentExtractionRecord) -> AttachmentExtraction:
    return AttachmentExtraction.model_validate(
        {
            "extraction_id": record.extraction_id,
            "attachment_id": record.attachment_id,
            "organization_id": record.organization_id,
            "conversation_id": record.conversation_id,
            "text_content": record.text_content,
            "summary": record.summary,
            "structured_data": dict(record.structured_data_json or {}),
            "metadata": dict(record.metadata_json or {}),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


def _view_to_record(view: AttachmentView) -> AttachmentViewRecord:
    return AttachmentViewRecord(
        view_id=view.view_id,
        attachment_id=view.attachment_id,
        organization_id=view.organization_id,
        conversation_id=view.conversation_id,
        kind=view.kind,
        status=view.status,
        content_text=view.content_text,
        content_json=dict(view.content_json),
        metadata_json=dict(view.metadata),
        provider=view.provider,
        error_code=view.error_code,
        error_detail=view.error_detail,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _record_to_view(record: AttachmentViewRecord) -> AttachmentView:
    return AttachmentView.model_validate(
        {
            "view_id": record.view_id,
            "attachment_id": record.attachment_id,
            "organization_id": record.organization_id,
            "conversation_id": record.conversation_id,
            "kind": record.kind,
            "status": record.status,
            "content_text": record.content_text,
            "content_json": dict(record.content_json or {}),
            "metadata": dict(record.metadata_json or {}),
            "provider": record.provider,
            "error_code": record.error_code,
            "error_detail": record.error_detail,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


def _artifact_to_record(artifact: Artifact) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact.artifact_id,
        organization_id=artifact.organization_id,
        conversation_id=artifact.conversation_id,
        source_attachment_id=artifact.source_attachment_id,
        task_id=artifact.task_id,
        kind=artifact.kind,
        filename=artifact.filename,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        metadata_json=dict(artifact.metadata),
        created_at=artifact.created_at,
    )


def _record_to_artifact(record: ArtifactRecord) -> Artifact:
    return Artifact.model_validate(
        {
            "artifact_id": record.artifact_id,
            "organization_id": record.organization_id,
            "conversation_id": record.conversation_id,
            "source_attachment_id": record.source_attachment_id,
            "task_id": record.task_id,
            "kind": record.kind,
            "filename": record.filename,
            "content_type": record.content_type,
            "size_bytes": record.size_bytes,
            "sha256": record.sha256,
            "metadata": dict(record.metadata_json or {}),
            "created_at": record.created_at,
        }
    )
