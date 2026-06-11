from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import inspect
import logging
from threading import Event, Thread
from typing import Protocol

from .models import BrowserOperatorCommand, BrowserTaskSnapshot
from .service import BrowserTaskService
from .credentials import BrowserCredentialConnectionStore
from .artifacts import BrowserArtifactPublisher
from ..runtime_config import RuntimeSettings
from .worker_contracts import (
    BrowserWorkerError,
    BrowserGeneratedArtifact,
    BrowserWorkerProgress,
    BrowserWorkerRequest,
    BrowserWorkerResult,
)

logger = logging.getLogger(__name__)

# Recurring tick consumed by ruhu.worker (RP-2.2): each tick expires stale
# approvals and drains available tasks via the service's lease-claim store.
BROWSER_TASKS_JOB_TYPE = "browser_tasks.tick"


class BrowserWorkerAdapter(Protocol):
    def execute(
        self,
        request: BrowserWorkerRequest,
        report_progress: Callable[[BrowserWorkerProgress], None],
        poll_operator_commands: Callable[[], list[BrowserOperatorCommand]] | None = None,
        mark_operator_command_delivered: Callable[[str], None] | None = None,
        mark_operator_command_failed: Callable[[str, str], None] | None = None,
        publish_session_snapshot: Callable[[BrowserGeneratedArtifact], None] | None = None,
    ) -> BrowserWorkerResult: ...


class DisabledBrowserWorkerAdapter:
    def execute(
        self,
        request: BrowserWorkerRequest,
        report_progress: Callable[[BrowserWorkerProgress], None],
        poll_operator_commands: Callable[[], list[BrowserOperatorCommand]] | None = None,
        mark_operator_command_delivered: Callable[[str], None] | None = None,
        mark_operator_command_failed: Callable[[str, str], None] | None = None,
        publish_session_snapshot: Callable[[BrowserGeneratedArtifact], None] | None = None,
    ) -> BrowserWorkerResult:
        return BrowserWorkerResult(
            task_id=request.task_id,
            success=False,
            error=BrowserWorkerError(
                kind="worker_unavailable",
                message="browser worker adapter is disabled",
                retryable=False,
            ),
        )


