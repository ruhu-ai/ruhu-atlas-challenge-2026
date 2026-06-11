#!/usr/bin/env python
"""Standalone test runner for schema integration tests."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlmodel import select

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ruhu.domain.kpi import GoalDefinition as GoalDefinitionDomain
from ruhu.models.kpi.requests import CreateGoalRequest
from ruhu.models.kpi.responses import GoalResponse
from ruhu.db_sqlmodel import (
    GoalDefinition as GoalDefinitionRecord,
    DomainEvent,
    SQLModel,
)
from ruhu.projections.kpi_projection import GoalAnalyticsProjection
from ruhu.event_sourcing.event_store import EventStore
from ruhu.event_sourcing.event_bus import InMemoryEventBus
from ruhu.projections.kpi_event_handlers import process_kpi_event


async def setup_db():
    """Create in-memory SQLite session for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return SessionLocal, engine


async def test_kpi_goal_creation_event_flow():
    """Test: Create KPI goal → Event emitted → Projection updated."""
    print("\n=== Test: KPI Goal Creation Event Flow ===")

    SessionLocal, engine = await setup_db()

    try:
        async with SessionLocal() as session:
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
            print("✓ Request validation passed")

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
            print("✓ Domain model created")

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
            print("✓ Goal record persisted to DB")

            # Step 4: Emit event
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
                    "metric_direction": record.metric_direction,
                    "metric_unit": record.metric_unit,
                    "target_value": record.target_value,
                    "baseline_value": record.baseline_value,
                    "status": record.status,
                },
                organization_id=org_id,
            )
            await store.commit()
            print("✓ Event appended to event store")

            # Step 5: Event → Projection (via handler)
            await process_kpi_event(session, event)
            print("✓ Event handler processed")

            # Step 6: Verify projection created
            statement = select(GoalAnalyticsProjection).where(
                GoalAnalyticsProjection.definition_id == record.definition_id
            )
            result = await session.execute(statement)
            projection = result.scalar_one_or_none()

            assert projection is not None, "Projection not found"
            assert projection.definition_id == record.definition_id
            assert projection.goal_status == "draft"
            assert projection.target_value == 85.0
            print("✓ Projection created and verified")

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
            assert response.is_on_track is False
            assert response.days_active is None  # None for draft status
            print("✓ Response model created")

            print("\n✅ Test PASSED: Full event sourcing flow works end-to-end!\n")

    finally:
        await engine.dispose()


async def main():
    try:
        await test_kpi_goal_creation_event_flow()
    except Exception as e:
        print(f"\n❌ Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
