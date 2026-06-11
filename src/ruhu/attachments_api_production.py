"""Attachments API routes. Production-ready integration of all schema layers.

Pattern:
1. Request schema validates input (Pydantic coercion OK here)
2. Request → Domain conversion (business logic validation)
3. Domain → DB conversion (persistence)
4. DB → Response conversion (include computed fields from projection)
5. Return response schema
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ruhu.db import get_async_session
from ruhu.db_sqlmodel import Attachment as AttachmentRecord
from ruhu.domain.attachments import (
    Attachment as AttachmentDomain,
    AttachmentUploaded,
    AttachmentProcessingStarted,
    AttachmentProcessingCompleted,
)
from ruhu.models.attachments.requests import (
    UploadAttachmentRequest,
    UpdateAttachmentRequest,
)
from ruhu.models.attachments.responses import (
    AttachmentResponse,
    AttachmentListResponse,
    AttachmentUploadResponse,
)
from ruhu.api_auth import get_request_auth_context, RequestAuthContext

router = APIRouter(prefix="/attachments", tags=["attachments"])


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Conversion Helpers
# ─────────────────────────────────────────────────────────────────────


def request_to_domain(req: UploadAttachmentRequest, org_id: str) -> AttachmentDomain:
    """Convert API request to domain model."""
    storage_key = f"s3://ruhu-attachments/{org_id}/{req.conversation_id}/{str(uuid4())}-{req.filename}"
    attachment_type = req.attachment_type or _infer_type(req.mime_type)

    return AttachmentDomain(
        organization_id=org_id,
        conversation_id=req.conversation_id,
        filename=req.filename,
        file_size_bytes=req.file_size_bytes,
        mime_type=req.mime_type,
        attachment_type=attachment_type,  # type: ignore
        storage_key=storage_key,
    )


def domain_to_db(domain: AttachmentDomain) -> AttachmentRecord:
    """Convert domain model to DB record."""
    return AttachmentRecord(
        attachment_id=domain.attachment_id,
        organization_id=domain.organization_id,
        conversation_id=domain.conversation_id,
        filename=domain.filename,
        file_size_bytes=domain.file_size_bytes,
        mime_type=domain.mime_type,
        attachment_type=domain.attachment_type,
        storage_key=domain.storage_key,
        processing_status=domain.processing_status,
        content_type=domain.content_type,
        extracted_text=domain.extracted_text,
        extracted_metadata=domain.extracted_metadata,
        processing_error=domain.processing_error,
        uploaded_by=domain.uploaded_by,
        uploaded_at=domain.uploaded_at,
        processing_started_at=domain.processing_started_at,
        processing_completed_at=domain.processing_completed_at,
    )


def db_to_domain(record: AttachmentRecord) -> AttachmentDomain:
    """Convert DB record to domain model."""
    return AttachmentDomain(
        attachment_id=record.attachment_id,
        organization_id=record.organization_id,
        conversation_id=record.conversation_id,
        filename=record.filename,
        file_size_bytes=record.file_size_bytes,
        mime_type=record.mime_type,
        attachment_type=record.attachment_type,  # type: ignore
        storage_key=record.storage_key,
        processing_status=record.processing_status,  # type: ignore
        content_type=record.content_type,  # type: ignore
        extracted_text=record.extracted_text,
        extracted_metadata=record.extracted_metadata,
        processing_error=record.processing_error,
        uploaded_by=record.uploaded_by,
        uploaded_at=record.uploaded_at,
        processing_started_at=record.processing_started_at,
        processing_completed_at=record.processing_completed_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def domain_to_response(domain: AttachmentDomain) -> AttachmentResponse:
    """Convert domain model to API response."""
    return AttachmentResponse(
        attachment_id=domain.attachment_id,
        organization_id=domain.organization_id,
        conversation_id=domain.conversation_id,
        filename=domain.filename,
        file_size_bytes=domain.file_size_bytes,
        mime_type=domain.mime_type,
        attachment_type=domain.attachment_type,
        processing_status=domain.processing_status,
        content_type=domain.content_type,
        extracted_text=domain.extracted_text,
        extracted_metadata=domain.extracted_metadata,
        processing_error=domain.processing_error,
        uploaded_by=domain.uploaded_by,
        uploaded_at=domain.uploaded_at,
        processing_started_at=domain.processing_started_at,
        processing_completed_at=domain.processing_completed_at,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
    )


def _infer_type(mime_type: str) -> str:
    """Infer attachment type from MIME type."""
    if mime_type.startswith("image/"):
        return "image"
    elif mime_type.startswith("audio/"):
        return "audio"
    elif mime_type.startswith("video/"):
        return "video"
    elif mime_type in ("application/pdf", "application/msword", "text/plain"):
        return "document"
    else:
        return "other"


async def emit_event(
    session: AsyncSession,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
    organization_id: Optional[str] = None,
) -> None:
    """Emit a domain event via event store and bus."""
    from ruhu.event_sourcing.event_store import EventStore
    from ruhu.event_sourcing.event_bus import get_event_bus

    try:
        store = EventStore(session)
        event = await store.append(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            organization_id=organization_id,
            version=1,
        )
        await store.commit()

        bus = get_event_bus()
        await bus.publish(session, event)

    except Exception:
        import logging

        logger = logging.getLogger(__name__)
        logger.exception(f"Failed to emit event {event_type}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────
# Dependencies
# ─────────────────────────────────────────────────────────────────────


async def get_request_org_id(request: Request) -> str:
    """Extract organization ID from JWT token via auth context.

    Requires authenticated request (Bearer token in Authorization header).
    Applies row-level security (RLS) by scoping queries to org_id from JWT.
    """
    context: RequestAuthContext = get_request_auth_context(request)
    if context.principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return context.principal.organization.organization_id


async def get_current_user_id(request: Request) -> str:
    """Extract user ID from JWT token via auth context."""
    context: RequestAuthContext = get_request_auth_context(request)
    if context.principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return context.principal.user.user_id


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────


@router.post("", response_model=AttachmentUploadResponse, status_code=201)
async def upload_attachment(
    req: UploadAttachmentRequest,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
    user_id: str = Depends(get_current_user_id),
) -> AttachmentUploadResponse:
    """Create attachment record and return upload URL."""
    try:
        domain = request_to_domain(req, org_id)
        domain.uploaded_by = user_id
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    record = domain_to_db(domain)
    session.add(record)
    await session.commit()
    await session.refresh(record)

    await emit_event(
        session=session,
        event_type="AttachmentUploaded",
        aggregate_type="Attachment",
        aggregate_id=record.attachment_id,
        payload={
            "attachment_id": record.attachment_id,
            "filename": record.filename,
            "file_size_bytes": record.file_size_bytes,
        },
        organization_id=org_id,
    )

    return AttachmentUploadResponse(
        attachment_id=record.attachment_id,
        storage_key=record.storage_key,
        upload_url=f"https://upload.example.com/presigned?key={record.storage_key}",
        expires_in_seconds=3600,
    )


@router.get("/{attachment_id}", response_model=AttachmentResponse)
async def get_attachment(
    attachment_id: str,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> AttachmentResponse:
    """Get a single attachment by ID."""
    statement = select(AttachmentRecord).where(
        AttachmentRecord.attachment_id == attachment_id,
        AttachmentRecord.organization_id == org_id,
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=404, detail="Attachment not found")

    return domain_to_response(db_to_domain(record))


@router.patch("/{attachment_id}", response_model=AttachmentResponse)
async def update_attachment(
    attachment_id: str,
    req: UpdateAttachmentRequest,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> AttachmentResponse:
    """Update attachment processing status (patch semantics)."""
    statement = select(AttachmentRecord).where(
        AttachmentRecord.attachment_id == attachment_id,
        AttachmentRecord.organization_id == org_id,
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=404, detail="Attachment not found")

    update_data = req.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if hasattr(record, key):
            setattr(record, key, value)

    record.updated_at = utcnow()

    session.add(record)
    await session.commit()
    await session.refresh(record)

    await emit_event(
        session=session,
        event_type="AttachmentProcessingCompleted",
        aggregate_type="Attachment",
        aggregate_id=attachment_id,
        payload={
            "attachment_id": attachment_id,
            "processing_status": record.processing_status,
        },
        organization_id=org_id,
    )

    return domain_to_response(db_to_domain(record))


@router.get("/conversations/{conversation_id}/attachments", response_model=AttachmentListResponse)
async def list_conversation_attachments(
    conversation_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> AttachmentListResponse:
    """List attachments for a conversation."""
    count_stmt = select(func.count(AttachmentRecord.attachment_id)).where(
        AttachmentRecord.organization_id == org_id,
        AttachmentRecord.conversation_id == conversation_id,
    )
    count_result = await session.execute(count_stmt)
    total = count_result.scalar() or 0

    statement = (
        select(AttachmentRecord)
        .where(
            AttachmentRecord.organization_id == org_id,
            AttachmentRecord.conversation_id == conversation_id,
        )
        .order_by(AttachmentRecord.uploaded_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page + 1)
    )

    result = await session.execute(statement)
    records = result.scalars().all()

    has_more = len(records) > per_page
    records = records[:per_page]

    attachments = [domain_to_response(db_to_domain(r)) for r in records]

    return AttachmentListResponse(
        attachments=attachments,
        total=total,
        page=page,
        per_page=per_page,
        has_more=has_more,
    )
