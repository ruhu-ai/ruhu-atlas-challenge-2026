"""HTTP API for the continuous evaluation foundation.

Endpoints:
  - ``GET /live-eval/scores/trace/{trace_id}`` — every score for one trace
  - ``GET /live-eval/scores/conversation/{conversation_id}`` — score timeline
  - ``GET /live-eval/conversations/{conversation_id}/summary`` — rolled-up
    per-dimension stats (count + mean + min + max)

All endpoints are tenant-scoped: the path's conversation/trace must belong
to the authenticated principal's organization, otherwise we return 404
(rather than 403 — leaking which IDs exist across tenants is itself a
weak data leak).

The router is only installed when ``settings.live_eval_enabled`` is true,
because we don't want consumers querying an empty store and assuming
"the system is broken" when actually it's just disabled. With the flag
off, these endpoints return 404 (the route doesn't exist), which is the
right signal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .api_auth import require_authenticated_context
from .live_eval import (
    LiveEvalRuntime,
    QualityDimension,
    rollup_by_dimension,
)


class LiveTurnScoreResponse(BaseModel):
    """Wire shape for a single ``LiveTurnScore`` row."""

    trace_id: str
    conversation_id: str
    organization_id: str | None
    agent_id: str
    dimension: QualityDimension
    score: float = Field(ge=0.0, le=1.0)
    scorer_name: str
    scorer_version: str
    notes: str | None = None
    scored_at: datetime


class DimensionRollupResponse(BaseModel):
    """Aggregate stats for one dimension across a set of scores."""

    dimension: QualityDimension
    count: int
    mean: float
    min: float
    max: float


class ConversationRollupResponse(BaseModel):
    """Per-conversation summary across all 4 quality dimensions.

    Dimensions with zero scored turns are omitted from ``dimensions`` —
    callers shouldn't synthesise placeholder stats for missing data.
    """

    conversation_id: str
    organization_id: str
    total_score_count: int
    dimensions: list[DimensionRollupResponse]


def install_live_eval_router(
    app: FastAPI,
    *,
    runtime: LiveEvalRuntime,
    rate_limiter=None,
) -> None:
    """Mount /live-eval/* read-only endpoints onto ``app``.

    ``rate_limiter`` is the existing org-level Depends(), reused so live-
    eval queries respect the same tier-based quotas as the rest of the
    authenticated API.
    """
    router = APIRouter(
        prefix="/live-eval",
        tags=["live-eval"],
        dependencies=[rate_limiter] if rate_limiter else [],
    )

    def _principal(request: Request):
        ctx = require_authenticated_context(request)
        p = ctx.principal
        if p is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return p

    def _ensure_org_match(
        record_org_id: str | None,
        principal_org_id: str,
    ) -> None:
        """Refuse cross-tenant reads with a 404 (not 403) — see module docstring."""
        if record_org_id != principal_org_id:
            raise HTTPException(status_code=404, detail="not found")

    @router.get(
        "/scores/trace/{trace_id}",
        response_model=list[LiveTurnScoreResponse],
    )
    def list_scores_for_trace(
        trace_id: str,
        request: Request,
    ) -> list[LiveTurnScoreResponse]:
        principal = _principal(request)
        scores = runtime.store.list_for_trace(trace_id)
        # Filter to the principal's organization. Rows that don't belong
        # to this tenant are dropped silently — RLS would do this for the
        # SQL store, but the in-memory store doesn't enforce it; doing it
        # at the API layer is a defence-in-depth.
        return [
            _to_response(s)
            for s in scores
            if s.organization_id == principal.organization.organization_id
        ]

    @router.get(
        "/scores/conversation/{conversation_id}",
        response_model=list[LiveTurnScoreResponse],
    )
    def list_scores_for_conversation(
        conversation_id: str,
        request: Request,
    ) -> list[LiveTurnScoreResponse]:
        principal = _principal(request)
        scores = runtime.store.list_for_conversation(conversation_id)
        return [
            _to_response(s)
            for s in scores
            if s.organization_id == principal.organization.organization_id
        ]

    @router.get(
        "/conversations/{conversation_id}/summary",
        response_model=ConversationRollupResponse,
    )
    def get_conversation_summary(
        conversation_id: str,
        request: Request,
        since: datetime | None = Query(
            default=None,
            description=(
                "Optional lower bound (inclusive) on score.scored_at. "
                "Use to narrow the rollup to a recent window — e.g. "
                "?since=2026-05-01T00:00:00Z for the last 24h."
            ),
        ),
        until: datetime | None = Query(
            default=None,
            description="Optional upper bound (exclusive) on score.scored_at.",
        ),
    ) -> ConversationRollupResponse:
        principal = _principal(request)
        principal_org_id = principal.organization.organization_id
        if since is not None and until is not None and since >= until:
            raise HTTPException(
                status_code=422,
                detail="`since` must be earlier than `until`",
            )
        scores = [
            s
            for s in runtime.store.list_for_conversation(conversation_id)
            if s.organization_id == principal_org_id
            and _within_window(s.scored_at, since, until)
        ]
        rollups = rollup_by_dimension(scores)
        # Sort by dimension name so the response is deterministic across
        # repeated calls — useful for cache keys and snapshot tests.
        ordered = sorted(rollups.values(), key=lambda r: r.dimension)
        return ConversationRollupResponse(
            conversation_id=conversation_id,
            organization_id=principal_org_id,
            total_score_count=sum(r.count for r in ordered),
            dimensions=[
                DimensionRollupResponse(
                    dimension=r.dimension,
                    count=r.count,
                    mean=r.mean,
                    min=r.min,
                    max=r.max,
                )
                for r in ordered
            ],
        )

    app.include_router(router)


def _to_response(score) -> LiveTurnScoreResponse:
    return LiveTurnScoreResponse(
        trace_id=score.trace_id,
        conversation_id=score.conversation_id,
        organization_id=score.organization_id,
        agent_id=score.agent_id,
        dimension=score.dimension,
        score=score.score,
        scorer_name=score.scorer_name,
        scorer_version=score.scorer_version,
        notes=score.notes,
        scored_at=score.scored_at,
    )


def _within_window(
    scored_at: datetime,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    """Inclusive-since / exclusive-until window check.

    Naive datetimes are coerced to UTC before comparison so callers can
    pass ``"2026-05-01"`` (FastAPI parses it as a naive datetime) without
    surprise behaviour. Time-zone awareness is the responsibility of
    well-formed callers; this helper just makes "no tzinfo" → "UTC" so
    the comparison doesn't raise.
    """
    if scored_at is None:
        # Defensive: a row with NULL scored_at shouldn't exist (model
        # column is NOT NULL) but if it ever does, exclude it from the
        # window rather than crash on the comparison.
        return since is None and until is None
    ts = scored_at if scored_at.tzinfo else scored_at.replace(tzinfo=timezone.utc)
    if since is not None:
        s = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        if ts < s:
            return False
    if until is not None:
        u = until if until.tzinfo else until.replace(tzinfo=timezone.utc)
        if ts >= u:
            return False
    return True
