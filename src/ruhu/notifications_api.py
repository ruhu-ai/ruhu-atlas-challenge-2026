from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request

from .api_auth import require_authenticated_context
from .notifications.models import (
    DismissResponse,
    MarkReadRequest,
    MarkedResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from .notifications.store import NotificationStore


def install_notifications_router(
    app: FastAPI,
    *,
    notification_store: NotificationStore,
) -> None:
    router = APIRouter(tags=["notifications"])

    def _principal(request: Request):
        ctx = require_authenticated_context(request)
        p = ctx.principal
        if p is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return p

    # ------------------------------------------------------------------
    # GET /notifications
    # ------------------------------------------------------------------

    @router.get("/notifications", response_model=list[NotificationResponse])
    def list_notifications(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        unread_only: bool = False,
    ) -> list[NotificationResponse]:
        principal = _principal(request)
        records = notification_store.list_for_user(
            principal.organization.organization_id,
            principal.user.user_id,
            limit=limit,
            unread_only=unread_only,
        )
        return [NotificationResponse.from_record(r) for r in records]

    # ------------------------------------------------------------------
    # GET /notifications/unread-count
    # ------------------------------------------------------------------

    @router.get("/notifications/unread-count", response_model=UnreadCountResponse)
    def get_unread_count(request: Request) -> UnreadCountResponse:
        principal = _principal(request)
        count = notification_store.count_unread(
            principal.organization.organization_id,
            principal.user.user_id,
        )
        return UnreadCountResponse(unread_count=count)

    # ------------------------------------------------------------------
    # POST /notifications/mark-read
    # ------------------------------------------------------------------

    @router.post("/notifications/mark-read", response_model=MarkedResponse)
    def mark_read(request: Request, body: MarkReadRequest) -> MarkedResponse:
        principal = _principal(request)
        marked = notification_store.mark_read(
            body.notification_id,
            principal.organization.organization_id,
            principal.user.user_id,
        )
        return MarkedResponse(marked=1 if marked else 0)

    # ------------------------------------------------------------------
    # POST /notifications/mark-read-all
    # ------------------------------------------------------------------

    @router.post("/notifications/mark-read-all", response_model=MarkedResponse)
    def mark_all_read(request: Request) -> MarkedResponse:
        principal = _principal(request)
        count = notification_store.mark_all_read(
            principal.organization.organization_id,
            principal.user.user_id,
        )
        return MarkedResponse(marked=count)

    # ------------------------------------------------------------------
    # POST /notifications/{notification_id}/dismiss
    # ------------------------------------------------------------------

    @router.post(
        "/notifications/{notification_id}/dismiss",
        response_model=DismissResponse,
    )
    def dismiss_notification(
        notification_id: str,
        request: Request,
    ) -> DismissResponse:
        principal = _principal(request)
        dismissed = notification_store.dismiss(
            notification_id,
            principal.organization.organization_id,
            principal.user.user_id,
        )
        return DismissResponse(dismissed=dismissed)

    app.include_router(router)
