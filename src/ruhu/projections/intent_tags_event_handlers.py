"""Event handlers for Intent Tags projections."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ruhu.db_sqlmodel import DomainEvent
from ruhu.projections.intent_tags_projection import IntentAnalyticsProjection


class IntentTagsEventHandler:
    """Handles intent tags domain events."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def handle_intent_definition_created(self, event: DomainEvent) -> None:
        """Handle: IntentDefinitionCreated event."""
        payload = event.payload
        projection = IntentAnalyticsProjection(
            intent_definition_id=payload["intent_definition_id"],
            organization_id=payload["organization_id"],
            taxonomy_version_id=payload.get("taxonomy_version_id"),
            name=payload.get("name", ""),
            display_name=payload.get("display_name", ""),
            usage_count=0,
            is_active=True,
            updated_at=event.timestamp,
        )
        self.session.add(projection)

    async def handle_intent_definition_updated(self, event: DomainEvent) -> None:
        """Handle: IntentDefinitionUpdated event."""
        payload = event.payload
        intent_id = payload["intent_definition_id"]

        statement = select(IntentAnalyticsProjection).where(
            IntentAnalyticsProjection.intent_definition_id == intent_id
        )
        result = await self.session.execute(statement)
        projection = result.scalar_one_or_none()

        if projection:
            changes = payload.get("changes", {})
            if "status" in changes:
                projection.is_active = changes["status"] == "active"
            if "is_deprecated" in changes:
                projection.is_deprecated = changes["is_deprecated"]
            projection.updated_at = event.timestamp

    async def commit(self) -> None:
        """Commit projection updates."""
        await self.session.commit()


async def process_intent_tags_event(session: AsyncSession, event: DomainEvent) -> None:
    """Process intent tags event and update projections."""
    handler = IntentTagsEventHandler(session)

    if event.event_type == "IntentDefinitionCreated":
        await handler.handle_intent_definition_created(event)
    elif event.event_type == "IntentDefinitionUpdated":
        await handler.handle_intent_definition_updated(event)

    await handler.commit()
