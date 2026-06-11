from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import mimetypes
from pathlib import Path
import tempfile
from typing import Any, Protocol

from .models import BrowserOperatorCommand
from .task_packs import is_url_allowed
from .credentials import BrowserCredentialResolver, BrowserResolvedCredential
from .worker_contracts import (
    BrowserWorkerError,
    BrowserGeneratedArtifact,
    BrowserResolvedUpload,
    BrowserWorkerProgress,
    BrowserWorkerRequest,
    BrowserWorkerResult,
)


class BrowserUploadResolver(Protocol):
    def resolve(
        self,
        *,
        request: BrowserWorkerRequest,
        attachment_id: str,
    ) -> BrowserResolvedUpload: ...


@dataclass(slots=True)
class PlaywrightBrowserWorkerAdapter:
    headless: bool = True
    sync_playwright_factory: Callable[[], Any] | None = None
    credential_resolver: BrowserCredentialResolver | None = None
    upload_resolver: BrowserUploadResolver | None = None

    def execute(
        self,
        request: BrowserWorkerRequest,
        report_progress: Callable[[BrowserWorkerProgress], None],
        poll_operator_commands: Callable[[], list[BrowserOperatorCommand]] | None = None,
        mark_operator_command_delivered: Callable[[str], None] | None = None,
        mark_operator_command_failed: Callable[[str, str], None] | None = None,
        publish_session_snapshot: Callable[[BrowserGeneratedArtifact], None] | None = None,
    ) -> BrowserWorkerResult:
        resolved_credentials = self._resolve_credentials(request)
        if isinstance(resolved_credentials, BrowserWorkerResult):
            return resolved_credentials
        storage_state = self._storage_state_for_request(request, resolved_credentials)
        if isinstance(storage_state, BrowserWorkerResult):
            return storage_state
        factory = self.sync_playwright_factory or _load_sync_playwright()
        if factory is None:
            return BrowserWorkerResult(
                task_id=request.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="worker_unavailable",
                    message="playwright is not installed; install the browser-e2e extra to enable this adapter",
                    retryable=False,
                ),
            )

        report_progress(
            BrowserWorkerProgress(
                task_id=request.task_id,
                event_sequence=1,
                phase="starting",
                message="Starting isolated browser context.",
            )
        )
        next_sequence = 2
        if request.credentials:
            report_progress(
                BrowserWorkerProgress(
                    task_id=request.task_id,
                    event_sequence=next_sequence,
                    phase="authenticating",
                    message="Applying scoped browser session credentials.",
                    metadata={"credential_names": [credential.name for credential in request.credentials]},
                )
            )
            next_sequence += 1
        try:
            with factory() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                context_kwargs: dict[str, Any] = {"accept_downloads": request.policy.allow_downloads}
                if storage_state is not None:
                    context_kwargs["storage_state"] = storage_state
                context = browser.new_context(**context_kwargs)
                _install_domain_route(context, request)
                page = context.new_page()
                try:
                    report_progress(
                        BrowserWorkerProgress(
                            task_id=request.task_id,
                            event_sequence=next_sequence,
                            phase="navigating",
                            message="Opening task start URL.",
                            metadata={"url": request.start_url},
                        )
                    )
                    response = page.goto(
                        request.start_url,
                        wait_until="domcontentloaded",
                        timeout=request.policy.max_execution_seconds * 1000,
                    )
                    final_url = str(getattr(page, "url", "") or request.start_url)
                    if not is_url_allowed(final_url, request.policy.allowed_domains):
                        return BrowserWorkerResult(
                            task_id=request.task_id,
                            success=False,
                            error=BrowserWorkerError(
                                kind="policy_violation",
                                message="browser navigation left the allowed task-pack domains",
                                retryable=False,
                                metadata={"final_url": final_url},
                            ),
                        )
                    status = None if response is None else getattr(response, "status", None)
                    title = page.title()
                    operator_result = self._drain_operator_commands(
                        request=request,
                        page=page,
                        poll_operator_commands=poll_operator_commands,
                        mark_operator_command_delivered=mark_operator_command_delivered,
                        mark_operator_command_failed=mark_operator_command_failed,
                        report_progress=report_progress,
                        next_sequence=next_sequence + 1,
                    )
                    if isinstance(operator_result, BrowserWorkerResult):
                        return operator_result
                    snapshot_error = self._publish_live_snapshot(
                        request=request,
                        page=page,
                        final_url=final_url,
                        publish_session_snapshot=publish_session_snapshot,
                        snapshot_label="Live browser snapshot",
                    )
                    if snapshot_error is not None:
                        return snapshot_error
                    plan_output = self._execute_browser_plan(
                        request=request,
                        page=page,
                        report_progress=report_progress,
                        next_sequence=next_sequence + 1 + operator_result,
                        poll_operator_commands=poll_operator_commands,
                        mark_operator_command_delivered=mark_operator_command_delivered,
                        mark_operator_command_failed=mark_operator_command_failed,
                        publish_session_snapshot=publish_session_snapshot,
                    )
                    if isinstance(plan_output, BrowserWorkerResult):
                        return plan_output
                    completed_sequence = next_sequence + 1 + plan_output["steps_executed"]
                    report_progress(
                        BrowserWorkerProgress(
                            task_id=request.task_id,
                            event_sequence=completed_sequence,
                            phase="completed",
                            message=(
                                "Browser task completed."
                                if request.browser_plan is not None
                                else "Browser task inspection completed."
                            ),
                            metadata={"final_url": final_url},
                        )
                    )
                    if request.browser_plan is not None:
                        output = dict(plan_output["output"])
                    else:
                        output = {
                            "final_url": final_url,
                            "title": title,
                            **({"http_status": status} if status is not None else {}),
                        }
                    generated_artifacts = [
                        *plan_output.get("generated_artifacts", []),
                        *self._capture_screenshot_artifacts(
                            request=request,
                            page=page,
                            final_url=final_url,
                        ),
                    ]
                    return BrowserWorkerResult(
                        task_id=request.task_id,
                        success=True,
                        summary=(
                            "Browser task completed."
                            if request.browser_plan is not None
                            else "Browser task inspection completed."
                        ),
                        output=output,
                        generated_artifacts=generated_artifacts,
                    )
                finally:
                    context.close()
                    browser.close()
        except Exception as exc:
            return BrowserWorkerResult(
                task_id=request.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="navigation",
                    message=str(exc) or "browser navigation failed",
                    retryable=True,
                ),
            )

    def _resolve_credentials(
        self,
        request: BrowserWorkerRequest,
    ) -> list[BrowserResolvedCredential] | BrowserWorkerResult:
        if not request.credentials:
            return []
        if self.credential_resolver is None:
            return BrowserWorkerResult(
                task_id=request.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="policy_violation",
                    message="playwright browser adapter cannot resolve credential refs yet",
                    retryable=False,
                ),
            )
        resolved: list[BrowserResolvedCredential] = []
        for credential in request.credentials:
            try:
                resolved.append(self.credential_resolver.resolve(request=request, credential=credential))
            except Exception as exc:
                return BrowserWorkerResult(
                    task_id=request.task_id,
                    success=False,
                    error=BrowserWorkerError(
                        kind="authentication",
                        message=str(exc) or "browser credential resolution failed",
                        retryable=False,
                        metadata={"credential_name": credential.name},
                    ),
                )
        return resolved

    def _storage_state_for_request(
        self,
        request: BrowserWorkerRequest,
        credentials: list[BrowserResolvedCredential],
    ) -> dict[str, Any] | None | BrowserWorkerResult:
        wrong_kind = [credential.name for credential in credentials if credential.kind != "session"]
        if wrong_kind:
            return BrowserWorkerResult(
                task_id=request.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="policy_violation",
                    message="playwright browser adapter only accepts browser session credentials",
                    retryable=False,
                    metadata={"credential_names": wrong_kind},
                ),
            )
        storage_states = [credential.storage_state for credential in credentials if credential.storage_state]
        unsupported = [
            credential.name
            for credential in credentials
            if credential.storage_state is None
        ]
        if unsupported:
            return BrowserWorkerResult(
                task_id=request.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="policy_violation",
                    message="playwright browser adapter only accepts resolved browser session storage state",
                    retryable=False,
                    metadata={"credential_names": unsupported},
                ),
            )
        if len(storage_states) > 1:
            return BrowserWorkerResult(
                task_id=request.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="policy_violation",
                    message="playwright browser adapter accepts one browser session credential per task",
                    retryable=False,
                ),
            )
        return storage_states[0] if storage_states else None

    def _execute_browser_plan(
        self,
        *,
        request: BrowserWorkerRequest,
        page: Any,
        report_progress: Callable[[BrowserWorkerProgress], None],
        next_sequence: int,
        poll_operator_commands: Callable[[], list[BrowserOperatorCommand]] | None = None,
        mark_operator_command_delivered: Callable[[str], None] | None = None,
        mark_operator_command_failed: Callable[[str, str], None] | None = None,
        publish_session_snapshot: Callable[[BrowserGeneratedArtifact], None] | None = None,
    ) -> dict[str, Any] | BrowserWorkerResult:
        plan = request.browser_plan
        if plan is None:
            return {"output": {}, "steps_executed": 0, "generated_artifacts": []}
        total_steps = len(plan.actions) + len(plan.extractions)
        if total_steps > request.policy.max_steps:
            return BrowserWorkerResult(
                task_id=request.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="policy_violation",
                    message="browser plan exceeds max_steps policy",
                    retryable=False,
                    metadata={"max_steps": request.policy.max_steps, "plan_steps": total_steps},
                ),
            )
        steps_executed = 0
        report_progress(
            BrowserWorkerProgress(
                task_id=request.task_id,
                event_sequence=next_sequence,
                phase="acting",
                message="Executing bounded browser task plan.",
                metadata={"actions": len(plan.actions), "extractions": len(plan.extractions)},
            )
        )
        next_sequence += 1
        generated_artifacts: list[BrowserGeneratedArtifact] = []
        for action in plan.actions:
            steps_executed += 1
            try:
                if action.kind == "fill":
                    value = action.value
                    if action.value_from_input is not None:
                        raw_value = request.input.get(action.value_from_input)
                        if raw_value is None:
                            return BrowserWorkerResult(
                                task_id=request.task_id,
                                success=False,
                                error=BrowserWorkerError(
                                    kind="validation",
                                    message="browser plan input value is missing",
                                    retryable=False,
                                    metadata={"input_key": action.value_from_input},
                                ),
                            )
                        value = str(raw_value)
                    page.fill(action.selector, value or "", timeout=action.timeout_ms)
                elif action.kind == "click":
                    page.click(action.selector, timeout=action.timeout_ms)
                elif action.kind == "download":
                    if not request.policy.allow_downloads:
                        return BrowserWorkerResult(
                            task_id=request.task_id,
                            success=False,
                            error=BrowserWorkerError(
                                kind="policy_violation",
                                message="browser plan attempted a download but downloads are not allowed",
                                retryable=False,
                                metadata={"selector": action.selector},
                            ),
                        )
                    generated_artifacts.append(
                        self._execute_download_action(
                            request=request,
                            page=page,
                            selector=action.selector,
                            timeout_ms=action.timeout_ms,
                        )
                    )
                elif action.kind == "upload":
                    if not request.policy.allow_uploads:
                        return BrowserWorkerResult(
                            task_id=request.task_id,
                            success=False,
                            error=BrowserWorkerError(
                                kind="policy_violation",
                                message="browser plan attempted an upload but uploads are not allowed",
                                retryable=False,
                                metadata={"selector": action.selector},
                            ),
                        )
                    if self.upload_resolver is None:
                        return BrowserWorkerResult(
                            task_id=request.task_id,
                            success=False,
                            error=BrowserWorkerError(
                                kind="worker_unavailable",
                                message="browser upload resolver is not configured",
                                retryable=False,
                                metadata={"selector": action.selector},
                            ),
                        )
                    attachment_id = str(request.input.get(action.value_from_input or "") or "").strip()
                    if not attachment_id:
                        return BrowserWorkerResult(
                            task_id=request.task_id,
                            success=False,
                            error=BrowserWorkerError(
                                kind="validation",
                                message="browser plan upload attachment id is missing",
                                retryable=False,
                                metadata={"input_key": action.value_from_input},
                            ),
                        )
                    self._execute_upload_action(
                        request=request,
                        page=page,
                        selector=action.selector,
                        attachment_id=attachment_id,
                        timeout_ms=action.timeout_ms,
                    )
                elif action.kind == "wait_for_selector":
                    page.wait_for_selector(action.selector, timeout=action.timeout_ms)
                else:
                    return BrowserWorkerResult(
                        task_id=request.task_id,
                        success=False,
                        error=BrowserWorkerError(
                            kind="policy_violation",
                            message="unsupported browser plan action",
                            retryable=False,
                            metadata={"action": action.kind},
                        ),
                    )
            except Exception as exc:
                return BrowserWorkerResult(
                    task_id=request.task_id,
                    success=False,
                    error=BrowserWorkerError(
                        kind="navigation",
                        message=str(exc) or "browser plan action failed",
                        retryable=True,
                        metadata={"action": action.kind, "selector": action.selector},
                    ),
                )
            operator_result = self._drain_operator_commands(
                request=request,
                page=page,
                poll_operator_commands=poll_operator_commands,
                mark_operator_command_delivered=mark_operator_command_delivered,
                mark_operator_command_failed=mark_operator_command_failed,
                report_progress=report_progress,
                next_sequence=next_sequence,
            )
            if isinstance(operator_result, BrowserWorkerResult):
                return operator_result
            if operator_result > 0:
                snapshot_error = self._publish_live_snapshot(
                    request=request,
                    page=page,
                    final_url=str(getattr(page, "url", "") or request.start_url),
                    publish_session_snapshot=publish_session_snapshot,
                    snapshot_label="Live browser snapshot after operator action",
                )
                if snapshot_error is not None:
                    return snapshot_error
            next_sequence += operator_result

        output: dict[str, Any] = {}
        if plan.extractions:
            report_progress(
                BrowserWorkerProgress(
                    task_id=request.task_id,
                    event_sequence=next_sequence,
                    phase="acting",
                    message="Extracting browser task result fields.",
                )
            )
        for extraction in plan.extractions:
            steps_executed += 1
            try:
                locator = page.locator(extraction.selector).first
                value: str | None
                if extraction.attribute == "text":
                    value = locator.inner_text(timeout=extraction.timeout_ms)
                elif extraction.attribute == "value":
                    value = locator.input_value(timeout=extraction.timeout_ms)
                elif extraction.attribute == "href":
                    value = locator.get_attribute("href", timeout=extraction.timeout_ms)
                elif extraction.attribute == "aria_label":
                    value = locator.get_attribute("aria-label", timeout=extraction.timeout_ms)
                elif extraction.attribute == "data":
                    value = locator.get_attribute(f"data-{extraction.data_attribute}", timeout=extraction.timeout_ms)
                else:
                    value = None
            except Exception as exc:
                if extraction.required:
                    return BrowserWorkerResult(
                        task_id=request.task_id,
                        success=False,
                        error=BrowserWorkerError(
                            kind="navigation",
                            message=str(exc) or "browser plan extraction failed",
                            retryable=True,
                            metadata={"field": extraction.field, "selector": extraction.selector},
                        ),
                    )
                continue
            normalized = value.strip() if isinstance(value, str) else value
            if normalized or extraction.required:
                output[extraction.field] = normalized
            operator_result = self._drain_operator_commands(
                request=request,
                page=page,
                poll_operator_commands=poll_operator_commands,
                mark_operator_command_delivered=mark_operator_command_delivered,
                mark_operator_command_failed=mark_operator_command_failed,
                report_progress=report_progress,
                next_sequence=next_sequence,
            )
            if isinstance(operator_result, BrowserWorkerResult):
                return operator_result
            if operator_result > 0:
                snapshot_error = self._publish_live_snapshot(
                    request=request,
                    page=page,
                    final_url=str(getattr(page, "url", "") or request.start_url),
                    publish_session_snapshot=publish_session_snapshot,
                    snapshot_label="Live browser snapshot after operator action",
                )
                if snapshot_error is not None:
                    return snapshot_error
            next_sequence += operator_result
        return {"output": output, "steps_executed": steps_executed, "generated_artifacts": generated_artifacts}

    def _execute_download_action(
        self,
        *,
        request: BrowserWorkerRequest,
        page: Any,
        selector: str,
        timeout_ms: int,
    ) -> BrowserGeneratedArtifact:
        with page.expect_download(timeout=timeout_ms) as download_info:
            page.click(selector, timeout=timeout_ms)
        download = download_info.value
        filename = str(getattr(download, "suggested_filename", "") or f"{request.task_id}-download.bin")
        path = download.path()
        content = Path(path).read_bytes()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return BrowserGeneratedArtifact(
            kind="download",
            filename=filename,
            content_type=content_type,
            content_bytes=content,
            label=f"Downloaded {filename}",
            metadata={
                "selector": selector,
                "final_url": str(getattr(page, "url", "") or request.start_url),
            },
        )

    def _execute_upload_action(
        self,
        *,
        request: BrowserWorkerRequest,
        page: Any,
        selector: str,
        attachment_id: str,
        timeout_ms: int,
    ) -> None:
        if self.upload_resolver is None:
            raise ValueError("browser upload resolver is not configured")
        upload = self.upload_resolver.resolve(request=request, attachment_id=attachment_id)
        suffix = Path(upload.filename).suffix
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            handle.write(upload.content_bytes)
            path = handle.name
        finally:
            handle.close()
        try:
            page.set_input_files(selector, path, timeout=timeout_ms)
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    def _publish_live_snapshot(
        self,
        *,
        request: BrowserWorkerRequest,
        page: Any,
        final_url: str,
        publish_session_snapshot: Callable[[BrowserGeneratedArtifact], None] | None,
        snapshot_label: str,
    ) -> BrowserWorkerResult | None:
        if publish_session_snapshot is None or not request.policy.capture_screenshots:
            return None
        artifacts = self._capture_screenshot_artifacts(
            request=request,
            page=page,
            final_url=final_url,
            label=snapshot_label,
            full_page=False,
            metadata={"live_session_snapshot": True},
        )
        if not artifacts:
            return None
        try:
            publish_session_snapshot(artifacts[0])
        except Exception as exc:
            return BrowserWorkerResult(
                task_id=request.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="worker_unavailable",
                    message=str(exc) or "browser live session snapshot publish failed",
                    retryable=True,
                ),
            )
        return None

    def _drain_operator_commands(
        self,
        *,
        request: BrowserWorkerRequest,
        page: Any,
        poll_operator_commands: Callable[[], list[BrowserOperatorCommand]] | None,
        mark_operator_command_delivered: Callable[[str], None] | None,
        mark_operator_command_failed: Callable[[str, str], None] | None,
        report_progress: Callable[[BrowserWorkerProgress], None],
        next_sequence: int,
    ) -> int | BrowserWorkerResult:
        if poll_operator_commands is None or mark_operator_command_delivered is None:
            return 0
        try:
            commands = poll_operator_commands()
        except Exception as exc:
            return BrowserWorkerResult(
                task_id=request.task_id,
                success=False,
                error=BrowserWorkerError(
                    kind="worker_unavailable",
                    message=str(exc) or "browser operator command polling failed",
                    retryable=True,
                ),
            )
        delivered = 0
        for command in commands:
            try:
                self._execute_operator_command(page, command)
                current_url = str(getattr(page, "url", "") or request.start_url)
                if not is_url_allowed(current_url, request.policy.allowed_domains):
                    raise ValueError("operator command navigated outside the allowed task-pack domains")
                mark_operator_command_delivered(command.command_id)
                delivered += 1
                report_progress(
                    BrowserWorkerProgress(
                        task_id=request.task_id,
                        event_sequence=next_sequence + delivered - 1,
                        phase="acting",
                        message=f"Executed operator command: {command.command_type}.",
                        metadata={
                            "command_id": command.command_id,
                            "command_type": command.command_type,
                        },
                    )
                )
            except Exception as exc:
                error_message = str(exc) or "browser operator command failed"
                if mark_operator_command_failed is not None:
                    try:
                        mark_operator_command_failed(command.command_id, error_message)
                    except Exception as mark_exc:
                        return BrowserWorkerResult(
                            task_id=request.task_id,
                            success=False,
                            error=BrowserWorkerError(
                                kind="worker_unavailable",
                                message=str(mark_exc) or "browser operator command failure recording failed",
                                retryable=True,
                                metadata={
                                    "command_id": command.command_id,
                                    "command_type": command.command_type,
                                },
                            ),
                        )
                return BrowserWorkerResult(
                    task_id=request.task_id,
                    success=False,
                    error=BrowserWorkerError(
                        kind="navigation",
                        message=error_message,
                        retryable=True,
                        metadata={
                            "command_id": command.command_id,
                            "command_type": command.command_type,
                        },
                    ),
                )
        return delivered

    def _execute_operator_command(self, page: Any, command: BrowserOperatorCommand) -> None:
        payload = command.payload
        if command.command_type == "click":
            selector = payload.get("selector")
            if isinstance(selector, str) and selector:
                page.click(selector, timeout=5000)
                return
            page.mouse.click(float(payload["x"]), float(payload["y"]))
            return
        if command.command_type == "type_text":
            selector = payload.get("selector")
            text = str(payload["text"])
            if isinstance(selector, str) and selector:
                page.fill(selector, text, timeout=5000)
                return
            page.keyboard.type(text)
            return
        if command.command_type == "press_key":
            page.keyboard.press(str(payload["key"]))
            return
        if command.command_type == "scroll":
            direction = str(payload["direction"])
            pages = float(payload.get("pages", 1))
            delta = 700 * pages
            if direction == "up":
                page.mouse.wheel(0, -delta)
            elif direction == "down":
                page.mouse.wheel(0, delta)
            elif direction == "left":
                page.mouse.wheel(-delta, 0)
            elif direction == "right":
                page.mouse.wheel(delta, 0)
            return
        if command.command_type == "navigate_back":
            page.go_back(wait_until="domcontentloaded", timeout=5000)
            return
        if command.command_type == "navigate_forward":
            page.go_forward(wait_until="domcontentloaded", timeout=5000)
            return
        if command.command_type == "wait":
            selector = payload.get("selector")
            if isinstance(selector, str) and selector:
                page.wait_for_selector(selector, timeout=5000)
                return
            page.wait_for_timeout(int(payload["duration_ms"]))
            return
        raise ValueError(f"unsupported operator command type: {command.command_type}")

    def _capture_screenshot_artifacts(
        self,
        *,
        request: BrowserWorkerRequest,
        page: Any,
        final_url: str,
        label: str = "Final browser screenshot",
        full_page: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> list[BrowserGeneratedArtifact]:
        if not request.policy.capture_screenshots:
            return []
        try:
            masks = [
                page.locator(selector)
                for selector in request.policy.screenshot_redaction_selectors
            ]
            screenshot_kwargs: dict[str, Any] = {
                "full_page": full_page,
                "type": "png",
            }
            if masks:
                screenshot_kwargs["mask"] = masks
            content = page.screenshot(**screenshot_kwargs)
        except Exception:
            return []
        return [
            BrowserGeneratedArtifact(
                kind="screenshot",
                filename=f"{request.task_id}-final.png",
                content_type="image/png",
                content_bytes=content,
                label=label,
                metadata={
                    "redacted": bool(request.policy.screenshot_redaction_selectors),
                    "redaction_selector_count": len(request.policy.screenshot_redaction_selectors),
                    "final_url": final_url,
                    "screenshot_full_page": full_page,
                    "screenshot_scope": "full_page" if full_page else "viewport",
                    **dict(metadata or {}),
                },
            )
        ]


def _load_sync_playwright() -> Callable[[], Any] | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


def _install_domain_route(context: Any, request: BrowserWorkerRequest) -> None:
    route_method = getattr(context, "route", None)
    if not callable(route_method):
        return

    def guard(route: Any) -> None:
        route_request = getattr(route, "request", None)
        url = str(getattr(route_request, "url", "") or "")
        if url and is_url_allowed(url, request.policy.allowed_domains):
            route.continue_()
            return
        route.abort()

    route_method("**/*", guard)
