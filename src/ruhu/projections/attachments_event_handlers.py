"""Event handlers for Attachments projections."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ruhu.db_sqlmodel import DomainEvent
from ruhu.projections.attachments_projection import (
    AttachmentProcessingProjection,
    ConversationAttachmentSummaryProjection,
)


class AttachmentsEventHandler:
    """Handles attachment domain events."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def handle_attachment_uploaded(self, event: DomainEvent) -> None:
        """Handle: AttachmentUploaded event."""
        payload = event.payload

        # Create processing projection
        projection = AttachmentProcessingProjection(
            attachment_id=payload["attachment_id"],
            organization_id=payload["organization_id"],
            conversation_id=payload.get("conversation_id", ""),
            processing_status="pending",
            attachment_type=payload.get("attachment_type", "other"),
            file_size_bytes=payload.get("file_size_bytes", 0),
            uploaded_at=event.timestamp,
        )
        self.session.add(projection)

        # Update conversation summary
        conv_id = payload.get("conversation_id", "")
        if conv_id:
            statement = select(ConversationAttachmentSummaryProjection).where(
                ConversationAttachmentSummaryProjection.conversation_id == conv_id
            )
            result = await self.session.execute(statement)
            summary = result.scalar_one_or_none()

            if not summary:
                summary = ConversationAttachmentSummaryProjection(
                    conversation_id=conv_id,
                    organization_id=payload["organization_id"],
                )
                self.session.add(summary)

            summary.total_attachments += 1
            summary.total_size_bytes += payload.get("file_size_bytes", 0)
            summary.pending_count += 1

    async def handle_attachment_processing_completed(self, event: DomainEvent) -> None:
        """Handle: AttachmentProcessingCompleted event."""
        payload = event.payload
        att_id = payload["attachment_id"]

        statement = select(AttachmentProcessingProjection).where(
            AttachmentProcessingProjection.attachment_id == att_id
        )
        result = await self.session.execute(statement)
        projection = result.scalar_one_or_none()

        if projection:
            old_status = projection.processing_status
            new_status = payload.get("processing_status", "completed")

            projection.processing_status = new_status
            projection.processing_completed_at = event.timestamp

            # Update conversation summary (status transitions)
            if projection.conversation_id:
                statement = select(ConversationAttachmentSummaryProjection).where(
                    ConversationAttachmentSummaryProjection.conversation_id == projection.conversation_id
                )
                result = await self.session.execute(statement)
                summary = result.scalar_one_or_none()

                if summary:
                    # Decrement old status count
                    if old_status == "pending":
                        summary.pending_count = max(0, summary.pending_count - 1)
                    elif old_status == "processing":
                        summary.processing_count = max(0, summary.processing_count - 1)

                    # Increment new status count
                    if new_status == "completed":
                        summary.completed_count += 1
                    elif new_status == "failed":
                        summary.failed_count += 1

    async def commit(self) -> None:
        """Commit projection updates."""
        await self.session.commit()


async def process_attachments_event(session: AsyncSession, event: DomainEvent) -> None:
    """Process attachments event and update projections."""
    handler = AttachmentsEventHandler(session)

    if event.event_type == "AttachmentUploaded":
        await handler.handle_attachment_uploaded(event)
    elif event.event_type == "AttachmentProcessingCompleted":
        await handler.handle_attachment_processing_completed(event)

    await handler.commit()
