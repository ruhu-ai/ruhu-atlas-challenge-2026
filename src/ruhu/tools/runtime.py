from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone
from typing import Any

from ruhu.validation.schema import ValidationError as SchemaValidationError

from .authorizer import DefaultToolAuthorizer, ToolAuthorizer
from .catalog import NullToolCatalogResolver, ToolCatalogResolver
from .circuit_breaker import CircuitBreakerRegistry
from .executors.base import ToolExecutor
from .integration_runtime import ToolIntegrationRuntime
from .pii import TieredPiiScanner
from .pii_redactor import PiiRedactor
from .registry import ToolRegistry
from .specs import ToolSpec
from .store import InMemoryToolInvocationStore, ToolInvocationStore
from .tool_rate_limiter import ToolRateLimiter
from .types import ToolCall, ToolCaller, ToolFailureKind, ToolInvocation, ToolResult

_CANCELLABLE_INVOCATION_STATUSES = frozenset(
    {"waiting_confirmation", "queued", "waiting_poll", "waiting_webhook", "retry_scheduled"}
)
_DEFAULT_CONFIRMATION_TTL_SECONDS = 30 * 60


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _failure_metadata(
    kind: ToolFailureKind,
    *,
    error_type: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"failure_kind": kind}
    metadata["error_type"] = error_type or kind
    metadata.update(extra)
    return metadata


