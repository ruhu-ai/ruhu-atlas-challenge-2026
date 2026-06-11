from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from ..deferred import DeferredToolTransition
from ..specs import ToolSpec
from ..template_renderer import SecureTemplateRenderer, TemplateRenderError
from ..types import ToolCall, ToolIntegrationJob, ToolResult


class SupportsHttpRequest(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> Any: ...


class AuthRefreshCallback(Protocol):
    """Hook the runtime provides to refresh stale auth on a 401.

    Called with the executor's ``request_config`` (which carries
    ``connection_id`` and ``provider``). Returns either a fresh
    ``{"Authorization": "Bearer <new_token>"}``-shaped dict (the
    executor retries the request once with these merged on top of the
    existing headers) or ``None`` (executor surfaces the 401 unchanged).

    Implementations should be idempotent and bounded — the executor
    retries at most once per call, so the callback should NOT itself
    retry. Returning None is the right answer when the refresh failed
    or the connection is in a state that needs user action
    (``requires_reauth``, ``revoked``).
    """

    def __call__(self, request_config: dict[str, Any]) -> dict[str, str] | None: ...


class HttpExecutor:
    kind = "http"

    def __init__(
        self,
        client: SupportsHttpRequest | None = None,
        *,
        on_unauthorized: "AuthRefreshCallback | None" = None,
    ) -> None:
        """``on_unauthorized`` is called when an outbound HTTP call returns
        401, IF the spec carries a ``connection_id`` in its
        ``executor_config``. The callback returns either a fresh headers
        dict (executor retries the request once with those headers) or
        None (executor reports the 401 as-is — typically because the
        token couldn't be refreshed and the user must reconnect).

        The callback is the single seam between this executor and the
        OAuth runtime. The executor never calls into ``OAuthFlowManager``
        directly — it just asks the caller "I got a 401, can you make me
        new auth headers?". This keeps the executor OAuth-agnostic and
        lets non-OAuth deployments wire ``on_unauthorized=None``.
        """
        if client is None:
            import httpx

            client = httpx.Client()
        self._client = client
        self._renderer = SecureTemplateRenderer()
        self._on_unauthorized = on_unauthorized

    def set_on_unauthorized(self, callback: "AuthRefreshCallback | None") -> None:
        """Late-bind the auth-refresh hook.

        Used by the app factory: the OAuth flow manager is constructed
        well after the tool runtime, so the wiring layer attaches its
        callback once the manager exists. Calling with ``None`` detaches
        the hook (executor reverts to surfacing 401s as-is).
        """
        self._on_unauthorized = callback

    def execute(self, spec: ToolSpec, call: ToolCall) -> ToolResult:
        config = dict(spec.executor_config)
        url = str(config.get("url") or "")
        if not url:
            raise ValueError("http tool requires executor_config.url")

        method = str(config.get("method") or "POST")
        headers = dict(config.get("headers") or {})

        try:
            _status_code, response_payload = self._perform_request(
                spec,
                url=url,
                method=method,
                headers=headers,
                args=call.args,
                request_config=config,
                call=call,
            )
        except ValueError as exc:
            if str(exc).startswith("SSRF protection:"):
                return ToolResult(
                    invocation_id=call.invocation_id,
                    tool_ref=call.tool_ref,
                    status="error",
                    error=str(exc),
                    metadata={
                        "failure_kind": "validation_error",
                        "error_type": "ssrf_blocked",
                    },
                )
            raise
        status_code = _status_code

        # 401 → ask the runtime for fresh auth and retry once. The token
        # may have rotated since spec compilation (background refresher,
        # provider-side rotation, near-expiry race). We only retry when
        # the runtime supplied an ``on_unauthorized`` hook AND the spec
        # carries a ``connection_id``: without those, we have no way to
        # refresh and a retry would just reproduce the failure. One
        # attempt only — a second 401 means the token is genuinely bad
        # and the user must reconnect (refresher will mark
        # ``requires_reauth`` on its next tick).
        if (
            status_code == 401
            and self._on_unauthorized is not None
            and config.get("connection_id")
        ):
            new_auth = self._on_unauthorized(config)
            if new_auth:
                retry_headers = {**headers, **new_auth}
                _status_code, response_payload = self._perform_request(
                    spec,
                    url=url,
                    method=method,
                    headers=retry_headers,
                    args=call.args,
                    request_config=config,
                    call=call,
                )
                status_code = _status_code
        if 200 <= status_code < 300:
            result_metadata = {"http_status": status_code}
            if config.get("connection_id"):
                result_metadata["connection_id"] = config.get("connection_id")
            if config.get("provider"):
                result_metadata["provider"] = config.get("provider")
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="success",
                output=response_payload,
                metadata=result_metadata,
            )

        error = f"http request failed with status {status_code}"
        detail = self._response_error_detail(response_payload)
        if detail:
            error = f"{error}: {detail}"
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="error",
            error=error,
            metadata={
                "failure_kind": (
                    "transient_upstream_error"
                    if status_code >= 500 or status_code == 429
                    else "permanent_upstream_error"
                ),
                "error_type": "http_error",
                "http_status": status_code,
                "error_response": response_payload,
            },
        )

    def submit_deferred(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
    ) -> DeferredToolTransition:
        deferred = self._deferred_config(spec)
        submit = dict(deferred.get("submit") or {})
        if not submit:
            raise ValueError("deferred http tool requires executor_config.deferred.submit")
        status_code, payload = self._perform_stage_request(spec, call, submit, job=job)
        resolution_mode = str(spec.executor_config.get("resolution_mode") or "manual").lower()
        stage = "submit"
        transition = self._transition_from_stage_payload(
            spec=spec,
            call=call,
            job=job,
            stage=stage,
            stage_config=submit,
            payload=payload,
            http_status=status_code,
            default_resolution_mode=resolution_mode,
        )
        return transition

    def poll_deferred(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
    ) -> DeferredToolTransition:
        deferred = self._deferred_config(spec)
        poll = dict(deferred.get("poll") or {})
        if not poll:
            raise ValueError("deferred http tool requires executor_config.deferred.poll")
        status_code, payload = self._perform_stage_request(spec, call, poll, job=job)
        return self._transition_from_stage_payload(
            spec=spec,
            call=call,
            job=job,
            stage="poll",
            stage_config=poll,
            payload=payload,
            http_status=status_code,
            default_resolution_mode="polling",
        )

    def handle_deferred_callback(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
        *,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> DeferredToolTransition:
        deferred = self._deferred_config(spec)
        callback = dict(deferred.get("callback") or {})
        self._verify_callback_payload(
            spec,
            call,
            job,
            stage_config=callback,
            payload=payload,
            headers=headers,
            raw_body=raw_body,
        )
        merged_payload = dict(payload)
        if headers:
            merged_payload.setdefault("_headers", dict(headers))
        return self._transition_from_stage_payload(
            spec=spec,
            call=call,
            job=job,
            stage="callback",
            stage_config=callback,
            payload=merged_payload,
            http_status=None,
            default_resolution_mode="webhook",
        )

    @staticmethod
    def _parse_response_payload(response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
            return payload if isinstance(payload, dict) else {"data": payload}
        except Exception:
            return {"text": getattr(response, "text", "")}

    @staticmethod
    def _response_error_detail(payload: dict[str, Any]) -> str | None:
        for key in ("detail", "error", "message", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _resolve_url_and_args(url: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        remaining_args = dict(args)

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in remaining_args:
                raise ValueError(f"http tool missing path parameter: {key}")
            value = remaining_args.pop(key)
            return str(value)

        resolved_url = re.sub(r"\{([a-zA-Z0-9_]+)\}", replace, url)
        # Catch malformed placeholders the simple-identifier regex above skipped
        # (e.g. "{foo-bar}", unclosed "{foo", whitespace). Leaving them literal in
        # the URL would ship a broken request silently.
        leftover = re.search(r"\{[^{}]*\}|\{", resolved_url)
        if leftover is not None:
            raise ValueError(
                f"http tool url has unresolved placeholder: {leftover.group(0)!r}"
            )
        return resolved_url, remaining_args

    def _perform_stage_request(
        self,
        spec: ToolSpec,
        call: ToolCall,
        stage_config: dict[str, Any],
        *,
        job: ToolIntegrationJob | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = str(stage_config.get("url") or "")
        if not url:
            raise ValueError("deferred http stage requires url")
        args = self._build_stage_args(call, job=job, stage_config=stage_config)
        return self._perform_request(
            spec,
            url=url,
            method=str(stage_config.get("method") or "POST"),
            headers=dict(stage_config.get("headers") or {}),
            args=args,
            request_config=stage_config,
            call=call,
            job=job,
        )

    def _perform_request(
        self,
        spec: ToolSpec,
        *,
        url: str,
        method: str,
        headers: dict[str, Any],
        args: dict[str, Any],
        request_config: dict[str, Any] | None = None,
        call: ToolCall | None = None,
        job: ToolIntegrationJob | None = None,
    ) -> tuple[int, dict[str, Any]]:
        from ..url_validator import SSRFBlockedError, validate_url

        resolved_url, normalized_method, normalized_headers, params, body = self._build_request_parts(
            url=url,
            method=method,
            headers=headers,
            args=args,
            request_config=request_config or {},
            call=call,
            job=job,
        )
        try:
            validate_url(resolved_url)
        except SSRFBlockedError as exc:
            raise ValueError(f"SSRF protection: {exc.reason}") from exc

        timeout = spec.timeout_ms / 1000
        response = self._client.request(
            normalized_method,
            resolved_url,
            headers=normalized_headers or None,
            params=params,
            json=body,
            timeout=timeout,
        )
        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int):
            raise ValueError("http tool response missing status code")
        return status_code, self._parse_response_payload(response)

    def _build_request_parts(
        self,
        *,
        url: str,
        method: str,
        headers: dict[str, Any],
        args: dict[str, Any],
        request_config: dict[str, Any],
        call: ToolCall | None,
        job: ToolIntegrationJob | None,
    ) -> tuple[str, str, dict[str, str], dict[str, Any] | None, Any]:
        resolved_url, remaining_args = self._resolve_url_and_args(url, args)
        normalized_method = method.upper()
        normalized_headers = {str(k): str(v) for k, v in headers.items()}
        template_context = self._template_context(
            call=call,
            job=job,
            args=args,
            headers=normalized_headers,
        )

        url_template = str(request_config.get("url_template") or "").strip()
        if url_template:
            resolved_url = self._render_string(url_template, template_context, field="url_template")

        if request_config.get("headers_template") is not None:
            rendered_headers = self._renderer.render_value(
                request_config.get("headers_template"),
                template_context,
            )
            if not isinstance(rendered_headers, dict):
                raise ValueError("headers_template must render to an object")
            normalized_headers.update(
                {str(key): str(value) for key, value in rendered_headers.items()}
            )

        uses_query = normalized_method in {"GET", "DELETE"}
        if request_config.get("query_template") is not None:
            rendered_query = self._renderer.render_value(
                request_config.get("query_template"),
                template_context,
            )
            if not isinstance(rendered_query, dict):
                raise ValueError("query_template must render to an object")
            params = {str(key): value for key, value in rendered_query.items()}
        else:
            params = remaining_args if uses_query else None

        if request_config.get("body_template") is not None:
            body = self._renderer.render_value(
                request_config.get("body_template"),
                template_context,
            )
        else:
            body = None if uses_query else remaining_args

        return resolved_url, normalized_method, normalized_headers, params, body

    def _verify_callback_payload(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
        *,
        stage_config: dict[str, Any],
        payload: dict[str, Any],
        headers: dict[str, str] | None,
        raw_body: bytes | None,
    ) -> None:
        verification = dict(stage_config.get("verification") or {})
        if not verification:
            return
        mode = str(verification.get("mode") or "none").strip().lower()
        if mode in {"", "none"}:
            return
        if mode != "hmac_sha256":
            raise ValueError(f"unsupported callback verification mode: {mode}")

        secret = self._resolve_verification_secret(verification)
        if not secret:
            raise ValueError("webhook verification secret is not configured")

        header_name = str(verification.get("header") or "X-Signature").strip()
        lower_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
        provided = lower_headers.get(header_name.lower())
        if not provided:
            raise ValueError("missing webhook signature")

        signature_prefix = str(verification.get("prefix") or "").strip()
        if signature_prefix and provided.startswith(signature_prefix):
            provided = provided[len(signature_prefix):]

        signed_payload = self._signed_callback_payload(
            verification,
            payload=payload,
            headers=lower_headers,
            raw_body=raw_body,
        )
        expected = hmac.new(
            secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(provided.strip().lower(), expected.lower()):
            raise ValueError("invalid webhook signature")

    def _signed_callback_payload(
        self,
        verification: dict[str, Any],
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        raw_body: bytes | None,
    ) -> bytes:
        payload_mode = str(verification.get("signed_payload") or "body").strip().lower()
        body_bytes = raw_body
        if body_bytes is None:
            body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if payload_mode == "body":
            return body_bytes
        if payload_mode == "timestamp_body":
            timestamp_header = str(verification.get("timestamp_header") or "X-Timestamp").strip()
            timestamp = headers.get(timestamp_header.lower())
            if not timestamp:
                raise ValueError("missing webhook timestamp")
            tolerance_seconds = int(verification.get("tolerance_seconds") or 300)
            try:
                timestamp_value = int(timestamp)
            except ValueError as exc:
                raise ValueError("invalid webhook timestamp") from exc
            if abs(int(time.time()) - timestamp_value) > max(1, tolerance_seconds):
                raise ValueError("webhook timestamp outside tolerance window")
            separator = str(verification.get("separator") or "").encode("utf-8")
            return timestamp.encode("utf-8") + separator + body_bytes
        raise ValueError(f"unsupported signed_payload mode: {payload_mode}")

    @staticmethod
    def _resolve_verification_secret(verification: dict[str, Any]) -> str:
        explicit = str(verification.get("secret") or "").strip()
        if explicit:
            return explicit
        env_var = str(verification.get("secret_env_var") or "").strip()
        if env_var:
            return str(os.environ.get(env_var) or "").strip()
        return ""

    def _template_context(
        self,
        *,
        call: ToolCall | None,
        job: ToolIntegrationJob | None,
        args: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "args": dict(args),
            "headers": dict(headers),
            "call": None
            if call is None
            else {
                "invocation_id": call.invocation_id,
                "tool_ref": call.tool_ref,
                "caller": call.caller.model_dump(mode="json"),
                "metadata": dict(call.metadata),
            },
            "job": None if job is None else job.model_dump(mode="json"),
        }

    def _render_string(self, template: str, context: dict[str, Any], *, field: str) -> str:
        try:
            return self._renderer.render(template, context)
        except TemplateRenderError as exc:
            raise ValueError(f"{field} render failed: {exc}") from exc

    def _transition_from_stage_payload(
        self,
        *,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
        stage: str,
        stage_config: dict[str, Any],
        payload: dict[str, Any],
        http_status: int | None,
        default_resolution_mode: str,
    ) -> DeferredToolTransition:
        status_path = str(stage_config.get("status_path") or "status")
        raw_status = self._lookup_path(payload, status_path)
        normalized_status = str(raw_status or "").strip().lower()
        pending_values = {str(value).strip().lower() for value in stage_config.get("pending_values", ["queued", "running", "processing", "pending"])}
        success_values = {str(value).strip().lower() for value in stage_config.get("success_values", ["completed", "complete", "success", "succeeded"])}
        failure_values = {str(value).strip().lower() for value in stage_config.get("failure_values", ["failed", "error", "cancelled", "canceled"])}
        resolution_mode = str(spec.executor_config.get("resolution_mode") or default_resolution_mode).lower()
        external_job_id = self._coerce_optional_string(
            self._lookup_path(payload, str(stage_config.get("external_job_id_path") or "job_id"))
        ) or job.external_job_id
        callback_correlation_id = self._coerce_optional_string(
            self._lookup_path(payload, str(stage_config.get("callback_correlation_id_path") or "callback_correlation_id"))
        ) or job.callback_correlation_id

        if stage == "submit" and 200 <= (http_status or 200) < 300 and not normalized_status:
            if resolution_mode == "webhook":
                return DeferredToolTransition(
                    action="wait_webhook",
                    external_job_id=external_job_id,
                    callback_correlation_id=callback_correlation_id or job.callback_correlation_id or job.invocation_id,
                    metadata={"http_status": http_status, "stage": stage},
                )
            if resolution_mode == "polling":
                return DeferredToolTransition(
                    action="wait_poll",
                    external_job_id=external_job_id,
                    next_poll_at=self._next_poll_at(stage_config),
                    metadata={"http_status": http_status, "stage": stage},
                )

        if normalized_status in success_values:
            return DeferredToolTransition(
                action="complete",
                result=self._complete_result(call, spec, payload, stage_config, http_status=http_status),
            )
        if normalized_status in failure_values:
            return DeferredToolTransition(
                action="fail",
                error=self._failure_message(payload, http_status=http_status),
                metadata={"http_status": http_status, "stage": stage},
            )
        if stage == "callback" and not normalized_status and payload:
            return DeferredToolTransition(
                action="complete",
                result=self._complete_result(call, spec, payload, stage_config, http_status=http_status),
            )
        if resolution_mode == "webhook" and (normalized_status in pending_values or stage == "submit"):
            return DeferredToolTransition(
                action="wait_webhook",
                external_job_id=external_job_id,
                callback_correlation_id=callback_correlation_id or job.callback_correlation_id or job.invocation_id,
                metadata={"http_status": http_status, "stage": stage},
            )
        return DeferredToolTransition(
            action="wait_poll",
            external_job_id=external_job_id,
            next_poll_at=self._next_poll_at(stage_config),
            metadata={"http_status": http_status, "stage": stage},
        )

    def _complete_result(
        self,
        call: ToolCall,
        spec: ToolSpec,
        payload: dict[str, Any],
        stage_config: dict[str, Any],
        *,
        http_status: int | None,
    ) -> ToolResult:
        output_path = str(stage_config.get("result_path") or "")
        output = payload if not output_path else self._lookup_path(payload, output_path)
        if isinstance(output, dict):
            result_output = dict(output)
        else:
            result_output = {"data": output}
        metadata: dict[str, Any] = {}
        if http_status is not None:
            metadata["http_status"] = http_status
        provider = spec.executor_config.get("provider")
        if provider:
            metadata["provider"] = provider
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="success",
            output=result_output,
            metadata=metadata,
        )

    @staticmethod
    def _deferred_config(spec: ToolSpec) -> dict[str, Any]:
        return dict(spec.executor_config.get("deferred") or {})

    @staticmethod
    def _lookup_path(payload: dict[str, Any], path: str) -> Any:
        if not path:
            return payload
        current: Any = payload
        for segment in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(segment)
        return current

    @staticmethod
    def _coerce_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _next_poll_at(stage_config: dict[str, Any]) -> datetime:
        interval_seconds = max(1.0, float(stage_config.get("poll_interval_seconds") or 30.0))
        return datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)

    @staticmethod
    def _failure_message(payload: dict[str, Any], *, http_status: int | None) -> str:
        detail = HttpExecutor._response_error_detail(payload)
        if http_status is None:
            return detail or "deferred http request failed"
        return f"http request failed with status {http_status}" + (f": {detail}" if detail else "")

    @staticmethod
    def _build_stage_args(
        call: ToolCall,
        *,
        job: ToolIntegrationJob | None,
        stage_config: dict[str, Any],
    ) -> dict[str, Any]:
        args = dict(call.args)
        if job is not None:
            args.setdefault("job_id", job.job_id)
            args.setdefault("external_job_id", job.external_job_id)
            args.setdefault("callback_correlation_id", job.callback_correlation_id)
        defaults = dict(stage_config.get("args") or {})
        defaults.update(args)
        return defaults
