from collections.abc import Callable
from datetime import timedelta
import time

from ruhu.browser_tasks import (
    BrowserTaskPack,
    BrowserTaskPackArtifactPolicy,
    BrowserTaskPackRegistry,
    BrowserTaskRuntime,
    BrowserGeneratedArtifact,
    DisabledBrowserWorkerAdapter,
    BrowserWorkerProgress,
    BrowserWorkerRequest,
    BrowserWorkerResult,
    build_browser_task_runtime,
)
from ruhu.runtime_config import RuntimeSettings
from ruhu.browser_tasks.service import BrowserTaskService
from ruhu.browser_tasks.store import InMemoryBrowserTaskStore


class SuccessfulAdapter:
    def execute(
        self,
        request: BrowserWorkerRequest,
        report_progress: Callable[[BrowserWorkerProgress], None],
    ) -> BrowserWorkerResult:
        report_progress(
            BrowserWorkerProgress(
                task_id=request.task_id,
                event_sequence=1,
                phase="navigating",
                message="Opening target site.",
            )
        )
        return BrowserWorkerResult(
            task_id=request.task_id,
            success=True,
            summary="Lookup complete.",
            output={"status": "found"},
        )


class FailingAdapter:
    def execute(
        self,
        request: BrowserWorkerRequest,
        report_progress: Callable[[BrowserWorkerProgress], None],
    ) -> BrowserWorkerResult:
        raise RuntimeError("cloud browser unavailable")


class SlowAdapter:
    def execute(
        self,
        request: BrowserWorkerRequest,
        report_progress: Callable[[BrowserWorkerProgress], None],
    ) -> BrowserWorkerResult:
        time.sleep(0.22)
        return BrowserWorkerResult(
            task_id=request.task_id,
            success=True,
            summary="Slow lookup complete.",
        )


class ArtifactAdapter:
    def execute(
        self,
        request: BrowserWorkerRequest,
        report_progress: Callable[[BrowserWorkerProgress], None],
    ) -> BrowserWorkerResult:
        return BrowserWorkerResult(
            task_id=request.task_id,
            success=True,
            summary="Screenshot captured.",
            generated_artifacts=[
                BrowserGeneratedArtifact(
                    kind="screenshot",
                    filename="screen.png",
                    content_type="image/png",
                    content_bytes=b"png-bytes",
                    label="Final screenshot",
                    metadata={"redacted": True},
                )
            ],
        )


class DisallowedArtifactAdapter:
    def execute(
        self,
        request: BrowserWorkerRequest,
        report_progress: Callable[[BrowserWorkerProgress], None],
    ) -> BrowserWorkerResult:
        return BrowserWorkerResult(
            task_id=request.task_id,
            success=True,
            generated_artifacts=[
                BrowserGeneratedArtifact(
                    kind="download",
                    filename="file.bin",
                    content_type="application/octet-stream",
                    content_bytes=b"payload",
                )
            ],
        )


class FakeArtifactPublisher:
    def __init__(self) -> None:
        self.published = []

    def publish_generated_artifact(self, *, task, artifact):
        from ruhu.browser_tasks import BrowserArtifactRef

        self.published.append((task.task_id, artifact.filename))
        return BrowserArtifactRef(
            artifact_id="art_1",
            kind=artifact.kind,
            uri="artifact:art_1",
            label=artifact.label,
            metadata={
                "filename": artifact.filename,
                "content_type": artifact.content_type,
                "size_bytes": len(artifact.content_bytes),
                "internal_download_url": "/internal/browser-tasks/artifacts/art_1/download",
                "public_widget_download_url": "/public/widget/sessions/conv_runtime_artifact/artifacts/art_1/download",
            },
        )


