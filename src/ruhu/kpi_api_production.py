"""KPI API routes. Production-ready integration of all schema layers.

This is the new version using the schema architecture.
Integrate request → domain → DB → response layers.

Pattern:
1. Request schema validates input (Pydantic coercion OK here)
2. Request → Domain conversion (business logic validation)
3. Domain → DB conversion (persistence)
4. DB → Response conversion (include computed fields from projection)
5. Return response schema

Separation of concerns:
- Request schema: API contract (what clients send)
- Domain model: business logic (what kernel works with)
- DB model: persistence (what's stored)
- Response schema: API response (what clients receive)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ruhu.db import get_async_session
from ruhu.db_sqlmodel import GoalDefinition as GoalDefinitionRecord
from ruhu.db_sqlmodel import GoalExecution as GoalExecutionRecord
from ruhu.domain.kpi import (
    GoalDefinition as GoalDefinitionDomain,
    GoalExecution as GoalExecutionDomain,
    GoalDefinitionCreated,
    GoalDefinitionUpdated,
    GoalObservationRecorded,
)
from ruhu.models.kpi import (
    CreateGoalRequest,
    UpdateGoalRequest,
    RecordObservationRequest,
    GoalResponse,
    GoalListResponse,
    GoalExecutionResponse,
    ObservationListResponse,
)
from ruhu.api_auth import get_request_auth_context, RequestAuthContext

router = APIRouter(prefix="/kpis/goals", tags=["kpi"])


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Conversion Helpers
# ─────────────────────────────────────────────────────────────────────


def request_to_domain(req: CreateGoalRequest, org_id: str) -> GoalDefinitionDomain:
    """Convert API request to domain model."""
    return GoalDefinitionDomain(
        organization_id=org_id,
        kind=req.kind,  # type: ignore
        name=req.name,
        description=req.description,
        metric_key=req.metric_key,
        metric_direction=req.metric_direction,  # type: ignore
        metric_unit=req.metric_unit,  # type: ignore
        target_value=req.target_value,
        baseline_value=req.baseline_value,
        tags=req.tags,
    )


def domain_to_db(domain: GoalDefinitionDomain) -> GoalDefinitionRecord:
    """Convert domain model to DB record."""
    return GoalDefinitionRecord(
        definition_id=domain.definition_id,
        organization_id=domain.organization_id,
        kind=domain.kind,
        name=domain.name,
        description=domain.description,
        metric_key=domain.metric_key,
        metric_direction=domain.metric_direction,
        metric_unit=domain.metric_unit,
        target_value=domain.target_value,
        baseline_value=domain.baseline_value,
        status=domain.status,
        tags=domain.tags,
    )


def db_to_domain(record: GoalDefinitionRecord) -> GoalDefinitionDomain:
    """Convert DB record to domain model."""
    return GoalDefinitionDomain(
        definition_id=record.definition_id,
        organization_id=record.organization_id,
        kind=record.kind,  # type: ignore
        name=record.name,
        description=record.description,
        metric_key=record.metric_key,
        metric_direction=record.metric_direction,  # type: ignore
        metric_unit=record.metric_unit,  # type: ignore
        target_value=record.target_value,
        baseline_value=record.baseline_value,
        status=record.status,  # type: ignore
        created_by=record.created_by,
        updated_by=record.updated_by,
        tags=record.tags or [],
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def domain_to_response(
    domain: GoalDefinitionDomain,
    current_value: Optional[float] = None,
    progress_pct: Optional[float] = None,
    trend: Optional[str] = None,
    confidence: Optional[float] = None,
    last_observed_at: Optional[datetime] = None,
) -> GoalResponse:
    """Convert domain model to API response (add computed fields)."""
    return GoalResponse(
        definition_id=domain.definition_id,
        organization_id=domain.organization_id,
        kind=domain.kind,
        name=domain.name,
        description=domain.description,
        metric_key=domain.metric_key,
        metric_direction=domain.metric_direction,
        metric_unit=domain.metric_unit,
        target_value=domain.target_value,
        baseline_value=domain.baseline_value,
        status=domain.status,
        tags=domain.tags,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
        created_by=domain.created_by,
        updated_by=domain.updated_by,
        # Computed fields from projection
        current_value=current_value,
        progress_pct=progress_pct,
        trend=trend,
        confidence=confidence,
        last_observed_at=last_observed_at,
    )


async def emit_event(
    session: AsyncSession,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
    organization_id: Optional[str] = None,
) -> None:
    """Emit a domain event via event store and bus.

    Creates event record, stores it, and dispatches to handlers.
    Handlers update projections (read models) and can trigger webhooks.

    Args:
        session: Database session
        event_type: Event class name (e.g., 'GoalDefinitionCreated')
        aggregate_type: Root aggregate type (e.g., 'GoalDefinition')
        aggregate_id: Aggregate instance ID
        payload: Event payload
        organization_id: Tenant/organization ID
    """
    from ruhu.event_sourcing.event_store import EventStore
    from ruhu.event_sourcing.event_bus import get_event_bus

    try:
        # Append to event store
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

        # Dispatch to event bus (handlers update projections, etc.)
        bus = get_event_bus()
        await bus.publish(session, event)

    except Exception:
        # Log but don't fail the API call if event emission fails
        # In production, would alert ops
        import logging

        logger = logging.getLogger(__name__)
        logger.exception(f"Failed to emit event {event_type}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────
# Dependencies
# ─────────────────────────────────────────────────────────────────────


async def get_request_org_id(
    request: Request,
) -> str:
    """Extract organization ID from JWT token via auth context.

    Requires authenticated request (Bearer token in Authorization header).
    Applies row-level security (RLS) by scoping queries to org_id from JWT.
    """
    context: RequestAuthContext = get_request_auth_context(request)
    if context.principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return context.principal.organization.organization_id


async def get_current_user_id(
    request: Request,
) -> str:
    """Extract user ID from JWT token via auth context."""
    context: RequestAuthContext = get_request_auth_context(request)
    if context.principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return context.principal.user.user_id


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────


@router.post("", response_model=GoalResponse, status_code=201)
async def create_goal(
    req: CreateGoalRequest,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
    user_id: str = Depends(get_current_user_id),
) -> GoalResponse:
    """Create a new KPI goal."""
    # Step 1: Request is already validated by FastAPI/Pydantic
    # Step 2: Request → Domain
    try:
        goal = request_to_domain(req, org_id)
        goal.created_by = user_id
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Step 3: Domain → DB
    record = domain_to_db(goal)
    session.add(record)
    await session.commit()
    await session.refresh(record)

    # Step 4: Emit event (triggers projection updates)
    #
    # IMPORTANT: the payload is the source of truth for projection rebuilding.
    # KPIEventHandler.handle_goal_definition_created reads target_value,
    # baseline_value, and status from this payload — if they're absent, the
    # projection gets default zeros (an invariant-violating data bug that
    # customers will see as "goal created with target_value=0").
    # Every field the projection needs must travel on the event.
    await emit_event(
        session=session,
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

    # Step 5: DB → Response
    return domain_to_response(db_to_domain(record))


@router.get("/{definition_id}", response_model=GoalResponse)
async def get_goal(
    definition_id: str,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> GoalResponse:
    """Get a single KPI goal by ID."""
    statement = select(GoalDefinitionRecord).where(
        GoalDefinitionRecord.definition_id == definition_id,
        GoalDefinitionRecord.organization_id == org_id,
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=404, detail="Goal not found")

    return domain_to_response(db_to_domain(record))


@router.patch("/{definition_id}", response_model=GoalResponse)
async def update_goal(
    definition_id: str,
    req: UpdateGoalRequest,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
    user_id: str = Depends(get_current_user_id),
) -> GoalResponse:
    """Update a KPI goal (patch semantics)."""
    statement = select(GoalDefinitionRecord).where(
        GoalDefinitionRecord.definition_id == definition_id,
        GoalDefinitionRecord.organization_id == org_id,
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Apply updates
    update_data = req.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if hasattr(record, key):
            setattr(record, key, value)

    record.updated_by = user_id
    record.updated_at = utcnow()

    session.add(record)
    await session.commit()
    await session.refresh(record)

    # Emit event (triggers projection updates)
    await emit_event(
        session=session,
        event_type="GoalDefinitionUpdated",
        aggregate_type="GoalDefinition",
        aggregate_id=definition_id,
        payload={"definition_id": definition_id, "changes": update_data},
        organization_id=org_id,
    )

    return domain_to_response(db_to_domain(record))


@router.get("", response_model=GoalListResponse)
async def list_goals(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> GoalListResponse:
    """List KPI goals for organization."""
    count_stmt = select(func.count(GoalDefinitionRecord.definition_id)).where(
        GoalDefinitionRecord.organization_id == org_id
    )
    count_result = await session.execute(count_stmt)
    total = count_result.scalar() or 0

    statement = (
        select(GoalDefinitionRecord)
        .where(GoalDefinitionRecord.organization_id == org_id)
        .order_by(GoalDefinitionRecord.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page + 1)
    )

    result = await session.execute(statement)
    records = result.scalars().all()

    has_more = len(records) > per_page
    records = records[:per_page]

    goals = [domain_to_response(db_to_domain(r)) for r in records]

    return GoalListResponse(
        goals=goals,
        total=total,
        page=page,
        per_page=per_page,
        has_more=has_more,
    )


@router.post("/{definition_id}/observations", response_model=GoalExecutionResponse, status_code=201)
async def record_observation(
    definition_id: str,
    req: RecordObservationRequest,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> GoalExecutionResponse:
    """Record an observation for a goal."""
    goal_stmt = select(GoalDefinitionRecord).where(
        GoalDefinitionRecord.definition_id == definition_id,
        GoalDefinitionRecord.organization_id == org_id,
    )
    goal_result = await session.execute(goal_stmt)
    goal_record = goal_result.scalar_one_or_none()

    if not goal_record:
        raise HTTPException(status_code=404, detail="Goal not found")

    execution_record = GoalExecutionRecord(
        organization_id=org_id,
        definition_id=definition_id,
        observation_kind=req.observation_kind,
        observed_value=req.observed_value,
        confidence=req.confidence,
        observed_at=datetime.now(timezone.utc),
        metadata_json=req.metadata_json or {},
    )

    session.add(execution_record)
    await session.commit()
    await session.refresh(execution_record)

    await emit_event(
        session=session,
        event_type="GoalObservationRecorded",
        aggregate_type="GoalExecution",
        aggregate_id=execution_record.execution_id,
        payload={
            "execution_id": execution_record.execution_id,
            "definition_id": definition_id,
            "observed_value": req.observed_value,
            "observation_kind": req.observation_kind,
        },
        organization_id=org_id,
    )

    return GoalExecutionResponse(
        execution_id=execution_record.execution_id,
        definition_id=execution_record.definition_id,
        organization_id=execution_record.organization_id,
        observation_kind=execution_record.observation_kind,
        observed_value=execution_record.observed_value,
        confidence=execution_record.confidence,
        observed_at=execution_record.observed_at,
        created_at=execution_record.created_at,
        metadata_json=execution_record.metadata_json or {},
    )


@router.get("/{definition_id}/observations", response_model=ObservationListResponse)
async def list_observations(
    definition_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> ObservationListResponse:
    """List observations for a goal."""
    goal_stmt = select(GoalDefinitionRecord).where(
        GoalDefinitionRecord.definition_id == definition_id,
        GoalDefinitionRecord.organization_id == org_id,
    )
    goal_result = await session.execute(goal_stmt)
    if not goal_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Goal not found")

    count_stmt = select(func.count(GoalExecutionRecord.execution_id)).where(
        GoalExecutionRecord.definition_id == definition_id,
        GoalExecutionRecord.organization_id == org_id,
    )
    count_result = await session.execute(count_stmt)
    total = count_result.scalar() or 0

    statement = (
        select(GoalExecutionRecord)
        .where(
            GoalExecutionRecord.definition_id == definition_id,
            GoalExecutionRecord.organization_id == org_id,
        )
        .order_by(GoalExecutionRecord.observed_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page + 1)
    )

    result = await session.execute(statement)
    records = result.scalars().all()

    has_more = len(records) > per_page
    records = records[:per_page]

    observations = [
        GoalExecutionResponse(
            execution_id=r.execution_id,
            definition_id=r.definition_id,
            organization_id=r.organization_id,
            observation_kind=r.observation_kind,
            observed_value=r.observed_value,
            confidence=r.confidence,
            observed_at=r.observed_at,
            created_at=r.created_at,
            metadata_json=r.metadata_json or {},
        )
        for r in records
    ]

    return ObservationListResponse(
        observations=observations,
        total=total,
        page=page,
        per_page=per_page,
        has_more=has_more,
    )
