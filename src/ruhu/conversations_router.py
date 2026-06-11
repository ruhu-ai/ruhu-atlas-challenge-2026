"""Conversations + dashboard router — extracted from api.py in Phase 5.

Provides /conversations, /dashboard/stats, /agents/{id}/metrics with org-level
rate limiting applied via the router-level dependency.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import APIRouter, Query, Request

from .api_models import (
    DashboardPerformance,
    DashboardResolutionPoint,
    DashboardStats,
)
from .agent_review import AgentOperationalMetrics, build_agent_metrics
from .schemas import ConversationState


def build_conversations_router(
    *,
    conversation_store,
    trace_store,
    agent_registry,
    agent_summary_fn: Callable,
    get_organization_id: Callable[[Request], str | None],
    rate_limiter=None,
) -> APIRouter:
    """Build an APIRouter for conversation-related endpoints.

    Dependencies are passed in rather than imported so that the router stays
    decoupled from create_app() internals. DTOs are imported directly from
    ``api_models`` (Phase C Batch 1 extraction) rather than passed as
    parameters — this lets the nested handlers carry real return annotations
    that ``typing.get_type_hints()`` can resolve (PEP 563-safe).
    """
    router = APIRouter(
        tags=["conversations"],
        dependencies=[rate_limiter] if rate_limiter else [],
    )

    @router.get("/conversations", response_model=list[ConversationState])
    def list_conversations(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[ConversationState]:
        organization_id = get_organization_id(request)
        # Pagination happens in the store (SQL LIMIT/OFFSET) — RP-3.3 retired
        # the load-everything-and-slice-in-memory pattern here.
        page = conversation_store.list_conversations(
            organization_id=organization_id, limit=limit, offset=offset
        )
        try:
            from .observability.metrics import list_endpoint_row_count
            list_endpoint_row_count.labels(endpoint="conversations").observe(len(page))
        except Exception:
            pass
        return page

    @router.get("/dashboard/stats", response_model=DashboardStats)
    def get_dashboard_stats(
        request: Request,
        days: int = Query(default=7, ge=1, le=90),
    ) -> DashboardStats:
        organization_id = get_organization_id(request)
        conversations = conversation_store.list_conversations(organization_id=organization_id)
        agents = agent_registry.list_agents(organization_id=organization_id)
        traces = trace_store.all(organization_id=organization_id)

        live = [c for c in conversations if c.mode == "live"]

        ended = [c for c in live if c.status == "ended"]
        active_count = sum(1 for c in live if c.status == "active")
        resolved_count = sum(1 for c in ended if c.outcome == "resolved")
        resolution_rate = (resolved_count / len(ended) * 100) if ended else 0.0

        handle_times = [
            (c.ended_at - c.started_at).total_seconds()
            for c in ended
            if c.ended_at is not None
        ]
        avg_handle_time = sum(handle_times) / len(handle_times) if handle_times else 0.0

        agent_perf: list[DashboardPerformance] = []
        for reg in agents:
            summary = agent_summary_fn(reg, organization_id=organization_id)
            if summary is None:
                continue
            agent_live = [c for c in live if c.agent_id == summary.id]
            agent_ended = [c for c in agent_live if c.status == "ended"]
            agent_active = sum(1 for c in agent_live if c.status == "active")
            agent_resolved = sum(1 for c in agent_ended if c.outcome == "resolved")
            agent_resolution = (agent_resolved / len(agent_ended) * 100) if agent_ended else 0.0
            agent_handle = [
                (c.ended_at - c.started_at).total_seconds()
                for c in agent_ended
                if c.ended_at is not None
            ]
            agent_traces = [t for t in traces if t.agent_id == summary.id]
            turn_counts: dict[str, int] = {}
            for t in agent_traces:
                turn_counts[t.conversation_id] = turn_counts.get(t.conversation_id, 0) + 1
            agent_conversation_ids = {c.conversation_id for c in agent_live}
            relevant_turns = [v for k, v in turn_counts.items() if k in agent_conversation_ids]
            avg_turns = sum(relevant_turns) / len(relevant_turns) if relevant_turns else 0.0

            agent_perf.append(DashboardPerformance(
                agent_id=summary.id,
                agent_name=summary.name,
                status="published" if summary.current_published_version_id else "draft",
                conversation_count=len(agent_live),
                active_conversations=agent_active,
                resolution_rate=agent_resolution,
                avg_turns_per_conversation=avg_turns,
                avg_handle_time_seconds=sum(agent_handle) / len(agent_handle) if agent_handle else 0.0,
            ))

        now = datetime.now(timezone.utc)
        trend: list[DashboardResolutionPoint] = []
        for offset in range(days - 1, -1, -1):
            day_start = (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            day_ended = [
                c for c in ended
                if c.ended_at is not None and day_start <= c.ended_at < day_end
            ]
            day_resolved = sum(1 for c in day_ended if c.outcome == "resolved")
            day_rate = (day_resolved / len(day_ended) * 100) if day_ended else 0.0
            trend.append(DashboardResolutionPoint(
                date=day_start.strftime("%Y-%m-%d"),
                resolved=day_resolved,
                total=len(day_ended),
                rate=round(day_rate, 1),
            ))

        return DashboardStats(
            total_agents=len(agents),
            active_conversations=active_count,
            resolution_rate=round(resolution_rate, 1),
            avg_handle_time_seconds=round(avg_handle_time, 1),
            agent_performance=agent_perf,
            resolution_trend=trend,
        )

    @router.get("/agents/{agent_id}/metrics", response_model=AgentOperationalMetrics)
    def get_agent_metrics(
        agent_id: str,
        request: Request,
        agent_version_id: str | None = None,
    ) -> AgentOperationalMetrics:
        organization_id = get_organization_id(request)
        conversations = conversation_store.list_conversations(
            organization_id=organization_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
        )
        traces = trace_store.all(
            organization_id=organization_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
        )
        return build_agent_metrics(
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            conversations=conversations,
            traces=traces,
        )

    return router
