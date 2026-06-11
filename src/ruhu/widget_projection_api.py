from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .attachments import AttachmentProjection, AttachmentRuntime
from .browser_tasks import BrowserTaskService, BrowserTaskSnapshot
from .realtime import RealtimeControlPlane
from .schemas import ConversationState
from .session_http import read_request_body_limited
from .tools.types import ToolInvocation

ConversationLoader = Callable[[str], ConversationState | None]
PendingInvocationsLoader = Callable[[str], list[ToolInvocation]]
ConversationRequestAuthorizer = Callable[[Request, ConversationState], None]

logger = logging.getLogger(__name__)


class BrowserApprovalDecisionRequest(BaseModel):
    reason: str | None = None


class BrowserTaskCancelRequest(BaseModel):
    reason: str = "cancelled by user"


class WidgetBrowserApprovalProjection(BaseModel):
    approval_id: str
    kind: str
    state: str
    prompt: str
    expires_at: datetime | None = None
    task_pack_label: str | None = None
    domain_label: str | None = None
    performs_write: bool = False
    approval_kind: str | None = None
    credential_labels: list[str] = Field(default_factory=list)


class WidgetBrowserArtifactProjection(BaseModel):
    artifact_id: str
    filename: str | None = None
    kind: str | None = None
    content_type: str | None = None
    public_widget_download_url: str | None = None


class WidgetBrowserTaskProjection(BaseModel):
    task_id: str
    title: str
    summary: str | None = None
    state: str
    approval_state: str
    task_pack_id: str | None = None
    task_pack_version: str | None = None
    task_pack_label: str | None = None
    domain_label: str | None = None
    latest_progress: str | None = None
    approval: WidgetBrowserApprovalProjection | None = None
    artifacts: list[WidgetBrowserArtifactProjection] = Field(default_factory=list)
    cancellable: bool = False
    show_live_snapshot: bool = False
    live_snapshot_artifact_id: str | None = None
    updated_at: datetime


class WidgetProjectionSnapshot(BaseModel):
    snapshot_id: str
    conversation_id: str
    pending_tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    attachments: list[AttachmentProjection] = Field(default_factory=list)
    browser_tasks: list[WidgetBrowserTaskProjection] = Field(default_factory=list)
    interaction_status: list[dict[str, Any]] = Field(default_factory=list)
    voice_activity: dict[str, Any] | None = None
    voice_interaction_policy: dict[str, Any] | None = None
    # Names of snapshot components whose fetch raised. Empty means the snapshot
    # is complete; a non-empty list signals that those component lists/dicts
    # fell back to empty/None and consumers should not treat them as ground
    # truth.
    degraded_components: list[str] = Field(default_factory=list)