class RenewCountingBrowserTaskService(BrowserTaskService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.renewal_count = 0

    def renew_task_lease(self, *args, **kwargs):
        self.renewal_count += 1
        return super().renew_task_lease(*args, **kwargs)


def test_browser_task_runtime_executes_claimed_task_with_adapter() -> None:
    service = BrowserTaskService(
        InMemoryBrowserTaskStore(),
        task_pack_registry=BrowserTaskPackRegistry(
            [
                BrowserTaskPack(
                    pack_id="lookup_order",
                    version="1.0.0",
                    display_name="Lookup order",
                    allowed_domains=["merchant.example"],
                    start_url="https://merchant.example/orders",
                )
            ]
        ),
    )
    created = service.create_task(
        conversation_id="conv_runtime",
        organization_id="org_1",
        title="Lookup order",
        task_pack_id="lookup_order",
        input_payload={"order_id": "ord_123"},
    )
    runtime = BrowserTaskRuntime(
        service=service,
        adapter=SuccessfulAdapter(),
        worker_identity="browser-runtime",
    )

    processed = runtime.process_available_tasks_once(max_tasks=1, organization_id="org_1")

    assert len(processed) == 1
    assert processed[0].task.task_id == created.task.task_id
    assert processed[0].task.state == "completed"
    assert processed[0].task.lease_owner is None
    assert processed[0].task.result["summary"] == "Lookup complete."
    assert processed[0].task.result["status"] == "found"
    assert [event.event_type for event in processed[0].recent_events] == [
        "browser.preparing",
        "browser.worker_claimed",
        "browser.worker_navigating",
        "browser.completed",
    ]


def test_browser_task_runtime_heartbeats_while_adapter_runs_without_progress() -> None:
    service = RenewCountingBrowserTaskService(
        InMemoryBrowserTaskStore(),
        task_pack_registry=BrowserTaskPackRegistry(
            [
                BrowserTaskPack(
                    pack_id="lookup_order",
                    version="1.0.0",
                    display_name="Lookup order",
                    allowed_domains=["merchant.example"],
                    start_url="https://merchant.example/orders",
                )
            ]
        ),
    )
    service.create_task(
        conversation_id="conv_runtime_heartbeat",
        organization_id="org_1",
        title="Lookup order",
        task_pack_id="lookup_order",
    )
    runtime = BrowserTaskRuntime(
        service=service,
        adapter=SlowAdapter(),
        worker_identity="browser-runtime",
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )

    processed = runtime.process_available_tasks_once(max_tasks=1, organization_id="org_1")

    assert processed[0].task.state == "completed"
    assert service.renewal_count >= 1


def test_browser_task_runtime_publishes_generated_artifacts_before_completion() -> None:
    service = BrowserTaskService(
        InMemoryBrowserTaskStore(),
        task_pack_registry=BrowserTaskPackRegistry(
            [
                BrowserTaskPack(
                    pack_id="lookup_order",
                    version="1.0.0",
                    display_name="Lookup order",
                    allowed_domains=["merchant.example"],
                    start_url="https://merchant.example/orders",
                )
            ]
        ),
    )
    created = service.create_task(
        conversation_id="conv_runtime_artifact",
        organization_id="org_1",
        title="Lookup order",
        task_pack_id="lookup_order",
    )
    publisher = FakeArtifactPublisher()
    runtime = BrowserTaskRuntime(
        service=service,
        adapter=ArtifactAdapter(),
        worker_identity="browser-runtime",
        artifact_publisher=publisher,
    )

    processed = runtime.process_available_tasks_once(max_tasks=1, organization_id="org_1")

    assert processed[0].task.state == "completed"
    assert publisher.published == [(created.task.task_id, "screen.png")]
    artifacts = processed[0].task.result["artifacts"]
    assert artifacts[0]["artifact_id"] == "art_1"
    assert artifacts[0]["label"] == "Final screenshot"
    assert "browser.artifact_ready" in [event.event_type for event in processed[0].recent_events]


def test_browser_task_runtime_rejects_disallowed_generated_artifact_kind() -> None:
    service = BrowserTaskService(
        InMemoryBrowserTaskStore(),
        task_pack_registry=BrowserTaskPackRegistry(
            [
                BrowserTaskPack(
                    pack_id="lookup_order",
                    version="1.0.0",
                    display_name="Lookup order",
                    allowed_domains=["merchant.example"],
                    start_url="https://merchant.example/orders",
                    artifact_policy=BrowserTaskPackArtifactPolicy(allowed_artifacts=["screenshot"]),
                )
            ]
        ),
    )
    service.create_task(
        conversation_id="conv_runtime_artifact_policy",
        organization_id="org_1",
        title="Lookup order",
        task_pack_id="lookup_order",
    )
    runtime = BrowserTaskRuntime(
        service=service,
        adapter=DisallowedArtifactAdapter(),
        worker_identity="browser-runtime",
        artifact_publisher=FakeArtifactPublisher(),
    )

    processed = runtime.process_available_tasks_once(max_tasks=1, organization_id="org_1")

    assert processed[0].task.state == "failed"
    assert processed[0].task.error == "browser worker generated an artifact kind that is not allowed by the task pack"


def test_browser_task_runtime_fails_unexecutable_task_without_task_pack() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())
    created = service.create_task(
        conversation_id="conv_runtime_no_pack",
        organization_id="org_1",
        title="Lookup order",
    )
    runtime = BrowserTaskRuntime(
        service=service,
        adapter=SuccessfulAdapter(),
        worker_identity="browser-runtime",
    )

    processed = runtime.process_available_tasks_once(max_tasks=1, organization_id="org_1")

    assert len(processed) == 1
    assert processed[0].task.task_id == created.task.task_id
    assert processed[0].task.state == "failed"
    assert "does not reference a task pack" in (processed[0].task.error or "")


