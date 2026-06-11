"""
Tests for the Ruhu Audit System.

Covers:
  1. Events — model, hash computation, hash chain, event type classification
  2. Store — InMemoryAuditStore CRUD, filtering, ordering
  3. Router — sync vs async routing, hash chain maintenance, queue full handling
  4. Middleware — mutating capture, skip paths, outcome mapping, non-HTTP passthrough
  5. Flusher — batch drain, stop event, error tolerance
  6. Emitter — emit_audit_event() public API
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from ruhu.audit.events import (
    AUTH_LOGIN,
    AUTH_LOGIN_FAILED,
    RESOURCE_CREATED,
    RESOURCE_DELETED,
    RESOURCE_UPDATED,
    SECURITY_PERMISSION_DENIED,
    AuditEvent,
    method_to_event_type,
    operation_from_event_type,
    path_to_resource_type,
    redact_sensitive,
    requires_sync_write,
)
from ruhu.audit.store import InMemoryAuditStore
from ruhu.audit.router import AuditEventRouter
from ruhu.audit.flusher import run_audit_flusher
from ruhu.audit.emitter import emit_audit_event
from ruhu.audit.middleware import AuditMiddleware


# ══════════════════════════════════════════════════════════════════════════════
# 1. EVENTS — model, hashing, classification
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditEvent:
    def test_event_id_auto_generated(self) -> None:
        e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        assert e.event_id
        assert len(e.event_id) == 36  # UUID format

    def test_operation_derived_from_event_type(self) -> None:
        assert AuditEvent(event_type=RESOURCE_CREATED, organization_id="o").operation == "create"
        assert AuditEvent(event_type=RESOURCE_UPDATED, organization_id="o").operation == "update"
        assert AuditEvent(event_type=RESOURCE_DELETED, organization_id="o").operation == "delete"
        assert AuditEvent(event_type=AUTH_LOGIN, organization_id="o").operation == "auth"
        assert AuditEvent(event_type=SECURITY_PERMISSION_DENIED, organization_id="o").operation == "security"

    def test_created_at_auto_set(self) -> None:
        e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        assert e.created_at.endswith("Z")

    def test_to_dict_includes_all_fields(self) -> None:
        e = AuditEvent(
            event_type=RESOURCE_UPDATED,
            organization_id="org-1",
            actor_id="user-1",
            resource_type="agent",
            resource_id="agent-123",
            detail={"changes": {"name": {"old": "v1", "new": "v2"}}},
        )
        d = e.to_dict()
        assert d["event_type"] == RESOURCE_UPDATED
        assert d["organization_id"] == "org-1"
        assert d["actor_id"] == "user-1"
        assert d["resource_type"] == "agent"
        assert d["resource_id"] == "agent-123"
        assert d["detail"]["changes"]["name"]["new"] == "v2"
        assert "event_id" in d
        assert "created_at" in d

    def test_default_outcome_is_success(self) -> None:
        e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="o")
        assert e.outcome == "success"


class TestContentHash:
    def test_deterministic(self) -> None:
        e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1", event_id="fixed-id")
        h1 = e.compute_hash()
        h2 = e.compute_hash()
        assert h1 == h2

    def test_different_events_different_hashes(self) -> None:
        e1 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e2 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        assert e1.compute_hash() != e2.compute_hash()  # Different event_ids

    def test_hash_covers_detail(self) -> None:
        """Modifying detail must change the hash."""
        e1 = AuditEvent(event_type=RESOURCE_UPDATED, organization_id="org-1", event_id="x", detail={"a": 1})
        e2 = AuditEvent(event_type=RESOURCE_UPDATED, organization_id="org-1", event_id="x", detail={"a": 2})
        # Force same created_at
        e2.created_at = e1.created_at
        assert e1.compute_hash() != e2.compute_hash()

    def test_hash_is_64_hex_chars(self) -> None:
        e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        h = e.compute_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_finalize_sets_hash_and_prev(self) -> None:
        e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e.finalize(prev_hash="abc123")
        assert e.content_hash
        assert e.prev_hash == "abc123"

    def test_finalize_with_none_prev(self) -> None:
        e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e.finalize(prev_hash=None)
        assert e.content_hash
        assert e.prev_hash is None


class TestHashChain:
    def test_chain_links_events(self) -> None:
        e1 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e1.finalize(prev_hash=None)

        e2 = AuditEvent(event_type=RESOURCE_UPDATED, organization_id="org-1")
        e2.finalize(prev_hash=e1.content_hash)

        assert e2.prev_hash == e1.content_hash
        assert e2.content_hash != e1.content_hash

    def test_modification_breaks_chain(self) -> None:
        e1 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e1.finalize(prev_hash=None)
        original_hash = e1.content_hash

        # Tamper with the event
        e1.actor_id = "attacker"
        assert e1.compute_hash() != original_hash


class TestEventTypeClassification:
    def test_sync_write_for_auth_events(self) -> None:
        assert requires_sync_write(AUTH_LOGIN) is True
        assert requires_sync_write(AUTH_LOGIN_FAILED) is True

    def test_sync_write_for_security_events(self) -> None:
        assert requires_sync_write(SECURITY_PERMISSION_DENIED) is True

    def test_async_write_for_resource_events(self) -> None:
        assert requires_sync_write(RESOURCE_CREATED) is False
        assert requires_sync_write(RESOURCE_UPDATED) is False

    def test_operation_from_event_type(self) -> None:
        assert operation_from_event_type(RESOURCE_CREATED) == "create"
        assert operation_from_event_type(AUTH_LOGIN) == "auth"
        assert operation_from_event_type(SECURITY_PERMISSION_DENIED) == "security"


class TestSensitiveRedaction:
    def test_redacts_password(self) -> None:
        assert redact_sensitive({"password": "secret", "name": "Alice"}) == {"password": "***", "name": "Alice"}

    def test_redacts_nested(self) -> None:
        result = redact_sensitive({"user": {"api_key": "abc", "role": "admin"}})
        assert result["user"]["api_key"] == "***"
        assert result["user"]["role"] == "admin"

    def test_case_insensitive(self) -> None:
        assert redact_sensitive({"PASSWORD": "x"}) == {"PASSWORD": "***"}

    def test_depth_limit(self) -> None:
        obj: dict = {"secret": "deep"}
        for _ in range(6):
            obj = {"n": obj}
        result = redact_sensitive(obj)
        assert isinstance(result, dict)

    def test_non_dict(self) -> None:
        assert redact_sensitive(42) == 42
        assert redact_sensitive("hello") == "hello"


class TestPathHelpers:
    def test_path_to_resource_type(self) -> None:
        assert path_to_resource_type("/agents/123") == "agents"
        assert path_to_resource_type("/conversations") == "conversations"
        assert path_to_resource_type("/") == "unknown"

    def test_method_to_event_type(self) -> None:
        assert method_to_event_type("POST") == RESOURCE_CREATED
        assert method_to_event_type("PATCH") == RESOURCE_UPDATED
        assert method_to_event_type("DELETE") == RESOURCE_DELETED


# ══════════════════════════════════════════════════════════════════════════════
# 2. STORE — InMemoryAuditStore
# ══════════════════════════════════════════════════════════════════════════════

class TestInMemoryAuditStore:
    def test_save_and_get(self) -> None:
        store = InMemoryAuditStore()
        e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e.finalize()
        store.save(e)
        loaded = store.get(e.event_id, organization_id="org-1")
        assert loaded is not None
        assert loaded.event_id == e.event_id

    def test_get_enforces_org_scope(self) -> None:
        store = InMemoryAuditStore()
        e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e.finalize()
        store.save(e)
        assert store.get(e.event_id, organization_id="org-2") is None

    def test_save_batch(self) -> None:
        store = InMemoryAuditStore()
        events = [AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1") for _ in range(3)]
        for ev in events:
            ev.finalize()
        store.save_batch(events)
        assert len(store.list_events(organization_id="org-1")) == 3

    def test_list_filters_by_event_type(self) -> None:
        store = InMemoryAuditStore()
        e1 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e2 = AuditEvent(event_type=RESOURCE_DELETED, organization_id="org-1")
        e1.finalize()
        e2.finalize()
        store.save(e1)
        store.save(e2)
        result = store.list_events(organization_id="org-1", event_type=RESOURCE_CREATED)
        assert len(result) == 1
        assert result[0].event_type == RESOURCE_CREATED

    def test_list_filters_by_resource(self) -> None:
        store = InMemoryAuditStore()
        e1 = AuditEvent(event_type=RESOURCE_UPDATED, organization_id="org-1", resource_type="agent", resource_id="agent-1")
        e2 = AuditEvent(event_type=RESOURCE_UPDATED, organization_id="org-1", resource_type="agent", resource_id="agent-2")
        e1.finalize()
        e2.finalize()
        store.save(e1)
        store.save(e2)
        result = store.list_events(organization_id="org-1", resource_id="agent-1")
        assert len(result) == 1

    def test_list_filters_by_actor(self) -> None:
        store = InMemoryAuditStore()
        e1 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1", actor_id="u-1")
        e2 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1", actor_id="u-2")
        e1.finalize()
        e2.finalize()
        store.save(e1)
        store.save(e2)
        result = store.list_events(organization_id="org-1", actor_id="u-1")
        assert len(result) == 1

    def test_list_sorted_desc_by_created_at(self) -> None:
        store = InMemoryAuditStore()
        e1 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e1.created_at = "2026-01-01T00:00:00Z"
        e2 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e2.created_at = "2026-01-02T00:00:00Z"
        e1.finalize()
        e2.finalize()
        store.save(e1)
        store.save(e2)
        result = store.list_events(organization_id="org-1")
        assert result[0].created_at > result[1].created_at

    def test_list_with_limit_and_offset(self) -> None:
        store = InMemoryAuditStore()
        for i in range(5):
            e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
            e.finalize()
            store.save(e)
        result = store.list_events(organization_id="org-1", limit=2, offset=1)
        assert len(result) == 2

    def test_get_latest_hash(self) -> None:
        store = InMemoryAuditStore()
        e1 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        e1.created_at = "2026-01-01T00:00:00Z"
        e1.finalize()
        store.save(e1)

        e2 = AuditEvent(event_type=RESOURCE_UPDATED, organization_id="org-1")
        e2.created_at = "2026-01-02T00:00:00Z"
        e2.finalize(prev_hash=e1.content_hash)
        store.save(e2)

        assert store.get_latest_hash("org-1") == e2.content_hash
        assert store.get_latest_hash("org-nonexistent") is None

    def test_count_events(self) -> None:
        store = InMemoryAuditStore()
        for _ in range(3):
            e = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
            e.finalize()
            store.save(e)
        e_fail = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1", outcome="failure")
        e_fail.finalize()
        store.save(e_fail)
        assert store.count_events(organization_id="org-1") == 4
        assert store.count_events(organization_id="org-1", outcome="failure") == 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. ROUTER — sync vs async, hash chain, queue full
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditEventRouter:
    def _make_router(self, queue_size: int = 1000) -> tuple[AuditEventRouter, InMemoryAuditStore]:
        store = InMemoryAuditStore()
        queue = asyncio.Queue(maxsize=queue_size)
        router = AuditEventRouter(store=store, queue=queue)
        return router, store

    def test_security_event_written_sync(self) -> None:
        router, store = self._make_router()
        event = AuditEvent(event_type=AUTH_LOGIN, organization_id="org-1", actor_id="u-1")
        router.route(event)
        # Sync write — should be immediately in the store
        assert store.get(event.event_id, organization_id="org-1") is not None

    def test_operational_event_enqueued_async(self) -> None:
        store = InMemoryAuditStore()
        queue = asyncio.Queue(maxsize=100)
        router = AuditEventRouter(store=store, queue=queue)
        event = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        router.route(event)
        # Async — should be in queue, not in store
        assert store.get(event.event_id, organization_id="org-1") is None
        assert queue.qsize() == 1

    def test_hash_chain_maintained(self) -> None:
        router, store = self._make_router()
        e1 = AuditEvent(event_type=AUTH_LOGIN, organization_id="org-1")
        e2 = AuditEvent(event_type=AUTH_LOGIN, organization_id="org-1")
        router.route(e1)
        router.route(e2)
        assert e1.prev_hash is None  # First in chain
        assert e2.prev_hash == e1.content_hash

    def test_hash_chain_scoped_per_org(self) -> None:
        router, store = self._make_router()
        e1 = AuditEvent(event_type=AUTH_LOGIN, organization_id="org-1")
        e2 = AuditEvent(event_type=AUTH_LOGIN, organization_id="org-2")
        router.route(e1)
        router.route(e2)
        # e2 is in a different org — its prev_hash should be None, not e1's hash
        assert e2.prev_hash is None

    def test_disabled_router_does_not_write(self) -> None:
        store = InMemoryAuditStore()
        queue = asyncio.Queue(maxsize=100)
        router = AuditEventRouter(store=store, queue=queue, enabled=False)
        event = AuditEvent(event_type=AUTH_LOGIN, organization_id="org-1")
        router.route(event)
        assert store.get(event.event_id, organization_id="org-1") is None
        assert queue.empty()

    def test_queue_full_drops_and_counts(self) -> None:
        store = InMemoryAuditStore()
        queue = asyncio.Queue(maxsize=1)
        router = AuditEventRouter(store=store, queue=queue)
        # Fill queue
        e1 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        router.route(e1)
        assert queue.qsize() == 1
        # Next one should be dropped
        e2 = AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")
        router.route(e2)  # Should not raise
        assert queue.qsize() == 1  # Still 1


# ══════════════════════════════════════════════════════════════════════════════
# 4. MIDDLEWARE — ASGI capture
# ══════════════════════════════════════════════════════════════════════════════

def _make_scope(method: str = "POST", path: str = "/agents") -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }


def _make_asgi_app(status_code: int = 200):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status_code, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    return app


async def _async_receive():
    return {"type": "http.request", "body": b""}


async def _async_noop_send(msg: dict) -> None:
    pass


class TestAuditMiddleware:
    def test_non_http_passes_through(self) -> None:
        router = MagicMock()
        middleware = AuditMiddleware(_make_asgi_app(), router=router)
        received = []

        async def run():
            async def app(scope, receive, send):
                received.append(scope)
            mw = AuditMiddleware(app, router=router)
            await mw({"type": "websocket", "path": "/ws"}, None, None)

        anyio.run(run)
        assert len(received) == 1
        router.route.assert_not_called()

    def test_get_not_captured(self) -> None:
        router = MagicMock()
        middleware = AuditMiddleware(_make_asgi_app(), router=router)

        async def run():
            scope = _make_scope("GET", "/agents")
            await middleware(scope, _async_receive, _async_noop_send)

        anyio.run(run)
        router.route.assert_not_called()

    def test_skip_path_not_captured(self) -> None:
        router = MagicMock()
        middleware = AuditMiddleware(_make_asgi_app(), router=router)

        async def run():
            scope = _make_scope("POST", "/health")
            messages = []
            await middleware(scope, _async_receive, _async_noop_send)

        anyio.run(run)
        router.route.assert_not_called()

    def test_post_captured_with_correct_fields(self) -> None:
        captured_events = []
        router = MagicMock()
        router.route = lambda e: captured_events.append(e)

        # Build scope with auth context
        mock_state = MagicMock()
        mock_state.auth_context = MagicMock()
        mock_state.auth_context.principal = MagicMock()
        mock_state.auth_context.principal.organization = MagicMock()
        mock_state.auth_context.principal.organization.organization_id = "org-1"
        mock_state.auth_context.principal.user = MagicMock()
        mock_state.auth_context.principal.user.user_id = "user-1"
        mock_state.auth_context.principal.session = MagicMock()
        mock_state.auth_context.principal.session.session_id = "sess-1"

        scope = _make_scope("POST", "/agents")
        scope["state"] = mock_state

        async def run():
            middleware = AuditMiddleware(_make_asgi_app(201), router=router)
            messages = []
            await middleware(scope, _async_receive, _async_noop_send)

        anyio.run(run)
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.http_method == "POST"
        assert event.http_path == "/agents"
        assert event.http_status == 201
        assert event.outcome == "success"
        assert event.organization_id == "org-1"
        assert event.actor_id == "user-1"
        assert event.resource_type == "agents"
        assert event.duration_ms is not None

    def test_403_outcome_is_denied(self) -> None:
        captured = []
        router = MagicMock()
        router.route = lambda e: captured.append(e)

        mock_state = MagicMock()
        mock_state.auth_context = MagicMock()
        mock_state.auth_context.principal = MagicMock()
        mock_state.auth_context.principal.organization = MagicMock()
        mock_state.auth_context.principal.organization.organization_id = "org-1"
        mock_state.auth_context.principal.user = MagicMock()
        mock_state.auth_context.principal.user.user_id = "u-1"
        mock_state.auth_context.principal.session = MagicMock()
        mock_state.auth_context.principal.session.session_id = "s-1"

        scope = _make_scope("DELETE", "/agents/abc")
        scope["state"] = mock_state

        async def run():
            middleware = AuditMiddleware(_make_asgi_app(403), router=router)
            messages = []
            await middleware(scope, _async_receive, _async_noop_send)

        anyio.run(run)
        assert captured[0].outcome == "denied"

    def test_no_org_context_skips_event(self) -> None:
        router = MagicMock()
        middleware = AuditMiddleware(_make_asgi_app(), router=router)

        async def run():
            scope = _make_scope("POST", "/agents")
            # No auth context set
            messages = []
            await middleware(scope, _async_receive, _async_noop_send)

        anyio.run(run)
        router.route.assert_not_called()

    def test_crash_still_emits_event(self) -> None:
        captured = []
        router = MagicMock()
        router.route = lambda e: captured.append(e)

        async def crashing_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 500, "headers": []})
            raise RuntimeError("boom")

        mock_state = MagicMock()
        mock_state.auth_context = MagicMock()
        mock_state.auth_context.principal = MagicMock()
        mock_state.auth_context.principal.organization = MagicMock()
        mock_state.auth_context.principal.organization.organization_id = "org-1"
        mock_state.auth_context.principal.user = MagicMock()
        mock_state.auth_context.principal.user.user_id = "u-1"
        mock_state.auth_context.principal.session = MagicMock()
        mock_state.auth_context.principal.session.session_id = "s-1"

        scope = _make_scope("POST", "/agents")
        scope["state"] = mock_state

        async def run():
            middleware = AuditMiddleware(crashing_app, router=router)
            with pytest.raises(RuntimeError):
                async def noop(msg):
                    pass
                await middleware(scope, _async_receive, noop)

        anyio.run(run)
        assert len(captured) == 1
        assert captured[0].http_status == 500


# ══════════════════════════════════════════════════════════════════════════════
# 5. FLUSHER — batch drain to store
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditFlusher:
    def _run_flusher(self, events: list[AuditEvent], store: InMemoryAuditStore, **kwargs) -> None:
        async def run():
            queue: asyncio.Queue = asyncio.Queue()
            stop = asyncio.Event()
            for e in events:
                await queue.put(e)
            task = asyncio.create_task(
                run_audit_flusher(
                    queue,
                    store,
                    stop_event=stop,
                    batch_size=kwargs.get("batch_size", 10),
                    flush_interval=kwargs.get("flush_interval", 0.02),
                )
            )
            await asyncio.sleep(0.08)
            stop.set()
            await task

        anyio.run(run)

    def test_flushes_events_to_store(self) -> None:
        store = InMemoryAuditStore()
        events = [AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1") for _ in range(3)]
        for e in events:
            e.finalize()
        self._run_flusher(events, store)
        assert store.count_events(organization_id="org-1") == 3

    def test_empty_queue_does_not_error(self) -> None:
        store = InMemoryAuditStore()
        self._run_flusher([], store)
        assert store.count_events(organization_id="org-1") == 0

    def test_store_error_is_tolerated(self) -> None:
        store = MagicMock()
        store.save_batch = MagicMock(side_effect=ConnectionError("db down"))
        events = [AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1")]
        events[0].finalize()
        # Should not raise
        self._run_flusher(events, store)

    def test_stop_event_drains_remaining(self) -> None:
        store = InMemoryAuditStore()
        events = [AuditEvent(event_type=RESOURCE_CREATED, organization_id="org-1") for _ in range(5)]
        for e in events:
            e.finalize()
        self._run_flusher(events, store)
        assert store.count_events(organization_id="org-1") == 5


# ══════════════════════════════════════════════════════════════════════════════
# 6. EMITTER — public API
# ══════════════════════════════════════════════════════════════════════════════

class TestEmitAuditEvent:
    def test_emits_through_router(self) -> None:
        store = InMemoryAuditStore()
        queue = asyncio.Queue()
        router = AuditEventRouter(store=store, queue=queue)

        emit_audit_event(
            router,
            event_type=AUTH_LOGIN,
            organization_id="org-1",
            actor_id="user-1",
            actor_ip="1.2.3.4",
        )

        # auth.login is sync — should be in store immediately
        events = store.list_events(organization_id="org-1")
        assert len(events) == 1
        assert events[0].event_type == AUTH_LOGIN
        assert events[0].actor_id == "user-1"
        assert events[0].actor_ip == "1.2.3.4"

    def test_emits_with_detail(self) -> None:
        store = InMemoryAuditStore()
        queue = asyncio.Queue()
        router = AuditEventRouter(store=store, queue=queue)

        emit_audit_event(
            router,
            event_type=AUTH_LOGIN_FAILED,
            organization_id="org-1",
            actor_id="user-1",
            outcome="failure",
            detail={"reason": "invalid_password", "attempts": 3},
        )

        events = store.list_events(organization_id="org-1")
        assert events[0].outcome == "failure"
        assert events[0].detail["reason"] == "invalid_password"

    def test_tolerates_router_error(self) -> None:
        router = MagicMock()
        router.route = MagicMock(side_effect=RuntimeError("router broken"))
        # Should not raise
        emit_audit_event(
            router,
            event_type=AUTH_LOGIN,
            organization_id="org-1",
        )

    def test_operational_event_goes_to_queue(self) -> None:
        store = InMemoryAuditStore()
        queue = asyncio.Queue()
        router = AuditEventRouter(store=store, queue=queue)

        emit_audit_event(
            router,
            event_type=RESOURCE_UPDATED,
            organization_id="org-1",
            resource_type="agent",
            resource_id="agent-123",
            detail={"changes": {"name": {"old": "v1", "new": "v2"}}},
        )

        # Operational — should be in queue, not store
        assert store.count_events(organization_id="org-1") == 0
        assert queue.qsize() == 1