def install_widget_projection_router(
    app: FastAPI,
    *,
    attachment_runtime: AttachmentRuntime | None,
    browser_task_service: BrowserTaskService | None,
    realtime_control_plane: RealtimeControlPlane | None,
    load_conversation: ConversationLoader,
    list_pending_tool_invocations: PendingInvocationsLoader,
    authorize_conversation_request: ConversationRequestAuthorizer,
) -> None:
    router = APIRouter(tags=["widget"])

    def _conversation(conversation_id: str) -> ConversationState:
        conversation = load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        return conversation

    def _attachment_runtime() -> AttachmentRuntime:
        if attachment_runtime is None:
            raise HTTPException(status_code=503, detail="attachment runtime is not configured")
        return attachment_runtime

    def _browser_tasks() -> BrowserTaskService:
        if browser_task_service is None:
            raise HTTPException(status_code=503, detail="browser task service is not configured")
        return browser_task_service

    def _raise_upload_error(exc: ValueError) -> None:
        detail = str(exc)
        status_code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if "exceeds limit" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc

    def _snapshot(conversation_id: str) -> WidgetProjectionSnapshot:
        conversation = _conversation(conversation_id)
        attachments: list[AttachmentProjection] = []
        browser_tasks: list[WidgetBrowserTaskProjection] = []
        pending_tool_invocations: list[ToolInvocation] = []
        degraded: list[str] = []
        if attachment_runtime is not None:
            try:
                attachments = attachment_runtime.service.list_conversation_attachments(
                    conversation_id=conversation_id,
                    organization_id=conversation.organization_id,
                )
            except Exception:
                logger.exception(
                    "widget projection attachments snapshot failed",
                    extra={"conversation_id": conversation_id},
                )
                attachments = []
                degraded.append("attachments")
        if browser_task_service is not None:
            try:
                browser_tasks = [
                    _project_browser_task(snapshot)
                    for snapshot in browser_task_service.list_conversation_tasks(
                        conversation_id=conversation_id,
                        organization_id=conversation.organization_id,
                    )
                ]
            except Exception:
                logger.exception(
                    "widget projection browser-task snapshot failed",
                    extra={"conversation_id": conversation_id},
                )
                browser_tasks = []
                degraded.append("browser_tasks")
        try:
            pending_tool_invocations = list_pending_tool_invocations(conversation_id)
        except Exception:
            logger.exception(
                "widget projection pending-tool snapshot failed",
                extra={"conversation_id": conversation_id},
            )
            pending_tool_invocations = []
            degraded.append("pending_tool_invocations")
        try:
            interaction_status = _interaction_status_snapshot(conversation)
        except Exception:
            logger.exception(
                "widget projection interaction-status snapshot failed",
                extra={"conversation_id": conversation_id},
            )
            interaction_status = []
            degraded.append("interaction_status")
        try:
            voice_activity = _latest_voice_activity(conversation_id)
        except Exception:
            logger.exception(
                "widget projection voice-activity snapshot failed",
                extra={"conversation_id": conversation_id},
            )
            voice_activity = None
            degraded.append("voice_activity")
        try:
            voice_interaction_policy = _latest_voice_interaction_policy(conversation_id)
        except Exception:
            logger.exception(
                "widget projection voice-policy snapshot failed",
                extra={"conversation_id": conversation_id},
            )
            voice_interaction_policy = None
            degraded.append("voice_interaction_policy")
        try:
            digest_basis = jsonable_encoder({
                "pending_tool_invocations": [
                    {
                        "invocation_id": item.invocation_id,
                        "status": item.status,
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in pending_tool_invocations
                ],
                "attachments": [
                    {
                        "attachment_id": item.attachment.attachment_id,
                        "scan_status": item.attachment.scan_status,
                        "extraction_status": item.attachment.extraction_status,
                        "updated_at": item.attachment.updated_at.isoformat(),
                    }
                    for item in attachments
                ],
                "browser_tasks": [
                    {
                        "task_id": item.task_id,
                        "state": item.state,
                        "approval_state": item.approval_state,
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in browser_tasks
                ],
                "interaction_status": interaction_status,
                "voice_activity": voice_activity,
                "voice_interaction_policy": voice_interaction_policy,
                "degraded_components": sorted(degraded),
            })
            snapshot_id = hashlib.sha1(
                json.dumps(digest_basis, ensure_ascii=True, sort_keys=True).encode("utf-8")
            ).hexdigest()
        except Exception:
            logger.exception(
                "widget projection snapshot hash failed",
                extra={"conversation_id": conversation_id},
            )
            snapshot_id = hashlib.sha1(
                f"degraded:{conversation_id}".encode("utf-8")
            ).hexdigest()
        return WidgetProjectionSnapshot(
            snapshot_id=snapshot_id,
            conversation_id=conversation_id,
            pending_tool_invocations=pending_tool_invocations,
            attachments=attachments,
            browser_tasks=browser_tasks,
            interaction_status=interaction_status,
            voice_activity=voice_activity,
            voice_interaction_policy=voice_interaction_policy,
            degraded_components=sorted(degraded),
        )

    def _interaction_status_snapshot(conversation: ConversationState) -> list[dict[str, Any]]:
        control = conversation.control_state
        items: list[dict[str, Any]] = []
        if control.pending_action is not None:
            label = (
                control.pending_action.action_label
                or control.pending_action.tool_ref
                or control.pending_action.action_type
            )
            items.append(
                {
                    "item_id": f"activity:{control.pending_action.action_id}",
                    "item_type": "activity",
                    "summary": f"{label}: {control.pending_action.status}",
                    "source_ref": control.pending_action.action_id,
                }
            )
        if control.pending_permission is not None:
            label = (
                control.pending_permission.user_visible_context.get("action_label")
                or control.pending_permission.permission_kind
            )
            items.append(
                {
                    "item_id": f"permission:{control.pending_permission.request_id}",
                    "item_type": "permission",
                    "summary": f"{label}: {control.pending_permission.status}",
                    "source_ref": control.pending_permission.request_id,
                }
            )
        if control.active_repair is not None:
            repair_ref = control.active_repair.target_ref or control.active_repair.repair_kind
            items.append(
                {
                    "item_id": f"repair:{repair_ref}",
                    "item_type": "repair",
                    "summary": control.active_repair.summary or control.active_repair.repair_kind,
                    "source_ref": control.active_repair.target_ref,
                }
            )
        return items

    def _safe_string(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _domain_label(snapshot: BrowserTaskSnapshot) -> str | None:
        context = snapshot.approval.context if snapshot.approval is not None else {}
        allowed_domains = context.get("allowed_domains")
        if isinstance(allowed_domains, list):
            for item in allowed_domains:
                label = _safe_string(item)
                if label is not None:
                    return label
        start_url = _safe_string(context.get("start_url")) or snapshot.task.start_url
        if start_url:
            parsed = urlparse(start_url)
            if parsed.hostname:
                return parsed.hostname
        return None

    def _task_pack_label(snapshot: BrowserTaskSnapshot) -> str | None:
        context = snapshot.approval.context if snapshot.approval is not None else {}
        return _safe_string(context.get("task_pack_display_name")) or snapshot.task.task_pack_id

    def _approval_projection(
        snapshot: BrowserTaskSnapshot,
    ) -> WidgetBrowserApprovalProjection | None:
        approval = snapshot.approval
        if approval is None:
            return None
        context = approval.context
        credential_labels: list[str] = []
        credential_refs = context.get("credential_refs")
        if isinstance(credential_refs, list):
            for item in credential_refs:
                if not isinstance(item, dict):
                    continue
                label = _safe_string(item.get("ref_label"))
                name = _safe_string(item.get("name"))
                kind = _safe_string(item.get("kind"))
                if label and name:
                    credential_labels.append(f"{name}: {label}")
                elif label:
                    credential_labels.append(label)
                elif name and kind:
                    credential_labels.append(f"{name}: {kind}")
        return WidgetBrowserApprovalProjection(
            approval_id=approval.approval_id,
            kind=approval.kind,
            state=approval.state,
            prompt=approval.prompt,
            expires_at=approval.expires_at,
            task_pack_label=_task_pack_label(snapshot),
            domain_label=_domain_label(snapshot),
            performs_write=bool(context.get("performs_write", False)),
            approval_kind=_safe_string(context.get("approval_kind")) or approval.kind,
            credential_labels=credential_labels,
        )

    def _artifact_projection(item: object) -> WidgetBrowserArtifactProjection | None:
        if not isinstance(item, dict):
            return None
        artifact_id = _safe_string(item.get("artifact_id"))
        if artifact_id is None:
            return None
        return WidgetBrowserArtifactProjection(
            artifact_id=artifact_id,
            filename=_safe_string(item.get("filename")),
            kind=_safe_string(item.get("kind")),
            content_type=_safe_string(item.get("content_type")),
            public_widget_download_url=_safe_string(item.get("public_widget_download_url")),
        )

    def _project_browser_task(snapshot: BrowserTaskSnapshot) -> WidgetBrowserTaskProjection:
        raw_artifacts = snapshot.task.result.get("artifacts")
        artifacts = (
            [
                projected
                for item in raw_artifacts
                if (projected := _artifact_projection(item)) is not None
            ]
            if isinstance(raw_artifacts, list)
            else []
        )
        latest_progress = snapshot.task.summary
        if snapshot.recent_events:
            latest_progress = snapshot.recent_events[-1].message or latest_progress
        return WidgetBrowserTaskProjection(
            task_id=snapshot.task.task_id,
            title=snapshot.task.title,
            summary=snapshot.task.summary,
            state=snapshot.task.state,
            approval_state=snapshot.task.approval_state,
            task_pack_id=snapshot.task.task_pack_id,
            task_pack_version=snapshot.task.task_pack_version,
            task_pack_label=_task_pack_label(snapshot),
            domain_label=_domain_label(snapshot),
            latest_progress=latest_progress,
            approval=_approval_projection(snapshot),
            artifacts=artifacts,
            cancellable=snapshot.task.state not in {"completed", "failed", "cancelled"},
            show_live_snapshot=False,
            live_snapshot_artifact_id=None,
            updated_at=snapshot.task.updated_at,
        )

    def _latest_voice_activity(conversation_id: str) -> dict[str, Any] | None:
        if realtime_control_plane is None:
            return None
        try:
            events = realtime_control_plane.events.replay(conversation_id=conversation_id)
        except Exception:
            return None
        for event in reversed(events):
            if event.family != "voice":
                continue
            return {
                "name": event.name,
                "payload": jsonable_encoder(dict(event.payload)),
                "created_at": event.created_at.isoformat(),
            }
        return None

    def _latest_voice_interaction_policy(conversation_id: str) -> dict[str, Any] | None:
        if realtime_control_plane is None:
            return None
        sessions_store = getattr(realtime_control_plane, "sessions", None)
        if sessions_store is None:
            return None
        try:
            sessions = sessions_store.list_by_conversation(conversation_id)
        except Exception:
            return None
        candidates: list[tuple[bool, Any, Any, dict[str, Any]]] = []
        for session in sessions:
            if getattr(session, "surface", None) != "voice":
                continue
            transport_metadata = getattr(session, "transport_metadata", None)
            if not isinstance(transport_metadata, dict):
                continue
            policy = transport_metadata.get("voice_interaction_policy")
            if not isinstance(policy, dict) or not policy:
                continue
            last_seen = getattr(session, "last_seen_at", None) or getattr(session, "updated_at", None)
            created_at = getattr(session, "created_at", None)
            candidates.append(
                (
                    getattr(session, "status", None) == "active",
                    last_seen.isoformat() if hasattr(last_seen, "isoformat") else "",
                    created_at.isoformat() if hasattr(created_at, "isoformat") else "",
                    dict(policy),
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return candidates[0][3]

    def _record_projection_event(
        *,
        conversation: ConversationState,
        name: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if realtime_control_plane is None:
            return
        realtime_control_plane.events.append(
            conversation_id=conversation.conversation_id,
            organization_id=conversation.organization_id,
            family="projection",
            name=name,
            payload={
                "channel": "web_widget",
                "transport": "sse" if name.startswith("widget_stream_") else "poll",
                **dict(payload or {}),
            },
            actor_type="system",
            visibility="internal",
            outbox_topic="conversation_projection",
        )

    @router.get(
        "/public/widget/sessions/{conversation_id}/attachments",
        response_model=list[AttachmentProjection],
    )
    def list_widget_attachments(conversation_id: str, request: Request) -> list[AttachmentProjection]:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        return _attachment_runtime().service.list_conversation_attachments(
            conversation_id=conversation_id,
            organization_id=conversation.organization_id,
        )

    @router.post(
        "/public/widget/sessions/{conversation_id}/attachments",
        response_model=AttachmentProjection,
    )
    async def upload_widget_attachment(
        conversation_id: str,
        request: Request,
        filename: str = Query(..., min_length=1),
    ) -> AttachmentProjection:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        runtime = _attachment_runtime()
        payload = await read_request_body_limited(
            request,
            max_bytes=runtime.service.max_file_bytes,
            resource_name="attachment",
        )
        try:
            attachment = runtime.service.upload_attachment(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
                channel="web_widget",
                filename=filename,
                content_type=request.headers.get("content-type", "application/octet-stream"),
                content_bytes=payload,
            )
        except ValueError as exc:
            _raise_upload_error(exc)
        runtime.schedule_processing(
            attachment_id=attachment.attachment_id,
            organization_id=conversation.organization_id,
        )
        projection = runtime.service.get_projection(
            attachment_id=attachment.attachment_id,
            organization_id=conversation.organization_id,
        )
        if projection is None:
            raise HTTPException(status_code=500, detail="attachment was not stored")
        return projection

    @router.get(
        "/public/widget/sessions/{conversation_id}/attachments/{attachment_id}",
        response_model=AttachmentProjection,
    )
    def get_widget_attachment(conversation_id: str, attachment_id: str, request: Request) -> AttachmentProjection:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        projection = _attachment_runtime().service.get_projection(
            attachment_id=attachment_id,
            organization_id=conversation.organization_id,
        )
        if projection is None or projection.attachment.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="unknown attachment id")
        return projection

    @router.get("/public/widget/sessions/{conversation_id}/attachments/{attachment_id}/download")
    def download_widget_attachment(conversation_id: str, attachment_id: str, request: Request) -> Response:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        payload = _attachment_runtime().service.get_attachment_bytes(
            attachment_id=attachment_id,
            organization_id=conversation.organization_id,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown attachment id")
        attachment, content_bytes = payload
        if attachment.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="unknown attachment id")
        headers = {
            "Content-Disposition": f'attachment; filename="{attachment.filename}"',
            "X-Ruhu-Attachment-Id": attachment.attachment_id,
        }
        return Response(content=content_bytes, media_type=attachment.content_type, headers=headers)

    @router.get("/public/widget/sessions/{conversation_id}/artifacts/{artifact_id}/download")
    def download_widget_artifact(conversation_id: str, artifact_id: str, request: Request) -> Response:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        payload = _attachment_runtime().service.get_artifact_bytes(
            artifact_id=artifact_id,
            organization_id=conversation.organization_id,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown artifact id")
        artifact, content_bytes = payload
        if artifact.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="unknown artifact id")
        headers = {
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Ruhu-Artifact-Id": artifact.artifact_id,
        }
        return Response(content=content_bytes, media_type=artifact.content_type, headers=headers)

    @router.get(
        "/public/widget/sessions/{conversation_id}/browser-tasks",
        response_model=list[WidgetBrowserTaskProjection],
    )
    def list_widget_browser_tasks(conversation_id: str, request: Request) -> list[WidgetBrowserTaskProjection]:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        return [
            _project_browser_task(snapshot)
            for snapshot in _browser_tasks().list_conversation_tasks(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
            )
        ]

    @router.post(
        "/public/widget/sessions/{conversation_id}/browser-tasks/{task_id}/approvals/{approval_id}/approve",
        response_model=WidgetBrowserTaskProjection,
    )
    def approve_widget_browser_task(
        conversation_id: str,
        task_id: str,
        approval_id: str,
        request: Request,
        payload: BrowserApprovalDecisionRequest | None = None,
    ) -> WidgetBrowserTaskProjection:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        task_snapshot = _browser_tasks().get_snapshot(task_id, organization_id=conversation.organization_id)
        if task_snapshot.task.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="unknown browser task id")
        if task_snapshot.approval is None or task_snapshot.approval.approval_id != approval_id:
            raise HTTPException(status_code=404, detail="unknown browser task id")
        try:
            snapshot = _browser_tasks().approve(
                approval_id=approval_id,
                organization_id=conversation.organization_id,
                reason=None if payload is None else payload.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if snapshot.task.task_id != task_id or snapshot.task.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="unknown browser task id")
        return _project_browser_task(snapshot)

    @router.post(
        "/public/widget/sessions/{conversation_id}/browser-tasks/{task_id}/approvals/{approval_id}/deny",
        response_model=WidgetBrowserTaskProjection,
    )
    def deny_widget_browser_task(
        conversation_id: str,
        task_id: str,
        approval_id: str,
        request: Request,
        payload: BrowserApprovalDecisionRequest | None = None,
    ) -> WidgetBrowserTaskProjection:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        task_snapshot = _browser_tasks().get_snapshot(task_id, organization_id=conversation.organization_id)
        if task_snapshot.task.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="unknown browser task id")
        if task_snapshot.approval is None or task_snapshot.approval.approval_id != approval_id:
            raise HTTPException(status_code=404, detail="unknown browser task id")
        try:
            snapshot = _browser_tasks().deny(
                approval_id=approval_id,
                organization_id=conversation.organization_id,
                reason=None if payload is None else payload.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if snapshot.task.task_id != task_id or snapshot.task.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="unknown browser task id")
        return _project_browser_task(snapshot)

    @router.post(
        "/public/widget/sessions/{conversation_id}/browser-tasks/{task_id}/cancel",
        response_model=WidgetBrowserTaskProjection,
    )
    def cancel_widget_browser_task(
        conversation_id: str,
        task_id: str,
        request: Request,
        payload: BrowserTaskCancelRequest | None = None,
    ) -> WidgetBrowserTaskProjection:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        task_snapshot = _browser_tasks().get_snapshot(task_id, organization_id=conversation.organization_id)
        if task_snapshot.task.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="unknown browser task id")
        try:
            snapshot = _browser_tasks().cancel_task(
                task_id=task_id,
                organization_id=conversation.organization_id,
                reason="cancelled by user" if payload is None else payload.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if snapshot.task.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="unknown browser task id")
        return _project_browser_task(snapshot)

    @router.get(
        "/public/widget/sessions/{conversation_id}/projection",
        response_model=WidgetProjectionSnapshot,
    )
    def get_widget_projection_snapshot(conversation_id: str, request: Request) -> WidgetProjectionSnapshot:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)
        snapshot = _snapshot(conversation_id)
        _record_projection_event(
            conversation=conversation,
            name="widget_snapshot_requested",
            payload={"snapshot_id": snapshot.snapshot_id},
        )
        return snapshot

    @router.get("/public/widget/sessions/{conversation_id}/events")
    async def stream_widget_projection_events(
        conversation_id: str,
        request: Request,
        poll_interval_seconds: float = Query(1.0, ge=0.25, le=10.0),
    ) -> StreamingResponse:
        conversation = _conversation(conversation_id)
        authorize_conversation_request(request, conversation)

        async def _stream():
            last_snapshot_id: str | None = None
            _record_projection_event(conversation=conversation, name="widget_stream_opened")
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    snapshot = _snapshot(conversation_id)
                    event_name = "heartbeat"
                    data: dict[str, Any] = {}
                    if snapshot.snapshot_id != last_snapshot_id:
                        last_snapshot_id = snapshot.snapshot_id
                        event_name = "widget.snapshot"
                        data = snapshot.model_dump(mode="json")
                    yield f"event: {event_name}\n".encode("utf-8")
                    yield f"data: {json.dumps(data, ensure_ascii=True)}\n\n".encode("utf-8")
                    await asyncio.sleep(poll_interval_seconds)
            finally:
                _record_projection_event(
                    conversation=conversation,
                    name="widget_stream_closed",
                    payload={"last_snapshot_id": last_snapshot_id},
                )

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    app.include_router(router)
