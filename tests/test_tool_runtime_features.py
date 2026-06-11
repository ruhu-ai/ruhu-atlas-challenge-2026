"""
Tests for 5 tool runtime features:

  1. Parallel execution (invoke_parallel)
  2. SSRF protection (url_validator)
  3. Granular tool RBAC (agent_policy + authorizer integration)
  4. Tool-level rate limiting (tool_rate_limiter)
  5. PII redaction (pii_redactor)
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from ruhu.tools.agent_policy import (
    CachedAgentToolPolicy,
    InMemoryAgentToolPolicy,
)
from ruhu.tools.authorizer import DefaultToolAuthorizer
from ruhu.tools.executors.builtin import BuiltinExecutor
from ruhu.tools.executors.http import HttpExecutor
from ruhu.tools.pii_redactor import PiiRedactor
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.runtime import ToolRuntime
from ruhu.tools.specs import ToolSpec
from ruhu.tools.tool_rate_limiter import ToolRateLimiter, ToolRateLimitResult
from ruhu.tools.types import ToolCall, ToolCaller, ToolResult
from ruhu.tools.url_validator import SSRFBlockedError, validate_url


# ── Shared helpers ──────────────────────────────────────────────────────────────

def _spec(**overrides: object) -> ToolSpec:
    data = {
        "ref": "test.tool",
        "kind": "builtin",
        "display_name": "Test Tool",
        "description": "A test tool for unit testing purposes only.",
        "input_schema": {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "query param."}},
            "required": [],
            "additionalProperties": False,
        },
    }
    data.update(overrides)
    return ToolSpec.model_validate(data)


def _call(ref: str = "test.tool", **overrides: object) -> ToolCall:
    data = {
        "tool_ref": ref,
        "args": {},
        "caller": ToolCaller(channel="web_chat"),
    }
    data.update(overrides)
    return ToolCall.model_validate(data)


def _runtime_with_handler(spec: ToolSpec, handler, **kwargs) -> ToolRuntime:
    registry = ToolRegistry([spec])
    executor = BuiltinExecutor({spec.ref: handler})
    return ToolRuntime(registry, executors={"builtin": executor}, **kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# 1. PARALLEL EXECUTION — invoke_parallel
# ══════════════════════════════════════════════════════════════════════════════

class TestInvokeParallel:
    def test_empty_input_returns_empty_list(self) -> None:
        spec = _spec()
        runtime = _runtime_with_handler(spec, lambda call, _s: {"ok": True})

        async def run():
            return await runtime.invoke_parallel([])

        results = anyio.run(run)
        assert results == []

    def test_single_call_returns_single_result(self) -> None:
        spec = _spec()
        runtime = _runtime_with_handler(spec, lambda call, _s: {"val": 42})

        async def run():
            return await runtime.invoke_parallel([_call()])

        results = anyio.run(run)
        assert len(results) == 1
        assert results[0].status == "success"
        assert results[0].output["val"] == 42

    def test_multiple_calls_preserve_order(self) -> None:
        specs = [
            _spec(ref="tool.a", display_name="A", executor_key="tool.a"),
            _spec(ref="tool.b", display_name="B", executor_key="tool.b"),
            _spec(ref="tool.c", display_name="C", executor_key="tool.c"),
        ]
        registry = ToolRegistry(specs)
        handlers = {
            "tool.a": lambda call, _s: {"id": "a"},
            "tool.b": lambda call, _s: {"id": "b"},
            "tool.c": lambda call, _s: {"id": "c"},
        }
        executor = BuiltinExecutor(handlers)
        runtime = ToolRuntime(registry, executors={"builtin": executor})

        calls = [_call("tool.a"), _call("tool.b"), _call("tool.c")]

        async def run():
            return await runtime.invoke_parallel(calls)

        results = anyio.run(run)
        assert len(results) == 3
        assert results[0].output["id"] == "a"
        assert results[1].output["id"] == "b"
        assert results[2].output["id"] == "c"

    def test_individual_failure_does_not_cancel_others(self) -> None:
        good = _spec(ref="tool.good", display_name="Good", executor_key="tool.good")
        bad = _spec(ref="tool.bad", display_name="Bad", executor_key="tool.bad")
        registry = ToolRegistry([good, bad])

        def raise_handler(call, _s):
            raise RuntimeError("boom")

        executor = BuiltinExecutor({
            "tool.good": lambda call, _s: {"ok": True},
            "tool.bad": raise_handler,
        })
        runtime = ToolRuntime(registry, executors={"builtin": executor})

        calls = [_call("tool.good"), _call("tool.bad"), _call("tool.good")]

        async def run():
            return await runtime.invoke_parallel(calls)

        results = anyio.run(run)
        assert results[0].status == "success"
        assert results[1].status == "error"
        assert results[2].status == "success"

    def test_max_concurrency_is_respected(self) -> None:
        """Verify that no more than max_concurrency tools execute at once."""
        spec = _spec()
        peak_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        # We need a handler that takes time and tracks concurrency.
        # Since execute_async uses run_in_executor, the handler runs in a thread.
        import threading
        thread_lock = threading.Lock()
        thread_peak = [0]
        thread_current = [0]

        def slow_handler(call, _s):
            with thread_lock:
                thread_current[0] += 1
                if thread_current[0] > thread_peak[0]:
                    thread_peak[0] = thread_current[0]
            time.sleep(0.05)
            with thread_lock:
                thread_current[0] -= 1
            return {"ok": True}

        runtime = _runtime_with_handler(spec, slow_handler)
        calls = [_call() for _ in range(6)]

        async def run():
            return await runtime.invoke_parallel(calls, max_concurrency=2)

        results = anyio.run(run)
        assert all(r.status == "success" for r in results)
        assert thread_peak[0] <= 2

    def test_max_concurrency_clamped_to_valid_range(self) -> None:
        spec = _spec()
        runtime = _runtime_with_handler(spec, lambda call, _s: {"ok": True})

        async def run():
            # 0 should be clamped to 1
            return await runtime.invoke_parallel([_call()], max_concurrency=0)

        results = anyio.run(run)
        assert len(results) == 1
        assert results[0].status == "success"


# ══════════════════════════════════════════════════════════════════════════════
# 2. SSRF PROTECTION — url_validator
# ══════════════════════════════════════════════════════════════════════════════

class TestSSRFUrlValidator:
    def test_allows_public_https_url(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 443)),
            ]
            result = validate_url("https://example.com/api/v1")
            assert result == "https://example.com/api/v1"

    def test_allows_public_http_url(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 80)),
            ]
            result = validate_url("http://example.com/webhook")
            assert result == "http://example.com/webhook"

    def test_blocks_ftp_scheme(self) -> None:
        with pytest.raises(SSRFBlockedError, match="scheme.*not allowed"):
            validate_url("ftp://internal.host/data")

    def test_blocks_file_scheme(self) -> None:
        with pytest.raises(SSRFBlockedError, match="scheme.*not allowed"):
            validate_url("file:///etc/passwd")

    def test_blocks_empty_scheme(self) -> None:
        with pytest.raises(SSRFBlockedError, match="scheme.*not allowed"):
            validate_url("//example.com/path")

    def test_blocks_localhost(self) -> None:
        with pytest.raises(SSRFBlockedError, match="hostname.*blocked"):
            validate_url("https://localhost/admin")

    def test_blocks_metadata_google_internal(self) -> None:
        with pytest.raises(SSRFBlockedError, match="hostname.*blocked"):
            validate_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_blocks_missing_hostname(self) -> None:
        with pytest.raises(SSRFBlockedError, match="missing hostname"):
            validate_url("https:///path-only")

    def test_blocks_private_ipv4_10(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("10.0.0.1", 443))]
            with pytest.raises(SSRFBlockedError, match="10.0.0.0/8"):
                validate_url("https://internal.corp/api")

    def test_blocks_private_ipv4_172(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("172.16.5.1", 443))]
            with pytest.raises(SSRFBlockedError, match="172.16.0.0/12"):
                validate_url("https://internal.corp/api")

    def test_blocks_private_ipv4_192(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("192.168.1.1", 443))]
            with pytest.raises(SSRFBlockedError, match="192.168.0.0/16"):
                validate_url("https://internal.corp/api")

    def test_blocks_loopback_127(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
            with pytest.raises(SSRFBlockedError, match="127.0.0.0/8"):
                validate_url("https://sneaky.example.com/api")

    def test_blocks_link_local_169_254(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("169.254.1.1", 80))]
            with pytest.raises(SSRFBlockedError, match="169.254.0.0/16"):
                validate_url("http://evil.example.com/steal")

    def test_blocks_cloud_metadata_ip(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("169.254.169.254", 80))]
            with pytest.raises(SSRFBlockedError):
                validate_url("http://evil.example.com/metadata")

    def test_blocks_this_network_0_0_0_0(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("0.0.0.1", 443))]
            with pytest.raises(SSRFBlockedError, match="0.0.0.0/8"):
                validate_url("https://zero.example.com/api")

    def test_blocks_reserved_240(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("240.0.0.1", 443))]
            with pytest.raises(SSRFBlockedError, match="240.0.0.0/4"):
                validate_url("https://reserved.example.com/api")

    def test_blocks_shared_address_space_100_64(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("100.64.0.1", 443))]
            with pytest.raises(SSRFBlockedError, match="100.64.0.0/10"):
                validate_url("https://carrier.example.com/api")

    def test_blocks_ipv4_mapped_ipv6(self) -> None:
        """::ffff:10.0.0.1 must be unwrapped and blocked as 10.0.0.1."""
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (10, 1, 6, "", ("::ffff:10.0.0.1", 443, 0, 0)),
            ]
            with pytest.raises(SSRFBlockedError, match="10.0.0.0/8"):
                validate_url("https://ipv6mapped.example.com/api")

    def test_blocks_ipv6_loopback(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (10, 1, 6, "", ("::1", 443, 0, 0)),
            ]
            with pytest.raises(SSRFBlockedError, match="::1/128"):
                validate_url("https://ipv6loop.example.com/api")

    def test_blocks_ipv6_link_local(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (10, 1, 6, "", ("fe80::1", 443, 0, 0)),
            ]
            with pytest.raises(SSRFBlockedError, match="fe80::/10"):
                validate_url("https://ipv6ll.example.com/api")

    def test_blocks_dns_resolution_failure(self) -> None:
        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            import socket
            mock_gai.side_effect = socket.gaierror("Name or service not known")
            with pytest.raises(SSRFBlockedError, match="DNS resolution failed"):
                validate_url("https://nonexistent.invalid/api")


class TestSSRFInHttpExecutor:
    """Verify that the HttpExecutor integrates SSRF protection."""

    def test_http_executor_blocks_ssrf_url(self) -> None:
        spec = _spec(
            kind="http",
            executor_config={"url": "http://localhost/admin", "method": "GET"},
        )
        executor = HttpExecutor(client=MagicMock())
        call = _call(ref=spec.ref)
        result = executor.execute(spec, call)
        assert result.status == "error"
        assert "SSRF protection" in result.error
        assert result.metadata.get("error_type") == "ssrf_blocked"
        assert result.metadata.get("failure_kind") == "validation_error"

    def test_http_executor_allows_valid_url(self) -> None:
        spec = _spec(
            kind="http",
            executor_config={"url": "https://api.example.com/v1/action", "method": "POST"},
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "ok"}
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response

        executor = HttpExecutor(client=mock_client)
        call = _call(ref=spec.ref)

        with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
            result = executor.execute(spec, call)

        assert result.status == "success"
        assert result.output["result"] == "ok"


# ══════════════════════════════════════════════════════════════════════════════
# 3. GRANULAR TOOL RBAC — agent_policy + authorizer
# ══════════════════════════════════════════════════════════════════════════════

class TestInMemoryAgentToolPolicy:
    def test_no_policy_returns_none(self) -> None:
        policy = InMemoryAgentToolPolicy()
        result = policy.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="some.tool"
        )
        assert result is None

    def test_set_and_read_deny_policy(self) -> None:
        policy = InMemoryAgentToolPolicy()
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="some.tool", enabled=False
        )
        result = policy.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="some.tool"
        )
        assert result is False

    def test_set_and_read_allow_policy(self) -> None:
        policy = InMemoryAgentToolPolicy()
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="some.tool", enabled=True
        )
        result = policy.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="some.tool"
        )
        assert result is True

    def test_policies_scoped_by_org_agent_tool(self) -> None:
        policy = InMemoryAgentToolPolicy()
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.a", enabled=False
        )
        # Same org, different agent
        assert policy.is_tool_enabled(
            organization_id="org-1", agent_id="agent-2", tool_ref="tool.a"
        ) is None
        # Different org, same agent
        assert policy.is_tool_enabled(
            organization_id="org-2", agent_id="agent-1", tool_ref="tool.a"
        ) is None
        # Same org+agent, different tool
        assert policy.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.b"
        ) is None

    def test_remove_policy(self) -> None:
        policy = InMemoryAgentToolPolicy()
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="some.tool", enabled=False
        )
        policy.remove_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="some.tool"
        )
        assert policy.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="some.tool"
        ) is None

    def test_remove_nonexistent_policy_does_not_raise(self) -> None:
        policy = InMemoryAgentToolPolicy()
        # Should not raise
        policy.remove_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="missing.tool"
        )


class TestCachedAgentToolPolicy:
    def test_caches_result(self) -> None:
        backend = InMemoryAgentToolPolicy()
        backend.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x", enabled=False
        )
        cached = CachedAgentToolPolicy(backend, ttl_seconds=60.0)

        # First call: populates cache
        assert cached.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
        ) is False

        # Change backend — cached should still return old value
        backend.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x", enabled=True
        )
        assert cached.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
        ) is False

    def test_cache_expires_after_ttl(self) -> None:
        backend = InMemoryAgentToolPolicy()
        backend.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x", enabled=False
        )
        cached = CachedAgentToolPolicy(backend, ttl_seconds=10.0)

        mock_time = [1000.0]
        with patch("ruhu.tools.agent_policy.time") as time_mod:
            time_mod.monotonic = lambda: mock_time[0]

            assert cached.is_tool_enabled(
                organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
            ) is False

            backend.set_policy(
                organization_id="org-1", agent_id="agent-1", tool_ref="tool.x", enabled=True
            )
            # Still cached
            assert cached.is_tool_enabled(
                organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
            ) is False

            # Advance past TTL
            mock_time[0] = 1011.0
            assert cached.is_tool_enabled(
                organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
            ) is True

    def test_invalidate_single_entry(self) -> None:
        backend = InMemoryAgentToolPolicy()
        backend.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x", enabled=False
        )
        cached = CachedAgentToolPolicy(backend, ttl_seconds=300.0)

        cached.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
        )
        backend.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x", enabled=True
        )
        cached.invalidate(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
        )
        assert cached.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
        ) is True

    def test_invalidate_all(self) -> None:
        backend = InMemoryAgentToolPolicy()
        backend.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x", enabled=False
        )
        cached = CachedAgentToolPolicy(backend, ttl_seconds=300.0)

        cached.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
        )
        backend.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x", enabled=True
        )
        cached.invalidate_all()
        assert cached.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
        ) is True

    def test_none_result_is_cached(self) -> None:
        """Open-default (None) should also be cached to avoid repeated lookups."""
        backend = InMemoryAgentToolPolicy()
        cached = CachedAgentToolPolicy(backend, ttl_seconds=300.0)

        assert cached.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
        ) is None
        # Now set a policy — cache should still return None
        backend.set_policy(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x", enabled=False
        )
        assert cached.is_tool_enabled(
            organization_id="org-1", agent_id="agent-1", tool_ref="tool.x"
        ) is None


class TestAuthorizerWithAgentPolicy:
    def test_open_default_allows_when_no_policy(self) -> None:
        policy = InMemoryAgentToolPolicy()
        authorizer = DefaultToolAuthorizer(agent_tool_policy=policy)
        spec = _spec()
        call = _call(
            caller=ToolCaller(channel="web_chat", tenant_id="org-1", agent_id="agent-1"),
        )
        result = authorizer.authorize(spec, call)
        assert result.decision == "allow"

    def test_explicit_deny_blocks_tool(self) -> None:
        policy = InMemoryAgentToolPolicy()
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1",
            tool_ref="test.tool", enabled=False,
        )
        authorizer = DefaultToolAuthorizer(agent_tool_policy=policy)
        spec = _spec()
        call = _call(
            caller=ToolCaller(channel="web_chat", tenant_id="org-1", agent_id="agent-1"),
        )
        result = authorizer.authorize(spec, call)
        assert result.decision == "deny"
        assert result.reason == "agent_tool_policy_denied"

    def test_explicit_allow_permits_tool(self) -> None:
        policy = InMemoryAgentToolPolicy()
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1",
            tool_ref="test.tool", enabled=True,
        )
        authorizer = DefaultToolAuthorizer(agent_tool_policy=policy)
        spec = _spec()
        call = _call(
            caller=ToolCaller(channel="web_chat", tenant_id="org-1", agent_id="agent-1"),
        )
        result = authorizer.authorize(spec, call)
        assert result.decision == "allow"

    def test_policy_not_checked_without_tenant_id(self) -> None:
        policy = InMemoryAgentToolPolicy()
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1",
            tool_ref="test.tool", enabled=False,
        )
        authorizer = DefaultToolAuthorizer(agent_tool_policy=policy)
        spec = _spec()
        # No tenant_id — policy should be skipped
        call = _call(
            caller=ToolCaller(channel="web_chat", agent_id="agent-1"),
        )
        result = authorizer.authorize(spec, call)
        assert result.decision == "allow"

    def test_policy_not_checked_without_agent_id(self) -> None:
        policy = InMemoryAgentToolPolicy()
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1",
            tool_ref="test.tool", enabled=False,
        )
        authorizer = DefaultToolAuthorizer(agent_tool_policy=policy)
        spec = _spec()
        # No agent_id — policy should be skipped
        call = _call(
            caller=ToolCaller(channel="web_chat", tenant_id="org-1"),
        )
        result = authorizer.authorize(spec, call)
        assert result.decision == "allow"

    def test_blocked_refs_takes_precedence_over_policy(self) -> None:
        policy = InMemoryAgentToolPolicy()
        # Policy says allow, but global block says deny
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1",
            tool_ref="test.tool", enabled=True,
        )
        authorizer = DefaultToolAuthorizer(
            blocked_refs={"test.tool"},
            agent_tool_policy=policy,
        )
        spec = _spec()
        call = _call(
            caller=ToolCaller(channel="web_chat", tenant_id="org-1", agent_id="agent-1"),
        )
        result = authorizer.authorize(spec, call)
        assert result.decision == "deny"
        assert result.reason == "tool_ref_blocked"

    def test_deny_metadata_includes_context(self) -> None:
        policy = InMemoryAgentToolPolicy()
        policy.set_policy(
            organization_id="org-1", agent_id="agent-1",
            tool_ref="test.tool", enabled=False,
        )
        authorizer = DefaultToolAuthorizer(agent_tool_policy=policy)
        spec = _spec()
        call = _call(
            caller=ToolCaller(channel="web_chat", tenant_id="org-1", agent_id="agent-1"),
        )
        result = authorizer.authorize(spec, call)
        assert result.metadata["organization_id"] == "org-1"
        assert result.metadata["agent_id"] == "agent-1"
        assert result.metadata["tool_ref"] == "test.tool"


# ══════════════════════════════════════════════════════════════════════════════
# 4. TOOL-LEVEL RATE LIMITING
# ══════════════════════════════════════════════════════════════════════════════

class TestToolRateLimiterLocal:
    """Tests for the in-memory fallback (no Redis)."""

    def test_allows_within_limit(self) -> None:
        limiter = ToolRateLimiter()

        async def run():
            result = await limiter.check("my.tool", limit=5, window_seconds=60)
            return result

        result = anyio.run(run)
        assert result.allowed is True
        assert result.current_count == 1

    def test_blocks_when_over_limit(self) -> None:
        limiter = ToolRateLimiter()

        async def run():
            for _ in range(3):
                await limiter.check("my.tool", limit=3, window_seconds=60)
            return await limiter.check("my.tool", limit=3, window_seconds=60)

        result = anyio.run(run)
        assert result.allowed is False
        assert result.retry_after > 0

    def test_per_tenant_isolation(self) -> None:
        limiter = ToolRateLimiter()

        async def run():
            for _ in range(3):
                await limiter.check("my.tool", tenant_id="org-1", limit=3, window_seconds=60)
            # org-2 should still be allowed
            return await limiter.check("my.tool", tenant_id="org-2", limit=3, window_seconds=60)

        result = anyio.run(run)
        assert result.allowed is True

    def test_window_expiry_allows_again(self) -> None:
        limiter = ToolRateLimiter()

        async def run():
            for _ in range(2):
                await limiter.check("my.tool", limit=2, window_seconds=0.05)
            # Wait for window to expire
            await asyncio.sleep(0.06)
            return await limiter.check("my.tool", limit=2, window_seconds=0.05)

        result = anyio.run(run)
        assert result.allowed is True

    def test_different_tools_have_separate_counters(self) -> None:
        limiter = ToolRateLimiter()

        async def run():
            for _ in range(3):
                await limiter.check("tool.a", limit=3, window_seconds=60)
            return await limiter.check("tool.b", limit=3, window_seconds=60)

        result = anyio.run(run)
        assert result.allowed is True


class TestToolRateLimiterRedis:
    """Tests for the Redis path with mocked Redis."""

    def test_delegates_to_redis_when_available(self) -> None:
        limiter = ToolRateLimiter(redis_url="redis://localhost")
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=[1, 1, 0])
        limiter._redis = mock_redis

        async def run():
            return await limiter.check("my.tool", limit=10, window_seconds=60)

        result = anyio.run(run)
        assert result.allowed is True
        mock_redis.eval.assert_awaited_once()

    def test_falls_back_to_local_on_redis_error(self) -> None:
        limiter = ToolRateLimiter(redis_url="redis://localhost")
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(side_effect=ConnectionError("redis down"))
        limiter._redis = mock_redis

        async def run():
            return await limiter.check("my.tool", limit=10, window_seconds=60)

        result = anyio.run(run)
        # Falls back to local — still allowed
        assert result.allowed is True

    def test_redis_rate_limited_response(self) -> None:
        limiter = ToolRateLimiter(redis_url="redis://localhost")
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=[0, 10, 5])
        limiter._redis = mock_redis

        async def run():
            return await limiter.check("my.tool", limit=10, window_seconds=60)

        result = anyio.run(run)
        assert result.allowed is False
        assert result.retry_after == 5


# ══════════════════════════════════════════════════════════════════════════════
# 5. PII REDACTION
# ══════════════════════════════════════════════════════════════════════════════

class TestPiiRedactorStrings:
    def test_redacts_email(self) -> None:
        r = PiiRedactor()
        assert r.redact_string("contact user@example.com for info") == "contact [REDACTED] for info"

    def test_redacts_multiple_emails(self) -> None:
        r = PiiRedactor()
        result = r.redact_string("from: a@x.com, to: b@y.org")
        assert "a@x.com" not in result
        assert "b@y.org" not in result

    def test_redacts_international_phone(self) -> None:
        r = PiiRedactor()
        assert "[REDACTED]" in r.redact_string("Call +1 555-123-4567 now")

    def test_redacts_phone_with_country_code(self) -> None:
        r = PiiRedactor()
        assert "[REDACTED]" in r.redact_string("Kenya: +254 712 345 678")

    def test_does_not_redact_plain_numbers(self) -> None:
        """A plain 10-digit number without + prefix should NOT be redacted as phone."""
        r = PiiRedactor()
        result = r.redact_string("Order 1234567890 is ready")
        assert "1234567890" in result

    def test_redacts_ssn(self) -> None:
        r = PiiRedactor()
        assert "[REDACTED]" in r.redact_string("SSN: 123-45-6789")
        assert "123-45-6789" not in r.redact_string("SSN: 123-45-6789")

    def test_redacts_credit_card(self) -> None:
        r = PiiRedactor()
        result = r.redact_string("Card: 4111 1111 1111 1111")
        assert "4111" not in result

    def test_preserves_non_pii_text(self) -> None:
        r = PiiRedactor()
        text = "Hello, welcome to Ruhu support."
        assert r.redact_string(text) == text

    def test_empty_string(self) -> None:
        r = PiiRedactor()
        assert r.redact_string("") == ""


class TestPiiRedactorDict:
    def test_redacts_flat_dict_values(self) -> None:
        r = PiiRedactor()
        data = {"email": "user@example.com", "name": "John"}
        result = r.redact_dict(data)
        assert result["email"] == "[REDACTED]"
        assert result["name"] == "John"

    def test_redacts_nested_dict(self) -> None:
        r = PiiRedactor()
        data = {"customer": {"contact": "user@example.com", "id": 42}}
        result = r.redact_dict(data)
        assert result["customer"]["contact"] == "[REDACTED]"
        assert result["customer"]["id"] == 42

    def test_redacts_list_values(self) -> None:
        r = PiiRedactor()
        data = {"emails": ["a@x.com", "b@y.org", "no-pii"]}
        result = r.redact_dict(data)
        assert result["emails"][0] == "[REDACTED]"
        assert result["emails"][1] == "[REDACTED]"
        assert result["emails"][2] == "no-pii"

    def test_preserves_non_string_values(self) -> None:
        r = PiiRedactor()
        data = {"count": 42, "active": True, "tags": None}
        result = r.redact_dict(data)
        assert result == data

    def test_depth_limit_prevents_infinite_recursion(self) -> None:
        r = PiiRedactor()
        # Build a deeply nested dict
        data: dict = {"email": "user@example.com"}
        current = data
        for i in range(15):
            current["nested"] = {"email": f"deep{i}@example.com"}
            current = current["nested"]

        # Should not raise — depth limit stops recursion
        result = r.redact_dict(data)
        assert result["email"] == "[REDACTED]"

    def test_original_dict_not_mutated(self) -> None:
        r = PiiRedactor()
        data = {"email": "user@example.com"}
        r.redact_dict(data)
        assert data["email"] == "user@example.com"

    def test_custom_patterns(self) -> None:
        import re
        custom = [(re.compile(r"SECRET_\w+"), "***")]
        r = PiiRedactor(patterns=custom)
        result = r.redact_string("Token: SECRET_abc123")
        assert "SECRET_abc123" not in result
        assert "***" in result


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: Rate limiting in execute_async path
# ══════════════════════════════════════════════════════════════════════════════

class TestRuntimeRateLimitIntegration:
    def test_rate_limited_tool_returns_error(self) -> None:
        spec = _spec(executor_config={"rate_limit": 1, "rate_limit_window_seconds": 60})
        limiter = ToolRateLimiter()  # In-memory

        runtime = _runtime_with_handler(
            spec,
            lambda call, _s: {"ok": True},
            tool_rate_limiter=limiter,
        )

        async def run():
            # First call succeeds
            r1 = await runtime.execute_async(_call())
            # Second call should be rate limited
            r2 = await runtime.execute_async(_call())
            return r1, r2

        r1, r2 = anyio.run(run)
        assert r1.status == "success"
        assert r2.status == "error"
        assert r2.error == "tool_rate_limited"

    def test_no_rate_limit_config_skips_check(self) -> None:
        spec = _spec()  # No rate_limit in executor_config
        limiter = ToolRateLimiter()

        runtime = _runtime_with_handler(
            spec,
            lambda call, _s: {"ok": True},
            tool_rate_limiter=limiter,
        )

        async def run():
            r1 = await runtime.execute_async(_call())
            r2 = await runtime.execute_async(_call())
            return r1, r2

        r1, r2 = anyio.run(run)
        assert r1.status == "success"
        assert r2.status == "success"


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: PII redaction in execute_async path
# ══════════════════════════════════════════════════════════════════════════════

class TestRuntimePiiRedactionIntegration:
    def test_redaction_when_enabled(self) -> None:
        spec = _spec(executor_config={"redact_pii": True})
        runtime = _runtime_with_handler(
            spec,
            lambda call, _s: {"email": "user@example.com", "status": "active"},
        )

        async def run():
            return await runtime.execute_async(_call())

        result = anyio.run(run)
        assert result.status == "success"
        assert result.output["email"] == "[REDACTED]"
        assert result.output["status"] == "active"

    def test_no_redaction_when_disabled(self) -> None:
        spec = _spec()  # redact_pii not set
        runtime = _runtime_with_handler(
            spec,
            lambda call, _s: {"email": "user@example.com"},
        )

        async def run():
            return await runtime.execute_async(_call())

        result = anyio.run(run)
        assert result.output["email"] == "user@example.com"

    def test_redaction_not_applied_on_error(self) -> None:
        spec = _spec(executor_config={"redact_pii": True})

        def fail_handler(call, _s):
            raise RuntimeError("tool failed")

        runtime = _runtime_with_handler(spec, fail_handler)

        async def run():
            return await runtime.execute_async(_call())

        result = anyio.run(run)
        assert result.status == "error"
