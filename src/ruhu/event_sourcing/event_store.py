"""Event Store: Append-only log for event sourcing.

The event store is the source of truth. All state changes are captured as events.
Events are immutable and can be replayed to reconstruct any past state.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ruhu.db_sqlmodel import DomainEvent


class EventStore:
    """Append-only event log for event sourcing."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def append(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict,
        organization_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        version: int = 1,
    ) -> DomainEvent:
        """Append an event to the log.

        Args:
            event_type: Event class name (e.g., 'GoalDefinitionCreated')
            aggregate_type: Root aggregate type (e.g., 'GoalDefinition')
            aggregate_id: Aggregate instance ID
            payload: Event payload (JSON-serializable dict)
            organization_id: Tenant/organization ID
            causation_id: ID of event that caused this
            correlation_id: Correlation ID for tracing
            version: Schema version of the event

        Returns:
            The created DomainEvent record
        """
        event = DomainEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            organization_id=organization_id,
            causation_id=causation_id,
            correlation_id=correlation_id,
            version=version,
            timestamp=datetime.now(timezone.utc),
        )
        self.session.add(event)
        return event

    async def get_events_for_aggregate(
        self,
        aggregate_type: str,
        aggregate_id: str,
    ) -> list[DomainEvent]:
        """Get all events for an aggregate (timeline reconstruction)."""
        statement = (
            select(DomainEvent)
            .where(
                DomainEvent.aggregate_type == aggregate_type,
                DomainEvent.aggregate_id == aggregate_id,
            )
            .order_by(DomainEvent.timestamp.asc())
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def commit(self) -> None:
        """Commit all appended events."""
        await self.session.commit()
