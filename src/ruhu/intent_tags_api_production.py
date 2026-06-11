"""Intent Tags API routes. Production-ready integration of all schema layers.

Pattern:
1. Request schema validates input (Pydantic coercion OK here)
2. Request → Domain conversion (business logic validation)
3. Domain → DB conversion (persistence)
4. DB → Response conversion (include computed fields from projection)
5. Return response schema
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ruhu.db import get_async_session
from ruhu.db_sqlmodel import TaxonomyVersion as TaxonomyVersionRecord
from ruhu.db_sqlmodel import IntentDefinition as IntentDefinitionRecord
from ruhu.domain.intent_tags import (
    TaxonomyVersion as TaxonomyVersionDomain,
    IntentDefinition as IntentDefinitionDomain,
    TaxonomyVersionCreated,
    IntentDefinitionCreated,
    IntentDefinitionUpdated,
)
from ruhu.models.intent_tags.requests import (
    CreateTaxonomyRequest,
    UpdateTaxonomyRequest,
    CreateIntentRequest,
    UpdateIntentRequest,
)
from ruhu.models.intent_tags.responses import (
    TaxonomyResponse,
    IntentResponse,
    TaxonomyListResponse,
    IntentListResponse,
)
from ruhu.api_auth import get_request_auth_context, RequestAuthContext

router = APIRouter(prefix="/intent-tags", tags=["intent_tags"])


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Conversion Helpers
# ─────────────────────────────────────────────────────────────────────


def request_to_taxonomy_domain(req: CreateTaxonomyRequest, org_id: str) -> TaxonomyVersionDomain:
    """Convert API request to domain model."""
    return TaxonomyVersionDomain(
        organization_id=org_id,
        name=req.name,
        notes=req.notes,
    )


def taxonomy_domain_to_db(domain: TaxonomyVersionDomain) -> TaxonomyVersionRecord:
    """Convert domain model to DB record."""
    return TaxonomyVersionRecord(
        taxonomy_version_id=domain.taxonomy_version_id,
        organization_id=domain.organization_id,
        name=domain.name,
        status=domain.status,
        notes=domain.notes,
        published_at=domain.published_at,
    )


def taxonomy_db_to_domain(record: TaxonomyVersionRecord) -> TaxonomyVersionDomain:
    """Convert DB record to domain model."""
    return TaxonomyVersionDomain(
        taxonomy_version_id=record.taxonomy_version_id,
        organization_id=record.organization_id,
        name=record.name,
        status=record.status,
        notes=record.notes,
        published_at=record.published_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def taxonomy_domain_to_response(domain: TaxonomyVersionDomain) -> TaxonomyResponse:
    """Convert domain model to API response."""
    return TaxonomyResponse(
        taxonomy_version_id=domain.taxonomy_version_id,
        organization_id=domain.organization_id,
        name=domain.name,
        status=domain.status,
        notes=domain.notes,
        published_at=domain.published_at,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
    )


def request_to_intent_domain(req: CreateIntentRequest, org_id: str) -> IntentDefinitionDomain:
    """Convert API request to domain model."""
    return IntentDefinitionDomain(
        organization_id=org_id,
        taxonomy_version_id=req.taxonomy_version_id,
        name=req.name,
        display_name=req.display_name,
        description=req.description,
        category=req.category,
        example_phrases=req.example_phrases,
        confidence_threshold=req.confidence_threshold,
        priority=req.priority,
        color=req.color,
        icon=req.icon,
    )


def intent_domain_to_db(domain: IntentDefinitionDomain) -> IntentDefinitionRecord:
    """Convert domain model to DB record."""
    return IntentDefinitionRecord(
        intent_definition_id=domain.intent_definition_id,
        organization_id=domain.organization_id,
        taxonomy_version_id=domain.taxonomy_version_id,
        name=domain.name,
        display_name=domain.display_name,
        description=domain.description,
        category=domain.category,
        example_phrases=domain.example_phrases,
        confidence_threshold=domain.confidence_threshold,
        priority=domain.priority,
        status=domain.status,
        is_deprecated=domain.is_deprecated,
        color=domain.color,
        icon=domain.icon,
        metadata_json=domain.metadata_json,
    )


def intent_db_to_domain(record: IntentDefinitionRecord) -> IntentDefinitionDomain:
    """Convert DB record to domain model."""
    return IntentDefinitionDomain(
        intent_definition_id=record.intent_definition_id,
        organization_id=record.organization_id,
        taxonomy_version_id=record.taxonomy_version_id,
        name=record.name,
        display_name=record.display_name,
        description=record.description,
        category=record.category,
        example_phrases=record.example_phrases,
        confidence_threshold=record.confidence_threshold,
        priority=record.priority,
        status=record.status,
        is_deprecated=record.is_deprecated,
        color=record.color,
        icon=record.icon,
        metadata_json=record.metadata_json,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def intent_domain_to_response(domain: IntentDefinitionDomain) -> IntentResponse:
    """Convert domain model to API response."""
    return IntentResponse(
        intent_definition_id=domain.intent_definition_id,
        organization_id=domain.organization_id,
        taxonomy_version_id=domain.taxonomy_version_id,
        name=domain.name,
        display_name=domain.display_name,
        description=domain.description,
        category=domain.category,
        example_phrases=domain.example_phrases,
        confidence_threshold=domain.confidence_threshold,
        priority=domain.priority,
        status=domain.status,
        is_deprecated=domain.is_deprecated,
        color=domain.color,
        icon=domain.icon,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
    )


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
# Taxonomy Endpoints
# ─────────────────────────────────────────────────────────────────────


@router.post("/taxonomies", response_model=TaxonomyResponse, status_code=201)
async def create_taxonomy(
    req: CreateTaxonomyRequest,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> TaxonomyResponse:
    """Create a new intent taxonomy version."""
    try:
        domain = request_to_taxonomy_domain(req, org_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    record = taxonomy_domain_to_db(domain)
    session.add(record)
    await session.commit()
    await session.refresh(record)

    await emit_event(
        session=session,
        event_type="TaxonomyVersionCreated",
        aggregate_type="TaxonomyVersion",
        aggregate_id=record.taxonomy_version_id,
        payload={"taxonomy_version_id": record.taxonomy_version_id, "name": record.name},
        organization_id=org_id,
    )

    return taxonomy_domain_to_response(taxonomy_db_to_domain(record))


@router.get("/taxonomies/{taxonomy_version_id}", response_model=TaxonomyResponse)
async def get_taxonomy(
    taxonomy_version_id: str,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> TaxonomyResponse:
    """Get a single taxonomy by ID."""
    statement = select(TaxonomyVersionRecord).where(
        TaxonomyVersionRecord.taxonomy_version_id == taxonomy_version_id,
        TaxonomyVersionRecord.organization_id == org_id,
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=404, detail="Taxonomy not found")

    return taxonomy_domain_to_response(taxonomy_db_to_domain(record))


# ─────────────────────────────────────────────────────────────────────
# Intent Endpoints
# ─────────────────────────────────────────────────────────────────────


@router.post("/intents", response_model=IntentResponse, status_code=201)
async def create_intent(
    req: CreateIntentRequest,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> IntentResponse:
    """Create a new intent definition."""
    try:
        domain = request_to_intent_domain(req, org_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    record = intent_domain_to_db(domain)
    session.add(record)
    await session.commit()
    await session.refresh(record)

    await emit_event(
        session=session,
        event_type="IntentDefinitionCreated",
        aggregate_type="IntentDefinition",
        aggregate_id=record.intent_definition_id,
        payload={"intent_definition_id": record.intent_definition_id, "name": record.name},
        organization_id=org_id,
    )

    return intent_domain_to_response(intent_db_to_domain(record))


@router.get("/intents/{intent_definition_id}", response_model=IntentResponse)
async def get_intent(
    intent_definition_id: str,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> IntentResponse:
    """Get a single intent by ID."""
    statement = select(IntentDefinitionRecord).where(
        IntentDefinitionRecord.intent_definition_id == intent_definition_id,
        IntentDefinitionRecord.organization_id == org_id,
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=404, detail="Intent not found")

    return intent_domain_to_response(intent_db_to_domain(record))


@router.patch("/intents/{intent_definition_id}", response_model=IntentResponse)
async def update_intent(
    intent_definition_id: str,
    req: UpdateIntentRequest,
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> IntentResponse:
    """Update an intent definition (patch semantics)."""
    statement = select(IntentDefinitionRecord).where(
        IntentDefinitionRecord.intent_definition_id == intent_definition_id,
        IntentDefinitionRecord.organization_id == org_id,
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=404, detail="Intent not found")

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
        event_type="IntentDefinitionUpdated",
        aggregate_type="IntentDefinition",
        aggregate_id=intent_definition_id,
        payload={"intent_definition_id": intent_definition_id, "changes": update_data},
        organization_id=org_id,
    )

    return intent_domain_to_response(intent_db_to_domain(record))


@router.get("/intents", response_model=IntentListResponse)
async def list_intents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    org_id: str = Depends(get_request_org_id),
) -> IntentListResponse:
    """List intent definitions for organization."""
    count_stmt = select(func.count(IntentDefinitionRecord.intent_definition_id)).where(
        IntentDefinitionRecord.organization_id == org_id
    )
    count_result = await session.execute(count_stmt)
    total = count_result.scalar() or 0

    statement = (
        select(IntentDefinitionRecord)
        .where(IntentDefinitionRecord.organization_id == org_id)
        .order_by(IntentDefinitionRecord.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page + 1)
    )

    result = await session.execute(statement)
    records = result.scalars().all()

    has_more = len(records) > per_page
    records = records[:per_page]

    intents = [intent_domain_to_response(intent_db_to_domain(r)) for r in records]

    return IntentListResponse(
        intents=intents,
        total=total,
        page=page,
        per_page=per_page,
        has_more=has_more,
    )