def test_browser_task_runtime_converts_adapter_exception_to_failed_task() -> None:
    service = BrowserTaskService(
        InMemoryBrowserTaskStore(),
        task_pack_registry=BrowserTaskPackRegistry(
            [
                BrowserTaskPack(
                    pack_id="lookup_order",
                    version="1.0.0",
                    display_name="Lookup order",
                    allowed_domains=["merchant.example"],
                    start_url="https://merchant.example/orders",
                )
            ]
        ),
    )
    created = service.create_task(
        conversation_id="conv_runtime_fail",
        organization_id="org_1",
        title="Lookup order",
        task_pack_id="lookup_order",
    )
    runtime = BrowserTaskRuntime(
        service=service,
        adapter=FailingAdapter(),
        worker_identity="browser-runtime",
    )

    processed = runtime.process_available_tasks_once(max_tasks=1, organization_id="org_1")

    assert len(processed) == 1
    assert processed[0].task.task_id == created.task.task_id
    assert processed[0].task.state == "failed"
    assert processed[0].task.error == "cloud browser unavailable"


def test_browser_task_runtime_tick_sweeps_approvals_and_drains_tasks() -> None:
    """One worker tick (browser_tasks.tick in ruhu.worker): expire stale
    approvals, then drain available tasks."""
    store = InMemoryBrowserTaskStore()
    service = BrowserTaskService(
        store,
        task_pack_registry=BrowserTaskPackRegistry(
            [
                BrowserTaskPack(
                    pack_id="lookup_order",
                    version="1.0.0",
                    display_name="Lookup order",
                    allowed_domains=["merchant.example"],
                    start_url="https://merchant.example/orders",
                )
            ]
        ),
    )
    ready = service.create_task(
        conversation_id="conv_runtime_tick",
        organization_id="org_1",
        title="Lookup order",
        task_pack_id="lookup_order",
    )
    stale = service.create_task(
        conversation_id="conv_runtime_tick_stale",
        organization_id="org_1",
        title="Lookup order",
        task_pack_id="lookup_order",
        requires_approval=True,
        approval_ttl_seconds=60,
    )
    store.save_approval(
        stale.approval.model_copy(
            update={"expires_at": stale.approval.requested_at - timedelta(seconds=1)}
        )
    )
    runtime = BrowserTaskRuntime(
        service=service,
        adapter=SuccessfulAdapter(),
        worker_identity="browser-runtime",
    )

    expired = runtime.sweep_once(organization_id="org_1")
    processed = runtime.process_available_tasks_once(max_tasks=5, organization_id="org_1")

    assert expired == 1
    assert [snapshot.task.task_id for snapshot in processed] == [ready.task.task_id]
    assert processed[0].task.state == "completed"
    stale_snapshot = service.get_snapshot(stale.task.task_id, organization_id="org_1")
    assert stale_snapshot.task.approval_state == "expired"


def test_disabled_browser_worker_adapter_returns_non_retryable_failure() -> None:
    request = BrowserWorkerRequest.from_task_pack(
        request_id="req_1",
        task_id="task_1",
        conversation_id="conv_1",
        title="Lookup order",
        pack=BrowserTaskPack(
            pack_id="lookup_order",
            version="1.0.0",
            display_name="Lookup order",
            allowed_domains=["merchant.example"],
            start_url="https://merchant.example/orders",
        ),
    )

    result = DisabledBrowserWorkerAdapter().execute(request, lambda _progress: None)

    assert result.success is False
    assert result.error is not None
    assert result.error.kind == "worker_unavailable"
    assert result.error.retryable is False
    assert result.error.message == "browser worker adapter is disabled"


def test_build_browser_task_runtime_is_disabled_by_default() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())

    runtime = build_browser_task_runtime(
        service=service,
        runtime_settings=RuntimeSettings(),
        adapter=SuccessfulAdapter(),
    )

    assert runtime is None


def test_build_browser_task_runtime_fails_closed_when_enabled_with_disabled_adapter() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())

    try:
        build_browser_task_runtime(
            service=service,
            runtime_settings=RuntimeSettings(browser_task_worker_enabled=True),
        )
    except RuntimeError as exc:
        assert str(exc) == "browser task worker is enabled but the adapter is disabled"
    else:
        raise AssertionError("expected disabled browser worker adapter to fail closed")


def test_build_browser_task_runtime_uses_explicit_adapter_settings() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())

    runtime = build_browser_task_runtime(
        service=service,
        runtime_settings=RuntimeSettings(
            browser_task_worker_enabled=True,
            browser_task_worker_adapter="custom",
            browser_task_worker_identity="browser-worker-test",
            browser_task_worker_lease_seconds=120,
            browser_task_worker_heartbeat_interval_seconds=15,
        ),
        adapter=SuccessfulAdapter(),
    )

    assert runtime is not None
    assert runtime.worker_identity == "browser-worker-test"
    assert runtime.lease_seconds == 120
    assert runtime.heartbeat_interval_seconds == 15


def test_build_browser_task_runtime_constructs_playwright_adapter() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())

    runtime = build_browser_task_runtime(
        service=service,
        runtime_settings=RuntimeSettings(
            browser_task_worker_enabled=True,
            browser_task_worker_adapter="playwright",
        ),
    )

    assert runtime is not None
    assert runtime.adapter.__class__.__name__ == "PlaywrightBrowserWorkerAdapter"
