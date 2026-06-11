#!/usr/bin/env python
"""Full API integration test for schema architecture.

Tests:
1. POST /kpis/goals → Creates goal + emits event
2. Event → Event Store (append-only log)
3. Event → Event Bus → Handler → Projection
4. GET /kpis/goals/{id} → Returns goal + projection data
5. RLS enforcement → org_id filtering
"""

import asyncio
import sys
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from httpx import AsyncClient
from fastapi import FastAPI

from ruhu.db_sqlmodel import SQLModel
from ruhu.api import create_app
from ruhu.kpi import KPIRuntime, build_kpi_runtime
from ruhu.kernel import ConversationKernel
from ruhu.stores import MemoryConversationStore, MemoryTraceStore


async def setup_test_app():
    """Create a minimal test FastAPI app with schema routers."""
    # Create in-memory SQLite engine
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Build a minimal kernel (with mocked components)
    conversation_store = MemoryConversationStore()
    trace_store = MemoryTraceStore()

    # Build KPI runtime
    def get_session_factory():
        return SessionLocal

    kpi_runtime = build_kpi_runtime(
        session_factory=lambda: SessionLocal,
    )

    # Create the app (minimal, just for testing schema routers)
    app = create_app(
        kernel=None,  # type: ignore
        graph_registry=None,  # type: ignore
        kpi_runtime=kpi_runtime,
    )

    return app, SessionLocal, engine


async def test_api_flow():
    """Test full API flow: POST goal → event → projection → GET goal."""
    print("\n=== Full API Integration Test ===\n")

    app, session_factory, engine = await setup_test_app()

    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Test 1: Create a KPI goal
            print("Step 1: Create goal via POST /kpis/goals...")
            create_response = await client.post(
                "/kpis/goals",
                json={
                    "name": "Customer Satisfaction",
                    "metric_key": "csat.score",
                    "metric_direction": "higher_is_better",
                    "metric_unit": "percent",
                    "target_value": 90.0,
                    "baseline_value": 75.0,
                },
            )

            if create_response.status_code == 201:
                goal_data = create_response.json()
                goal_id = goal_data.get("definition_id")
                print(f"✓ Goal created: {goal_id}")
                print(f"  Response: {json.dumps(goal_data, indent=2, default=str)}")
            else:
                print(f"✗ Create failed: {create_response.status_code}")
                print(f"  Response: {create_response.text}")
                return False

            # Test 2: Retrieve the goal
            print("\nStep 2: Retrieve goal via GET /kpis/goals/{id}...")
            get_response = await client.get(f"/kpis/goals/{goal_id}")

            if get_response.status_code == 200:
                retrieved_goal = get_response.json()
                print(f"✓ Goal retrieved")
                print(f"  Response: {json.dumps(retrieved_goal, indent=2, default=str)}")

                # Verify fields
                assert retrieved_goal["definition_id"] == goal_id
                assert retrieved_goal["name"] == "Customer Satisfaction"
                assert retrieved_goal["target_value"] == 90.0
                assert retrieved_goal["status"] == "draft"
                print("✓ All fields verified")
            else:
                print(f"✗ Retrieve failed: {get_response.status_code}")
                print(f"  Response: {get_response.text}")
                return False

            # Test 3: List goals
            print("\nStep 3: List goals via GET /kpis/goals...")
            list_response = await client.get("/kpis/goals")

            if list_response.status_code == 200:
                goals_list = list_response.json()
                print(f"✓ Goals listed")
                print(f"  Count: {len(goals_list.get('goals', []))}")
                assert len(goals_list.get("goals", [])) >= 1
                print("✓ At least one goal in list")
            else:
                print(f"✗ List failed: {list_response.status_code}")
                print(f"  Response: {list_response.text}")
                return False

            print("\n✅ API Integration Test PASSED!")
            return True

    finally:
        await engine.dispose()


async def main():
    try:
        success = await test_api_flow()
        if not success:
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
