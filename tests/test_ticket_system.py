from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from ruhu.api import build_default_app
from ruhu.auth import AuthService
from ruhu.db import build_session_factory
from tests._fixtures.templates import load_template_agent_document
from ruhu.db_models import (
    ConversationRecord,
    RealtimeEventRecord,
    RealtimeSessionRecord,
    TicketingActivityRecord,
    TicketingConnectionRecord,
)
from ruhu.identity import Organization, OrganizationMembership, User
from ruhu.identity_sqlalchemy import SQLAlchemyIdentityStore
from ruhu.runtime_config import RuntimeSettings
from ruhu.session_http import ACCESS_TOKEN_COOKIE_NAME, REFRESH_TOKEN_COOKIE_NAME
from ruhu.ticket_system import TicketSystemService
from ruhu.ticketing_worker import TicketingRetryWorker
from ruhu.ticketing_providers import ProviderConnectionConfig, RemoteCase, TicketingProviderError, WebhookSyncResult


TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


@dataclass
class FakeTicketingHarness:
    remote_cases: dict[str, RemoteCase]
    failures: dict[str, int]


def _install_fake_ticketing_service(monkeypatch) -> FakeTicketingHarness:
    harness = FakeTicketingHarness(remote_cases={}, failures={})

    class FakeTicketingAdapter:
        def __init__(self, config: ProviderConnectionConfig) -> None:
            self._config = config

        def _maybe_fail(self, action: str) -> None:
            remaining = int(harness.failures.get(action, 0) or 0)
            if remaining <= 0:
                return
            harness.failures[action] = remaining - 1
            raise TicketingProviderError(
                f"temporary {action} failure",
                provider=self._config.provider,
                status_code=503,
                retryable=True,
            )

        def health_check(self) -> dict[str, object]:
            self._maybe_fail("health_check")
            if not self._config.credentials_ref:
                raise TicketingProviderError("missing credentials_ref", provider=self._config.provider, status_code=400)
            return {"provider": self._config.provider, "status": "ok"}

        def create_case(
            self,
            *,
            title: str,
            description: str,
            priority: str | None,
            status: str | None,
            participant_email: str | None,
            participant_display: str | None,
            tags: list[str] | None,
            metadata: dict[str, object] | None,
        ) -> RemoteCase:
            del participant_email, participant_display
            self._maybe_fail("create_case")
            case_id = f"{self._config.provider}-remote-{len(harness.remote_cases) + 1}"
            remote = RemoteCase(
                external_case_id=case_id,
                external_case_key=case_id.upper(),
                external_case_url=f"https://tickets.example.com/{case_id}",
                external_case_status=status or "open",
                external_case_priority=priority or "medium",
                payload={
                    "title": title,
                    "description": description,
                    "tags": list(tags or []),
                    "metadata": dict(metadata or {}),
                },
            )
            harness.remote_cases[case_id] = remote
            return remote

        def fetch_case(self, external_case_id: str) -> RemoteCase | None:
            self._maybe_fail("fetch_case")
            return harness.remote_cases.get(external_case_id)

        def search_cases(self, *, query: str, limit: int = 20) -> list[RemoteCase]:
            matches = [
                case
                for case in harness.remote_cases.values()
                if query.lower() in case.external_case_id.lower()
                or query.lower() in str(case.payload.get("title") or "").lower()
            ]
            return matches[:limit]

        def add_comment(self, *, external_case_id: str, body: str, visibility: str) -> dict[str, object]:
            self._maybe_fail("add_comment")
            if external_case_id not in harness.remote_cases:
                raise TicketingProviderError("unknown remote case", provider=self._config.provider, status_code=404)
            return {"commented": True, "visibility": visibility, "body": body}

        def transition_case(self, *, external_case_id: str, status_value: str) -> RemoteCase:
            self._maybe_fail("transition_case")
            remote = harness.remote_cases.get(external_case_id)
            if remote is None:
                raise TicketingProviderError("unknown remote case", provider=self._config.provider, status_code=404)
            updated = replace(remote, external_case_status=status_value, payload={**remote.payload, "status": status_value})
            harness.remote_cases[external_case_id] = updated
            return updated

        def parse_webhook(self, *, payload: dict[str, object], headers: dict[str, str] | None = None) -> WebhookSyncResult:
            del headers
            case_id = str(payload.get("external_case_id") or "").strip()
            if not case_id:
                raise TicketingProviderError("missing external_case_id", provider=self._config.provider, status_code=400)
            remote = harness.remote_cases.get(case_id)
            status_value = str(payload.get("status") or (None if remote is None else remote.external_case_status) or "updated")
            if remote is not None:
                remote = replace(remote, external_case_status=status_value, payload={**remote.payload, **payload})
                harness.remote_cases[case_id] = remote
            return WebhookSyncResult(
                event_type=str(payload.get("event_type") or "case_updated"),
                external_case_id=case_id,
                external_case_key=None if remote is None else remote.external_case_key,
                external_case_url=None if remote is None else remote.external_case_url,
                external_case_status=status_value,
                external_case_priority=None if remote is None else remote.external_case_priority,
                payload_snapshot=dict(payload),
                comments=[],
            )

    def fake_builder(config: ProviderConnectionConfig) -> FakeTicketingAdapter:
        return FakeTicketingAdapter(config)

    monkeypatch.setattr(
        "ruhu.api.TicketSystemService",
        lambda session_factory: TicketSystemService(session_factory, adapter_builder=fake_builder),
    )
    return harness


