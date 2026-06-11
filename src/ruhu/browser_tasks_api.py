from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .attachments import AttachmentRuntime, Artifact
from .api_auth import get_request_auth_context
from .browser_tasks import (
    BROWSER_TASKS_JOB_TYPE,
    BrowserOperatorCommand,
    BrowserTaskPack,
    BrowserTaskService,
    BrowserTaskSnapshot,
    BrowserWorkerProgress,
    BrowserWorkerRequest,
    BrowserWorkerResult,
)
from .jobs import JobStore, recurring_tick_status
from .session_http import read_request_body_limited
from .session_http import build_session_audit_context

RequestAuthorizer = Callable[[Request], None]


class BrowserTaskCreateRequest(BaseModel):
    conversation_id: str
    organization_id: str | None = None
    agent_id: str | None = None
    title: str
    summary: str | None = None
    requested_channel: str = "browser"
    task_pack_id: str | None = None
    task_pack_version: str | None = None
    start_url: str | None = None
    input_payload: dict[str, object] = Field(default_factory=dict)
    credential_refs: dict[str, str] = Field(default_factory=dict)
    requires_approval: bool = False
    approval_kind: str = "generic_access"
    approval_prompt: str | None = None
    approval_ttl_seconds: int | None = 300
    metadata: dict[str, object] = Field(default_factory=dict)


class BrowserTaskProgressRequest(BaseModel):
    event_type: str
    message: str
    state: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class BrowserTaskClaimRequest(BaseModel):
    worker_id: str
    organization_id: str | None = None
    lease_seconds: int = 60


class BrowserTaskLeaseRequest(BaseModel):
    worker_id: str
    lease_seconds: int = 60


class BrowserTaskReleaseRequest(BaseModel):
    worker_id: str
    reason: str = "worker released task lease"


class BrowserTaskTakeoverRequest(BaseModel):
    operator_id: str | None = None
    ttl_seconds: int = 300
    reason: str | None = None


class BrowserTaskTakeoverReleaseRequest(BaseModel):
    operator_id: str | None = None
    reason: str = "operator released takeover"


class BrowserTaskOperatorCommandRequest(BaseModel):
    operator_id: str | None = None
    command_type: str
    payload: dict[str, object] = Field(default_factory=dict)


class BrowserTaskOperatorCommandListRequest(BaseModel):
    worker_id: str
    limit: int = 100


class BrowserTaskOperatorCommandAckRequest(BaseModel):
    worker_id: str


class BrowserTaskOperatorCommandFailRequest(BaseModel):
    worker_id: str
    error: str


class BrowserTaskDecisionRequest(BaseModel):
    reason: str | None = None


class BrowserTaskCancelRequest(BaseModel):
    reason: str = "cancelled by operator"


class BrowserTaskRetryRequest(BaseModel):
    reason: str = "manual retry requested"


class BrowserTaskExpireApprovalsRequest(BaseModel):
    organization_id: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class BrowserTaskRuntimeSweepRequest(BaseModel):
    organization_id: str | None = None


class BrowserTaskRuntimeTickStatus(BaseModel):
    """Jobs-table view of the ``browser_tasks.tick`` recurring job.

    The browser task runtime lives in the worker process (``ruhu.worker``),
    so liveness is "a tick job is queued or running" and history is the most
    recently finished tick.
    """

    scheduled: bool = False
    last_tick_at: str | None = None
    last_tick_status: str | None = None
    last_error: str | None = None


class BrowserTaskRuntimeSweepResponse(BaseModel):
    expired_approvals: int = 0


class BrowserTaskWorkerRequestPayload(BaseModel):
    worker_id: str


class BrowserTaskWorkerProgressPayload(BaseModel):
    worker_id: str
    progress: BrowserWorkerProgress


class BrowserTaskWorkerResultPayload(BaseModel):
    worker_id: str
    result: BrowserWorkerResult


class BrowserTaskCompleteRequest(BaseModel):
    message: str = "Browser task completed."
    result: dict[str, object] = Field(default_factory=dict)


class BrowserTaskFailRequest(BaseModel):
    error: str


class BrowserTaskArtifactResponse(BaseModel):
    artifact: Artifact
    internal_download_url: str
    public_widget_download_url: str


