"""Audit API endpoints.

All endpoints are org-scoped via the authenticated principal. No cross-tenant
access is possible — the organization_id is always taken from the auth context,
never from a query parameter.

Endpoints:
  GET  /audit/events                      — list with filters
  GET  /audit/events/{event_id}           — single event detail
  GET  /audit/resources/{type}/{id}       — timeline for a specific resource
  GET  /audit/actors/{user_id}            — timeline for a specific user
  GET  /audit/stats                       — aggregate stats
  POST /audit/export                      — JSON/CSV export
"""
from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ruhu.api_auth import RequestAuthContext, require_authenticated_context

from .store import AuditStore


# ── Response models ──────────────────────────────────────────────────────────

class AuditEventResponse(BaseModel):
    event_id: str
    organization_id: str
    actor_id: str | None
    actor_ip: str | None
    actor_session_id: str | None
    event_type: str
    operation: str
    resource_type: str | None
    resource_id: str | None
    detail: dict[str, Any]
    outcome: str
    http_method: str | None
    http_path: str | None
    http_status: int | None
    duration_ms: int | None
    request_id: str | None
    content_hash: str
    prev_hash: str | None
    created_at: str


class AuditStatsResponse(BaseModel):
    total_events: int
    events_by_type: dict[str, int]
    events_by_outcome: dict[str, int]
    events_by_operation: dict[str, int]
    period_start: str | None
    period_end: str | None


class AuditExportRequest(BaseModel):
    format: str = Field(default="json", pattern="^(json|csv)$")
    event_type: str | None = None
    operation: str | None = None
    resource_type: str | None = None
    actor_id: str | None = None
    outcome: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    limit: int = Field(default=10000, ge=1, le=100000)


# ── Router factory ───────────────────────────────────────────────────────────

def build_audit_router(store: AuditStore) -> APIRouter:
    router = APIRouter(prefix="/audit", tags=["audit"])

    def _org_id(ctx: RequestAuthContext) -> str:
        return ctx.principal.organization.organization_id

    @router.get("/events", response_model=list[AuditEventResponse])
    def list_events(
        ctx: RequestAuthContext = Depends(require_authenticated_context),
        event_type: str | None = Query(None),
        operation: str | None = Query(None),
        resource_type: str | None = Query(None),
        resource_id: str | None = Query(None),
        actor_id: str | None = Query(None),
        outcome: str | None = Query(None),
        start_date: str | None = Query(None),
        end_date: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict]:
        events = store.list_events(
            organization_id=_org_id(ctx),
            event_type=event_type,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
            outcome=outcome,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return [e.to_dict() for e in events]

    @router.get("/events/{event_id}", response_model=AuditEventResponse)
    def get_event(
        event_id: str,
        ctx: RequestAuthContext = Depends(require_authenticated_context),
    ) -> dict:
        event = store.get(event_id, organization_id=_org_id(ctx))
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audit event not found")
        return event.to_dict()

    @router.get("/resources/{resource_type}/{resource_id}", response_model=list[AuditEventResponse])
    def resource_timeline(
        resource_type: str,
        resource_id: str,
        ctx: RequestAuthContext = Depends(require_authenticated_context),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict]:
        events = store.list_events(
            organization_id=_org_id(ctx),
            resource_type=resource_type,
            resource_id=resource_id,
            limit=limit,
            offset=offset,
        )
        return [e.to_dict() for e in events]

    @router.get("/actors/{actor_id}", response_model=list[AuditEventResponse])
    def actor_timeline(
        actor_id: str,
        ctx: RequestAuthContext = Depends(require_authenticated_context),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict]:
        events = store.list_events(
            organization_id=_org_id(ctx),
            actor_id=actor_id,
            limit=limit,
            offset=offset,
        )
        return [e.to_dict() for e in events]

    @router.get("/stats", response_model=AuditStatsResponse)
    def audit_stats(
        ctx: RequestAuthContext = Depends(require_authenticated_context),
        start_date: str | None = Query(None),
        end_date: str | None = Query(None),
    ) -> dict:
        org_id = _org_id(ctx)
        total = store.count_events(organization_id=org_id, start_date=start_date, end_date=end_date)

        # Count by event type
        event_types = [
            "resource.created", "resource.updated", "resource.deleted",
            "auth.login", "auth.login_failed", "auth.logout",
            "security.permission_denied", "admin.settings_changed",
        ]
        by_type: dict[str, int] = {}
        for et in event_types:
            c = store.count_events(organization_id=org_id, event_type=et, start_date=start_date, end_date=end_date)
            if c > 0:
                by_type[et] = c

        # Count by outcome
        by_outcome: dict[str, int] = {}
        for oc in ("success", "failure", "denied"):
            c = store.count_events(organization_id=org_id, outcome=oc, start_date=start_date, end_date=end_date)
            if c > 0:
                by_outcome[oc] = c

        return {
            "total_events": total,
            "events_by_type": by_type,
            "events_by_outcome": by_outcome,
            "events_by_operation": {},
            "period_start": start_date,
            "period_end": end_date,
        }

    @router.post("/export")
    def export_events(
        body: AuditExportRequest,
        ctx: RequestAuthContext = Depends(require_authenticated_context),
    ) -> StreamingResponse:
        events = store.list_events(
            organization_id=_org_id(ctx),
            event_type=body.event_type,
            operation=body.operation,
            resource_type=body.resource_type,
            actor_id=body.actor_id,
            outcome=body.outcome,
            start_date=body.start_date,
            end_date=body.end_date,
            limit=body.limit,
        )
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())

        if body.format == "csv":
            return _export_csv(events, timestamp)
        return _export_json(events, timestamp)

    return router


# ── Export helpers ────────────────────────────────────────────────────────────

_CSV_COLUMNS = [
    "event_id", "created_at", "event_type", "operation", "outcome",
    "actor_id", "resource_type", "resource_id", "http_method",
    "http_path", "http_status", "duration_ms", "actor_ip", "request_id",
]


def _export_csv(events, timestamp: str) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for event in events:
        writer.writerow(event.to_dict())
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit_events_{timestamp}.csv"'},
    )


def _export_json(events, timestamp: str) -> StreamingResponse:
    data = json.dumps([e.to_dict() for e in events], indent=2, default=str)
    return StreamingResponse(
        io.BytesIO(data.encode()),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="audit_events_{timestamp}.json"'},
    )