@dataclass(slots=True)
class BrowserTaskRuntime:
    """Thread-free browser task drain.

    Runs as the ``browser_tasks.tick`` recurring job in ``ruhu.worker``:
    each tick calls :meth:`sweep_once` then
    :meth:`process_available_tasks_once`. The per-task lease heartbeat
    thread during adapter execution is task machinery, not a polling loop,
    and stays.
    """

    service: BrowserTaskService
    adapter: BrowserWorkerAdapter
    worker_identity: str = "ruhu-browser-worker"
    lease_seconds: int = 60
    heartbeat_interval_seconds: float = 10.0
    artifact_publisher: BrowserArtifactPublisher | None = None

    def process_available_tasks_once(
        self,
        *,
        max_tasks: int = 1,
        organization_id: str | None = None,
    ) -> list[BrowserTaskSnapshot]:
        processed: list[BrowserTaskSnapshot] = []
        for index in range(max(1, max_tasks)):
            worker_id = self._worker_id(index)
            claimed = self.service.claim_next_task(
                worker_id=worker_id,
                organization_id=organization_id,
                lease_seconds=self.lease_seconds,
            )
            if claimed is None:
                break
            processed.append(self._execute_claimed_task(claimed, worker_id=worker_id))
        return processed

    def sweep_once(self, *, organization_id: str | None = None) -> int:
        expired = self.service.expire_stale_approvals(organization_id=organization_id)
        return len(expired)

    def _execute_claimed_task(
        self,
        claimed: BrowserTaskSnapshot,
        *,
        worker_id: str,
    ) -> BrowserTaskSnapshot:
        task_id = claimed.task.task_id
        organization_id = claimed.task.organization_id
        try:
            request = self.service.build_worker_request(
                task_id=task_id,
                worker_id=worker_id,
                organization_id=organization_id,
            )
        except Exception as exc:
            return self._fail_runtime_task(
                task_id=task_id,
                worker_id=worker_id,
                organization_id=organization_id,
                error=f"browser worker request failed: {exc}",
            )

        def report_progress(progress: BrowserWorkerProgress) -> None:
            self.service.record_worker_progress(
                worker_id=worker_id,
                progress=progress,
                organization_id=organization_id,
            )
            self.service.renew_task_lease(
                task_id=task_id,
                worker_id=worker_id,
                organization_id=organization_id,
                lease_seconds=self.lease_seconds,
            )

        def poll_operator_commands() -> list[BrowserOperatorCommand]:
            try:
                return self.service.list_pending_operator_commands(
                    task_id=task_id,
                    worker_id=worker_id,
                    organization_id=organization_id,
                    limit=25,
                )
            except Exception:
                logger.warning(
                    "browser runtime operator command polling failed",
                    extra={"task_id": task_id, "organization_id": organization_id, "worker_id": worker_id},
                )
                return []

        def mark_operator_command_delivered(command_id: str) -> None:
            self.service.mark_operator_command_delivered(
                command_id=command_id,
                worker_id=worker_id,
                organization_id=organization_id,
            )

        def mark_operator_command_failed(command_id: str, error: str) -> None:
            self.service.mark_operator_command_failed(
                command_id=command_id,
                worker_id=worker_id,
                error=error,
                organization_id=organization_id,
            )

        def publish_session_snapshot(artifact: BrowserGeneratedArtifact) -> None:
            self._publish_live_session_snapshot(
                claimed,
                artifact=artifact,
                worker_id=worker_id,
            )

        try:
            heartbeat_stop = Event()
            heartbeat_thread = Thread(
                target=self._renew_lease_until_stopped,
                kwargs={
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "organization_id": organization_id,
                    "stop_event": heartbeat_stop,
                },
                name=f"{worker_id}-heartbeat",
                daemon=True,
            )
            heartbeat_thread.start()
            result = self._execute_adapter(
                request=request,
                report_progress=report_progress,
                poll_operator_commands=poll_operator_commands,
                mark_operator_command_delivered=mark_operator_command_delivered,
                mark_operator_command_failed=mark_operator_command_failed,
                publish_session_snapshot=publish_session_snapshot,
            )
        except Exception as exc:
            logger.exception(
                "browser worker adapter failed",
                extra={"task_id": task_id, "organization_id": organization_id, "worker_id": worker_id},
            )
            result = BrowserWorkerResult(
                task_id=task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="worker_unavailable",
                    message=str(exc) or "browser worker adapter failed",
                    retryable=True,
                ),
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)
        try:
            result = self._publish_generated_artifacts(
                claimed,
                result=result,
                worker_id=worker_id,
            )
            snapshot = self.service.apply_worker_result(
                worker_id=worker_id,
                result=result,
                organization_id=organization_id,
            )
        except Exception as exc:
            return self._fail_runtime_task(
                task_id=task_id,
                worker_id=worker_id,
                organization_id=organization_id,
                error=f"browser worker result failed: {exc}",
            )
        return snapshot

    def _execute_adapter(
        self,
        *,
        request: BrowserWorkerRequest,
        report_progress: Callable[[BrowserWorkerProgress], None],
        poll_operator_commands: Callable[[], list[BrowserOperatorCommand]],
        mark_operator_command_delivered: Callable[[str], None],
        mark_operator_command_failed: Callable[[str, str], None],
        publish_session_snapshot: Callable[[BrowserGeneratedArtifact], None],
    ) -> BrowserWorkerResult:
        adapter_execute = self.adapter.execute
        supported_kwargs = {
            "poll_operator_commands": poll_operator_commands,
            "mark_operator_command_delivered": mark_operator_command_delivered,
            "mark_operator_command_failed": mark_operator_command_failed,
            "publish_session_snapshot": publish_session_snapshot,
        }
        try:
            signature = inspect.signature(adapter_execute)
        except (TypeError, ValueError):
            return adapter_execute(request, report_progress, **supported_kwargs)
        parameters = signature.parameters
        accepts_arbitrary_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts_arbitrary_kwargs:
            return adapter_execute(request, report_progress, **supported_kwargs)
        filtered_kwargs = {
            name: value
            for name, value in supported_kwargs.items()
            if name in parameters
        }
        return adapter_execute(request, report_progress, **filtered_kwargs)

    def _publish_generated_artifacts(
        self,
        claimed: BrowserTaskSnapshot,
        *,
        result: BrowserWorkerResult,
        worker_id: str,
    ) -> BrowserWorkerResult:
        if not result.generated_artifacts:
            return result
        if self.artifact_publisher is None:
            return BrowserWorkerResult(
                task_id=result.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="worker_unavailable",
                    message="browser worker generated artifacts but no artifact publisher is configured",
                    retryable=False,
                ),
            )
        published = list(result.artifacts)
        artifact_policy = self._artifact_policy_for_task(claimed)
        artifact_policy_error = self._validate_generated_artifact_policy(
            claimed,
            result=result,
            artifact_policy=artifact_policy,
        )
        if artifact_policy_error is not None:
            return BrowserWorkerResult(
                task_id=result.task_id,
                success=False,
                error=artifact_policy_error,
            )
        for generated in result.generated_artifacts:
            try:
                artifact_ref = self.artifact_publisher.publish_generated_artifact(
                    task=claimed.task,
                    artifact=generated,
                )
            except Exception as exc:
                logger.exception(
                    "browser artifact publish failed",
                    extra={
                        "task_id": claimed.task.task_id,
                        "organization_id": claimed.task.organization_id,
                        "worker_id": worker_id,
                    },
                )
                return BrowserWorkerResult(
                    task_id=result.task_id,
                    success=False,
                    error=BrowserWorkerError(
                        kind="worker_unavailable",
                        message=str(exc) or "browser artifact publish failed",
                        retryable=True,
                    ),
                )
            published.append(artifact_ref)
            self.service.attach_artifact(
                task_id=claimed.task.task_id,
                organization_id=claimed.task.organization_id,
                artifact={
                    "artifact_id": artifact_ref.artifact_id,
                    "kind": artifact_ref.kind,
                    "label": artifact_ref.label,
                    **dict(artifact_ref.metadata),
                },
                message=f"Browser artifact ready: {artifact_ref.label or artifact_ref.artifact_id}.",
            )
        return result.model_copy(update={"artifacts": published, "generated_artifacts": []})

    def _publish_live_session_snapshot(
        self,
        claimed: BrowserTaskSnapshot,
        *,
        artifact: BrowserGeneratedArtifact,
        worker_id: str,
    ) -> None:
        if self.artifact_publisher is None:
            return
        artifact_policy = self._artifact_policy_for_task(claimed)
        artifact_policy_error = self._validate_generated_artifact_policy(
            claimed,
            result=BrowserWorkerResult(
                task_id=claimed.task.task_id,
                success=True,
                generated_artifacts=[artifact],
            ),
            artifact_policy=artifact_policy,
        )
        if artifact_policy_error is not None:
            raise ValueError(artifact_policy_error.message)
        artifact_ref = self.artifact_publisher.publish_generated_artifact(
            task=claimed.task,
            artifact=artifact,
        )
        self.service.attach_artifact(
            task_id=claimed.task.task_id,
            organization_id=claimed.task.organization_id,
            artifact={
                "artifact_id": artifact_ref.artifact_id,
                "kind": artifact_ref.kind,
                "label": artifact_ref.label,
                **dict(artifact_ref.metadata),
            },
            message=f"Browser session snapshot ready: {artifact_ref.label or artifact_ref.artifact_id}.",
        )

    def _artifact_policy_for_task(self, claimed: BrowserTaskSnapshot):
        task = claimed.task
        if task.task_pack_id is None:
            return None
        try:
            task_pack = self.service.task_pack_registry.get(task.task_pack_id, task.task_pack_version)
        except KeyError:
            return None
        return task_pack.artifact_policy

    def _validate_generated_artifact_policy(
        self,
        claimed: BrowserTaskSnapshot,
        *,
        result: BrowserWorkerResult,
        artifact_policy,
    ) -> BrowserWorkerError | None:
        if artifact_policy is None:
            return None
        allowed_artifacts = set(artifact_policy.allowed_artifacts)
        disallowed = [
            artifact.kind
            for artifact in result.generated_artifacts
            if artifact.kind not in allowed_artifacts
        ]
        if disallowed:
            return BrowserWorkerError(
                kind="policy_violation",
                message="browser worker generated an artifact kind that is not allowed by the task pack",
                retryable=False,
                metadata={"artifact_kinds": disallowed},
            )
        for artifact in result.generated_artifacts:
            if artifact.kind == "screenshot" and artifact_policy.screenshot_redaction_required:
                if artifact.metadata.get("redacted") is not True:
                    return BrowserWorkerError(
                        kind="policy_violation",
                        message="browser worker generated an unredacted screenshot",
                        retryable=False,
                        metadata={"filename": artifact.filename},
                    )
            if artifact.kind == "download":
                content_type = artifact.content_type.split(";", 1)[0].strip().lower()
                if content_type not in set(artifact_policy.allowed_download_content_types):
                    return BrowserWorkerError(
                        kind="policy_violation",
                        message="browser worker generated a download with a disallowed content type",
                        retryable=False,
                        metadata={
                            "filename": artifact.filename,
                            "content_type": artifact.content_type,
                        },
                    )
                if len(artifact.content_bytes) > artifact_policy.max_download_bytes:
                    return BrowserWorkerError(
                        kind="policy_violation",
                        message="browser worker generated a download that exceeds the task pack size limit",
                        retryable=False,
                        metadata={
                            "filename": artifact.filename,
                            "size_bytes": len(artifact.content_bytes),
                            "max_download_bytes": artifact_policy.max_download_bytes,
                        },
                    )
        return None

    def _fail_runtime_task(
        self,
        *,
        task_id: str,
        worker_id: str,
        organization_id: str | None,
        error: str,
    ) -> BrowserTaskSnapshot:
        logger.warning(
            "browser runtime task failed",
            extra={"task_id": task_id, "organization_id": organization_id, "worker_id": worker_id},
        )
        return self.service.fail_task(task_id=task_id, organization_id=organization_id, error=error)

    def _renew_lease_until_stopped(
        self,
        *,
        task_id: str,
        worker_id: str,
        organization_id: str | None,
        stop_event: Event,
    ) -> None:
        interval = max(0.1, min(self.heartbeat_interval_seconds, self.lease_seconds / 3))
        while not stop_event.wait(interval):
            try:
                self.service.renew_task_lease(
                    task_id=task_id,
                    worker_id=worker_id,
                    organization_id=organization_id,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                logger.warning(
                    "browser runtime heartbeat failed",
                    extra={
                        "task_id": task_id,
                        "organization_id": organization_id,
                        "worker_id": worker_id,
                    },
                )
                return

    def _worker_id(self, index: int) -> str:
        return f"{self.worker_identity}:{index + 1}"


def build_browser_task_runtime(
    *,
    service: BrowserTaskService,
    runtime_settings: RuntimeSettings,
    adapter: BrowserWorkerAdapter | None = None,
    connection_store: BrowserCredentialConnectionStore | None = None,
    artifact_publisher: BrowserArtifactPublisher | None = None,
    attachment_service: object | None = None,
) -> BrowserTaskRuntime | None:
    if not runtime_settings.browser_task_worker_enabled:
        return None
    adapter_name = runtime_settings.browser_task_worker_adapter.strip().lower()
    isolation_mode = runtime_settings.browser_task_worker_isolation_mode.strip().lower()
    if runtime_settings.environment.strip().lower() == "production" and isolation_mode == "local":
        raise RuntimeError(
            "browser task worker cannot use local isolation in production; "
            "deploy a locked-down browser worker and set RUHU_BROWSER_TASK_WORKER_ISOLATION_MODE"
        )
    if adapter is None:
        if adapter_name == "disabled":
            raise RuntimeError("browser task worker is enabled but the adapter is disabled")
        if adapter_name in {"playwright", "local-playwright"}:
            from .credentials import APIConnectionBrowserCredentialResolver
            from .playwright_adapter import PlaywrightBrowserWorkerAdapter
            from .uploads import AttachmentBrowserUploadResolver

            adapter = PlaywrightBrowserWorkerAdapter(
                credential_resolver=(
                    APIConnectionBrowserCredentialResolver(
                        connection_store,
                        actor_id=runtime_settings.browser_task_worker_identity,
                        audit_router=service.audit_router,
                    )
                    if connection_store is not None
                    else None
                ),
                upload_resolver=(
                    AttachmentBrowserUploadResolver(attachment_service)
                    if attachment_service is not None
                    else None
                ),
            )
        else:
            raise RuntimeError(
                f"unsupported browser task worker adapter: {runtime_settings.browser_task_worker_adapter}"
            )
    return BrowserTaskRuntime(
        service=service,
        adapter=adapter,
        worker_identity=runtime_settings.browser_task_worker_identity,
        lease_seconds=runtime_settings.browser_task_worker_lease_seconds,
        heartbeat_interval_seconds=runtime_settings.browser_task_worker_heartbeat_interval_seconds,
        artifact_publisher=artifact_publisher,
    )