def _seed_identity_store(store: SQLAlchemyIdentityStore) -> None:
    admin = store.save_user(
        User(
            user_id="user-admin",
            email="admin@example.com",
            display_name="Admin",
        )
    )
    analyst = store.save_user(
        User(
            user_id="user-analyst",
            email="analyst@example.com",
            display_name="Analyst",
        )
    )
    store.save_organization(
        Organization(
            organization_id="org-1",
            slug="acme",
            name="Acme Voice",
        )
    )
    store.add_organization_membership(
        OrganizationMembership(
            user_id=admin.user_id,
            organization_id="org-1",
            role="admin",
            is_account_owner=True,
        )
    )
    store.add_organization_membership(
        OrganizationMembership(
            user_id=analyst.user_id,
            organization_id="org-1",
            role="analyst",
        )
    )


def _authorize_client(
    client: httpx.AsyncClient,
    *,
    auth_service: AuthService,
    user_id: str,
    organization_id: str,
) -> None:
    issued = auth_service.issue_browser_session(
        user_id=user_id,
        organization_id=organization_id,
    )
    client.headers["Authorization"] = f"Bearer {issued.access_token}"
    client.cookies.set(ACCESS_TOKEN_COOKIE_NAME, issued.access_token)
    client.cookies.set(REFRESH_TOKEN_COOKIE_NAME, issued.refresh_token)