class ToolRuntime:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        authorizer: ToolAuthorizer | None = None,
        store: ToolInvocationStore | None = None,
        executors: dict[str, ToolExecutor] | None = None,
        catalog_resolver: ToolCatalogResolver | None = None,
        tool_rate_limiter: ToolRateLimiter | None = None,
        pii_redactor: PiiRedactor | None = None,
        tiered_pii_scanner: TieredPiiScanner | None = None,
        integration_runtime: ToolIntegrationRuntime | None = None,
        confirmation_ttl_seconds: int = _DEFAULT_CONFIRMATION_TTL_SECONDS,
    ) -> None:
        self._registry = registry
        self._authorizer = authorizer or DefaultToolAuthorizer()
        self._store = store or InMemoryToolInvocationStore()
        self._executors: dict[str, ToolExecutor] = dict(executors or {})
        self._circuit_registry = CircuitBreakerRegistry()
        self._catalog_resolver: ToolCatalogResolver = catalog_resolver or NullToolCatalogResolver()
        self._tool_rate_limiter = tool_rate_limiter
        self._pii_redactor = pii_redactor or PiiRedactor()
        self._tiered_pii_scanner = tiered_pii_scanner
        self._integration_runtime = integration_runtime
        self._confirmation_ttl_seconds = max(1, int(confirmation_ttl_seconds))

    @property
    def store(self) -> ToolInvocationStore:
        return self._store

    @property
    def integration_runtime(self) -> ToolIntegrationRuntime | None:
        return self._integration_runtime

    def list_specs(self) -> list[ToolSpec]:
        return self._registry.list()

    def register_spec(self, spec: ToolSpec) -> None:
        self._registry.register(spec)

    def get_spec(
        self,
        ref: str,
        *,
        organization_id: str | None = None,
        caller: ToolCaller | None = None,
    ) -> ToolSpec:
        return self._resolve_spec(ref, organization_id=organization_id, caller=caller)

    def lookup_tool_spec(
        self,
        tool_ref: str,
        *,
        organization_id: str | None = None,
    ) -> ToolSpec | None:
        """Return the ``ToolSpec`` for ``tool_ref`` or ``None`` if not found.

        Companion to :meth:`get_spec` that returns ``None`` instead of
        raising ``KeyError``.  Used by P5 of doc 43 (WI-3) so the kernel's
        move-selection validator can probe tool existence without a
        try/except for the common "LLM hallucinated a tool name" path.

        ``caller=None`` is intentional — this is a metadata lookup, not a
        decrypt path, so no audit event is emitted.
        """
        try:
            return self._resolve_spec(
                tool_ref, organization_id=organization_id, caller=None,
            )
        except KeyError:
            return None

    def _confirmation_expiry(self, *, now: datetime | None = None) -> datetime:
        return (now or _utcnow()) + timedelta(seconds=self._confirmation_ttl_seconds)

    def _expire_invocation_if_needed(self, invocation: ToolInvocation) -> ToolInvocation:
        if invocation.status != "waiting_confirmation":
            return invocation
        if invocation.expires_at is None or invocation.expires_at > _utcnow():
            return invocation
        invocation.status = "timed_out"
        invocation.error = "tool confirmation expired"
        invocation.updated_at = _utcnow()
        self._store.save(invocation)
        return invocation

    def load_invocation(self, invocation_id: str, *, organization_id: str | None = None) -> ToolInvocation | None:
        invocation = self._store.load(invocation_id, organization_id=organization_id)
        if invocation is None:
            return None
        return self._expire_invocation_if_needed(invocation)

    def list_conversation_invocations(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[ToolInvocation]:
        items = self._store.by_conversation(conversation_id, organization_id=organization_id)
        return [self._expire_invocation_if_needed(item) for item in items]

    def register_executor(self, executor: ToolExecutor) -> None:
        self._executors[executor.kind] = executor

    def get_executor(self, kind: str) -> ToolExecutor | None:
        return self._executors.get(kind)

    def load_result(self, invocation_id: str, *, organization_id: str | None = None) -> ToolResult | None:
        invocation = self.load_invocation(invocation_id, organization_id=organization_id)
        if invocation is None:
            return None
        return self._tool_result_from_invocation(invocation)

    def _resolve_spec(
        self,
        tool_ref: str,
        *,
        organization_id: str | None,
        caller: ToolCaller | None = None,
    ) -> ToolSpec:
        """Resolve a spec: built-in registry first, then the custom catalog.

        Builtins always win — a customer-defined tool with the same ref as a
        builtin is silently ignored, preventing platform spec shadowing.

        When ``caller`` is provided the catalog resolver decrypts
        per-connection credentials under the caller's identity and emits one
        ``credential.decrypted`` audit event per decrypt.  ``list_for_agent``
        passes ``caller=None`` because catalog-list is a UI/preview path —
        no decrypt, no audit.
        """
        spec = self._registry.get(tool_ref)
        if spec is not None:
            return spec
        custom = self._catalog_resolver.resolve(
            tool_ref, organization_id=organization_id, caller=caller
        )
        if custom is not None:
            return custom
        raise KeyError(tool_ref)

    def list_for_agent(
        self,
        *,
        agent_id: str | None = None,
        organization_id: str | None = None,
    ) -> list[ToolSpec]:
        """Return all specs visible to an agent: builtins merged with custom tools.

        Deduplication is builtins-first: if a custom tool ref collides with a
        builtin, the builtin spec wins. ``agent_id`` scopes the listing when
        per-agent assignment filtering is configured.

        This is the preview / catalog-listing path — no ``caller`` is
        propagated, so compilers skip credential decryption.  The returned
        specs are safe to show in a UI but are NOT safe to execute (their
        ``Authorization`` headers are empty); execution must go through
        :py:meth:`invoke`, which re-resolves with the real caller.
        """
        builtin_map = {spec.ref: spec for spec in self._registry.list()}
        custom = self._catalog_resolver.list_for_organization(
            organization_id=organization_id, caller=None
        )
        merged: dict[str, ToolSpec] = dict(builtin_map)
        for spec in custom:
            if spec.ref not in merged:
                merged[spec.ref] = spec
        return list(merged.values())

    def invoke(self, call: ToolCall) -> ToolResult:
        # Axis 2 of the publish-gate gradient (per
        # docs/templates/Template-Required-Tools-Onboarding-Spec.md):
        # convert a missing tool spec into a structured error result
        # instead of letting KeyError propagate up the call stack and
        # crash the conversation. Missing tool specs become structured
        # tool errors that authored outcome branches can handle.
        #
        # Catching here (not at the kernel call site) is the right
        # boundary because every kernel surface that invokes a tool
        # — deterministic action-state binding, P5 LLM-proposed
        # propose_tool_use commit — funnels through this method, so
        # one fix keeps both paths consistent.
        try:
            spec = self._resolve_spec(
                call.tool_ref,
                organization_id=call.caller.tenant_id,
                caller=call.caller,
            )
        except KeyError:
            logger.warning(
                "tool_unavailable",
                extra={
                    "tool_ref": call.tool_ref,
                    "conversation_id": call.caller.conversation_id,
                    "organization_id": call.caller.tenant_id,
                    "agent_id": call.caller.agent_id,
                    "step_id": call.caller.step_id,
                    "invocation_id": call.invocation_id,
                },
            )
            # Persist a minimal invocation row so the audit trail
            # records that the agent attempted an unavailable tool —
            # ops debugging "why did the conversation degrade?" will
            # find this in the per-conversation invocation list.
            unavailable_invocation = ToolInvocation(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                executor_kind="builtin",  # placeholder — no executor will run
                status="failed",
                caller=call.caller,
                args=call.args,
                dedupe_key=call.dedupe_key,
                metadata={
                    **dict(call.metadata),
                    "failure_kind": "tool_unavailable",
                },
                error="tool_unavailable: ref not configured for organization",
                updated_at=_utcnow(),
            )
            try:
                self._store.save(unavailable_invocation)
            except Exception:  # noqa: BLE001
                # Persistence is best-effort for the audit trail; the
                # caller still gets the structured result.
                logger.exception(
                    "failed to persist unavailable tool invocation",
                    extra={"tool_ref": call.tool_ref, "invocation_id": call.invocation_id},
                )
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error=f"tool not configured for organization: {call.tool_ref}",
                metadata=_failure_metadata(
                    "tool_unavailable",
                    error_type="tool_unavailable",
                    tool_ref=call.tool_ref,
                ),
            )
        invocation = ToolInvocation(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            executor_kind=spec.kind,
            status="pending",
            caller=call.caller,
            args=call.args,
            dedupe_key=call.dedupe_key,
            metadata=dict(call.metadata),
        )
        self._store.save(invocation)

        # ── Validate input against tool spec schema ───────────────────────────────
        try:
            spec.validate_input(call.args)
        except SchemaValidationError as schema_error:
            invocation.status = "failed"
            invocation.error = f"input validation failed: {str(schema_error)}"
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error=invocation.error,
                metadata=_failure_metadata("validation_error", error_type="input_validation"),
            )

        authorization = self._authorizer.authorize(spec, call)
        invocation.decision = authorization.decision
        invocation.decision_reason = authorization.reason
        invocation.metadata.update({"authorization": authorization.metadata})

        if authorization.decision == "deny":
            invocation.status = "blocked"
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="blocked",
                error=authorization.reason,
                metadata=_failure_metadata("authorization_denied"),
            )

        if authorization.decision == "confirm":
            invocation.status = "waiting_confirmation"
            if spec.confirmation_prompt:
                invocation.metadata["confirmation_prompt"] = spec.confirmation_prompt
            invocation.expires_at = self._confirmation_expiry()
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="confirmation_required",
                error=spec.confirmation_prompt or authorization.reason,
                metadata=_failure_metadata(
                    "confirmation_required",
                    confirmation_prompt=spec.confirmation_prompt or authorization.reason,
                ),
            )

        # Scan tool arguments for PII (global scan)
        call = self._scan_tool_args(spec, call)

        return self._run_allowed(spec, call, invocation)

    def confirm(self, invocation_id: str) -> ToolResult:
        invocation = self.load_invocation(invocation_id)
        if invocation is None:
            raise KeyError(invocation_id)
        if invocation.status != "waiting_confirmation":
            if invocation.status == "timed_out" and invocation.error == "tool confirmation expired":
                raise ValueError("invocation confirmation has expired")
            raise ValueError("invocation is not waiting for confirmation")
        spec = self._resolve_spec(invocation.tool_ref, organization_id=invocation.caller.tenant_id)
        call = ToolCall(
            invocation_id=invocation.invocation_id,
            tool_ref=invocation.tool_ref,
            args=dict(invocation.args),
            caller=invocation.caller,
            dedupe_key=invocation.dedupe_key,
            metadata=dict(invocation.metadata),
            requested_at=invocation.created_at,
        )
        return self._run_allowed(spec, call, invocation)

    def cancel(self, invocation_id: str, *, reason: str = "cancelled") -> ToolResult:
        invocation = self.load_invocation(invocation_id)
        if invocation is None:
            raise KeyError(invocation_id)
        if invocation.status not in _CANCELLABLE_INVOCATION_STATUSES:
            raise ValueError("invocation cannot be cancelled from current state")
        integration_job_id = str(invocation.metadata.get("integration_job_id") or "")
        if integration_job_id and self._integration_runtime is not None:
            cancelled_job = self._integration_runtime.cancel_job(integration_job_id, reason=reason)
            if cancelled_job is None:
                raise ValueError("invocation cannot be cancelled from current state")
            invocation = self.load_invocation(invocation_id) or invocation
        invocation.status = "cancelled"
        invocation.error = reason
        invocation.updated_at = _utcnow()
        self._store.save(invocation)
        return ToolResult(
            invocation_id=invocation.invocation_id,
            tool_ref=invocation.tool_ref,
            status="cancelled",
            error=reason,
        )

    async def execute_async(self, call: ToolCall) -> ToolResult:
        """Async-interface execution for route handlers and the kernel adapter.

        Executors remain synchronous — they run inside ``loop.run_in_executor``.
        The async wrapper:
          - Validates args and checks authorization (same rules as ``invoke()``)
          - Checks the circuit breaker; rejects immediately when OPEN
          - Enforces timeout via ``asyncio.wait_for`` (unblocks the event loop)
          - Records success/failure to the circuit breaker
          - Emits Prometheus metrics for every outcome

        ``invoke()`` is unchanged and is still used by the synchronous kernel path.
        """
        from ruhu.observability.metrics import (
            tool_invocation_duration_seconds,
            tool_invocations_total,
        )

        spec = self._resolve_spec(call.tool_ref, organization_id=call.caller.tenant_id)
        invocation = ToolInvocation(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            executor_kind=spec.kind,
            status="pending",
            caller=call.caller,
            args=call.args,
            dedupe_key=call.dedupe_key,
            metadata=dict(call.metadata),
        )
        self._store.save(invocation)

        # ── Validation ────────────────────────────────────────────────────────
        try:
            spec.validate_input(call.args)
        except SchemaValidationError as schema_error:
            invocation.status = "failed"
            invocation.error = f"input validation failed: {str(schema_error)}"
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            tool_invocations_total.labels(executor_kind=spec.kind, status="error").inc()
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error=invocation.error,
                metadata=_failure_metadata("validation_error", error_type="input_validation"),
            )

        # ── Authorization ─────────────────────────────────────────────────────
        authorization = self._authorizer.authorize(spec, call)
        invocation.decision = authorization.decision
        invocation.decision_reason = authorization.reason
        invocation.metadata.update({"authorization": authorization.metadata})

        if authorization.decision == "deny":
            invocation.status = "blocked"
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            tool_invocations_total.labels(executor_kind=spec.kind, status="blocked").inc()
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="blocked",
                error=authorization.reason,
                metadata=_failure_metadata("authorization_denied"),
            )

        if authorization.decision == "confirm":
            invocation.status = "waiting_confirmation"
            if spec.confirmation_prompt:
                invocation.metadata["confirmation_prompt"] = spec.confirmation_prompt
            invocation.expires_at = self._confirmation_expiry()
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="confirmation_required",
                error=spec.confirmation_prompt or authorization.reason,
                metadata={
                    **_failure_metadata("confirmation_required"),
                    "confirmation_prompt": spec.confirmation_prompt or authorization.reason,
                },
            )

        # Scan tool arguments for PII (global scan)
        call = self._scan_tool_args(spec, call)

        if self._should_defer_execution(spec):
            return self._submit_deferred_job(spec=spec, call=call, invocation=invocation)

        # ── Circuit breaker ───────────────────────────────────────────────────
        breaker = await self._circuit_registry.get(call.tool_ref)
        if not await breaker.can_execute():
            invocation.status = "blocked"
            invocation.error = "tool_unavailable: circuit breaker is open"
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            tool_invocations_total.labels(
                executor_kind=spec.kind, status="circuit_open"
            ).inc()
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error="tool_unavailable: circuit breaker is open",
                metadata={
                    **_failure_metadata(
                        "transient_upstream_error",
                        error_type="circuit_open",
                    ),
                    "circuit_state": "open",
                },
            )

        # ── Tool-level rate limiting ──────────────────────────────────────────
        if self._tool_rate_limiter is not None:
            rl_limit = spec.executor_config.get("rate_limit")
            rl_window = spec.executor_config.get("rate_limit_window_seconds")
            if rl_limit is not None and rl_window is not None:
                rl_result = await self._tool_rate_limiter.check(
                    call.tool_ref,
                    tenant_id=call.caller.tenant_id,
                    limit=int(rl_limit),
                    window_seconds=int(rl_window),
                )
                if not rl_result.allowed:
                    invocation.status = "blocked"
                    invocation.error = "tool_rate_limited"
                    invocation.updated_at = _utcnow()
                    self._store.save(invocation)
                    tool_invocations_total.labels(
                        executor_kind=spec.kind, status="rate_limited"
                    ).inc()
                    return ToolResult(
                        invocation_id=call.invocation_id,
                        tool_ref=call.tool_ref,
                        status="error",
                        error="tool_rate_limited",
                        metadata={
                            **_failure_metadata(
                                "transient_upstream_error",
                                error_type="rate_limited",
                            ),
                            "retry_after": rl_result.retry_after,
                            "current_count": rl_result.current_count,
                        },
                    )

        # ── Executor lookup ───────────────────────────────────────────────────
        executor = self._executors.get(spec.kind)
        if executor is None:
            invocation.status = "failed"
            invocation.error = f"no executor registered for kind {spec.kind}"
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            tool_invocations_total.labels(executor_kind=spec.kind, status="error").inc()
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error=invocation.error,
                metadata=_failure_metadata(
                    "permanent_upstream_error",
                    error_type="missing_executor",
                ),
            )

        invocation.status = "running"
        invocation.updated_at = _utcnow()
        self._store.save(invocation)

        # ── Async execution ───────────────────────────────────────────────────
        start = time.perf_counter()
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, executor.execute, spec, call),
                timeout=spec.timeout_ms / 1000,
            )
            await breaker.record_success()
        except asyncio.TimeoutError:
            await breaker.record_failure()
            result = ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="timeout",
                error=f"tool exceeded timeout of {spec.timeout_ms}ms",
                latency_ms=spec.timeout_ms,
                metadata=_failure_metadata("timeout"),
            )
        except Exception as exc:
            await breaker.record_failure()
            result = ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error=str(exc),
                latency_ms=int((time.perf_counter() - start) * 1000),
                metadata=_failure_metadata(
                    "permanent_upstream_error",
                    error_type="unexpected_executor_error",
                ),
            )

        latency_ms = result.latency_ms or int((time.perf_counter() - start) * 1000)
        tool_invocations_total.labels(
            executor_kind=spec.kind, status=result.status
        ).inc()
        tool_invocation_duration_seconds.labels(executor_kind=spec.kind).observe(
            latency_ms / 1000
        )

        # ── Validate output against tool spec schema ───────────────────────────────
        result = self._validate_tool_output(spec, result)
        result = self._normalize_failure_result(result)

        # ── PII redaction (opt-in via spec.executor_config) ────────────────────
        result = self._apply_pii_policy(spec, result)
        # Global PII scan (independent of per-tool policy)
        result = self._scan_tool_output(spec, call, result)

        invocation.status = self._map_result_status(result.status)
        invocation.output = dict(result.output)
        invocation.error = result.error
        invocation.latency_ms = latency_ms
        invocation.metadata.update(result.metadata)
        invocation.updated_at = _utcnow()
        self._store.save(invocation)
        return result

    def _run_allowed(self, spec: ToolSpec, call: ToolCall, invocation: ToolInvocation) -> ToolResult:
        if self._should_defer_execution(spec):
            return self._submit_deferred_job(spec=spec, call=call, invocation=invocation)

        # ── Circuit breaker (parity with async path) ──────────────────────────
        breaker = self._circuit_registry.get_sync(call.tool_ref)
        if not breaker.can_execute_sync():
            invocation.status = "blocked"
            invocation.error = "tool_unavailable: circuit breaker is open"
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            tool_invocations_total.labels(
                executor_kind=spec.kind, status="circuit_open"
            ).inc()
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error="tool_unavailable: circuit breaker is open",
                metadata={
                    **_failure_metadata(
                        "transient_upstream_error",
                        error_type="circuit_open",
                    ),
                    "circuit_state": "open",
                },
            )

        executor = self._executors.get(spec.kind)
        if executor is None:
            invocation.status = "failed"
            invocation.error = f"no executor registered for kind {spec.kind}"
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error=invocation.error,
                metadata=_failure_metadata(
                    "permanent_upstream_error",
                    error_type="missing_executor",
                ),
            )

        invocation.status = "running"
        invocation.updated_at = _utcnow()
        self._store.save(invocation)

        started = time.perf_counter()
        try:
            result = self._execute_with_timeout(executor, spec, call)
        except Exception as exc:
            breaker.record_failure_sync()
            latency_ms = int((time.perf_counter() - started) * 1000)
            invocation.status = "failed"
            invocation.error = str(exc)
            invocation.latency_ms = latency_ms
            invocation.updated_at = _utcnow()
            self._store.save(invocation)
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error=str(exc),
                latency_ms=latency_ms,
                metadata=_failure_metadata(
                    "permanent_upstream_error",
                    error_type="unexpected_executor_error",
                ),
            )

        result = self._validate_tool_output(spec, result)
        result = self._normalize_failure_result(result)
        result = self._apply_pii_policy(spec, result)
        # Global PII scan (independent of per-tool policy)
        result = self._scan_tool_output(spec, call, result)
        # Update breaker based on executor result status
        if result.status in {"error", "timeout"}:
            breaker.record_failure_sync()
        else:
            breaker.record_success_sync()
        invocation.status = self._map_result_status(result.status)
        invocation.output = dict(result.output)
        invocation.error = result.error
        invocation.latency_ms = result.latency_ms
        invocation.metadata.update(result.metadata)
        invocation.updated_at = _utcnow()
        self._store.save(invocation)
        return result

    def _execute_with_timeout(self, executor: ToolExecutor, spec: ToolSpec, call: ToolCall) -> ToolResult:
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ruhu-tool")
        future = pool.submit(executor.execute, spec, call)
        started = time.perf_counter()
        timed_out = False
        try:
            result = future.result(timeout=spec.timeout_ms / 1000)
        except FuturesTimeoutError:
            timed_out = True
            future.cancel()
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="timeout",
                error=f"tool exceeded timeout of {spec.timeout_ms}ms",
                latency_ms=spec.timeout_ms,
                metadata=_failure_metadata("timeout"),
            )
        finally:
            pool.shutdown(wait=not timed_out, cancel_futures=timed_out)

        if result.latency_ms is None:
            result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    def _validate_tool_output(self, spec: ToolSpec, result: ToolResult) -> ToolResult:
        if result.status != "success":
            return result
        try:
            spec.validate_output(result.output)
            return result
        except SchemaValidationError as exc:
            if spec.output_validation_mode != "strict":
                return result
            return ToolResult(
                invocation_id=result.invocation_id,
                tool_ref=result.tool_ref,
                status="error",
                error=str(exc),
                latency_ms=result.latency_ms,
                metadata={
                    **dict(result.metadata),
                    **_failure_metadata(
                        "validation_error",
                        error_type="output_validation_error",
                    ),
                },
            )

    def _normalize_failure_result(self, result: ToolResult) -> ToolResult:
        if result.status == "success":
            return result
        metadata = dict(result.metadata)
        if metadata.get("failure_kind"):
            return result
        if result.status == "confirmation_required":
            metadata.update(_failure_metadata("confirmation_required"))
        elif result.status == "blocked":
            metadata.update(_failure_metadata("authorization_denied"))
        elif result.status == "timeout":
            metadata.update(_failure_metadata("timeout"))
        elif result.status == "cancelled":
            metadata.setdefault("error_type", "cancelled")
        else:
            metadata.update(
                _failure_metadata(
                    "permanent_upstream_error",
                    error_type=str(metadata.get("error_type") or "unknown_error"),
                )
            )
        return result.model_copy(update={"metadata": metadata})

    async def invoke_parallel(
        self,
        calls: list[ToolCall],
        *,
        max_concurrency: int = 5,
    ) -> list[ToolResult]:
        """Execute multiple tool calls concurrently with bounded parallelism.

        Returns results in the same order as the input ``calls`` list,
        regardless of completion order.  Each call is independently validated,
        authorized, and executed — a failure in one does not cancel others.

        ``max_concurrency`` caps the number of tools executing simultaneously
        to prevent thread-pool exhaustion and downstream overload.
        """
        if not calls:
            return []
        max_concurrency = max(1, min(max_concurrency, 20))
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _run(index: int, call: ToolCall) -> tuple[int, ToolResult]:
            async with semaphore:
                result = await self.execute_async(call)
                return index, result

        tasks = [_run(i, c) for i, c in enumerate(calls)]
        indexed_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[ToolResult] = [None] * len(calls)  # type: ignore[list-item]
        for item in indexed_results:
            if isinstance(item, BaseException):
                # Should not happen — execute_async never raises.
                # Defensive: synthesize an error result.
                results[0] = ToolResult(
                    invocation_id="unknown",
                    tool_ref="unknown",
                    status="error",
                    error=f"unexpected error in parallel execution: {item}",
                )
                continue
            idx, result = item
            results[idx] = result
        return results

    @staticmethod
    def _map_result_status(status: str) -> str:
        mapping: dict[str, str] = {
            "success": "completed",
            "timeout": "timed_out",
            "blocked": "blocked",
            "confirmation_required": "waiting_confirmation",
            "cancelled": "cancelled",
            "error": "failed",
        }
        return mapping[status]

    def _should_defer_execution(self, spec: ToolSpec) -> bool:
        execution_mode = str(spec.executor_config.get("execution_mode") or "").strip().lower()
        return execution_mode == "deferred" and self._integration_runtime is not None

    def _submit_deferred_job(
        self,
        *,
        spec: ToolSpec,
        call: ToolCall,
        invocation: ToolInvocation,
    ) -> ToolResult:
        if self._integration_runtime is None:
            raise RuntimeError("deferred execution requested without an integration runtime")
        job = self._integration_runtime.submit(spec=spec, call=call, invocation=invocation)
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="success",
            output={"job_id": job.job_id, "status": job.status},
            metadata={
                "deferred": True,
                "integration_job_id": job.job_id,
                "integration_status": job.status,
                "integration_resolution_mode": job.resolution_mode,
                "integration_queue_name": job.queue_name,
            },
        )

    @staticmethod
    def _tool_result_from_invocation(invocation: ToolInvocation) -> ToolResult:
        status_mapping: dict[str, str] = {
            "completed": "success",
            "waiting_confirmation": "confirmation_required",
            "blocked": "blocked",
            "timed_out": "timeout",
            "cancelled": "cancelled",
            "failed": "error",
            "dead_lettered": "error",
            "pending": "error",
            "queued": "success",
            "running": "success",
            "waiting_poll": "success",
            "waiting_webhook": "success",
            "retry_scheduled": "success",
        }
        metadata = dict(invocation.metadata)
        if invocation.status in {"queued", "running", "waiting_poll", "waiting_webhook", "retry_scheduled"}:
            metadata["deferred"] = True
            metadata["integration_status"] = invocation.status
        return ToolResult(
            invocation_id=invocation.invocation_id,
            tool_ref=invocation.tool_ref,
            status=status_mapping.get(invocation.status, "error"),  # type: ignore[arg-type]
            output=dict(invocation.output),
            error=invocation.error,
            latency_ms=invocation.latency_ms,
            metadata=metadata,
        )

    def _scan_tool_args(self, spec: ToolSpec, call: ToolCall) -> ToolCall:
        """Scan tool arguments for PII (global scan, if scanner is configured).

        Returns a new ToolCall with redacted args, or the original if scanning is disabled.
        """
        if self._tiered_pii_scanner is None:
            return call
        try:
            scan = self._tiered_pii_scanner.scan_and_redact_dict(
                dict(call.args),
                context={
                    "field_context": "tool_args",
                    "tool_ref": spec.ref,
                    "organization_id": call.caller.tenant_id,
                    "conversation_id": call.caller.conversation_id,
                },
            )
            if scan.redacted_dict is not None:
                return call.model_copy(update={"args": scan.redacted_dict})
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Tool args PII scan failed for {spec.ref}: {e}", exc_info=True)
        return call

    def _scan_tool_output(self, spec: ToolSpec, call: ToolCall, result: ToolResult) -> ToolResult:
        """Globally scan tool output for PII (independent of per-tool redact_pii policy).

        Returns result with redacted output, or original if scanning is disabled or fails.
        """
        if self._tiered_pii_scanner is None or result.status != "success":
            return result
        try:
            scan = self._tiered_pii_scanner.scan_and_redact_dict(
                dict(result.output),
                context={
                    "field_context": "tool_output",
                    "tool_ref": spec.ref,
                    "organization_id": call.caller.tenant_id,
                    "conversation_id": call.caller.conversation_id,
                },
            )
            if scan.redacted_dict is not None:
                return ToolResult(
                    invocation_id=result.invocation_id,
                    tool_ref=result.tool_ref,
                    status=result.status,
                    output=scan.redacted_dict,
                    error=result.error,
                    latency_ms=result.latency_ms,
                    metadata=dict(result.metadata),
                )
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Tool output PII scan failed for {spec.ref}: {e}", exc_info=True)
        return result

    def _apply_pii_policy(self, spec: ToolSpec, result: ToolResult) -> ToolResult:
        if result.status != "success":
            return result
        if not spec.executor_config.get("redact_pii"):
            return result
        policy = dict(spec.executor_config.get("pii_redaction") or {})
        processed = self._pii_redactor.process_dict(result.output, policy=policy)
        metadata = {**dict(result.metadata), **processed.metadata}
        status = "error" if processed.blocked else result.status
        error = processed.error if processed.blocked else result.error
        return ToolResult(
            invocation_id=result.invocation_id,
            tool_ref=result.tool_ref,
            status=status,
            output=processed.output,
            error=error,
            latency_ms=result.latency_ms,
            metadata=metadata,
        )
