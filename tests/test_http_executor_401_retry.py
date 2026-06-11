"""Tier 4: HTTP executor force-refreshes OAuth and retries once on 401.

When an outbound tool call hits 401 — usually because the access token
rotated mid-flight or expired between compile and execute — the executor
asks the runtime for a fresh token via ``on_unauthorized``, retries once
with the new bearer, and surfaces the second response. Without this hook
customers see cryptic 401s with no auto-recovery; with it the fast path
is invisible.

These tests pin five guarantees:

1. The 401 retry path fires only when ``on_unauthorized`` was supplied
   AND the executor_config carries a ``connection_id``.
2. Returning ``None`` from the callback means "couldn't refresh"; the
   executor surfaces the original 401 as-is (no infinite loop).
3. A successful retry overwrites the original 401 — caller sees only
   the post-retry result.
4. Single retry only — a second 401 is final, even if the callback
   would offer another refresh.
5. Non-401 statuses are NOT retried (200, 500, 429, etc. flow through
   the existing classification untouched).

Plus a higher-level test that ``OAuthFlowManager.force_refresh_sync``
actually rotates the connection's tokens against a real Postgres row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet

from ruhu.db import build_session_factory
from ruhu.db_models import APIConnectionRecord
from ruhu.tools import oauth as oauth_module
from ruhu.tools.executors.http import HttpExecutor
from ruhu.tools.management import APIConnectionStore, CredentialCipher
from ruhu.tools.oauth import OAuthFlowManager
from ruhu.tools.specs import ToolSpec
from ruhu.tools.types import ToolCall, ToolCaller


# ── Fake HTTP client that scripts a sequence of responses ───────────────


class _ScriptedClient:
    """Returns each scripted response in order, recording calls so we
    can assert which headers each attempt sent."""

    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, *, headers=None, params=None, json=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "params": params,
                "json": json,
            }
        )
        if not self._responses:
            raise AssertionError("no more scripted responses")
        status, body = self._responses.pop(0)
        return _ScriptedResponse(status, body)


class _ScriptedResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body


def _spec_with_connection(connection_id: str = "conn_1") -> ToolSpec:
    return ToolSpec(
        ref="x.tool",
        kind="http",
        display_name="x",
        description="Trigger an outbound demo request for executor unit tests.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        executor_config={
            "url": "https://example.com/v1/x",
            "method": "GET",
            "headers": {"Authorization": "Bearer stale-token"},
            "connection_id": connection_id,
            "organization_id": "org-A",
            "provider": "demo",
        },
    )


def _spec_no_connection() -> ToolSpec:
    return ToolSpec(
        ref="x.public",
        kind="http",
        display_name="x",
        description="Trigger an outbound demo request for executor unit tests.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        executor_config={
            "url": "https://example.com/v1/public",
            "method": "GET",
            "headers": {},
        },
    )


def _make_call() -> ToolCall:
    return ToolCall(
        invocation_id="inv_1",
        tool_ref="x.tool",
        args={},
        caller=ToolCaller(
            channel="web_chat",
            tenant_id="org-A",
            agent_id="agent-A",
            conversation_id="conv-A",
        ),
    )


# ── Executor-level retry semantics ──────────────────────────────────────


def test_executor_retries_once_on_401_and_uses_refreshed_headers() -> None:
    """Happy path: 401 → callback returns new headers → retry succeeds."""
    refresh_calls: list[dict[str, Any]] = []

    def on_unauthorized(request_config: dict[str, Any]) -> dict[str, str] | None:
        refresh_calls.append(dict(request_config))
        return {"Authorization": "Bearer fresh-token"}

    client = _ScriptedClient(
        [
            (401, {"error": "token expired"}),
            (200, {"ok": True, "data": "after-retry"}),
        ]
    )
    executor = HttpExecutor(client=client, on_unauthorized=on_unauthorized)
    result = executor.execute(_spec_with_connection(), _make_call())

    assert result.status == "success"
    assert result.output == {"ok": True, "data": "after-retry"}

    # Refresh was triggered exactly once with the request_config that carried
    # connection_id + organization_id.
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["connection_id"] == "conn_1"
    assert refresh_calls[0]["organization_id"] == "org-A"

    # Two HTTP calls: original (stale token) + retry (fresh token).
    assert len(client.calls) == 2
    assert client.calls[0]["headers"]["Authorization"] == "Bearer stale-token"
    assert client.calls[1]["headers"]["Authorization"] == "Bearer fresh-token"


def test_executor_does_not_retry_when_no_callback_supplied() -> None:
    """No ``on_unauthorized`` → 401 flows through unchanged. Backwards-
    compatible default for non-OAuth deployments."""
    client = _ScriptedClient([(401, {"error": "token expired"})])
    executor = HttpExecutor(client=client)  # no callback

    result = executor.execute(_spec_with_connection(), _make_call())

    assert result.status == "error"
    assert result.metadata["http_status"] == 401
    assert len(client.calls) == 1  # no retry


def test_executor_does_not_retry_when_no_connection_id() -> None:
    """Even with a callback, a spec without connection_id (public API
    or non-OAuth tool) cannot benefit from refresh — skip the hook."""
    callback_invocations: list[dict[str, Any]] = []

    def on_unauthorized(request_config: dict[str, Any]) -> dict[str, str] | None:
        callback_invocations.append(request_config)
        return {"Authorization": "Bearer fresh-token"}

    client = _ScriptedClient([(401, {"error": "unauthorized"})])
    executor = HttpExecutor(client=client, on_unauthorized=on_unauthorized)
    result = executor.execute(_spec_no_connection(), _make_call())

    assert result.status == "error"
    assert callback_invocations == []  # never called
    assert len(client.calls) == 1


def test_executor_surfaces_original_401_when_callback_returns_none() -> None:
    """Callback returning None means refresh failed (provider rejected,
    credentials missing). Executor surfaces the original 401 — caller
    sees ``http_status=401`` and can route to a 'reconnect' UI flow."""
    client = _ScriptedClient([(401, {"error": "token expired"})])
    executor = HttpExecutor(
        client=client,
        on_unauthorized=lambda _cfg: None,
    )

    result = executor.execute(_spec_with_connection(), _make_call())

    assert result.status == "error"
    assert result.metadata["http_status"] == 401
    assert len(client.calls) == 1  # no retry attempted after None


def test_executor_does_not_retry_more_than_once_on_repeated_401() -> None:
    """If the second attempt also returns 401, give up — no infinite
    loop. The token might be genuinely invalid (provider revoked it
    out-of-band); user must reconnect."""
    refresh_calls: list[dict[str, Any]] = []

    def on_unauthorized(request_config: dict[str, Any]) -> dict[str, str] | None:
        refresh_calls.append(dict(request_config))
        return {"Authorization": "Bearer also-bad"}

    client = _ScriptedClient(
        [
            (401, {"error": "token expired"}),
            (401, {"error": "token still bad"}),
        ]
    )
    executor = HttpExecutor(client=client, on_unauthorized=on_unauthorized)
    result = executor.execute(_spec_with_connection(), _make_call())

    assert result.status == "error"
    assert result.metadata["http_status"] == 401
    assert len(client.calls) == 2  # original + one retry, not three
    assert len(refresh_calls) == 1


def test_executor_does_not_retry_non_401_failures() -> None:
    """500, 429, 400 etc. are NOT auth problems — fall through to the
    existing classification path so transient-vs-permanent semantics
    stay unchanged."""

    def fail_callback(_cfg):
        raise AssertionError("on_unauthorized must not run for non-401")

    for status in (200, 400, 403, 429, 500, 502):
        client = _ScriptedClient([(status, {"x": 1})])
        executor = HttpExecutor(client=client, on_unauthorized=fail_callback)
        executor.execute(_spec_with_connection(), _make_call())
        assert len(client.calls) == 1, f"status {status} should not retry"


def test_executor_set_on_unauthorized_late_binding() -> None:
    """The wiring layer attaches the callback after construction (the
    OAuth manager is built later than the tool runtime). Verify the
    setter actually engages the retry path."""
    client = _ScriptedClient(
        [
            (401, {"error": "expired"}),
            (200, {"ok": True}),
        ]
    )
    executor = HttpExecutor(client=client)  # constructed without callback

    # Late-bind it.
    executor.set_on_unauthorized(lambda _cfg: {"Authorization": "Bearer fresh"})

    result = executor.execute(_spec_with_connection(), _make_call())
    assert result.status == "success"
    assert len(client.calls) == 2


# ── force_refresh_sync against a real DB ────────────────────────────────


def test_force_refresh_sync_rotates_tokens_in_db(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    """End-to-end: ``force_refresh_sync`` calls the token endpoint
    synchronously, persists new tokens to the connection row, and
    returns the new payload."""
    sf = build_session_factory(postgres_database_url_factory())
    store = APIConnectionStore(sf, blob_cipher=credential_cipher)
    record = store.create(
        organization_id="org-A",
        display_name="hubspot",
        provider="hubspot",
        auth_type="oauth2",
        oauth_token={
            "access_token": "old-atk",
            "refresh_token": "rtk",
            "expires_in": 3600,
        },
    )
    with sf.begin() as session:
        row = session.get(APIConnectionRecord, record.connection_id)
        assert row is not None
        row.token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)

    # Capture the body the sync fetch sends; return a new token payload.
    captured: dict[str, Any] = {}

    def _mock_fetch_token_sync(**kwargs):
        captured.update(kwargs)
        return {
            "access_token": "rotated-atk",
            "refresh_token": "rotated-rtk",
            "expires_in": 3600,
        }

    monkeypatch.setattr(oauth_module, "_fetch_token_sync", _mock_fetch_token_sync)

    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )

    new = manager.force_refresh_sync(
        connection_id=record.connection_id,
        organization_id=record.organization_id,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    assert new is not None
    assert new["access_token"] == "rotated-atk"

    # The stored row reflects the rotation.
    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.oauth_token_json["access_token"] == "rotated-atk"
        assert after.refresh_failure_count == 0  # _persist_tokens reset


def test_force_refresh_sync_returns_none_on_unknown_connection(
    postgres_database_url_factory, credential_cipher
) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )
    result = manager.force_refresh_sync(
        connection_id="conn-nope",
        organization_id="org-A",
        get_credentials=lambda _provider: ("cid", "csecret"),
    )
    assert result is None


def test_force_refresh_sync_returns_none_when_provider_rejects(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    """A 401/invalid_grant from the provider during force-refresh must
    return None so the executor can surface the original 401 to the
    caller (caller decides 'reconnect' UX). The background refresher
    will mark requires_reauth on its next tick."""
    sf = build_session_factory(postgres_database_url_factory())
    store = APIConnectionStore(sf, blob_cipher=credential_cipher)
    record = store.create(
        organization_id="org-A",
        display_name="hubspot",
        provider="hubspot",
        auth_type="oauth2",
        oauth_token={
            "access_token": "atk",
            "refresh_token": "rtk",
            "expires_in": 3600,
        },
    )

    def _mock_fetch_token_sync(**kwargs):
        raise ValueError(
            'token endpoint returned 400: {"error":"invalid_grant"}'
        )

    monkeypatch.setattr(oauth_module, "_fetch_token_sync", _mock_fetch_token_sync)

    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )

    new = manager.force_refresh_sync(
        connection_id=record.connection_id,
        organization_id=record.organization_id,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    assert new is None
    # The stored token is unchanged — force_refresh_sync doesn't mutate
    # the row on failure. The background refresher handles classification.
    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.oauth_token_json["access_token"] == "atk"
