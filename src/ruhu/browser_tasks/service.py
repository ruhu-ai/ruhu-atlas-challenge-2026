from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .models import (
    BrowserApproval,
    BrowserOperatorCommand,
    BrowserTask,
    BrowserTaskEvent,
    BrowserTaskSnapshot,
    new_id,
    utc_now,
)
from .store import BrowserTaskStore
from .task_packs import BrowserTaskPackAccessPolicy, BrowserTaskPackRegistry, is_url_allowed
from .credentials import APIConnectionBrowserCredentialValidator
from ..audit.emitter import emit_audit_event
from ..validation.schema import ValidationError as JsonContractValidationError, validate_json_schema
from .worker_contracts import (
    BrowserAttachmentRef,
    BrowserCredentialRef,
    BrowserWorkerProgress,
    BrowserWorkerRequest,
    BrowserWorkerResult,
)

TERMINAL_TASK_STATES = {"completed", "failed", "cancelled"}
TERMINAL_APPROVAL_STATES = {"approved", "denied", "expired", "cancelled"}
DEFAULT_APPROVAL_TTL_SECONDS = 5 * 60
DEFAULT_WORKER_LEASE_SECONDS = 60
DEFAULT_OPERATOR_TAKEOVER_SECONDS = 5 * 60


@dataclass(slots=True)
class BrowserTaskService:
    store: BrowserTaskStore
    task_pack_registry: BrowserTaskPackRegistry = field(default_factory=BrowserTaskPackRegistry)
    credential_validator: APIConnectionBrowserCredentialValidator | None = None
    task_pack_access_policy: BrowserTaskPackAccessPolicy | None = None
    audit_router: Any | None = None

    def create_task(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        title: str,
        agent_id: str | None = None,
        summary: str | None = None,
        requested_channel: str = "browser",
        task_pack_id: str | None = None,
        task_pack_version: str | None = None,
        start_url: str | None = None,
        input_payload: dict[str, object] | None = None,
        credential_refs: dict[str, str] | None = None,
        requires_approval: bool = False,
        approval_kind: str = "generic_access",
        approval_prompt: str | None = None,
        approval_ttl_seconds: int | None = DEFAULT_APPROVAL_TTL_SECONDS,
        metadata: dict[str, object] | None = None,
    ) -> BrowserTaskSnapshot:
        now = utc_now()
        selected_task_pack_version = task_pack_version
        selected_start_url = start_url
        selected_credential_refs = self._normalize_credential_refs(credential_refs)
        selected_approval_context: dict[str, object] = {
            "approval_kind": approval_kind,
            "performs_write": False,
            "allowed_domains": [],
            "credential_refs": [],
        }
        if task_pack_id is not None:
            self._assert_task_pack_allowed(
                pack_id=task_pack_id,
                organization_id=organization_id,
                agent_id=agent_id,
            )
            task_pack = self.task_pack_registry.get(task_pack_id, task_pack_version)
            selected_task_pack_version = task_pack.version
            selected_start_url = selected_start_url or task_pack.start_url
            if selected_start_url is None:
                raise ValueError("start_url is required for browser task pack execution")
            if not is_url_allowed(selected_start_url, task_pack.allowed_domains):
                raise ValueError("start_url must match the browser task pack allowed domains")
            self._validate_task_pack_input(task_pack, input_payload or {})
            if task_pack.approval_policy.approval_required:
                requires_approval = True
                approval_kind = task_pack.approval_policy.approval_kinds[0]
                approval_ttl_seconds = task_pack.approval_policy.approval_ttl_seconds
            selected_approval_context = self._approval_context_for_task_pack(
                task_pack,
                approval_kind=approval_kind,
                start_url=selected_start_url,
                credential_refs=selected_credential_refs,
            )
            self._validate_task_pack_credentials(task_pack, selected_credential_refs)
            if self.credential_validator is not None:
                self.credential_validator.validate_task_credentials(
                    organization_id=organization_id,
                    task_pack=task_pack,
                    credential_refs=selected_credential_refs,
                )
        task = BrowserTask(
            organization_id=organization_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            title=title,
            summary=summary,
            requested_channel=requested_channel,  # type: ignore[arg-type]
            task_pack_id=task_pack_id,
            task_pack_version=selected_task_pack_version,
            start_url=selected_start_url,
            input_payload=dict(input_payload or {}),
            credential_refs=selected_credential_refs,
            state="awaiting_approval" if requires_approval else "queued",
            approval_state="pending" if requires_approval else "not_required",
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        approval = None
        if requires_approval:
            approval = BrowserApproval(
                task_id=task.task_id,
                organization_id=organization_id,
                conversation_id=conversation_id,
                kind=approval_kind,  # type: ignore[arg-type]
                prompt=approval_prompt or "Approve browser access to continue.",
                context=selected_approval_context,
                requested_at=now,
                expires_at=(
                    now + timedelta(seconds=max(1, approval_ttl_seconds))
                    if approval_ttl_seconds is not None
                    else None
                ),
            )
            task = task.model_copy(update={"current_approval_id": approval.approval_id})
        saved_task = self.store.save_task(task)
        if approval is not None:
            saved_approval = self.store.save_approval(approval)
            saved_task = self.store.save_task(
                saved_task.model_copy(
                    update={"current_approval_id": saved_approval.approval_id, "updated_at": utc_now()}
                )
            )
            self._event(
                task=saved_task,
                event_type="browser.awaiting_approval",
                message=saved_approval.prompt,
            )
            return self.get_snapshot(saved_task.task_id, organization_id=organization_id)
        self._event(task=saved_task, event_type="browser.preparing", message="Browser task queued.")
        return self.get_snapshot(saved_task.task_id, organization_id=organization_id)

    def approve(
        self,
        *,
        approval_id: str,
        organization_id: str | None = None,
        reason: str | None = None,
        actor_id: str | None = None,
        actor_ip: str | None = None,
        actor_session_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        approval = self.store.get_approval(approval_id, organization_id=organization_id)
        if approval is None:
            raise KeyError(approval_id)
        self._ensure_pending_approval(approval)
        decided_at = utc_now()
        saved_approval = self.store.save_approval(
            approval.model_copy(
                update={
                    "state": "approved",
                    "decision_reason": reason,
                    "decided_at": decided_at,
                }
            )
        )
        task = self._require_task(approval.task_id, organization_id=organization_id)
        self._ensure_not_terminal(task)
        saved_task = self.store.save_task(
            task.model_copy(
                update={
                    "state": "queued",
                    "approval_state": "approved",
                    "updated_at": decided_at,
                }
            )
        )
        self._event(task=saved_task, event_type="browser.approved", message="Browser task approved.")
        self._emit_approval_audit(
            event_type="security.browser_task_approved",
            outcome="success",
            task=saved_task,
            approval=saved_approval,
            actor_id=actor_id,
            actor_ip=actor_ip,
            actor_session_id=actor_session_id,
            reason=reason,
        )
        return BrowserTaskSnapshot(
            task=saved_task,
            approval=saved_approval,
            recent_events=self.store.list_events(saved_task.task_id, organization_id=organization_id),
        )

    def deny(
        self,
        *,
        approval_id: str,
        organization_id: str | None = None,
        reason: str | None = None,
        actor_id: str | None = None,
        actor_ip: str | None = None,
        actor_session_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        approval = self.store.get_approval(approval_id, organization_id=organization_id)
        if approval is None:
            raise KeyError(approval_id)
        self._ensure_pending_approval(approval)
        decided_at = utc_now()
        saved_approval = self.store.save_approval(
            approval.model_copy(
                update={
                    "state": "denied",
                    "decision_reason": reason,
                    "decided_at": decided_at,
                }
            )
        )
        task = self._require_task(approval.task_id, organization_id=organization_id)
        self._ensure_not_terminal(task)
        saved_task = self.store.save_task(
            task.model_copy(
                update={
                    "state": "cancelled",
                    "approval_state": "denied",
                    "error": reason or "approval denied",
                    "updated_at": decided_at,
                    "finished_at": decided_at,
                }
            )
        )
        self._event(task=saved_task, event_type="browser.denied", message=saved_task.error or "Browser task denied.")
        self._emit_approval_audit(
            event_type="security.browser_task_denied",
            outcome="denied",
            task=saved_task,
            approval=saved_approval,
            actor_id=actor_id,
            actor_ip=actor_ip,
            actor_session_id=actor_session_id,
            reason=reason,
        )
        return BrowserTaskSnapshot(
            task=saved_task,
            approval=saved_approval,
            recent_events=self.store.list_events(saved_task.task_id, organization_id=organization_id),
        )

    def expire_stale_approvals(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 100,
    ) -> list[BrowserTaskSnapshot]:
        now = utc_now()
        expired: list[BrowserTaskSnapshot] = []
        for approval in self.store.list_expired_pending_approvals(
            now=now,
            organization_id=organization_id,
            limit=limit,
        ):
            expired.append(self._expire_pending_approval(approval, now=now))
        return expired

    def cancel_task(
        self,
        *,
        task_id: str,
        organization_id: str | None = None,
        reason: str = "cancelled by user",
    ) -> BrowserTaskSnapshot:
        task = self._require_task(task_id, organization_id=organization_id)
        if task.state in TERMINAL_TASK_STATES:
            return self.get_snapshot(task_id, organization_id=organization_id)
        now = utc_now()
        saved_task = self.store.save_task(
            task.model_copy(
                update={
                    "state": "cancelled",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "operator_takeover_owner_id": None,
                    "operator_takeover_expires_at": None,
                    "error": reason,
                    "updated_at": now,
                    "finished_at": now,
                }
            )
        )
        approval = self.store.get_task_approval(task_id, organization_id=organization_id)
        if approval is not None and approval.state == "pending":
            approval = self.store.save_approval(
                approval.model_copy(
                    update={
                        "state": "cancelled",
                        "decision_reason": reason,
                        "decided_at": now,
                    }
                )
            )
        self._event(task=saved_task, event_type="browser.cancelled", message=reason)
        return BrowserTaskSnapshot(
            task=saved_task,
            approval=approval,
            recent_events=self.store.list_events(task_id, organization_id=organization_id),
        )

    def retry_task(
        self,
        *,
        task_id: str,
        organization_id: str | None = None,
        reason: str = "manual retry requested",
    ) -> BrowserTaskSnapshot:
        task = self._require_task(task_id, organization_id=organization_id)
        if task.state not in {"failed", "cancelled"}:
            raise ValueError("only failed or cancelled browser tasks can be retried")
        max_attempts = self._max_attempts_for_task(task)
        if task.attempt_count >= max_attempts:
            raise ValueError("browser task retry attempts are exhausted")
        now = utc_now()
        saved_task = self.store.save_task(
            task.model_copy(
                update={
                    "state": "queued",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "operator_takeover_owner_id": None,
                    "operator_takeover_expires_at": None,
                    "error": None,
                    "updated_at": now,
                    "finished_at": None,
                }
            )
        )
        self._event(
            task=saved_task,
            event_type="browser.manual_retry",
            message=reason,
            metadata={
                "attempt_count": task.attempt_count,
                "max_attempts": max_attempts,
            },
        )
        return self.get_snapshot(task_id, organization_id=organization_id)

    def record_progress(
        self,
        *,
        task_id: str,
        organization_id: str | None = None,
        event_type: str,
        message: str,
        state: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> BrowserTaskSnapshot:
        task = self._require_task(task_id, organization_id=organization_id)
        self._ensure_not_terminal(task)
        if task.state == "awaiting_approval":
            raise ValueError("browser task is awaiting approval")
        updates: dict[str, object] = {"updated_at": utc_now()}
        if state is not None:
            if state in TERMINAL_TASK_STATES:
                raise ValueError("use terminal APIs to complete, fail, or cancel browser tasks")
            if state not in {"queued", "awaiting_approval", "running"}:
                raise ValueError(f"invalid browser task progress state: {state}")
            if task.state == "queued" and state == "awaiting_approval":
                raise ValueError("queued browser task cannot return to awaiting approval through progress")
            updates["state"] = state
            if state == "running" and task.started_at is None:
                updates["started_at"] = utc_now()
        saved_task = self.store.save_task(task.model_copy(update=updates))
        self._event(task=saved_task, event_type=event_type, message=message, metadata=metadata)
        return self.get_snapshot(task_id, organization_id=organization_id)

    def claim_next_task(
        self,
        *,
        worker_id: str,
        organization_id: str | None = None,
        lease_seconds: int = DEFAULT_WORKER_LEASE_SECONDS,
    ) -> BrowserTaskSnapshot | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if lease_seconds < 5 or lease_seconds > 1800:
            raise ValueError("lease_seconds must be between 5 and 1800")
        claimed = self.store.claim_next_queued_task(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            organization_id=organization_id,
            now=utc_now(),
        )
        if claimed is None:
            return None
        self._event(
            task=claimed,
            event_type="browser.worker_claimed",
            message="Browser worker claimed the task.",
            metadata={"worker_id": worker_id, "lease_seconds": lease_seconds},
        )
        return self.get_snapshot(claimed.task_id, organization_id=organization_id)

    def renew_task_lease(
        self,
        *,
        task_id: str,
        worker_id: str,
        organization_id: str | None = None,
        lease_seconds: int = DEFAULT_WORKER_LEASE_SECONDS,
    ) -> BrowserTaskSnapshot:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if lease_seconds < 5 or lease_seconds > 1800:
            raise ValueError("lease_seconds must be between 5 and 1800")
        renewed = self.store.renew_task_lease(
            task_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            organization_id=organization_id,
            now=utc_now(),
        )
        if renewed is None:
            raise ValueError("browser task lease is not held by this worker")
        return self.get_snapshot(task_id, organization_id=organization_id)

    def release_task_lease(
        self,
        *,
        task_id: str,
        worker_id: str,
        organization_id: str | None = None,
        reason: str = "worker released task lease",
    ) -> BrowserTaskSnapshot:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        released = self.store.release_task_lease(
            task_id,
            worker_id=worker_id,
            organization_id=organization_id,
            now=utc_now(),
        )
        if released is None:
            raise ValueError("browser task lease is not held by this worker")
        self._event(
            task=released,
            event_type="browser.worker_released",
            message=reason,
            metadata={"worker_id": worker_id},
        )
        return self.get_snapshot(task_id, organization_id=organization_id)

    def request_operator_takeover(
        self,
        *,
        task_id: str,
        operator_id: str,
        organization_id: str | None = None,
        ttl_seconds: int = DEFAULT_OPERATOR_TAKEOVER_SECONDS,
        reason: str | None = None,
        actor_ip: str | None = None,
        actor_session_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        normalized_operator_id = operator_id.strip()
        if not normalized_operator_id:
            raise ValueError("operator_id is required")
        if ttl_seconds < 30 or ttl_seconds > 1800:
            raise ValueError("ttl_seconds must be between 30 and 1800")
        task = self._require_task(task_id, organization_id=organization_id)
        self._ensure_not_terminal(task)
        if task.state == "awaiting_approval":
            raise ValueError("browser task is awaiting approval")
        if task.state != "running":
            raise ValueError("operator takeover requires a running browser task")
        if not self._operator_takeover_enabled_for_task(task):
            raise ValueError("operator takeover is disabled for this browser task")
        now = utc_now()
        if (
            task.operator_takeover_owner_id is not None
            and task.operator_takeover_owner_id != normalized_operator_id
            and task.operator_takeover_expires_at is not None
            and task.operator_takeover_expires_at > now
        ):
            raise ValueError("browser task is already controlled by another operator")
        was_renewal = (
            task.operator_takeover_owner_id == normalized_operator_id
            and task.operator_takeover_expires_at is not None
            and task.operator_takeover_expires_at > now
        )
        saved_task = self.store.save_task(
            task.model_copy(
                update={
                    "operator_takeover_owner_id": normalized_operator_id,
                    "operator_takeover_expires_at": now + timedelta(seconds=ttl_seconds),
                    "updated_at": now,
                }
            )
        )
        self._event(
            task=saved_task,
            event_type="browser.operator_takeover_renewed" if was_renewal else "browser.operator_takeover_started",
            message="Operator takeover renewed." if was_renewal else "Operator takeover started.",
            metadata={
                "operator_id": normalized_operator_id,
                "ttl_seconds": ttl_seconds,
                **({"reason": reason} if reason else {}),
            },
        )
        self._emit_operator_takeover_audit(
            event_type=(
                "security.browser_task_takeover_renewed"
                if was_renewal
                else "security.browser_task_takeover_started"
            ),
            task=saved_task,
            operator_id=normalized_operator_id,
            actor_ip=actor_ip,
            actor_session_id=actor_session_id,
            reason=reason,
        )
        return self.get_snapshot(task_id, organization_id=organization_id)

    def release_operator_takeover(
        self,
        *,
        task_id: str,
        operator_id: str,
        organization_id: str | None = None,
        reason: str = "operator released takeover",
        actor_ip: str | None = None,
        actor_session_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        normalized_operator_id = operator_id.strip()
        if not normalized_operator_id:
            raise ValueError("operator_id is required")
        task = self._require_task(task_id, organization_id=organization_id)
        self._ensure_not_terminal(task)
        now = utc_now()
        active_takeover = (
            task.operator_takeover_owner_id is not None
            and task.operator_takeover_expires_at is not None
            and task.operator_takeover_expires_at > now
        )
        if not active_takeover:
            if task.operator_takeover_owner_id is None and task.operator_takeover_expires_at is None:
                return self.get_snapshot(task_id, organization_id=organization_id)
        elif task.operator_takeover_owner_id != normalized_operator_id:
            raise ValueError("browser task takeover is owned by another operator")
        saved_task = self.store.save_task(
            task.model_copy(
                update={
                    "operator_takeover_owner_id": None,
                    "operator_takeover_expires_at": None,
                    "updated_at": now,
                }
            )
        )
        self._event(
            task=saved_task,
            event_type="browser.operator_takeover_released",
            message=reason,
            metadata={"operator_id": normalized_operator_id},
        )
        self._emit_operator_takeover_audit(
            event_type="security.browser_task_takeover_released",
            task=saved_task,
            operator_id=normalized_operator_id,
            actor_ip=actor_ip,
            actor_session_id=actor_session_id,
            reason=reason,
        )
        return self.get_snapshot(task_id, organization_id=organization_id)

    def enqueue_operator_command(
        self,
        *,
        task_id: str,
        operator_id: str,
        command_type: str,
        payload: dict[str, object] | None = None,
        organization_id: str | None = None,
        actor_ip: str | None = None,
        actor_session_id: str | None = None,
    ) -> BrowserOperatorCommand:
        normalized_operator_id = operator_id.strip()
        if not normalized_operator_id:
            raise ValueError("operator_id is required")
        task = self._require_task(task_id, organization_id=organization_id)
        self._ensure_active_operator_takeover(task, normalized_operator_id)
        normalized_payload = self._validate_operator_command_payload(command_type, payload or {})
        command = BrowserOperatorCommand(
            task_id=task.task_id,
            organization_id=task.organization_id,
            conversation_id=task.conversation_id,
            operator_id=normalized_operator_id,
            command_type=command_type,  # type: ignore[arg-type]
            payload=normalized_payload,
        )
        saved_command = self.store.save_operator_command(command)
        self._event(
            task=task,
            event_type="browser.operator_command_queued",
            message=f"Operator command queued: {saved_command.command_type}.",
            metadata={
                "operator_id": normalized_operator_id,
                "command_id": saved_command.command_id,
                "command_type": saved_command.command_type,
            },
        )
        self._emit_operator_command_audit(
            event_type="security.browser_task_operator_command_enqueued",
            task=task,
            command=saved_command,
            actor_ip=actor_ip,
            actor_session_id=actor_session_id,
        )
        return saved_command

    def list_pending_operator_commands(
        self,
        *,
        task_id: str,
        worker_id: str,
        organization_id: str | None = None,
        limit: int = 100,
    ) -> list[BrowserOperatorCommand]:
        self._require_worker_lease(task_id, worker_id=worker_id, organization_id=organization_id)
        return self.store.list_operator_commands(
            task_id,
            organization_id=organization_id,
            state="queued",
            limit=limit,
        )

    def list_operator_commands(
        self,
        *,
        task_id: str,
        organization_id: str | None = None,
        limit: int = 100,
    ) -> list[BrowserOperatorCommand]:
        self._require_task(task_id, organization_id=organization_id)
        return self.store.list_operator_commands(
            task_id,
            organization_id=organization_id,
            limit=limit,
        )

    def mark_operator_command_delivered(
        self,
        *,
        command_id: str,
        worker_id: str,
        organization_id: str | None = None,
    ) -> BrowserOperatorCommand:
        command = self.store.get_operator_command(command_id, organization_id=organization_id)
        if command is None:
            raise KeyError(command_id)
        self._require_worker_lease(command.task_id, worker_id=worker_id, organization_id=organization_id)
        if command.state == "delivered":
            return command
        if command.state != "queued":
            raise ValueError("operator command is not queued")
        saved_command = self.store.save_operator_command(
            command.model_copy(update={"state": "delivered", "delivered_at": utc_now()})
        )
        task = self._require_task(saved_command.task_id, organization_id=organization_id)
        self._event(
            task=task,
            event_type="browser.operator_command_delivered",
            message=f"Operator command delivered: {saved_command.command_type}.",
            metadata={
                "operator_id": saved_command.operator_id,
                "command_id": saved_command.command_id,
                "command_type": saved_command.command_type,
                "worker_id": worker_id,
            },
        )
        self._emit_operator_command_audit(
            event_type="security.browser_task_operator_command_delivered",
            task=task,
            command=saved_command,
            actor_ip=None,
            actor_session_id=None,
            worker_id=worker_id,
        )
        return saved_command

    def mark_operator_command_failed(
        self,
        *,
        command_id: str,
        worker_id: str,
        error: str,
        organization_id: str | None = None,
    ) -> BrowserOperatorCommand:
        command = self.store.get_operator_command(command_id, organization_id=organization_id)
        if command is None:
            raise KeyError(command_id)
        self._require_worker_lease(command.task_id, worker_id=worker_id, organization_id=organization_id)
        if command.state == "failed":
            return command
        if command.state != "queued":
            raise ValueError("operator command is not queued")
        normalized_error = (error or "browser operator command failed").strip()[:1000]
        saved_command = self.store.save_operator_command(
            command.model_copy(
                update={
                    "state": "failed",
                    "delivered_at": utc_now(),
                    "error": normalized_error,
                }
            )
        )
        task = self._require_task(saved_command.task_id, organization_id=organization_id)
        self._event(
            task=task,
            event_type="browser.operator_command_failed",
            message=f"Operator command failed: {saved_command.command_type}.",
            metadata={
                "operator_id": saved_command.operator_id,
                "command_id": saved_command.command_id,
                "command_type": saved_command.command_type,
                "worker_id": worker_id,
                "error": normalized_error,
            },
        )
        self._emit_operator_command_audit(
            event_type="security.browser_task_operator_command_failed",
            task=task,
            command=saved_command,
            actor_ip=None,
            actor_session_id=None,
            outcome="failure",
            worker_id=worker_id,
            error=normalized_error,
        )
        return saved_command

    def build_worker_request(
        self,
        *,
        task_id: str,
        worker_id: str,
        organization_id: str | None = None,
    ) -> BrowserWorkerRequest:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        task = self._require_task(task_id, organization_id=organization_id)
        if task.state != "running" or task.lease_owner != worker_id:
            raise ValueError("browser task lease is not held by this worker")
        if task.task_pack_id is None:
            raise ValueError("browser task does not reference a task pack")
        if task.start_url is None:
            raise ValueError("browser task has no start_url")
        try:
            task_pack = self.task_pack_registry.get(task.task_pack_id, task.task_pack_version)
        except KeyError as exc:
            raise ValueError("unknown browser task pack") from exc
        return BrowserWorkerRequest.from_task_pack(
            request_id=new_id("bwreq_"),
            task_id=task.task_id,
            organization_id=task.organization_id,
            agent_id=task.agent_id,
            conversation_id=task.conversation_id,
            pack=task_pack,
            title=task.title,
            start_url=task.start_url,
            input=task.input_payload,
            credentials=self._worker_credentials_for_task(task_pack, task.credential_refs),
            attachments=self._worker_upload_attachments_for_task(task_pack, task.input_payload),
        )

    def record_worker_progress(
        self,
        *,
        worker_id: str,
        progress: BrowserWorkerProgress,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        task = self._require_worker_lease(
            progress.task_id,
            worker_id=worker_id,
            organization_id=organization_id,
        )
        if progress.phase in {"completed", "failed", "cancelled"}:
            raise ValueError("terminal worker phases must be reported through worker results")
        if self._has_worker_progress_event(
            task_id=task.task_id,
            worker_id=worker_id,
            worker_event_sequence=progress.event_sequence,
            organization_id=organization_id,
        ):
            return self.get_snapshot(task.task_id, organization_id=organization_id)
        saved_task = self.store.save_task(task.model_copy(update={"state": "running", "updated_at": utc_now()}))
        self._event(
            task=saved_task,
            event_type=f"browser.worker_{progress.phase}",
            message=progress.message,
            metadata={
                **dict(progress.metadata),
                "worker_id": worker_id,
                "worker_event_sequence": progress.event_sequence,
                "worker_phase": progress.phase,
            },
        )
        return self.get_snapshot(task.task_id, organization_id=organization_id)

    def apply_worker_result(
        self,
        *,
        worker_id: str,
        result: BrowserWorkerResult,
        organization_id: str | None = None,
    ) -> BrowserTaskSnapshot:
        task = self._require_worker_lease(
            result.task_id,
            worker_id=worker_id,
            organization_id=organization_id,
        )
        if result.success:
            payload: dict[str, object] = dict(result.output)
            if task.task_pack_id is not None:
                task_pack = self.task_pack_registry.get(task.task_pack_id, task.task_pack_version)
                self._validate_task_pack_result(task_pack, payload)
            if result.summary is not None:
                payload["summary"] = result.summary
            if result.artifacts:
                payload["artifacts"] = [
                    artifact.model_dump(mode="json", exclude_none=True)
                    for artifact in result.artifacts
                ]
            return self.complete_task(
                task_id=task.task_id,
                organization_id=organization_id,
                result=payload,
                message=result.summary or "Browser task completed.",
            )

        if result.error is None:
            raise ValueError("failed worker results must include an error")
        error_message = result.error.message
        if result.error.retryable and task.attempt_count < self._max_attempts_for_task(task):
            now = utc_now()
            saved_task = self.store.save_task(
                task.model_copy(
                    update={
                        "state": "queued",
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "operator_takeover_owner_id": None,
                        "operator_takeover_expires_at": None,
                        "error": error_message,
                        "updated_at": now,
                    }
                )
            )
            self._event(
                task=saved_task,
                event_type="browser.worker_retry",
                message=error_message,
                metadata={
                    **dict(result.error.metadata),
                    "worker_id": worker_id,
                    "error_kind": result.error.kind,
                    "attempt_count": task.attempt_count,
                },
            )
            return self.get_snapshot(task.task_id, organization_id=organization_id)
        return self.fail_task(
            task_id=task.task_id,
            organization_id=organization_id,
            error=error_message,
        )

    def complete_task(
        self,
        *,
        task_id: str,
        organization_id: str | None = None,
        result: dict[str, object] | None = None,
        message: str = "Browser task completed.",
    ) -> BrowserTaskSnapshot:
        task = self._require_task(task_id, organization_id=organization_id)
        if task.state == "completed":
            return self.get_snapshot(task_id, organization_id=organization_id)
        self._ensure_not_terminal(task)
        if task.state == "awaiting_approval":
            raise ValueError("browser task is awaiting approval")
        now = utc_now()
        merged_result = dict(task.result)
        merged_result.update(dict(result or {}))
        saved_task = self.store.save_task(
            task.model_copy(
                update={
                    "state": "completed",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "operator_takeover_owner_id": None,
                    "operator_takeover_expires_at": None,
                    "result": merged_result,
                    "updated_at": now,
                    "finished_at": now,
                    "error": None,
                }
            )
        )
        self._event(task=saved_task, event_type="browser.completed", message=message, metadata=merged_result)
        return self.get_snapshot(task_id, organization_id=organization_id)

    def fail_task(
        self,
        *,
        task_id: str,
        organization_id: str | None = None,
        error: str,
    ) -> BrowserTaskSnapshot:
        task = self._require_task(task_id, organization_id=organization_id)
        if task.state == "failed":
            return self.get_snapshot(task_id, organization_id=organization_id)
        self._ensure_not_terminal(task)
        now = utc_now()
        saved_task = self.store.save_task(
            task.model_copy(
                update={
                    "state": "failed",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "operator_takeover_owner_id": None,
                    "operator_takeover_expires_at": None,
                    "error": error,
                    "updated_at": now,
                    "finished_at": now,
                }
            )
        )
        self._event(task=saved_task, event_type="browser.failed", message=error)
        return self.get_snapshot(task_id, organization_id=organization_id)

    def list_conversation_tasks(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
    ) -> list[BrowserTaskSnapshot]:
        snapshots: list[BrowserTaskSnapshot] = []
        for task in self.store.list_tasks(conversation_id, organization_id=organization_id):
            snapshots.append(
                BrowserTaskSnapshot(
                    task=task,
                    approval=self.store.get_task_approval(task.task_id, organization_id=organization_id),
                    recent_events=self.store.list_events(task.task_id, organization_id=organization_id)[-8:],
                )
            )
        return snapshots

    def get_allowed_task_pack_ids(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
    ) -> set[str] | None:
        return self.store.list_allowed_task_pack_ids(
            organization_id=organization_id,
            agent_id=agent_id,
        )

    def replace_allowed_task_pack_ids(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        pack_ids: set[str] | None,
    ) -> set[str] | None:
        normalized = None if pack_ids is None else {pack_id.strip() for pack_id in pack_ids if pack_id.strip()}
        if normalized is not None:
            for pack_id in normalized:
                self.task_pack_registry.get(pack_id)
        return self.store.replace_allowed_task_pack_ids(
            organization_id=organization_id,
            agent_id=agent_id,
            pack_ids=normalized,
        )

    def list_recent_tasks(
        self,
        *,
        organization_id: str | None = None,
        conversation_id: str | None = None,
        state: str | None = None,
        approval_state: str | None = None,
        limit: int = 50,
    ) -> list[BrowserTaskSnapshot]:
        snapshots: list[BrowserTaskSnapshot] = []
        for task in self.store.list_recent_tasks(
            organization_id=organization_id,
            conversation_id=conversation_id,
            state=state,
            approval_state=approval_state,
            limit=limit,
        ):
            snapshots.append(
                BrowserTaskSnapshot(
                    task=task,
                    approval=self.store.get_task_approval(task.task_id, organization_id=organization_id),
                    recent_events=self.store.list_events(task.task_id, organization_id=organization_id)[-8:],
                )
            )
        return snapshots

    def get_snapshot(self, task_id: str, *, organization_id: str | None = None) -> BrowserTaskSnapshot:
        task = self._require_task(task_id, organization_id=organization_id)
        return BrowserTaskSnapshot(
            task=task,
            approval=self.store.get_task_approval(task_id, organization_id=organization_id),
            recent_events=self.store.list_events(task_id, organization_id=organization_id),
        )

    def attach_artifact(
        self,
        *,
        task_id: str,
        organization_id: str | None = None,
        artifact: dict[str, object],
        message: str = "Browser artifact is ready.",
    ) -> BrowserTaskSnapshot:
        task = self._require_task(task_id, organization_id=organization_id)
        self._ensure_not_terminal(task)
        result = dict(task.result)
        artifacts = list(result.get("artifacts", []))
        artifacts.append(dict(artifact))
        result["artifacts"] = artifacts
        saved_task = self.store.save_task(
            task.model_copy(update={"result": result, "updated_at": utc_now()})
        )
        self._event(
            task=saved_task,
            event_type="browser.artifact_ready",
            message=message,
            metadata=dict(artifact),
        )
        return self.get_snapshot(task_id, organization_id=organization_id)

    def _require_task(self, task_id: str, *, organization_id: str | None) -> BrowserTask:
        task = self.store.get_task(task_id, organization_id=organization_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _require_worker_lease(
        self,
        task_id: str,
        *,
        worker_id: str,
        organization_id: str | None,
    ) -> BrowserTask:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        task = self._require_task(task_id, organization_id=organization_id)
        self._ensure_not_terminal(task)
        if task.state != "running" or task.lease_owner != worker_id:
            raise ValueError("browser task lease is not held by this worker")
        return task

    def _has_worker_progress_event(
        self,
        *,
        task_id: str,
        worker_id: str,
        worker_event_sequence: int,
        organization_id: str | None,
    ) -> bool:
        for event in self.store.list_events(task_id, organization_id=organization_id):
            if (
                event.metadata.get("worker_id") == worker_id
                and event.metadata.get("worker_event_sequence") == worker_event_sequence
            ):
                return True
        return False

    def _assert_task_pack_allowed(
        self,
        *,
        pack_id: str,
        organization_id: str | None,
        agent_id: str | None,
    ) -> None:
        if self.task_pack_access_policy is not None:
            self.task_pack_access_policy.assert_allowed(
                pack_id=pack_id,
                organization_id=organization_id,
                agent_id=agent_id,
            )
        global_allowed = self.store.list_allowed_task_pack_ids(
            organization_id=None,
            agent_id=None,
        )
        if global_allowed is not None and pack_id not in global_allowed:
            raise ValueError(f"browser task pack is not enabled: {pack_id}")
        if organization_id is not None:
            org_allowed = self.store.list_allowed_task_pack_ids(
                organization_id=organization_id,
                agent_id=None,
            )
            if org_allowed is not None and pack_id not in org_allowed:
                raise ValueError(f"browser task pack is not enabled for this organization: {pack_id}")
        if agent_id is not None:
            agent_allowed = self.store.list_allowed_task_pack_ids(
                organization_id=organization_id,
                agent_id=agent_id,
            )
            if agent_allowed is None:
                agent_allowed = self.store.list_allowed_task_pack_ids(
                    organization_id=None,
                    agent_id=agent_id,
                )
            if agent_allowed is not None and pack_id not in agent_allowed:
                raise ValueError(f"browser task pack is not enabled for this agent: {pack_id}")

    def _max_attempts_for_task(self, task: BrowserTask) -> int:
        if task.task_pack_id is None:
            return 1
        try:
            task_pack = self.task_pack_registry.get(task.task_pack_id, task.task_pack_version)
        except KeyError:
            return 1
        return task_pack.execution_policy.retry_policy.max_attempts

    def _operator_takeover_enabled_for_task(self, task: BrowserTask) -> bool:
        if task.task_pack_id is None:
            return True
        try:
            task_pack = self.task_pack_registry.get(task.task_pack_id, task.task_pack_version)
        except KeyError:
            return False
        return task_pack.operator_policy.operator_takeover_enabled

    def _ensure_active_operator_takeover(self, task: BrowserTask, operator_id: str) -> None:
        self._ensure_not_terminal(task)
        if task.state != "running":
            raise ValueError("operator commands require a running browser task")
        now = utc_now()
        if (
            task.operator_takeover_owner_id != operator_id
            or task.operator_takeover_expires_at is None
            or task.operator_takeover_expires_at <= now
        ):
            raise ValueError("operator takeover is not active for this operator")

    def _validate_operator_command_payload(
        self,
        command_type: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if command_type == "click":
            selector = payload.get("selector")
            x = payload.get("x")
            y = payload.get("y")
            if isinstance(selector, str) and selector.strip():
                return {"selector": selector.strip()[:500]}
            if isinstance(x, int | float) and isinstance(y, int | float):
                return {"x": float(x), "y": float(y)}
            raise ValueError("click command requires selector or x/y coordinates")
        if command_type == "type_text":
            text = payload.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError("type_text command requires text")
            if len(text) > 2000:
                raise ValueError("type_text command text exceeds 2000 characters")
            selector = payload.get("selector")
            normalized: dict[str, object] = {"text": text}
            if isinstance(selector, str) and selector.strip():
                normalized["selector"] = selector.strip()[:500]
            return normalized
        if command_type == "press_key":
            key = payload.get("key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("press_key command requires key")
            if len(key) > 80:
                raise ValueError("press_key command key exceeds 80 characters")
            return {"key": key.strip()}
        if command_type == "scroll":
            direction = payload.get("direction")
            if direction not in {"up", "down", "left", "right"}:
                raise ValueError("scroll command direction must be up, down, left, or right")
            pages = payload.get("pages", 1)
            if not isinstance(pages, int | float) or pages <= 0 or pages > 10:
                raise ValueError("scroll command pages must be between 0 and 10")
            return {"direction": direction, "pages": float(pages)}
        if command_type in {"navigate_back", "navigate_forward"}:
            return {}
        if command_type == "wait":
            duration_ms = payload.get("duration_ms")
            selector = payload.get("selector")
            if isinstance(selector, str) and selector.strip():
                return {"selector": selector.strip()[:500]}
            if isinstance(duration_ms, int) and 0 < duration_ms <= 10000:
                return {"duration_ms": duration_ms}
            raise ValueError("wait command requires selector or duration_ms between 1 and 10000")
        raise ValueError(f"unsupported operator command type: {command_type}")

    def _normalize_credential_refs(self, credential_refs: dict[str, str] | None) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for name, secret_ref in dict(credential_refs or {}).items():
            normalized_name = str(name).strip()
            normalized_ref = str(secret_ref).strip()
            if not normalized_name:
                raise ValueError("credential reference name cannot be empty")
            if not normalized_ref:
                raise ValueError(f"credential reference for {normalized_name} cannot be empty")
            normalized[normalized_name] = normalized_ref
        return normalized

    def _validate_task_pack_credentials(self, task_pack, credential_refs: dict[str, str]) -> None:
        declared = {requirement.name: requirement for requirement in task_pack.credentials}
        unknown = sorted(set(credential_refs) - set(declared))
        if unknown:
            raise ValueError(f"unknown credential refs for task pack: {', '.join(unknown)}")
        missing = sorted(
            name
            for name, requirement in declared.items()
            if requirement.required and name not in credential_refs
        )
        if missing:
            raise ValueError(f"missing required credential refs for task pack: {', '.join(missing)}")

    def _validate_task_pack_input(self, task_pack, input_payload: dict[str, object]) -> None:
        try:
            validate_json_schema(input_payload, task_pack.input_schema)
        except JsonContractValidationError as exc:
            raise ValueError(f"browser task input does not match task pack schema: {exc}") from exc

    def _validate_task_pack_result(self, task_pack, result_payload: dict[str, object]) -> None:
        try:
            validate_json_schema(result_payload, task_pack.result_schema)
        except JsonContractValidationError as exc:
            raise ValueError(f"browser worker result does not match task pack schema: {exc}") from exc

    def _worker_upload_attachments_for_task(
        self,
        task_pack,
        input_payload: dict[str, object],
    ) -> list[BrowserAttachmentRef]:
        if task_pack.browser_plan is None:
            return []
        attachment_refs: list[BrowserAttachmentRef] = []
        seen: set[str] = set()
        for action in task_pack.browser_plan.actions:
            if action.kind != "upload" or action.value_from_input is None:
                continue
            raw_attachment_id = input_payload.get(action.value_from_input)
            if not isinstance(raw_attachment_id, str):
                continue
            attachment_id = raw_attachment_id.strip()
            if not attachment_id or attachment_id in seen:
                continue
            seen.add(attachment_id)
            attachment_refs.append(BrowserAttachmentRef(attachment_id=attachment_id))
        return attachment_refs

    def _approval_context_for_task_pack(
        self,
        task_pack,
        *,
        approval_kind: str,
        start_url: str | None,
        credential_refs: dict[str, str],
    ) -> dict[str, object]:
        requirements = {requirement.name: requirement for requirement in task_pack.credentials}
        credential_context: list[dict[str, object]] = []
        for name in sorted(credential_refs):
            requirement = requirements.get(name)
            credential_context.append(
                {
                    "name": name,
                    "kind": requirement.kind if requirement is not None else "unknown",
                    "provider": None if requirement is None else requirement.provider,
                    "auth_type": None if requirement is None else requirement.auth_type,
                    "ref_label": self._credential_ref_label(credential_refs[name]),
                }
            )
        return {
            "approval_kind": approval_kind,
            "task_pack_id": task_pack.pack_id,
            "task_pack_version": task_pack.version,
            "task_pack_display_name": task_pack.display_name,
            "performs_write": task_pack.performs_write,
            "allow_downloads": task_pack.execution_policy.allow_downloads,
            "allow_uploads": task_pack.execution_policy.allow_uploads,
            "allowed_artifacts": list(task_pack.artifact_policy.allowed_artifacts),
            "allowed_download_content_types": list(task_pack.artifact_policy.allowed_download_content_types),
            "max_download_bytes": task_pack.artifact_policy.max_download_bytes,
            "allowed_domains": list(task_pack.allowed_domains),
            "start_url": start_url,
            "credential_refs": credential_context,
            "requires_reapproval_after_navigation": task_pack.approval_policy.require_reapproval_after_navigation,
        }

    @staticmethod
    def _credential_ref_label(secret_ref: str) -> str:
        if secret_ref.startswith("connection:"):
            connection_id = secret_ref[len("connection:"):].strip()
            if len(connection_id) > 10:
                return f"connection:{connection_id[:6]}...{connection_id[-4:]}"
            return f"connection:{connection_id}"
        return "credential_ref"

    def _emit_approval_audit(
        self,
        *,
        event_type: str,
        outcome: str,
        task: BrowserTask,
        approval: BrowserApproval,
        actor_id: str | None,
        actor_ip: str | None,
        actor_session_id: str | None,
        reason: str | None,
    ) -> None:
        if self.audit_router is None or task.organization_id is None:
            return
        emit_audit_event(
            self.audit_router,
            event_type=event_type,
            organization_id=task.organization_id,
            outcome=outcome,
            actor_id=actor_id,
            actor_ip=actor_ip,
            actor_session_id=actor_session_id,
            resource_type="browser_task",
            resource_id=task.task_id,
            detail={
                "approval_id": approval.approval_id,
                "approval_kind": approval.kind,
                "conversation_id": task.conversation_id,
                "agent_id": task.agent_id,
                "task_pack_id": task.task_pack_id,
                "task_pack_version": task.task_pack_version,
                "performs_write": approval.context.get("performs_write"),
                "allowed_domains": approval.context.get("allowed_domains"),
                "credential_refs": approval.context.get("credential_refs"),
                **({"reason": reason} if reason else {}),
            },
        )

    def _emit_operator_takeover_audit(
        self,
        *,
        event_type: str,
        task: BrowserTask,
        operator_id: str,
        actor_ip: str | None,
        actor_session_id: str | None,
        reason: str | None,
    ) -> None:
        if self.audit_router is None or task.organization_id is None:
            return
        emit_audit_event(
            self.audit_router,
            event_type=event_type,
            organization_id=task.organization_id,
            outcome="success",
            actor_id=operator_id,
            actor_ip=actor_ip,
            actor_session_id=actor_session_id,
            resource_type="browser_task",
            resource_id=task.task_id,
            detail={
                "conversation_id": task.conversation_id,
                "agent_id": task.agent_id,
                "task_pack_id": task.task_pack_id,
                "task_pack_version": task.task_pack_version,
                "lease_owner": task.lease_owner,
                "operator_takeover_owner_id": task.operator_takeover_owner_id,
                "operator_takeover_expires_at": (
                    task.operator_takeover_expires_at.isoformat()
                    if task.operator_takeover_expires_at is not None
                    else None
                ),
                **({"reason": reason} if reason else {}),
            },
        )

    def _emit_operator_command_audit(
        self,
        *,
        event_type: str,
        task: BrowserTask,
        command: BrowserOperatorCommand,
        actor_ip: str | None,
        actor_session_id: str | None,
        outcome: str = "success",
        worker_id: str | None = None,
        error: str | None = None,
    ) -> None:
        if self.audit_router is None or task.organization_id is None:
            return
        emit_audit_event(
            self.audit_router,
            event_type=event_type,
            organization_id=task.organization_id,
            outcome=outcome,
            actor_id=command.operator_id,
            actor_ip=actor_ip,
            actor_session_id=actor_session_id,
            resource_type="browser_task",
            resource_id=task.task_id,
            detail={
                "command_id": command.command_id,
                "command_type": command.command_type,
                "payload_keys": sorted(command.payload),
                "conversation_id": task.conversation_id,
                "agent_id": task.agent_id,
                "task_pack_id": task.task_pack_id,
                "task_pack_version": task.task_pack_version,
                **({"worker_id": worker_id} if worker_id is not None else {}),
                **({"error": error} if error is not None else {}),
            },
        )

    def _worker_credentials_for_task(
        self,
        task_pack,
        credential_refs: dict[str, str],
    ) -> list[BrowserCredentialRef]:
        credentials: list[BrowserCredentialRef] = []
        for requirement in task_pack.credentials:
            secret_ref = credential_refs.get(requirement.name)
            if secret_ref is None:
                continue
            credentials.append(
                BrowserCredentialRef(
                    name=requirement.name,
                    kind=requirement.kind,
                    secret_ref=secret_ref,
                )
            )
        return credentials

    def _ensure_not_terminal(self, task: BrowserTask) -> None:
        if task.state in TERMINAL_TASK_STATES:
            raise ValueError(f"browser task is already {task.state}")

    def _ensure_pending_approval(self, approval: BrowserApproval) -> None:
        if approval.state != "pending":
            raise ValueError("approval is not pending")
        now = utc_now()
        if approval.expires_at is not None and approval.expires_at <= now:
            self._expire_pending_approval(approval, now=now)
            raise ValueError("approval expired")

    def _expire_pending_approval(
        self,
        approval: BrowserApproval,
        *,
        now: datetime,
    ) -> BrowserTaskSnapshot:
        if approval.state != "pending":
            return self.get_snapshot(approval.task_id, organization_id=approval.organization_id)
        saved_approval = self.store.save_approval(
            approval.model_copy(
                update={
                    "state": "expired",
                    "decision_reason": "approval expired",
                    "decided_at": now,
                }
            )
        )
        task = self._require_task(saved_approval.task_id, organization_id=saved_approval.organization_id)
        saved_task = task
        if task.state not in TERMINAL_TASK_STATES:
            saved_task = self.store.save_task(
                task.model_copy(
                    update={
                        "state": "failed",
                        "approval_state": "expired",
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "operator_takeover_owner_id": None,
                        "operator_takeover_expires_at": None,
                        "error": "approval expired",
                        "updated_at": now,
                        "finished_at": now,
                    }
                )
            )
            self._event(task=saved_task, event_type="browser.approval_expired", message="Browser approval expired.")
        return BrowserTaskSnapshot(
            task=saved_task,
            approval=saved_approval,
            recent_events=self.store.list_events(saved_task.task_id, organization_id=saved_task.organization_id),
        )

    def _event(
        self,
        *,
        task: BrowserTask,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> BrowserTaskEvent:
        return self.store.save_event(
            BrowserTaskEvent(
                task_id=task.task_id,
                organization_id=task.organization_id,
                conversation_id=task.conversation_id,
                event_type=event_type,
                message=message,
                metadata=dict(metadata or {}),
            )
        )
