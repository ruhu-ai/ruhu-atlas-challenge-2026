"""Widget-analytics summary route — extracted from api.py (RP-3.1 step 9).

Group 16's GET summary endpoint, and THE FIRST ASYNC CONVERSION of the
decomposition (blueprint async tier "ASYNC-NOW"): the inline SQLAlchemy
selects over ``WidgetEventRecord`` move verbatim onto an
``AsyncSession`` injected via ``Depends(get_db_session)`` from
``ruhu.db_async`` (the ``kpi_api_production`` / ``intent_tags_api_production``
router style). The companion events INGEST route
(``POST /public/widget/sessions/{conversation_id}/events``) stays in api.py —
it needs the kernel + public-widget session guard and ships with group 17 at
blueprint step 13.

Hazard H8 verified: the handler holds no sync ``Session`` across its body
(the old ``auth_session_factory`` usage is gone) and calls no sync kernel
path — its only non-DB collaborator is ``agent_registry.get_agent_registration``,
a short org-scope ownership check.

The DTO (``WidgetAnalyticsSummary``) still lives in ``ruhu.api``, so this
module is imported by ``create_app()`` AT THE MOUNT SITE rather than at
api.py's module top (hazard H7: DTO imports stay at this module's top for
PEP 563). No ``tags=`` / ``prefix=`` and unchanged handler name (hazard H1);
the response model is byte-identical so the OpenAPI schema must not move.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

# DTOs at module top (hazard H7: PEP 563 annotations resolve against this
# module's globals).
from ..api import WidgetAnalyticsSummary
from ..api_auth import RequestAuthContext
from ..db_async import get_db_session
from ..policy import require_organization_role


def build_widget_analytics_router(
    *,
    agent_registry,
) -> APIRouter:
    """Build the /agents/{agent_id}/widget-analytics summary router."""
    router = APIRouter()

    @router.get("/agents/{agent_id}/widget-analytics", response_model=WidgetAnalyticsSummary)
    async def get_widget_analytics(
        agent_id: str,
        period_start: datetime = Query(default=None),
        period_end: datetime = Query(default=None),
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
        db: AsyncSession = Depends(get_db_session),
    ) -> WidgetAnalyticsSummary:
        """Aggregate widget analytics for an agent over a time window."""
        from ..db_models import WidgetEventRecord
        from sqlalchemy import select as sa_select, func as sa_func
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        org_id = principal.organization.organization_id
        # Verify agent belongs to this org.
        try:
            agent_registry.get_agent_registration(agent_id, organization_id=org_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent id")
        _now = datetime.now(timezone.utc)
        _start = period_start or (_now - timedelta(days=7))
        _end = period_end or _now
        # Count distinct sessions that have at least one event for this agent
        # in the time window.  agent_id is denormalized on WidgetEventRecord so
        # we do NOT need to join through conversations.
        session_count = await db.scalar(
            sa_select(sa_func.count(sa_func.distinct(WidgetEventRecord.session_id))).where(
                WidgetEventRecord.organization_id == org_id,
                WidgetEventRecord.agent_id == agent_id,
                WidgetEventRecord.occurred_at >= _start,
                WidgetEventRecord.occurred_at < _end,
            )
        ) or 0
        rows = (
            await db.execute(
                sa_select(
                    WidgetEventRecord.event_type,
                    sa_func.count(WidgetEventRecord.event_id).label("cnt"),
                )
                .where(
                    WidgetEventRecord.organization_id == org_id,
                    WidgetEventRecord.agent_id == agent_id,
                    WidgetEventRecord.occurred_at >= _start,
                    WidgetEventRecord.occurred_at < _end,
                )
                .group_by(WidgetEventRecord.event_type)
            )
        ).fetchall()
        event_counts = {row[0]: row[1] for row in rows}
        return WidgetAnalyticsSummary(
            agent_id=agent_id,
            period_start=_start,
            period_end=_end,
            total_sessions=session_count,
            total_events=sum(event_counts.values()),
            event_counts=event_counts,
        )

    return router
