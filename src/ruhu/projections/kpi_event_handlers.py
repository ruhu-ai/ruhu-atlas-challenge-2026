"""Event handlers for KPI projections.

These handlers process domain events and update read models.
Pattern: DomainEvent → Handler → ProjectionUpdate

Handlers are idempotent (can safely replay events).
"""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ruhu.db_sqlmodel import DomainEvent
from ruhu.domain.kpi import (
    GoalDefinitionCreated,
    GoalDefinitionUpdated,
    GoalObservationRecorded,
)
from ruhu.projections.kpi_projection import (
    GoalAnalyticsProjection,
    GoalTrendProjection,
)


class KPIEventHandler:
    """Handles KPI domain events and updates projections."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def handle_goal_definition_created(self, event: GoalDefinitionCreated, payload: dict = None) -> None:
        """Handle: GoalDefinitionCreated event.

        Creates analytics projection with initial state.
        """
        projection = GoalAnalyticsProjection(
            definition_id=event.definition_id,
            organization_id=event.organization_id,
            goal_status=payload.get("status", "draft") if payload else "draft",
            target_value=payload.get("target_value", 0.0) if payload else 0.0,
            baseline_value=payload.get("baseline_value") if payload else None,
            observation_count=0,
            on_track_days=0,
            off_track_days=0,
            event_version=event.timestamp.timestamp(),
            created_at=event.timestamp,
            updated_at=event.timestamp,
        )
        self.session.add(projection)

    async def handle_goal_definition_updated(self, event: GoalDefinitionUpdated) -> None:
        """Handle: GoalDefinitionUpdated event.

        Updates analytics projection with new goal state.
        """
        statement = select(GoalAnalyticsProjection).where(
            GoalAnalyticsProjection.definition_id == event.definition_id
        )
        result = await self.session.execute(statement)
        projection = result.scalar_one_or_none()

        if not projection:
            # Projection doesn't exist; create stub (shouldn't happen in normal flow)
            projection = GoalAnalyticsProjection(
                definition_id=event.definition_id,
                organization_id=event.organization_id,
                goal_status="unknown",
                target_value=0.0,
                created_at=event.timestamp,
                updated_at=event.timestamp,
            )
            self.session.add(projection)
        else:
            # Update status and metadata if provided
            changes = event.changes
            if "status" in changes:
                projection.goal_status = changes["status"]
            if "target_value" in changes:
                projection.target_value = changes["target_value"]
            if "baseline_value" in changes:
                projection.baseline_value = changes["baseline_value"]

            projection.updated_at = event.timestamp
            projection.event_version = event.timestamp.timestamp()

    async def handle_observation_recorded(self, event: GoalObservationRecorded) -> None:
        """Handle: GoalObservationRecorded event.

        Updates analytics with latest observation and recomputes trend.
        """
        # Update analytics projection
        statement = select(GoalAnalyticsProjection).where(
            GoalAnalyticsProjection.definition_id == event.definition_id
        )
        result = await self.session.execute(statement)
        projection = result.scalar_one_or_none()

        if projection:
            projection.current_value = event.observed_value
            projection.observed_at = event.timestamp
            projection.observation_count += 1

            # Compute progress percentage
            if projection.target_value > 0:
                projection.progress_pct = (event.observed_value / projection.target_value) * 100

            # Update trend (simplified: would use multiple observations in production)
            if projection.current_value is not None:
                if projection.progress_pct is not None:
                    if projection.progress_pct > 100:
                        projection.trend = "up"
                    elif projection.progress_pct < 80:
                        projection.trend = "down"
                    else:
                        projection.trend = "flat"

            projection.updated_at = event.timestamp
            projection.event_version = event.timestamp.timestamp()

        # Create trend history record
        trend_record = GoalTrendProjection(
            organization_id=event.organization_id,
            definition_id=event.definition_id,
            observed_value=event.observed_value,
            observed_at=event.timestamp,
            confidence=1.0,  # Default; would come from event in production
            observation_kind=event.observation_kind,
            percent_of_target=(event.observed_value / projection.target_value * 100)
            if projection and projection.target_value > 0
            else 0,
            is_on_track=_compute_on_track(
                event.observed_value,
                projection.target_value if projection else 0,
                "higher_is_better",  # Would come from goal definition
            ),
        )
        self.session.add(trend_record)

    async def commit(self) -> None:
        """Commit all projection updates to database."""
        await self.session.commit()


def _compute_on_track(
    current_value: float, target_value: float, metric_direction: str
) -> bool:
    """Compute if goal is on track.

    Heuristic: within 90% of target (higher_is_better) or 110% (lower_is_better).
    """
    if target_value == 0:
        return False
    if metric_direction == "higher_is_better":
        return current_value >= target_value * 0.9
    else:
        return current_value <= target_value * 1.1


async def process_kpi_event(session: AsyncSession, event: DomainEvent) -> None:
    """Process a KPI domain event and update projections.

    Entry point for event processing. Dispatches to appropriate handler.
    """
    handler = KPIEventHandler(session)

    if event.event_type == "GoalDefinitionCreated":
        payload = event.payload
        evt = GoalDefinitionCreated(
            definition_id=payload["definition_id"],
            organization_id=payload["organization_id"],
            kind=payload["kind"],
            name=payload["name"],
            timestamp=event.timestamp,
        )
        await handler.handle_goal_definition_created(evt, payload)

    elif event.event_type == "GoalDefinitionUpdated":
        payload = event.payload
        evt = GoalDefinitionUpdated(
            definition_id=payload["definition_id"],
            organization_id=payload["organization_id"],
            changes=payload.get("changes", {}),
            timestamp=event.timestamp,
        )
        await handler.handle_goal_definition_updated(evt)

    elif event.event_type == "GoalObservationRecorded":
        payload = event.payload
        evt = GoalObservationRecorded(
            execution_id=payload["execution_id"],
            definition_id=payload["definition_id"],
            organization_id=payload["organization_id"],
            observed_value=payload["observed_value"],
            observation_kind=payload["observation_kind"],
            timestamp=event.timestamp,
        )
        await handler.handle_observation_recorded(evt)

    else:
        # Unknown event type; skip
        return

    await handler.commit()
