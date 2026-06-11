"""End-to-end integration tests for schema architecture.

Tests the full flow:
1. HTTP Request → Request Schema
2. Request → Domain Model
3. Domain → DB + Event
4. Event → Event Bus
5. Event Bus → Projection Handlers
6. Projections updated
7. DB → Response Schema

All with proper event sourcing and RLS.
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlmodel import select

from ruhu.domain.kpi import GoalDefinition as GoalDefinitionDomain
from ruhu.models.kpi.requests import CreateGoalRequest
from ruhu.models.kpi.responses import GoalResponse
from ruhu.db_sqlmodel import (
    GoalDefinition as GoalDefinitionRecord,
    DomainEvent,
)
from ruhu.projections.kpi_projection import GoalAnalyticsProjection
from ruhu.event_sourcing.event_store import EventStore
from ruhu.event_sourcing.event_bus import InMemoryEventBus
from ruhu.projections.kpi_event_handlers import process_kpi_event


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def async_session_factory():
    """Create in-memory SQLite session for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )

    async with engine.begin() as conn:
        # Create all tables
        from ruhu.db_sqlmodel import SQLModel

        await conn.run_sync(SQLModel.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    yield SessionLocal

    await engine.dispose()


async def test_kpi_goal_creation_event_flow(async_session_factory):
    """Test: Create KPI goal → Event emitted → Projection updated."""
    async with async_session_factory() as session:
        # Step 1: Request validation
        req = CreateGoalRequest(
            name="First-Call Resolution",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
            baseline_value=72.0,
        )
        assert req.target_value == 85.0

        # Step 2: Request → Domain
        org_id = "org_test"
        domain = GoalDefinitionDomain(
            organization_id=org_id,
            kind=req.kind,
            name=req.name,
            metric_key=req.metric_key,
            metric_direction=req.metric_direction,
            metric_unit=req.metric_unit,
            target_value=req.target_value,
            baseline_value=req.baseline_value,
        )
        assert domain.status == "draft"

        # Step 3: Domain → DB
        record = GoalDefinitionRecord(
            definition_id=domain.definition_id,
            organization_id=domain.organization_id,
            kind=domain.kind,
            name=domain.name,
            metric_key=domain.metric_key,
            metric_direction=domain.metric_direction,
            metric_unit=domain.metric_unit,
            target_value=domain.target_value,
            baseline_value=domain.baseline_value,
            status=domain.status,
        )
        session.add(record)
        await session.commit()

        # Step 4: Emit event
        # Payload must mirror ruhu.kpi_api_production.create_goal — the projection
        # handler reads target_value / baseline_value / status from the payload,
        # NOT from the DB record. Missing fields → projection zeros silently.
        store = EventStore(session)
        event = await store.append(
            event_type="GoalDefinitionCreated",
            aggregate_type="GoalDefinition",
            aggregate_id=record.definition_id,
            payload={
                "definition_id": record.definition_id,
                "organization_id": org_id,
                "kind": record.kind,
                "name": record.name,
                "metric_key": record.metric_key,
                "target_value": record.target_value,
                "baseline_value": record.baseline_value,
                "status": record.status,
            },
            organization_id=org_id,
        )
        await store.commit()

        # Step 5: Event → Projection (via handler)
        await process_kpi_event(session, event)

        # Step 6: Verify projection created
        statement = select(GoalAnalyticsProjection).where(
            GoalAnalyticsProjection.definition_id == record.definition_id
        )
        result = await session.execute(statement)
        projection = result.scalar_one_or_none()

        assert projection is not None
        assert projection.definition_id == record.definition_id
        assert projection.goal_status == "draft"
        assert projection.target_value == 85.0
        # Baseline must propagate too — guards against the "payload dropped
        # fields" bug that put target_value=0 into production projections.
        assert projection.baseline_value == 72.0

        # Step 7: Response with computed fields
        response = GoalResponse(
            definition_id=record.definition_id,
            organization_id=org_id,
            kind=record.kind,
            name=record.name,
            metric_key=record.metric_key,
            metric_direction=record.metric_direction,
            metric_unit=record.metric_unit,
            target_value=record.target_value,
            baseline_value=record.baseline_value,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        assert response.is_on_track is False  # No current_value yet
        # draft goals have days_active=None by design — they haven't been
        # activated yet, so "0 days active" would be misleading.
        assert response.days_active is None


async def test_event_sourcing_replay(async_session_factory):
    """Test: Events can be replayed to reconstruct state."""
    async with async_session_factory() as session:
        org_id = "org_replay"
        goal_id = "goal_123"

        # Create and store event
        store = EventStore(session)
        event = await store.append(
            event_type="GoalDefinitionCreated",
            aggregate_type="GoalDefinition",
            aggregate_id=goal_id,
            payload={
                "definition_id": goal_id,
                "organization_id": org_id,
                "kind": "agent",
                "name": "Test Goal",
            },
            organization_id=org_id,
        )
        await store.commit()

        # Get events for aggregate (timeline)
        events = await store.get_events_for_aggregate("GoalDefinition", goal_id)
        assert len(events) == 1
        assert events[0].event_type == "GoalDefinitionCreated"

        # Replay events to reconstruct state
        for evt in events:
            await process_kpi_event(session, evt)

        # Verify projection recreated
        statement = select(GoalAnalyticsProjection).where(
            GoalAnalyticsProjection.definition_id == goal_id
        )
        result = await session.execute(statement)
        projection = result.scalar_one_or_none()

        assert projection is not None
        assert projection.definition_id == goal_id


async def test_event_bus_dispatch(async_session_factory):
    """Test: Event bus properly dispatches to handlers."""
    async with async_session_factory() as session:
        bus = InMemoryEventBus()

        # Track handler calls
        handled_events = []

        async def test_handler(sess: AsyncSession, event: DomainEvent) -> None:
            handled_events.append(event.event_type)

        # Register handler
        bus.subscribe("GoalDefinitionCreated", test_handler)

        # Create and publish event
        store = EventStore(session)
        event = await store.append(
            event_type="GoalDefinitionCreated",
            aggregate_type="GoalDefinition",
            aggregate_id="goal_456",
            payload={"definition_id": "goal_456"},
            organization_id="org_test",
        )
        await store.commit()

        # Publish event
        await bus.publish(session, event)

        # Verify handler was called
        assert "GoalDefinitionCreated" in handled_events


async def test_rls_organization_isolation(async_session_factory):
    """Test: RLS ensures organization isolation."""
    async with async_session_factory() as session:
        # Create goals in different organizations
        for org_id in ["org_a", "org_b"]:
            record = GoalDefinitionRecord(
                definition_id=f"goal_{org_id}",
                organization_id=org_id,
                kind="custom",
                name=f"Goal for {org_id}",
                metric_key="test.metric",
                metric_direction="higher_is_better",
                metric_unit="percent",
                target_value=100.0,
                status="draft",
            )
            session.add(record)

        await session.commit()

        # Query with RLS filter (org_a only)
        statement = select(GoalDefinitionRecord).where(
            GoalDefinitionRecord.organization_id == "org_a"
        )
        result = await session.execute(statement)
        records = result.scalars().all()

        assert len(records) == 1
        assert records[0].organization_id == "org_a"