class BrowserTaskPackAccessResponse(BaseModel):
    organization_id: str | None = None
    agent_id: str | None = None
    allowed_pack_ids: list[str] | None = None


class BrowserTaskPackAccessUpdateRequest(BaseModel):
    organization_id: str | None = None
    agent_id: str | None = None
    allowed_pack_ids: list[str] | None = None


def install_browser_task_router(
    app: FastAPI,
    *,
    browser_task_service: BrowserTaskService | None,
    attachment_runtime: AttachmentRuntime | None,
    jobs_store: JobStore | None = None,
    authorize_request: RequestAuthorizer,
) -> None:
    router = APIRouter(tags=["browser-tasks"])

    def _service() -> BrowserTaskService:
        if browser_task_service is None:
            raise HTTPException(status_code=503, detail="browser task service is not configured")
        return browser_task_service

    def _attachments() -> AttachmentRuntime:
        if attachment_runtime is None:
            raise HTTPException(status_code=503, detail="attachment runtime is not configured")
        return attachment_runtime

    def _raise_artifact_error(exc: ValueError) -> None:
        detail = str(exc)
        status_code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if "exceeds limit" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc

    def _task(task_id: str, *, organization_id: str | None = None) -> BrowserTaskSnapshot:
        try:
            return _redact_snapshot(_service().get_snapshot(task_id, organization_id=organization_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc

    def _credential_ref_label(secret_ref: str) -> str:
        if secret_ref.startswith("connection:"):
            connection_id = secret_ref[len("connection:"):].strip()
            if len(connection_id) > 10:
                return f"connection:{connection_id[:6]}...{connection_id[-4:]}"
            return f"connection:{connection_id}"
        return "credential_ref"

    def _redact_snapshot(snapshot: BrowserTaskSnapshot) -> BrowserTaskSnapshot:
        if not snapshot.task.credential_refs:
            return snapshot
        return snapshot.model_copy(
            update={
                "task": snapshot.task.model_copy(
                    update={
                        "credential_refs": {
                            name: _credential_ref_label(secret_ref)
                            for name, secret_ref in snapshot.task.credential_refs.items()
                        }
                    }
                )
            }
        )

    def _redact_snapshots(snapshots: list[BrowserTaskSnapshot]) -> list[BrowserTaskSnapshot]:
        return [_redact_snapshot(snapshot) for snapshot in snapshots]

    def _conflict(exc: ValueError) -> HTTPException:
        return HTTPException(status_code=409, detail=str(exc))

    def _actor_id_for_request(request: Request) -> str | None:
        context = get_request_auth_context(request)
        if context.principal is None:
            return None
        return context.principal.user.user_id

    def _actor_session_id_for_request(request: Request) -> str | None:
        context = get_request_auth_context(request)
        if context.principal is None:
            return None
        return context.principal.session.session_id

    def _operator_id_for_request(request: Request, requested_operator_id: str | None) -> str:
        if requested_operator_id is not None and requested_operator_id.strip():
            return requested_operator_id.strip()
        actor_id = _actor_id_for_request(request)
        return actor_id or "operator"

    @router.get("/internal/browser-tasks", response_model=list[BrowserTaskSnapshot])
    def list_browser_tasks(
        request: Request,
        conversation_id: Annotated[str, Query(min_length=1)],
        organization_id: str | None = None,
    ) -> list[BrowserTaskSnapshot]:
        authorize_request(request)
        return _redact_snapshots(
            _service().list_conversation_tasks(
                conversation_id=conversation_id,
                organization_id=organization_id,
            )
        )

    @router.get("/internal/browser-task-inbox", response_model=list[BrowserTaskSnapshot])
    def list_browser_task_inbox(
        request: Request,
        organization_id: str | None = None,
        conversation_id: str | None = Query(default=None, min_length=1),
        state: str | None = Query(default=None, min_length=1),
        approval_state: str | None = Query(default=None, min_length=1),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[BrowserTaskSnapshot]:
        authorize_request(request)
        return _redact_snapshots(
            _service().list_recent_tasks(
                organization_id=organization_id,
                conversation_id=conversation_id,
                state=state,
                approval_state=approval_state,
                limit=limit,
            )
        )

    @router.get("/internal/browser-task-packs", response_model=list[BrowserTaskPack])
    def list_browser_task_packs(request: Request) -> list[BrowserTaskPack]:
        authorize_request(request)
        return _service().task_pack_registry.list_packs()

    @router.get("/internal/browser-task-runtime/status", response_model=BrowserTaskRuntimeTickStatus)
    def get_browser_task_runtime_status(request: Request) -> BrowserTaskRuntimeTickStatus:
        authorize_request(request)
        if jobs_store is None:
            raise HTTPException(status_code=503, detail="jobs store is not configured")
        tick = recurring_tick_status(jobs_store, BROWSER_TASKS_JOB_TYPE)
        return BrowserTaskRuntimeTickStatus(
            scheduled=tick.scheduled,
            last_tick_at=tick.last_tick_at.isoformat() if tick.last_tick_at else None,
            last_tick_status=tick.last_tick_status,
            last_error=tick.last_error,
        )

    @router.post("/internal/browser-task-runtime/sweep", response_model=BrowserTaskRuntimeSweepResponse)
    def sweep_browser_task_runtime(
        payload: BrowserTaskRuntimeSweepRequest,
        request: Request,
    ) -> BrowserTaskRuntimeSweepResponse:
        authorize_request(request)
        expired = _service().expire_stale_approvals(organization_id=payload.organization_id)
        return BrowserTaskRuntimeSweepResponse(expired_approvals=len(expired))

    @router.get("/internal/browser-task-pack-access", response_model=BrowserTaskPackAccessResponse)
    def get_browser_task_pack_access(
        request: Request,
        organization_id: str | None = None,
        agent_id: str | None = None,
    ) -> BrowserTaskPackAccessResponse:
        authorize_request(request)
        allowed = _service().get_allowed_task_pack_ids(
            organization_id=organization_id,
            agent_id=agent_id,
        )
        return BrowserTaskPackAccessResponse(
            organization_id=organization_id,
            agent_id=agent_id,
            allowed_pack_ids=None if allowed is None else sorted(allowed),
        )

    @router.put("/internal/browser-task-pack-access", response_model=BrowserTaskPackAccessResponse)
    def update_browser_task_pack_access(
        payload: BrowserTaskPackAccessUpdateRequest,
        request: Request,
    ) -> BrowserTaskPackAccessResponse:
        authorize_request(request)
        try:
            allowed = _service().replace_allowed_task_pack_ids(
                organization_id=payload.organization_id,
                agent_id=payload.agent_id,
                pack_ids=None if payload.allowed_pack_ids is None else set(payload.allowed_pack_ids),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task pack") from exc
        return BrowserTaskPackAccessResponse(
            organization_id=payload.organization_id,
            agent_id=payload.agent_id,
            allowed_pack_ids=None if allowed is None else sorted(allowed),
        )

    @router.post("/internal/browser-tasks", response_model=BrowserTaskSnapshot)
    def create_browser_task(payload: BrowserTaskCreateRequest, request: Request) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().create_task(
                    conversation_id=payload.conversation_id,
                    organization_id=payload.organization_id,
                    agent_id=payload.agent_id,
                    title=payload.title,
                    summary=payload.summary,
                    requested_channel=payload.requested_channel,
                    task_pack_id=payload.task_pack_id,
                    task_pack_version=payload.task_pack_version,
                    start_url=payload.start_url,
                    input_payload=payload.input_payload,
                    credential_refs=payload.credential_refs,
                    requires_approval=payload.requires_approval,
                    approval_kind=payload.approval_kind,
                    approval_prompt=payload.approval_prompt,
                    approval_ttl_seconds=payload.approval_ttl_seconds,
                    metadata=payload.metadata,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task pack") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/internal/browser-tasks/{task_id}", response_model=BrowserTaskSnapshot)
    def get_browser_task(request: Request, task_id: str, organization_id: str | None = None) -> BrowserTaskSnapshot:
        authorize_request(request)
        return _task(task_id, organization_id=organization_id)

    @router.get("/internal/browser-tasks/{task_id}/stream")
    async def stream_browser_task(
        request: Request,
        task_id: str,
        organization_id: str | None = None,
        interval_seconds: float = Query(default=1.0, ge=0.25, le=10.0),
        once: bool = False,
    ) -> StreamingResponse:
        authorize_request(request)
        _task(task_id, organization_id=organization_id)

        async def events():
            last_payload: str | None = None
            while True:
                if await request.is_disconnected():
                    break
                try:
                    snapshot = _service().get_snapshot(task_id, organization_id=organization_id)
                except KeyError:
                    yield "event: error\ndata: {\"detail\":\"unknown browser task id\"}\n\n"
                    break
                payload = json.dumps(jsonable_encoder(_redact_snapshot(snapshot)), separators=(",", ":"))
                if payload != last_payload:
                    yield f"event: snapshot\ndata: {payload}\n\n"
                    last_payload = payload
                    if once:
                        break
                else:
                    yield "event: heartbeat\ndata: {}\n\n"
                await asyncio.sleep(interval_seconds)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/internal/browser-tasks/approvals/{approval_id}/approve", response_model=BrowserTaskSnapshot)
    def approve_browser_task(
        request: Request,
        approval_id: str,
        payload: BrowserTaskDecisionRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().approve(
                    approval_id=approval_id,
                    organization_id=organization_id,
                    reason=payload.reason,
                    actor_id=_actor_id_for_request(request),
                    actor_ip=build_session_audit_context(request).ip,
                    actor_session_id=_actor_session_id_for_request(request),
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser approval id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/approvals/expire-stale", response_model=list[BrowserTaskSnapshot])
    def expire_stale_browser_task_approvals(
        payload: BrowserTaskExpireApprovalsRequest,
        request: Request,
    ) -> list[BrowserTaskSnapshot]:
        authorize_request(request)
        return _redact_snapshots(
            _service().expire_stale_approvals(
                organization_id=payload.organization_id,
                limit=payload.limit,
            )
        )

    @router.post("/internal/browser-tasks/approvals/{approval_id}/deny", response_model=BrowserTaskSnapshot)
    def deny_browser_task(
        request: Request,
        approval_id: str,
        payload: BrowserTaskDecisionRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().deny(
                    approval_id=approval_id,
                    organization_id=organization_id,
                    reason=payload.reason,
                    actor_id=_actor_id_for_request(request),
                    actor_ip=build_session_audit_context(request).ip,
                    actor_session_id=_actor_session_id_for_request(request),
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser approval id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/cancel", response_model=BrowserTaskSnapshot)
    def cancel_browser_task(
        request: Request,
        task_id: str,
        payload: BrowserTaskCancelRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().cancel_task(
                    task_id=task_id,
                    organization_id=organization_id,
                    reason=payload.reason,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc

    @router.post("/internal/browser-tasks/{task_id}/retry", response_model=BrowserTaskSnapshot)
    def retry_browser_task(
        request: Request,
        task_id: str,
        payload: BrowserTaskRetryRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().retry_task(
                    task_id=task_id,
                    organization_id=organization_id,
                    reason=payload.reason,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/progress", response_model=BrowserTaskSnapshot)
    def record_browser_task_progress(
        request: Request,
        task_id: str,
        payload: BrowserTaskProgressRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().record_progress(
                    task_id=task_id,
                    organization_id=organization_id,
                    event_type=payload.event_type,
                    message=payload.message,
                    state=payload.state,
                    metadata=payload.metadata,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/claim", response_model=BrowserTaskSnapshot | None)
    def claim_browser_task(payload: BrowserTaskClaimRequest, request: Request) -> BrowserTaskSnapshot | None:
        authorize_request(request)
        try:
            snapshot = _service().claim_next_task(
                worker_id=payload.worker_id,
                organization_id=payload.organization_id,
                lease_seconds=payload.lease_seconds,
            )
            return None if snapshot is None else _redact_snapshot(snapshot)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/internal/browser-tasks/{task_id}/lease", response_model=BrowserTaskSnapshot)
    def renew_browser_task_lease(
        request: Request,
        task_id: str,
        payload: BrowserTaskLeaseRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().renew_task_lease(
                    task_id=task_id,
                    worker_id=payload.worker_id,
                    organization_id=organization_id,
                    lease_seconds=payload.lease_seconds,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/release", response_model=BrowserTaskSnapshot)
    def release_browser_task_lease(
        request: Request,
        task_id: str,
        payload: BrowserTaskReleaseRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().release_task_lease(
                    task_id=task_id,
                    worker_id=payload.worker_id,
                    organization_id=organization_id,
                    reason=payload.reason,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/takeover", response_model=BrowserTaskSnapshot)
    def request_browser_task_takeover(
        request: Request,
        task_id: str,
        payload: BrowserTaskTakeoverRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().request_operator_takeover(
                    task_id=task_id,
                    operator_id=_operator_id_for_request(request, payload.operator_id),
                    organization_id=organization_id,
                    ttl_seconds=payload.ttl_seconds,
                    reason=payload.reason,
                    actor_ip=build_session_audit_context(request).ip,
                    actor_session_id=_actor_session_id_for_request(request),
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/takeover/release", response_model=BrowserTaskSnapshot)
    def release_browser_task_takeover(
        request: Request,
        task_id: str,
        payload: BrowserTaskTakeoverReleaseRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().release_operator_takeover(
                    task_id=task_id,
                    operator_id=_operator_id_for_request(request, payload.operator_id),
                    organization_id=organization_id,
                    reason=payload.reason,
                    actor_ip=build_session_audit_context(request).ip,
                    actor_session_id=_actor_session_id_for_request(request),
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/operator-commands", response_model=BrowserOperatorCommand)
    def enqueue_browser_task_operator_command(
        request: Request,
        task_id: str,
        payload: BrowserTaskOperatorCommandRequest,
        organization_id: str | None = None,
    ) -> BrowserOperatorCommand:
        authorize_request(request)
        try:
            return _service().enqueue_operator_command(
                task_id=task_id,
                operator_id=_operator_id_for_request(request, payload.operator_id),
                command_type=payload.command_type,
                payload=payload.payload,
                organization_id=organization_id,
                actor_ip=build_session_audit_context(request).ip,
                actor_session_id=_actor_session_id_for_request(request),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.get("/internal/browser-tasks/{task_id}/operator-commands", response_model=list[BrowserOperatorCommand])
    def list_browser_task_operator_commands(
        request: Request,
        task_id: str,
        organization_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ) -> list[BrowserOperatorCommand]:
        authorize_request(request)
        try:
            return _service().list_operator_commands(
                task_id=task_id,
                organization_id=organization_id,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc

    @router.post(
        "/internal/browser-tasks/{task_id}/operator-commands/pending",
        response_model=list[BrowserOperatorCommand],
    )
    def list_pending_browser_task_operator_commands(
        request: Request,
        task_id: str,
        payload: BrowserTaskOperatorCommandListRequest,
        organization_id: str | None = None,
    ) -> list[BrowserOperatorCommand]:
        authorize_request(request)
        try:
            return _service().list_pending_operator_commands(
                task_id=task_id,
                worker_id=payload.worker_id,
                organization_id=organization_id,
                limit=payload.limit,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post(
        "/internal/browser-tasks/operator-commands/{command_id}/delivered",
        response_model=BrowserOperatorCommand,
    )
    def mark_browser_task_operator_command_delivered(
        request: Request,
        command_id: str,
        payload: BrowserTaskOperatorCommandAckRequest,
        organization_id: str | None = None,
    ) -> BrowserOperatorCommand:
        authorize_request(request)
        try:
            return _service().mark_operator_command_delivered(
                command_id=command_id,
                worker_id=payload.worker_id,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser operator command id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post(
        "/internal/browser-tasks/operator-commands/{command_id}/failed",
        response_model=BrowserOperatorCommand,
    )
    def mark_browser_task_operator_command_failed(
        request: Request,
        command_id: str,
        payload: BrowserTaskOperatorCommandFailRequest,
        organization_id: str | None = None,
    ) -> BrowserOperatorCommand:
        authorize_request(request)
        try:
            return _service().mark_operator_command_failed(
                command_id=command_id,
                worker_id=payload.worker_id,
                error=payload.error,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser operator command id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/worker-request", response_model=BrowserWorkerRequest)
    def build_browser_worker_request(
        request: Request,
        task_id: str,
        payload: BrowserTaskWorkerRequestPayload,
        organization_id: str | None = None,
    ) -> BrowserWorkerRequest:
        authorize_request(request)
        try:
            return _service().build_worker_request(
                task_id=task_id,
                worker_id=payload.worker_id,
                organization_id=organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/worker-progress", response_model=BrowserTaskSnapshot)
    def record_browser_worker_progress(
        request: Request,
        task_id: str,
        payload: BrowserTaskWorkerProgressPayload,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        if payload.progress.task_id != task_id:
            raise HTTPException(status_code=400, detail="progress task_id does not match URL task_id")
        try:
            return _redact_snapshot(
                _service().record_worker_progress(
                    worker_id=payload.worker_id,
                    progress=payload.progress,
                    organization_id=organization_id,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/worker-result", response_model=BrowserTaskSnapshot)
    def apply_browser_worker_result(
        request: Request,
        task_id: str,
        payload: BrowserTaskWorkerResultPayload,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        if payload.result.task_id != task_id:
            raise HTTPException(status_code=400, detail="result task_id does not match URL task_id")
        try:
            return _redact_snapshot(
                _service().apply_worker_result(
                    worker_id=payload.worker_id,
                    result=payload.result,
                    organization_id=organization_id,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/complete", response_model=BrowserTaskSnapshot)
    def complete_browser_task(
        request: Request,
        task_id: str,
        payload: BrowserTaskCompleteRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().complete_task(
                    task_id=task_id,
                    organization_id=organization_id,
                    result=payload.result,
                    message=payload.message,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post("/internal/browser-tasks/{task_id}/fail", response_model=BrowserTaskSnapshot)
    def fail_browser_task(
        request: Request,
        task_id: str,
        payload: BrowserTaskFailRequest,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        authorize_request(request)
        try:
            return _redact_snapshot(
                _service().fail_task(
                    task_id=task_id,
                    organization_id=organization_id,
                    error=payload.error,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown browser task id") from exc
        except ValueError as exc:
            raise _conflict(exc) from exc

    @router.post(
        "/internal/browser-tasks/{task_id}/artifacts",
        response_model=BrowserTaskArtifactResponse,
    )
    async def create_browser_task_artifact(
        task_id: str,
        request: Request,
        filename: Annotated[str, Query(min_length=1)],
        kind: str = Query(default="other"),
        organization_id: str | None = None,
        content_type: str | None = Header(default=None),
    ) -> BrowserTaskArtifactResponse:
        authorize_request(request)
        snapshot = _task(task_id, organization_id=organization_id)
        attachment_runtime = _attachments()
        payload = await read_request_body_limited(
            request,
            max_bytes=attachment_runtime.service.max_file_bytes,
            resource_name="artifact",
        )
        try:
            artifact = attachment_runtime.service.create_artifact(
                conversation_id=snapshot.task.conversation_id,
                organization_id=snapshot.task.organization_id,
                filename=filename,
                content_type=content_type or "application/octet-stream",
                content_bytes=payload,
                kind=kind,
                task_id=task_id,
                metadata={"created_via": "browser_task_operator_api"},
            )
        except ValueError as exc:
            _raise_artifact_error(exc)
        _service().attach_artifact(
            task_id=task_id,
            organization_id=snapshot.task.organization_id,
            artifact={
                "artifact_id": artifact.artifact_id,
                "filename": artifact.filename,
                "content_type": artifact.content_type,
                "kind": artifact.kind,
                "size_bytes": artifact.size_bytes,
                "internal_download_url": f"/internal/browser-tasks/artifacts/{artifact.artifact_id}/download",
                "public_widget_download_url": (
                    f"/public/widget/sessions/{snapshot.task.conversation_id}/artifacts/{artifact.artifact_id}/download"
                ),
            },
        )
        return BrowserTaskArtifactResponse(
            artifact=artifact,
            internal_download_url=f"/internal/browser-tasks/artifacts/{artifact.artifact_id}/download",
            public_widget_download_url=(
                f"/public/widget/sessions/{snapshot.task.conversation_id}/artifacts/{artifact.artifact_id}/download"
            ),
        )

    @router.get("/internal/browser-tasks/artifacts/{artifact_id}/download")
    def download_browser_task_artifact(
        request: Request,
        artifact_id: str,
        organization_id: str | None = None,
    ) -> Response:
        authorize_request(request)
        payload = _attachments().service.get_artifact_bytes(
            artifact_id=artifact_id,
            organization_id=organization_id,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown artifact id")
        artifact, content_bytes = payload
        headers = {
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Ruhu-Artifact-Id": artifact.artifact_id,
        }
        return Response(content=content_bytes, media_type=artifact.content_type, headers=headers)

    app.include_router(router)