def test_tickets_page_renders_with_workspace_link(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        _seed_identity_store(SQLAlchemyIdentityStore(build_session_factory(auth_database_url)))

        app = build_default_app(
            agent_root=agent_root,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )
            response = await client.get("/tickets")
            assert response.status_code == 200
            assert "Tickets" in response.text
            assert "Recent conversations handled by your agents." in response.text
            assert "/api/tickets/dashboard" in response.text
            assert "Ticketing Connections" in response.text
            assert "Transcript" in response.text
            assert "Create support case" in response.text

            workspace = await client.get("/app")
            assert workspace.status_code == 200
            assert "Open Tickets" in workspace.text

    asyncio.run(run())

def test_support_case_and_external_ticketing_lifecycle(monkeypatch, postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        harness = _install_fake_ticketing_service(monkeypatch)
        _seed_identity_store(SQLAlchemyIdentityStore(build_session_factory(auth_database_url)))

        app = build_default_app(
            agent_root=agent_root,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
            runtime_settings=RuntimeSettings(
                database_url=runtime_database_url,
                auth_database_url=auth_database_url,
                auth_jwt_secret=TEST_HS256_SECRET,
                provider_shared_secret="provider-secret",
            ),
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            created_case = await client.post(
                "/support-cases",
                json={
                    "title": "Escalate callback issue",
                    "description": "Customer did not receive callback in promised window.",
                    "priority": "high",
                    "category": "callback",
                    "source": "api",
                    "tags": ["ops"],
                },
            )
            assert created_case.status_code == 200
            case_id = created_case.json()["case_id"]

            updated_case = await client.patch(
                f"/support-cases/{case_id}",
                json={
                    "status": "in_progress",
                    "assigned_team": "support-ops",
                    "participant_email": "customer@example.com",
                },
            )
            assert updated_case.status_code == 200
            assert updated_case.json()["status"] == "in_progress"
            assert updated_case.json()["assigned_team"] == "support-ops"

            note = await client.post(
                f"/support-cases/{case_id}/notes",
                json={"body": "Investigating callback logs now.", "visibility": "internal"},
            )
            assert note.status_code == 200
            notes = await client.get(f"/support-cases/{case_id}/notes")
            assert notes.status_code == 200
            assert notes.json()[0]["body"] == "Investigating callback logs now."
            events = await client.get(f"/support-cases/{case_id}/events")
            assert events.status_code == 200
            assert events.json()[0]["event_type"] == "case_created"

            resolved = await client.post(
                f"/support-cases/{case_id}/resolve",
                json={
                    "resolution_type": "callback_completed",
                    "summary": "Callback completed and customer confirmed.",
                    "requires_follow_up": False,
                },
            )
            assert resolved.status_code == 200
            assert resolved.json()["status"] == "resolved"

            closed = await client.post(f"/support-cases/{case_id}/close")
            assert closed.status_code == 200
            assert closed.json()["status"] == "closed"

            created_connection = await client.post(
                "/ticketing/connections",
                json={
                    "provider": "jira",
                    "display_name": "Acme Jira",
                    "auth_type": "api_token",
                    "provider_config": {"base_url": "https://jira.example.com", "project_key": "SUP"},
                },
            )
            assert created_connection.status_code == 200
            connection_id = created_connection.json()["connection_id"]

            health = await client.post(f"/ticketing/connections/{connection_id}/health-check")
            assert health.status_code == 200
            assert health.json()["status"] == "error"

            patched = await client.patch(
                f"/ticketing/connections/{connection_id}",
                json={"credentials_ref": "env:RUHU_JIRA_TOKEN"},
            )
            assert patched.status_code == 200
            health = await client.post(f"/ticketing/connections/{connection_id}/health-check")
            assert health.status_code == 200
            assert health.json()["status"] == "active"

            linked = await client.post(
                "/ticketing/external-cases",
                json={
                    "provider": "jira",
                    "connection_id": connection_id,
                    "support_case_id": case_id,
                    "title": "Escalated callback issue",
                    "description": "Create a remote ticket from the support case.",
                },
            )
            assert linked.status_code == 200
            link_id = linked.json()["link_id"]
            external_case_id = linked.json()["external_case_id"]
            assert external_case_id in harness.remote_cases

            comment = await client.post(
                f"/ticketing/external-cases/{link_id}/comment",
                json={"body": "Escalated from callback support case.", "visibility": "internal"},
            )
            assert comment.status_code == 200
            assert len(comment.json()["comments"]) == 1

            transitioned = await client.post(
                f"/ticketing/external-cases/{link_id}/transition",
                json={"status": "In Progress"},
            )
            assert transitioned.status_code == 200
            assert transitioned.json()["sync_status"] == "synced"
            assert transitioned.json()["external_case_status"] == "In Progress"

            synced = await client.post(f"/ticketing/external-cases/{link_id}/sync")
            assert synced.status_code == 200
            assert synced.json()["sync_status"] == "synced"

            search = await client.get("/ticketing/external-cases/search", params={"q": external_case_id})
            assert search.status_code == 200
            assert len(search.json()) == 1
            assert search.json()[0]["external_case_id"] == external_case_id

            remote_search = await client.get(
                f"/ticketing/connections/{connection_id}/remote-search",
                params={"q": "callback", "limit": 10},
            )
            assert remote_search.status_code == 200
            assert remote_search.json()[0]["external_case_id"] == external_case_id

            activity = await client.get(f"/ticketing/connections/{connection_id}/activity")
            assert activity.status_code == 200
            assert any(item["action"] == "health_check" for item in activity.json())
            assert any(item["action"] == "create_external_case" for item in activity.json())

            webhook = await client.post(
                f"/ticketing/webhooks/jira/{connection_id}",
                headers={"X-Ruhu-Provider-Secret": "provider-secret"},
                json={
                    "event_type": "case_updated",
                    "external_case_id": external_case_id,
                    "status": "Done",
                },
            )
            assert webhook.status_code == 200
            assert webhook.json()["action"] == "case_updated"

            refreshed = await client.post(f"/ticketing/external-cases/{link_id}/sync")
            assert refreshed.status_code == 200
            assert refreshed.json()["external_case_status"] == "Done"

    asyncio.run(run())


def test_ticketing_retry_queue_replays_retryable_provider_failures(monkeypatch, postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        harness = _install_fake_ticketing_service(monkeypatch)
        _seed_identity_store(SQLAlchemyIdentityStore(build_session_factory(auth_database_url)))

        app = build_default_app(
            agent_root=agent_root,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            created_case = await client.post(
                "/support-cases",
                json={
                    "title": "Retry sync callback case",
                    "description": "Retry queue should replay a transient sync failure.",
                    "priority": "high",
                    "category": "callback",
                    "source": "manual",
                },
            )
            assert created_case.status_code == 200
            case_id = created_case.json()["case_id"]

            created_connection = await client.post(
                "/ticketing/connections",
                json={
                    "provider": "jira",
                    "display_name": "Retry Jira",
                    "auth_type": "api_token",
                    "credentials_ref": "env:RUHU_JIRA_TOKEN",
                    "provider_config": {"base_url": "https://jira.example.com"},
                },
            )
            assert created_connection.status_code == 200
            connection_id = created_connection.json()["connection_id"]

            linked = await client.post(
                "/ticketing/external-cases",
                json={
                    "provider": "jira",
                    "connection_id": connection_id,
                    "support_case_id": case_id,
                    "title": "Retryable external issue",
                    "description": "Create a remote ticket that will hit a transient sync failure.",
                },
            )
            assert linked.status_code == 200
            link_id = linked.json()["link_id"]
            external_case_id = linked.json()["external_case_id"]
            assert external_case_id in harness.remote_cases

            harness.failures["fetch_case"] = 1
            failed_sync = await client.post(f"/ticketing/external-cases/{link_id}/sync")
            assert failed_sync.status_code == 502

            activity = await client.get(f"/ticketing/connections/{connection_id}/activity")
            assert activity.status_code == 200
            queued = next(item for item in activity.json() if item["action"] == "sync_case" and item["status"] == "error")
            assert queued["retry_status"] == "pending"
            assert queued["next_retry_at"] is not None

            retry_queue = await client.get("/ticketing/activities/retry-queue", params={"connection_id": connection_id})
            assert retry_queue.status_code == 200
            assert retry_queue.json()[0]["activity_id"] == queued["activity_id"]

            processed = await client.post(
                "/ticketing/activities/process-retries",
                json={"connection_id": connection_id, "limit": 10, "force": True},
            )
            assert processed.status_code == 200
            assert processed.json()[0]["activity_id"] == queued["activity_id"]
            assert processed.json()[0]["retry_status"] == "succeeded"
            assert processed.json()[0]["status"] == "retried"

            refreshed = await client.post(f"/ticketing/external-cases/{link_id}/sync")
            assert refreshed.status_code == 200
            assert refreshed.json()["sync_status"] == "synced"

    asyncio.run(run())


def test_ticketing_worker_processes_pending_retries(monkeypatch, postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        harness = _install_fake_ticketing_service(monkeypatch)
        runtime_session_factory = build_session_factory(runtime_database_url)
        _seed_identity_store(SQLAlchemyIdentityStore(build_session_factory(auth_database_url)))

        app = build_default_app(
            agent_root=agent_root,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )
            created_case = await client.post(
                "/support-cases",
                json={
                    "title": "Worker retry case",
                    "description": "Worker should process one queued retry.",
                    "priority": "high",
                    "category": "callback",
                    "source": "manual",
                },
            )
            assert created_case.status_code == 200
            case_id = created_case.json()["case_id"]

            created_connection = await client.post(
                "/ticketing/connections",
                json={
                    "provider": "jira",
                    "display_name": "Worker Jira",
                    "auth_type": "api_token",
                    "credentials_ref": "env:RUHU_JIRA_TOKEN",
                    "provider_config": {"base_url": "https://jira.example.com"},
                },
            )
            assert created_connection.status_code == 200
            connection_id = created_connection.json()["connection_id"]

            linked = await client.post(
                "/ticketing/external-cases",
                json={
                    "provider": "jira",
                    "connection_id": connection_id,
                    "support_case_id": case_id,
                    "title": "Worker retry external case",
                    "description": "Retry should be picked up by the worker.",
                },
            )
            assert linked.status_code == 200
            link_id = linked.json()["link_id"]
            harness.failures["fetch_case"] = 1
            failed_sync = await client.post(f"/ticketing/external-cases/{link_id}/sync")
            assert failed_sync.status_code == 502

        with runtime_session_factory.begin() as session:
            queued = (
                session.query(TicketingActivityRecord)
                .filter(
                    TicketingActivityRecord.action == "sync_case",
                    TicketingActivityRecord.status == "error",
                )
                .order_by(TicketingActivityRecord.created_at.desc())
                .first()
            )
            assert queued is not None
            queued.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            queued.updated_at = datetime.now(timezone.utc)

        worker = TicketingRetryWorker(
            session_factory=runtime_session_factory,
            service=app.state.ticket_system_service,
            interval_seconds=1.0,
            batch_size=10,
        )
        summary = worker.process_once()
        assert summary.processed_count == 1

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )
            retry_queue = await client.get("/ticketing/activities/retry-queue")
            assert retry_queue.status_code == 200
            assert retry_queue.json() == []

    asyncio.run(run())


def test_process_pending_retries_claims_activity_before_replay(monkeypatch, postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    service = TicketSystemService(session_factory)
    now = datetime.now(timezone.utc)

    with session_factory.begin() as session:
        session.add(
            TicketingConnectionRecord(
                connection_id="conn-retry-claim",
                organization_id="org-1",
                provider="jira",
                display_name="Retry Claim Connection",
                status="active",
                auth_type="api_token",
                credentials_ref="env:RUHU_JIRA_TOKEN",
                provider_config_json={},
                field_mappings_json={},
                status_mappings_json={},
                priority_mappings_json={},
                default_queue=None,
                created_at=now,
                updated_at=now,
            )
        )
    with session_factory.begin() as session:
        session.add(
            TicketingActivityRecord(
                activity_id="act-retry-claim",
                organization_id="org-1",
                connection_id="conn-retry-claim",
                link_id=None,
                provider="jira",
                direction="outbound",
                action="sync_case",
                status="error",
                external_case_id="jira-123",
                attempt_count=1,
                duration_ms=None,
                request_json={},
                response_json={},
                error_message="temporary sync failure",
                retry_status="pending",
                next_retry_at=now - timedelta(seconds=1),
                last_attempted_at=now - timedelta(minutes=1),
                created_at=now,
                updated_at=now,
            )
        )

    replayed_ids: list[str] = []
    nested_checked = False
    nested_results: list[object] = []

    def fake_replay(activity) -> None:
        nonlocal nested_checked
        replayed_ids.append(activity.activity_id)
        if not nested_checked:
            nested_checked = True
            nested_results.extend(
                service.process_pending_retries(
                    organization_id="org-1",
                    limit=10,
                    force=True,
                )
            )

    monkeypatch.setattr(service, "_replay_activity", fake_replay)

    processed = service.process_pending_retries(
        organization_id="org-1",
        limit=10,
        force=True,
    )

    assert replayed_ids == ["act-retry-claim"]
    assert nested_results == []
    assert len(processed) == 1
    assert processed[0].activity_id == "act-retry-claim"
    assert processed[0].retry_status == "succeeded"
    assert processed[0].status == "retried"


def test_ticketing_webhook_accepts_provider_specific_jira_signature(monkeypatch, postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        _install_fake_ticketing_service(monkeypatch)
        _seed_identity_store(SQLAlchemyIdentityStore(build_session_factory(auth_database_url)))

        app = build_default_app(
            agent_root=agent_root,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )
            created_connection = await client.post(
                "/ticketing/connections",
                json={
                    "provider": "jira",
                    "display_name": "Webhook Jira",
                    "auth_type": "api_token",
                    "provider_config": {
                        "cloud_id": "cloud-1",
                        "site_url": "https://jira.example.com",
                        "webhook_secret": "jira-webhook-secret",
                    },
                },
            )
            assert created_connection.status_code == 200
            connection_id = created_connection.json()["connection_id"]

            payload = {
                "event_type": "jira:issue_updated",
                "external_case_id": "jira-100",
                "status": "Done",
            }
            body = json.dumps(payload).encode("utf-8")
            signature = hmac.new(
                b"jira-webhook-secret",
                body,
                hashlib.sha256,
            ).hexdigest()

            webhook = await client.post(
                f"/ticketing/webhooks/jira/{connection_id}",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature": f"sha256={signature}",
                },
            )
            assert webhook.status_code == 200
            assert webhook.json()["action"] == "jira:issue_updated"

            invalid = await client.post(
                f"/ticketing/webhooks/jira/{connection_id}",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature": "sha256=deadbeef",
                },
            )
            assert invalid.status_code == 403

    asyncio.run(run())
