from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ruhu.api import build_default_app
from ruhu.api_auth import AuthContextResolver
from ruhu.audit.events import ADMIN_INVITATION_ACCEPTED, ADMIN_ROLE_CHANGED, ADMIN_USER_REMOVED
from ruhu.auth import AuthService, JWTCodec
from ruhu.db import build_session_factory
from ruhu.identity import Organization, OrganizationMembership, SessionAuditContext, User
from ruhu.identity_sqlalchemy import SQLAlchemyIdentityStore
from ruhu.kernel import ConversationKernel
from ruhu.runtime_config import RuntimeSettings
from ruhu.schemas import ActionRecord, ConversationState, TurnTrace
from ruhu.tools.deferred import DeferredToolTransition
from ruhu.tools.executors.builtin import BuiltinExecutor
from ruhu.tools.executors.http import HttpExecutor
from ruhu.tools.management import ToolDefinitionStore
from ruhu.tools.specs import ToolSpec
from ruhu.tools.types import ToolCaller, ToolInvocation, ToolResult
from tests.conftest import make_widget_publishable_key

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


def _auth_runtime_settings() -> RuntimeSettings:
    return RuntimeSettings(auth_allowed_redirect_origins=["http://testserver"])


def _widget_headers(session_token: str) -> dict[str, str]:
    return {"X-Ruhu-Widget-Session-Token": session_token}


async def _create_simple_simulation_fixture(client: httpx.AsyncClient, agent_id: str, *, name: str) -> dict:
    response = await client.post(
        f"/agents/{agent_id}/simulation-fixtures",
        json={
            "name": name,
            "default_channel": "web_chat",
            "turns": [
                {
                    "turn_id": f"{name}-turn-1",
                    "event_type": "user_message",
                    "modality": "text",
                    "text": "Hello there",
                    "metadata": {"source": "test"},
                }
            ],
            "assertions": [
                {
                    "assertion_id": f"{name}-assertion-1",
                    "kind": "turn_count_equals",
                    "config": {"count": 1},
                }
            ],
        },
    )
    assert response.status_code == 200
    return response.json()


async def _run_gate_evaluation(
    client: httpx.AsyncClient,
    agent_id: str,
    *,
    fixture_ids: list[str],
) -> dict:
    response = await client.post(
        f"/agents/{agent_id}/evaluation-runs",
        json={
            "fixture_ids": fixture_ids,
            "gate_eligible": True,
            "mode": "publish_gate",
            "source": "api",
            "execution_mode": "sync",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["qualified_at"] is not None
    return payload


async def _advance_sales_booking_to_invocation(
    client: httpx.AsyncClient,
    conversation_id: str,
    *,
    email: str,
    preferred_time: str = "Tomorrow at 2:00 PM works for me.",
    turn_prefix: str = "booking",
) -> tuple[dict, dict, str]:
    email_turn = await client.post(
        f"/conversations/{conversation_id}/turns",
        json={
            "turn_id": f"{turn_prefix}_email",
            "dedupe_key": f"{turn_prefix}_email",
            "channel": "web_chat",
            "modality": "text",
            "event_type": "user_message",
            "text": f"use {email}",
        },
    )
    assert email_turn.status_code == 200
    email_payload = email_turn.json()
    assert email_payload["step_after"] == "collect_booking_details"

    time_turn = await client.post(
        f"/conversations/{conversation_id}/turns",
        json={
            "turn_id": f"{turn_prefix}_time",
            "dedupe_key": f"{turn_prefix}_time",
            "channel": "web_chat",
            "modality": "text",
            "event_type": "user_message",
            "text": preferred_time,
        },
    )
    assert time_turn.status_code == 200
    time_payload = time_turn.json()
    assert time_payload["step_after"] == "submit_lead"
    assert time_payload["tool_calls"]
    assert time_payload["tool_calls"][0]["status"] == "confirmation_required"
    invocation_id = time_payload["tool_calls"][0]["invocation_id"]
    assert invocation_id is not None
    return email_payload, time_payload, invocation_id


async def _wait_for_evaluation_run(
    client: httpx.AsyncClient,
    evaluation_run_id: str,
    *,
    attempts: int = 40,
) -> dict:
    payload: dict = {}
    for _ in range(attempts):
        response = await client.get(f"/evaluation-runs/{evaluation_run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled", "stopped"}:
            return payload
        await asyncio.sleep(0.05)
    raise AssertionError(f"evaluation run did not finish: {evaluation_run_id}")


async def _wait_for_journey_job(
    client: httpx.AsyncClient,
    job_id: str,
    *,
    app=None,
    attempts: int = 40,
) -> dict:
    """Journey jobs drain in the worker process (journey_runtime.tick), not
    API threads — pass ``app`` so the poll loop drives the runtime's
    lease-claim drain the way ruhu.worker does."""
    payload: dict = {}
    for _ in range(attempts):
        if app is not None:
            app.state.journey_runtime.process_available_jobs_once(max_jobs=5)
        response = await client.get(f"/journey-runtime/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        await asyncio.sleep(0.05)
    raise AssertionError(f"journey job did not finish: {job_id}")


async def _create_journey_definition(client: httpx.AsyncClient, *, slug: str = "demo-booking") -> dict:
    response = await client.post(
        "/journey-definitions",
        json={
            "slug": slug,
            "name": "Demo booking",
            "subject_strategy": {
                "kind": "fact_name",
                "value": "customer_id",
            },
        },
    )
    assert response.status_code == 200
    return response.json()


async def _publish_journey_version(
    client: httpx.AsyncClient,
    *,
    definition_id: str,
    outcome_rules: dict[str, list[dict]] | None = None,
) -> dict:
    version_response = await client.post(
        f"/journey-definitions/{definition_id}/versions",
        json={
            "rules": {
                "entry_rules": [{"kind": "conversation_started"}],
                "milestones": [
                    {
                        "milestone_id": "discover",
                        "name": "Discover",
                        "order_index": 1,
                        "enter_when": [{"kind": "step_entered", "value": "discover"}],
                    }
                ],
                "outcome_rules": outcome_rules or {
                    "completed": [{"kind": "fact_present", "value": "booking_id"}],
                },
            }
        },
    )
    assert version_response.status_code == 200
    publish_response = await client.post(f"/journey-definitions/{definition_id}/publish", json={})
    assert publish_response.status_code == 200
    return publish_response.json()


def _extract_token_from_dev_outbox(
    app,
    *,
    path: str,
    query_key: str = "token",
    entry_index: int = -1,
) -> str:
    entries = getattr(app.state, "email_outbox", None)
    assert entries is not None
    assert len(entries) > 0
    entry = entries[entry_index]
    for candidate in filter(None, [entry.html_content, entry.text_content]):
        for part in str(candidate).split():
            parsed = urlparse(part.strip('"\'>)'))
            values = parse_qs(parsed.query).get(query_key)
            if parsed.path == path and values:
                return values[0]
    raise AssertionError(f"no {query_key} found for path {path}")


def _private_key_pem() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _seed_authenticated_api_store(
    database_url: str,
    *,
    admin_is_superuser: bool = False,
) -> SQLAlchemyIdentityStore:
    store = SQLAlchemyIdentityStore(build_session_factory(database_url))

    admin = store.save_user(
        User(
            user_id="user-admin",
            email="admin@example.com",
            display_name="Admin",
            is_superuser=admin_is_superuser,
        )
    )
    analyst = store.save_user(
        User(
            user_id="user-analyst",
            email="analyst@example.com",
            display_name="Analyst",
        )
    )
    existing = store.save_user(
        User(
            user_id="user-existing",
            email="existing@example.com",
            display_name="Existing",
        )
    )
    org_two_admin = store.save_user(
        User(
            user_id="user-org2-admin",
            email="org2@example.com",
            display_name="Other Org",
        )
    )

    store.save_organization(Organization(organization_id="org-1", slug="acme", name="Acme"))
    store.save_organization(Organization(organization_id="org-2", slug="other", name="Other"))
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
    store.add_organization_membership(
        OrganizationMembership(
            user_id=org_two_admin.user_id,
            organization_id="org-2",
            role="admin",
            is_account_owner=True,
        )
    )
    assert existing.user_id == "user-existing"
    return store


def _authorize_client(
    client: httpx.AsyncClient,
    *,
    auth_service: AuthService,
    user_id: str,
    organization_id: str,
    ip: str | None = None,
    user_agent: str | None = None,
):
    issued = auth_service.issue_browser_session(
        user_id=user_id,
        organization_id=organization_id,
        audit=SessionAuditContext(ip=ip, user_agent=user_agent),
    )
    client.headers["Authorization"] = f"Bearer {issued.access_token}"
    return issued


def _build_authenticated_api_app(
    *,
    agent_root: Path,
    database_url: str,
    auth_database_url: str,
):
    return build_default_app(
        agent_root=agent_root,
        database_url=database_url,
        interpreter_name="sales",
        runtime_settings=RuntimeSettings(
            auth_database_url=auth_database_url,
            auth_jwt_private_key_pem=_private_key_pem(),
            auth_jwt_active_kid="kid-rules-api",
            auth_allowed_redirect_origins=["http://testserver"],
        ),
    )


class _DeferredDemoLeadHandler:
    def submit(self, call, spec, job):
        return DeferredToolTransition(
            action="wait_webhook",
            external_job_id=f"lead-{job.invocation_id}",
            callback_correlation_id=f"demo-lead-{job.invocation_id}",
        )

    def poll(self, call, spec, job):
        return DeferredToolTransition(
            action="wait_poll",
            next_poll_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    def handle_callback(self, call, spec, job, *, payload, headers=None):
        status = str(payload.get("status") or "completed").lower()
        if status not in {"completed", "success"}:
            return DeferredToolTransition(action="fail", error=str(payload.get("error") or "provider failed"))
        return DeferredToolTransition(
            action="complete",
            result=ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="success",
                output={
                    "message": "Your demo request is booked.",
                    "lead": {"external_job_id": job.external_job_id, "email": call.args.get("email")},
                },
                metadata={"provider": "demo"},
            ),
        )


class _DeferredHttpResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _DeferredSubmitLeadHttpClient:
    def request(self, method, url, **kwargs):
        if method == "POST" and url == "https://example.com/imports":
            body = kwargs.get("json") or {}
            external_job_id = str(body.get("external_job_id") or "lead-pending")
            callback_correlation_id = str(body.get("callback_correlation_id") or "demo-lead-pending")
            return _DeferredHttpResponse(
                202,
                {
                    "status": "accepted",
                    "job": {"id": external_job_id},
                    "callback": {"id": callback_correlation_id},
                },
            )
        if method == "POST" and url == "https://example.com/calendar/imports":
            body = kwargs.get("json") or {}
            external_job_id = str(body.get("external_job_id") or "booking-pending")
            callback_correlation_id = str(body.get("callback_correlation_id") or "demo-booking-pending")
            return _DeferredHttpResponse(
                202,
                {
                    "status": "accepted",
                    "job": {"id": external_job_id},
                    "callback": {"id": callback_correlation_id},
                },
            )
        raise AssertionError(f"unexpected deferred HTTP request: {method} {url}")


class _SyncSubmitLeadHttpClient:
    def request(self, method, url, **kwargs):
        if method == "POST" and url == "https://example.com/leads":
            body = kwargs.get("json") or {}
            email = str(body.get("email") or "")
            return _DeferredHttpResponse(
                200,
                {
                    "message": "Your demo request is booked.",
                    "lead": {"email": email},
                },
            )
        if method == "POST" and url == "https://example.com/events":
            body = kwargs.get("json") or {}
            attendee_email = str(body.get("attendee_email") or "")
            start_time = str(body.get("start_time") or "")
            return _DeferredHttpResponse(
                200,
                {
                    "message": "Your demo booking is confirmed.",
                    "booking": {
                        "email": attendee_email,
                        "start_time": start_time,
                        "event_id": "evt_demo_123",
                    },
                },
            )
        raise AssertionError(f"unexpected sync HTTP request: {method} {url}")


def _seed_deferred_crm_submit_lead_tool(app, *, runtime_database_url: str) -> None:
    session_factory = build_session_factory(runtime_database_url)
    definition_store = ToolDefinitionStore(session_factory)
    connection_store = app.state.connection_store
    connection = connection_store.create(
        organization_id="org-1",
        display_name="Demo CRM",
        provider="demo_http",
        auth_type="none",
        base_url="https://example.com",
    )
    definition_store.create(
        organization_id="org-1",
        connection_id=connection.connection_id,
        tool_ref="crm.submit_lead",
        display_name="Submit CRM lead",
        description="Submit a captured demo lead to the external CRM asynchronously.",
        endpoint_path="/imports",
        http_method="POST",
        input_schema={
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Lead email address to submit to the CRM.",
                },
                "channel": {
                    "type": "string",
                    "description": "Conversation channel used to capture the lead.",
                },
            },
            "required": ["email"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "lead": {
                    "type": "object",
                    "description": "Completed CRM lead payload.",
                    "properties": {
                        "email": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "additionalProperties": True,
        },
        metadata={
            "annotations": {"destructive": True, "idempotent": True},
            "confirmation": "always",
            "confirmation_prompt": "Confirm and I’ll create the demo request now.",
            "executor_config": {
                "execution_mode": "deferred",
                "resolution_mode": "webhook",
                "deferred": {
                    "submit": {
                        "url": "https://example.com/imports",
                        "method": "POST",
                        "body_template": {
                            "email": "{{ args.email }}",
                            "external_job_id": "lead-{{ job.invocation_id }}",
                            "callback_correlation_id": "demo-lead-{{ job.invocation_id }}",
                        },
                        "status_path": "status",
                        "pending_values": ["accepted"],
                        "external_job_id_path": "job.id",
                        "callback_correlation_id_path": "callback.id",
                    },
                    "callback": {
                        "status_path": "status",
                        "success_values": ["completed"],
                        "failure_values": ["failed"],
                        "result_path": "result",
                    },
                },
            }
        },
    )
    definition_store.create(
        organization_id="org-1",
        connection_id=connection.connection_id,
        tool_ref="calendar.create_event",
        display_name="Create calendar event",
        description="Book a demo on the connected calendar asynchronously.",
        endpoint_path="/calendar/imports",
        http_method="POST",
        input_schema={
            "type": "object",
            "properties": {
                "attendee_email": {
                    "type": "string",
                    "description": "Email address that should receive the booked demo invite.",
                },
                "start_time": {
                    "type": "string",
                    "description": "Requested meeting date and time for the demo booking.",
                },
                "title": {
                    "type": "string",
                    "description": "Calendar event title that will appear on the booked demo.",
                },
                "channel": {
                    "type": "string",
                    "description": "Conversation channel that produced the demo booking request.",
                },
            },
            "required": ["attendee_email", "start_time"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "booking": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "start_time": {"type": "string"},
                        "event_id": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "additionalProperties": True,
        },
        metadata={
            "annotations": {"destructive": True, "idempotent": True},
            "confirmation": "always",
            "confirmation_prompt": "Confirm and I’ll book the demo now.",
            "executor_config": {
                "execution_mode": "deferred",
                "resolution_mode": "webhook",
                "deferred": {
                    "submit": {
                        "url": "https://example.com/calendar/imports",
                        "method": "POST",
                        "body_template": {
                            "attendee_email": "{{ args.attendee_email }}",
                            "start_time": "{{ args.start_time }}",
                            "title": "{{ args.title }}",
                            "channel": "{{ args.channel }}",
                            "external_job_id": "booking-{{ job.invocation_id }}",
                            "callback_correlation_id": "demo-booking-{{ job.invocation_id }}",
                        },
                        "status_path": "status",
                        "pending_values": ["accepted"],
                        "external_job_id_path": "job.id",
                        "callback_correlation_id_path": "callback.id",
                    },
                    "callback": {
                        "status_path": "status",
                        "success_values": ["completed"],
                        "failure_values": ["failed"],
                        "result_path": "result",
                    },
                },
            },
        },
    )
    http_executor = app.state.tool_runtime.get_executor("http")
    assert isinstance(http_executor, HttpExecutor)
    http_executor._client = _DeferredSubmitLeadHttpClient()


def _seed_sync_crm_submit_lead_tool(
    app,
    *,
    runtime_database_url: str,
    organization_id: str = "public",
) -> None:
    session_factory = build_session_factory(runtime_database_url)
    definition_store = ToolDefinitionStore(session_factory)
    connection_store = app.state.connection_store
    connection = connection_store.create(
        organization_id=organization_id,
        display_name="Demo CRM",
        provider="demo_http",
        auth_type="none",
        base_url="https://example.com",
    )
    definition_store.create(
        organization_id=organization_id,
        connection_id=connection.connection_id,
        tool_ref="crm.submit_lead",
        display_name="Submit CRM lead",
        description="Submit a captured demo lead to the external CRM synchronously.",
        endpoint_path="/leads",
        http_method="POST",
        input_schema={
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Lead email address to submit to the CRM."},
                "channel": {"type": "string", "description": "Conversation channel used to capture the lead."},
            },
            "required": ["email"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "lead": {
                    "type": "object",
                    "description": "Completed CRM lead payload.",
                    "properties": {"email": {"type": "string"}},
                    "additionalProperties": True,
                },
            },
            "additionalProperties": True,
        },
        metadata={
            "annotations": {"destructive": True, "idempotent": True},
            "confirmation": "always",
            "confirmation_prompt": "Confirm and I’ll create the demo request now.",
        },
    )
    definition_store.create(
        organization_id=organization_id,
        connection_id=connection.connection_id,
        tool_ref="calendar.create_event",
        display_name="Create calendar event",
        description="Book a demo on the connected calendar synchronously.",
        endpoint_path="/events",
        http_method="POST",
        input_schema={
            "type": "object",
            "properties": {
                "attendee_email": {
                    "type": "string",
                    "description": "Email address that should receive the booked demo invite.",
                },
                "start_time": {
                    "type": "string",
                    "description": "Requested meeting date and time for the demo booking.",
                },
                "title": {
                    "type": "string",
                    "description": "Calendar event title that will appear on the booked demo.",
                },
                "channel": {
                    "type": "string",
                    "description": "Conversation channel that produced the demo booking request.",
                },
            },
            "required": ["attendee_email", "start_time"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "booking": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "start_time": {"type": "string"},
                        "event_id": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
            "additionalProperties": True,
        },
        metadata={
            "annotations": {"destructive": True, "idempotent": True},
            "confirmation": "always",
            "confirmation_prompt": "Confirm and I’ll book the demo now.",
        },
    )
    http_executor = app.state.tool_runtime.get_executor("http")
    assert isinstance(http_executor, HttpExecutor)
    http_executor._client = _SyncSubmitLeadHttpClient()


def test_api_starts_conversation_and_processes_turn(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

            playground = await client.get("/playground")
            assert playground.status_code == 200
            assert "Ruhu Runtime Playground" in playground.text
            assert "/conversations" in playground.text

            agents = await client.get("/agents")
            assert agents.status_code == 200
            assert any(item["id"] == "sales" for item in agents.json())

            started = await client.post("/conversations", json={"agent_id": "sales"})
            assert started.status_code == 200
            payload = started.json()
            conversation_id = payload["conversation"]["conversation_id"]
            assert payload["conversation"]["step_id"] == "discover"
            assert payload["start"]["step_after"] == "discover"

            turn = await client.post(
                f"/conversations/{conversation_id}/turns",
                json={"text": "Can you explain what the product does?", "channel": "web_chat"},
            )
            assert turn.status_code == 200
            turn_payload = turn.json()
            assert turn_payload["step_after"] == "product_qa"
            assert turn_payload["chosen_action"]["type"] == "run_tool"
            assert turn_payload["tool_calls"][0]["tool_ref"] == "knowledge.lookup"
            assert turn_payload["tool_calls"][0]["status"] == "success"

            conversation = await client.get(f"/conversations/{conversation_id}")
            assert conversation.status_code == 200
            assert conversation.json()["agent_id"] == "sales"

            traces = await client.get(f"/conversations/{conversation_id}/traces")
            assert traces.status_code == 200
            traces_payload = traces.json()
            assert len(traces_payload) == 2
            # Reasoning-timeline fields are surfaced on the public trace
            # response (see ConversationTraceResponse). The product-question
            # turn ran a tool and chose a run_tool action — the trace must
            # carry that evidence so the frontend Reasoning Timeline can
            # render it without re-fetching from kernel-internal stores.
            answered_trace = next(
                (entry for entry in traces_payload if entry["step_after"] == "product_qa"),
                None,
            )
            assert answered_trace is not None
            assert answered_trace["chosen_action"]["type"] == "run_tool"
            assert answered_trace["chosen_action"]["reason"]
            assert answered_trace["tool_calls"], "tool_calls should be exposed for the timeline"
            assert answered_trace["tool_calls"][0]["tool_ref"] == "knowledge.lookup"
            assert answered_trace["tool_calls"][0]["status"] == "success"
            assert isinstance(answered_trace["guard_results"], list)
            assert isinstance(answered_trace["latency_breakdown_ms"], dict)

            realtime_events = await client.get(f"/conversations/{conversation_id}/realtime-events")
            assert realtime_events.status_code == 200
            assert isinstance(realtime_events.json(), list)

    asyncio.run(run())

def test_rules_api_admin_can_create_publish_library_bind_resolve_and_evaluate(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_api_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            create_definition = await client.post(
                "/api/rules/definitions",
                json={
                    "rule_id": "rule.account_number_guard",
                    "organization_scope": "organization",
                    "name": "Account Number Guard",
                    "summary": "Blocks account number collection in chat",
                    "stage": "turn_ingress",
                    "predicate": {
                        "kind": "match",
                        "path": "turn.text",
                        "operator": "contains",
                        "value": "account number",
                    },
                    "effect": {
                        "kind": "block",
                        "code": "account_number_blocked",
                        "message": "Account numbers are not allowed",
                    },
                    "tags": ["safety"],
                },
            )
            assert create_definition.status_code == 201
            created_definition = create_definition.json()
            assert created_definition["organization_id"] == "org-1"
            assert created_definition["status"] == "draft"

            publish_definition = await client.post(
                "/api/rules/definitions/rule.account_number_guard/revisions/1/publish"
            )
            assert publish_definition.status_code == 200
            assert publish_definition.json()["status"] == "published"

            create_library = await client.post(
                "/api/rules/libraries",
                json={
                    "organization_scope": "organization",
                    "library_id": "org-default",
                    "version": "v1",
                    "visibility": "organization",
                    "name": "Org Defaults",
                    "summary": "Default organization rules",
                    "entries": [
                        {
                            "rule_id": "rule.account_number_guard",
                            "revision": 1,
                            "sort_order": 10,
                        }
                    ],
                },
            )
            assert create_library.status_code == 201
            library_payload = create_library.json()
            assert library_payload["entries"][0]["rule_id"] == "rule.account_number_guard"
            assert library_payload["entries"][0]["library_entry_id"]

            create_binding = await client.post(
                "/api/rules/bindings",
                json={
                    "organization_scope": "organization",
                    "binding_id": "binding.account_number_guard",
                    "rule_id": "rule.account_number_guard",
                    "revision": 1,
                    "mode": "enforce",
                    "order": 10,
                    "scope": {
                        "channels": ["web_chat"],
                        "agent_ids": ["sales"],
                        "step_ids": ["entry"],
                        "tool_refs": [],
                        "event_types": ["user_message"],
                    },
                    "confirm_broad_scope": True,
                },
            )
            assert create_binding.status_code == 201
            assert create_binding.json()["binding_id"] == "binding.account_number_guard"

            resolve_program = await client.post(
                "/api/rules/programs/resolve",
                json={
                    "agent_id": "sales",
                    "step_id": "entry",
                    "channel": "web_chat",
                    "event_type": "user_message",
                },
            )
            assert resolve_program.status_code == 200
            program_payload = resolve_program.json()
            assert {item["binding_id"] for item in program_payload["bindings"]} == {
                "binding.account_number_guard"
            }
            assert {
                (item["rule_id"], item["revision"]) for item in program_payload["library"]["rules"]
            } == {("rule.account_number_guard", 1)}

            evaluate_program = await client.post(
                "/api/rules/evaluate",
                json={
                    "program": program_payload,
                    "context": {
                        "stage": "turn_ingress",
                        "conversation": {
                            "organization_id": "org-1",
                            "agent_id": "sales",
                            "step_id": "entry",
                            "channel": "web_chat",
                        },
                        "turn": {
                            "event_type": "user_message",
                            "text": "My account number is 12345",
                        },
                    },
                },
            )
            assert evaluate_program.status_code == 200
            evaluation_payload = evaluate_program.json()
            assert evaluation_payload["terminal_effect"]["kind"] == "block"
            assert evaluation_payload["terminal_effect"]["code"] == "account_number_blocked"

    asyncio.run(run())


def test_rules_api_requires_admin_role_for_mutation(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_api_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-analyst",
                organization_id="org-1",
            )

            create_definition = await client.post(
                "/api/rules/definitions",
                json={
                    "rule_id": "rule.analyst-mutation",
                    "organization_scope": "organization",
                    "name": "Analyst Mutation",
                    "summary": "Should fail",
                    "stage": "turn_ingress",
                    "predicate": {
                        "kind": "match",
                        "path": "turn.text",
                        "operator": "contains",
                        "value": "hello",
                    },
                    "effect": {
                        "kind": "trace",
                        "code": "analyst_trace",
                    },
                },
            )
            assert create_definition.status_code == 403
            assert create_definition.json()["detail"] == "admin role required for rules mutation"

            list_definitions = await client.get("/api/rules/definitions")
            assert list_definitions.status_code == 200

    asyncio.run(run())


def test_rules_api_exposes_seeded_starter_library_and_definitions(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_api_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-analyst",
                organization_id="org-1",
            )

            libraries = await client.get("/api/rules/libraries", params={"organization_scope": "all"})
            assert libraries.status_code == 200
            library_ids = {item["library_id"] for item in libraries.json()["items"]}
            assert "ruhu.starter.rules" in library_ids

            definitions = await client.get(
                "/api/rules/definitions",
                params={"organization_scope": "system", "status": "published"},
            )
            assert definitions.status_code == 200
            rule_ids = {item["rule_id"] for item in definitions.json()["items"]}
            assert "rule.turn.payment_card_data_block" in rule_ids

    asyncio.run(run())


def test_rules_api_rejects_broad_scope_binding_without_confirmation(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_api_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            create_definition = await client.post(
                "/api/rules/definitions",
                json={
                    "rule_id": "rule.scope-guard",
                    "organization_scope": "organization",
                    "name": "Scope Guard",
                    "summary": "Tests broad binding scope confirmation",
                    "stage": "turn_ingress",
                    "predicate": {
                        "kind": "match",
                        "path": "turn.text",
                        "operator": "contains",
                        "value": "pricing",
                    },
                    "effect": {
                        "kind": "trace",
                        "code": "pricing_trace",
                    },
                },
            )
            assert create_definition.status_code == 201

            publish_definition = await client.post(
                "/api/rules/definitions/rule.scope-guard/revisions/1/publish"
            )
            assert publish_definition.status_code == 200

            create_binding = await client.post(
                "/api/rules/bindings",
                json={
                    "organization_scope": "organization",
                    "binding_id": "binding.scope-guard",
                    "rule_id": "rule.scope-guard",
                    "revision": 1,
                    "mode": "enforce",
                    "order": 10,
                    "scope": {
                        "channels": ["web_chat"],
                        "agent_ids": ["sales"],
                        "step_ids": ["entry"],
                        "tool_refs": [],
                        "event_types": ["user_message"],
                    },
                },
            )
            assert create_binding.status_code == 422
            assert "broad scope requires explicit confirmation" in create_binding.json()["detail"]

    asyncio.run(run())


def test_rules_api_non_superuser_cannot_mutate_system_scoped_records(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url, admin_is_superuser=True)
        app = _build_authenticated_api_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            create_definition = await client.post(
                "/api/rules/definitions",
                json={
                    "rule_id": "rule.system-guard",
                    "organization_scope": "system",
                    "name": "System Guard",
                    "summary": "System-scoped rule",
                    "stage": "turn_ingress",
                    "predicate": {
                        "kind": "match",
                        "path": "turn.text",
                        "operator": "contains",
                        "value": "secret",
                    },
                    "effect": {
                        "kind": "trace",
                        "code": "system_trace",
                    },
                },
            )
            assert create_definition.status_code == 201

            publish_definition = await client.post(
                "/api/rules/definitions/rule.system-guard/revisions/1/publish"
            )
            assert publish_definition.status_code == 200

            create_binding = await client.post(
                "/api/rules/bindings",
                json={
                    "organization_scope": "system",
                    "binding_id": "binding.system-guard",
                    "rule_id": "rule.system-guard",
                    "revision": 1,
                    "mode": "enforce",
                    "order": 10,
                    "scope": {
                        "channels": ["web_chat"],
                        "agent_ids": ["sales"],
                        "step_ids": ["entry"],
                        "tool_refs": [],
                        "event_types": ["user_message"],
                    },
                    "confirm_broad_scope": True,
                },
            )
            assert create_binding.status_code == 201

            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-org2-admin",
                organization_id="org-2",
            )

            create_revision = await client.post(
                "/api/rules/definitions/rule.system-guard/revisions",
                json={
                    "name": "System Guard v2",
                    "summary": "Still system-scoped",
                    "stage": "turn_ingress",
                    "predicate": {
                        "kind": "match",
                        "path": "turn.text",
                        "operator": "contains",
                        "value": "secret",
                    },
                    "effect": {
                        "kind": "trace",
                        "code": "system_trace_v2",
                    },
                },
            )
            assert create_revision.status_code == 403
            assert create_revision.json()["detail"] == "superuser required for system scope"

            update_binding = await client.patch(
                "/api/rules/bindings/binding.system-guard",
                json={
                    "mode": "shadow",
                },
            )
            assert update_binding.status_code == 403
            assert update_binding.json()["detail"] == "superuser required for system scope"

    asyncio.run(run())


def test_rules_api_can_retire_published_revision(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_api_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            create_definition = await client.post(
                "/api/rules/definitions",
                json={
                    "rule_id": "rule.retire.api",
                    "organization_scope": "organization",
                    "name": "Retire API Rule",
                    "summary": "Lifecycle test",
                    "stage": "turn_ingress",
                    "predicate": {
                        "kind": "match",
                        "path": "turn.text",
                        "operator": "contains",
                        "value": "hello",
                    },
                    "effect": {
                        "kind": "trace",
                        "code": "trace.hello",
                    },
                },
            )
            assert create_definition.status_code == 201

            publish_definition = await client.post("/api/rules/definitions/rule.retire.api/revisions/1/publish")
            assert publish_definition.status_code == 200
            assert publish_definition.json()["status"] == "published"

            retire_definition = await client.post("/api/rules/definitions/rule.retire.api/revisions/1/retire")
            assert retire_definition.status_code == 200
            assert retire_definition.json()["status"] == "retired"

            listed = await client.get(
                "/api/rules/definitions",
                params={"organization_scope": "organization", "status": "retired"},
            )
            assert listed.status_code == 200
            listed_ids = {item["rule_id"] for item in listed.json()["items"]}
            assert "rule.retire.api" in listed_ids

    asyncio.run(run())


def test_internal_auth_diagnostics_reports_signing_posture(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url, admin_is_superuser=True)
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            runtime_settings=RuntimeSettings(
                auth_database_url=auth_database_url,
                environment="production",
                auth_require_asymmetric_tokens=True,
                auth_jwt_private_key_pem=_private_key_pem(),
                auth_jwt_active_kid="kid-internal",
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
            diagnostics = await client.get("/internal/auth/diagnostics")
            assert diagnostics.status_code == 200
            payload = diagnostics.json()
            assert payload["auth_enabled"] is True
            assert payload["environment"] == "production"
            assert payload["asymmetric_required"] is True
            assert payload["signing_algorithm"] == "RS256"
            assert payload["active_kid"] == "kid-internal"
            assert payload["hs256_fallback_enabled"] is False
            assert payload["signing_material_source"] == "inline"
            assert payload["verification_jwks_source"] == "embedded_active_key"
            assert payload["published_jwks_kids"] == ["kid-internal"]
            assert payload["verification_algorithms"] == ["RS256"]
            assert payload["verification_kids"] == ["kid-internal"]

    asyncio.run(run())


def test_api_reads_interpreter_selection_from_env(monkeypatch, postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        monkeypatch.setenv(
            "RUHU_AGENT_INTERPRETERS",
            json.dumps({"sales": "sales"}),
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            started = await client.post("/conversations", json={"agent_id": "sales"})
            conversation_id = started.json()["conversation"]["conversation_id"]

            turn = await client.post(
                f"/conversations/{conversation_id}/turns",
                json={"text": "Can you explain what the product does?", "channel": "web_chat"},
            )

            assert turn.status_code == 200
            assert turn.json()["step_after"] == "product_qa"

    asyncio.run(run())

def test_public_widget_support_triage_collects_account_id(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="support_triage",
            organization_id="test-org",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            widget_session = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "support_triage",
                    "channel": "web_widget",
                    "publishable_key": pk,
                },
            )
            assert widget_session.status_code == 200
            payload = widget_session.json()
            conversation_id = payload["conversation_id"]
            headers = _widget_headers(payload["session_token"])

            first_turn = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages",
                json={"text": "hello"},
                headers=headers,
            )
            assert first_turn.status_code == 200
            first_payload = first_turn.json()
            assert first_payload["step_after"] == "collect_account_id"
            assert any(
                "account id" in (item.get("text") or "").lower()
                for item in first_payload.get("messages", [])
            )

            second_turn = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages",
                json={"text": "1234"},
                headers=headers,
            )
            assert second_turn.status_code == 200
            second_payload = second_turn.json()
            assert second_payload["step_after"] == "handoff_support"
            assert any(
                "routing this to support" in (item.get("text") or "").lower()
                for item in second_payload.get("messages", [])
            )

            conversation = await client.get(f"/conversations/{conversation_id}")
            assert conversation.status_code == 200
            assert conversation.json()["facts"].get("account_id") == "1234"

    asyncio.run(run())


def test_public_widget_session_rejects_missing_publishable_key(postgres_database_url_factory) -> None:
    """Widget session creation without a publishable key must be rejected."""

    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            missing_key = await client.post(
                "/public/widget/sessions",
                json={"agent_id": "sales", "channel": "web_widget"},
            )
            assert missing_key.status_code == 422  # Pydantic validation error

            blank_key = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": "   ",
                },
            )
            assert blank_key.status_code == 400
            assert "publishable_key required" in blank_key.json()["detail"]

    asyncio.run(run())


def test_public_widget_session_rejects_invalid_publishable_key(postgres_database_url_factory) -> None:
    """Unknown or revoked publishable keys return 401."""

    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            unknown_key = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": "pk_test_not_a_real_key_12345",
                },
            )
            assert unknown_key.status_code == 401
            assert "invalid or revoked" in unknown_key.json()["detail"]

    asyncio.run(run())


def test_public_widget_session_rejects_key_agent_mismatch(postgres_database_url_factory) -> None:
    """A publishable key bound to agent A cannot create a session for agent B."""

    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk_bound_to_support = make_widget_publishable_key(
            database_url,
            agent_id="support_triage",
            organization_id="test-org",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            mismatch = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": pk_bound_to_support,
                },
            )
            assert mismatch.status_code == 403
            assert "different agent" in mismatch.json()["detail"]

    asyncio.run(run())


def test_public_widget_session_rejects_cross_tenant_agent(postgres_database_url_factory) -> None:
    """A publishable key from org-A cannot access an agent owned by org-B."""

    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        # Seed agents under "org-a"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="org-a",
        )
        # Publishable key under a DIFFERENT org, bound to the same agent id —
        # but the agent rows in this tenant-scoped install belong to org-a.
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="org-b",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            cross_tenant = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": pk,
                },
            )
            # org-b's key finds no sales under its tenant scope → 404.
            # (Snapshot-org cross-check also enforces this; either status is fine.)
            assert cross_tenant.status_code in (403, 404)

    asyncio.run(run())


def test_public_widget_session_requires_request_origin_when_key_has_allowed_origins(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="test-org",
            allowed_origins=["https://widget.example.com"],
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": pk,
                },
            )
            assert response.status_code == 403
            assert response.json()["detail"]["error"] == "origin_required"

    asyncio.run(run())


def test_public_widget_session_accepts_matching_referer_when_origin_header_is_absent(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="test-org",
            allowed_origins=["https://widget.example.com"],
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": pk,
                },
                headers={"Referer": "https://widget.example.com/page"},
            )
            assert response.status_code == 200, response.text

    asyncio.run(run())


def test_public_widget_session_created_with_referer_is_bound_to_that_origin(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="test-org",
            allowed_origins=["https://widget.example.com"],
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": pk,
                },
                headers={"Referer": "https://widget.example.com/page"},
            )
            assert created.status_code == 200
            payload = created.json()

            wrong_origin = await client.get(
                f"/public/widget/sessions/{payload['conversation_id']}",
                headers={
                    **_widget_headers(payload["session_token"]),
                    "Origin": "https://evil.example.com",
                },
            )
            assert wrong_origin.status_code == 403
            assert wrong_origin.json()["detail"]["error"] == "origin_mismatch"

            matching_referer = await client.get(
                f"/public/widget/sessions/{payload['conversation_id']}",
                headers={
                    **_widget_headers(payload["session_token"]),
                    "Referer": "https://widget.example.com/another-page",
                },
            )
            assert matching_referer.status_code == 200

    asyncio.run(run())


def test_public_widget_session_access_is_bound_to_the_origin_that_created_it(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="test-org",
            allowed_origins=["https://widget.example.com"],
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": pk,
                },
                headers={"Origin": "https://widget.example.com"},
            )
            assert created.status_code == 200
            payload = created.json()

            missing_origin = await client.get(
                f"/public/widget/sessions/{payload['conversation_id']}",
                headers=_widget_headers(payload["session_token"]),
            )
            assert missing_origin.status_code == 403
            assert missing_origin.json()["detail"]["error"] == "origin_required"

            wrong_origin = await client.get(
                f"/public/widget/sessions/{payload['conversation_id']}",
                headers={
                    **_widget_headers(payload["session_token"]),
                    "Origin": "https://evil.example.com",
                },
            )
            assert wrong_origin.status_code == 403
            assert wrong_origin.json()["detail"]["error"] == "origin_mismatch"

            correct_origin = await client.get(
                f"/public/widget/sessions/{payload['conversation_id']}",
                headers={
                    **_widget_headers(payload["session_token"]),
                    "Origin": "https://widget.example.com",
                },
            )
            assert correct_origin.status_code == 200

    asyncio.run(run())


def test_public_widget_message_stream_redacts_internal_errors(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="test-org",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": pk,
                },
            )
            assert created.status_code == 200
            payload = created.json()

            with patch.object(
                ConversationKernel,
                "process_turn",
                side_effect=RuntimeError("sensitive backend failure"),
            ):
                response = await client.post(
                    f"/public/widget/sessions/{payload['conversation_id']}/messages/stream",
                    headers=_widget_headers(payload["session_token"]),
                    json={"text": "hello"},
                )

            assert response.status_code == 200, response.text
            body = response.text
            assert "sensitive backend failure" not in body
            assert "message_processing_failed" in body
            assert "Please try again." in body

    asyncio.run(run())


def test_public_widget_sales_agent_answers_product_and_idle_follow_up(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="test-org",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": pk,
                },
            )
            assert created.status_code == 200
            payload = created.json()
            headers = _widget_headers(payload["session_token"])
            conversation_id = payload["conversation_id"]

            product = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages/stream",
                headers=headers,
                json={
                    "text": "I want information about product",
                    "dedupe_key": str(uuid4()),
                },
            )
            assert product.status_code == 200
            assert "Ruhu is a conversational AI platform" in product.text
            assert "couldn't find a grounded answer" not in product.text

            idle = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages/stream",
                headers=headers,
                json={
                    "text": "hello",
                    "dedupe_key": str(uuid4()),
                },
            )
            assert idle.status_code == 200
            assert "I can explain Ruhu" in idle.text

            pricing = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages/stream",
                headers=headers,
                json={
                    "text": "Can you help with pricing?",
                    "dedupe_key": str(uuid4()),
                },
            )
            assert pricing.status_code == 200
            assert "Ruhu pricing is flexible" in pricing.text
            assert "couldn't find a grounded answer" not in pricing.text

    asyncio.run(run())


def test_public_widget_sales_agent_answers_integration_follow_ups(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="test-org",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "publishable_key": pk,
                },
            )
            assert created.status_code == 200
            payload = created.json()
            headers = _widget_headers(payload["session_token"])
            conversation_id = payload["conversation_id"]

            product = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages/stream",
                headers=headers,
                json={
                    "text": "I want to learn about Ruhu product",
                    "dedupe_key": str(uuid4()),
                },
            )
            assert product.status_code == 200
            assert "Ruhu is a conversational AI platform" in product.text

            tools = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages/stream",
                headers=headers,
                json={
                    "text": "what kind of tools can i integrate with",
                    "dedupe_key": str(uuid4()),
                },
            )
            assert tools.status_code == 200
            assert "Ruhu integrations can connect agents" in tools.text
            assert "Which system do you want to connect?" in tools.text
            assert tools.text.count("Which area would you like to explore?") == 0

            integrations = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages/stream",
                headers=headers,
                json={
                    "text": "integrations",
                    "dedupe_key": str(uuid4()),
                },
            )
            assert integrations.status_code == 200
            assert "Ruhu integrations can connect agents" in integrations.text
            assert integrations.text.count("Which area would you like to explore?") == 0

    asyncio.run(run())


def test_public_widget_draft_session_requires_same_authenticated_org_as_publishable_key(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(database_url)
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="org-1",
            runtime_settings=RuntimeSettings(
                auth_database_url=database_url,
                auth_jwt_private_key_pem=_private_key_pem(),
                auth_jwt_active_kid="kid-widget-draft",
                auth_allowed_redirect_origins=["http://testserver"],
            ),
        )
        auth_service = app.state.auth_service
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="org-1",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=auth_service,
                user_id="user-org2-admin",
                organization_id="org-2",
            )
            response = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "target": "draft",
                    "publishable_key": pk,
                },
            )
            assert response.status_code == 403
            assert "same organization" in response.json()["detail"]

    asyncio.run(run())


def test_canvas_test_session_bootstraps_current_draft_without_publishable_key(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(database_url)
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="org-1",
            runtime_settings=RuntimeSettings(
                auth_database_url=database_url,
                auth_jwt_private_key_pem=_private_key_pem(),
                auth_jwt_active_kid="kid-canvas-test",
                auth_allowed_redirect_origins=["http://testserver"],
            ),
        )
        auth_service = app.state.auth_service
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )
            response = await client.post(
                "/agents/sales/test-session",
                json={"channel": "web_widget"},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["agent_id"] == "sales"
            assert payload["conversation_id"]
            assert payload["session_token"]
            assert payload["step_id"] is not None

    asyncio.run(run())


def test_public_widget_accepts_anonymous_id_and_attachment_ids(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="test-org",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            widget_session = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "anonymous_id": "anon-test-123",
                    "publishable_key": pk,
                },
            )
            assert widget_session.status_code == 200
            payload = widget_session.json()
            conversation_id = payload["conversation_id"]
            headers = _widget_headers(payload["session_token"])

            conversation = await client.get(f"/conversations/{conversation_id}")
            assert conversation.status_code == 200
            assert conversation.json()["metadata"]["anonymous_id"] == "anon-test-123"

            upload = await client.post(
                f"/public/widget/sessions/{conversation_id}/attachments",
                params={"filename": "demo-notes.txt"},
                headers={
                    **headers,
                    "Content-Type": "text/plain",
                },
                content=b"pricing and demo notes",
            )
            assert upload.status_code == 200
            attachment_id = upload.json()["attachment"]["attachment_id"]

            turn = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages",
                json={
                    "text": "Can you review my attached notes?",
                    "attachment_ids": [attachment_id],
                },
                headers=headers,
            )
            assert turn.status_code == 200
            turn_payload = turn.json()
            assert turn_payload["trace_id"]
            assert "messages" in turn_payload

            resumed = await client.get(
                f"/public/widget/sessions/{conversation_id}",
                headers=headers,
            )
            assert resumed.status_code == 200
            transcript = resumed.json()["messages"]
            user_messages = [message for message in transcript if message["role"] == "user"]
            assert user_messages
            assert user_messages[-1]["text"] == "Can you review my attached notes?"
            assert user_messages[-1]["attachments"][0]["attachment_id"] == attachment_id
            assert user_messages[-1]["attachments"][0]["extraction_status"] == "ready"
            assert "pricing and demo notes" in user_messages[-1]["attachments"][0]["extracted_text"]

            attachment_only_upload = await client.post(
                f"/public/widget/sessions/{conversation_id}/attachments",
                params={"filename": "attachment-only.txt"},
                headers={
                    **headers,
                    "Content-Type": "text/plain",
                },
                content=b"attachment only context",
            )
            assert attachment_only_upload.status_code == 200
            attachment_only_id = attachment_only_upload.json()["attachment"]["attachment_id"]

            attachment_only_turn = await client.post(
                f"/public/widget/sessions/{conversation_id}/messages",
                json={
                    "text": "",
                    "attachment_ids": [attachment_only_id],
                },
                headers=headers,
            )
            assert attachment_only_turn.status_code == 200

            attachment_only_resumed = await client.get(
                f"/public/widget/sessions/{conversation_id}",
                headers=headers,
            )
            assert attachment_only_resumed.status_code == 200
            attachment_only_transcript = attachment_only_resumed.json()["messages"]
            attachment_only_user_messages = [
                message for message in attachment_only_transcript if message["role"] == "user"
            ]
            assert attachment_only_user_messages[-1]["text"] == ""
            assert attachment_only_user_messages[-1]["attachments"][0]["attachment_id"] == attachment_only_id
            assert attachment_only_user_messages[-1]["attachments"][0]["extraction_status"] == "ready"
            assert "attachment only context" in attachment_only_user_messages[-1]["attachments"][0]["extracted_text"]

    asyncio.run(run())


def test_api_exposes_review_surfaces_replay_metrics_and_channel_adapters(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="test-org",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            publish_review = await client.get("/agents/sales/publish-review")
            assert publish_review.status_code == 200
            review_payload = publish_review.json()
            assert review_payload["agent_id"] == "sales"
            assert "validation" in review_payload
            assert "qualification" in review_payload
            assert any(item["code"] == "evaluation.missing_qualified_run" for item in review_payload["warnings"])

            diff = await client.get("/agents/sales/diff")
            assert diff.status_code == 200
            diff_payload = diff.json()
            assert diff_payload["agent_id"] == "sales"
            assert "summary" in diff_payload

            audit = await client.get("/agents/sales/audit")
            assert audit.status_code == 200
            assert audit.json()["events"]

            replay = await client.post(
                "/agents/sales/replay",
                json={
                    "turns": [
                        {
                            "event_type": "user_message",
                            "modality": "text",
                            "text": "Can you explain how the workflow builder works?",
                            "metadata": {"source": "fixture"},
                        },
                        {
                            "event_type": "user_message",
                            "modality": "text",
                            "text": "I want to book a demo.",
                            "metadata": {"source": "fixture"},
                        },
                    ],
                    "channel": "web_chat",
                    "conversation_id": "replay-demo-sales",
                    "starting_step_id": "discover",
                    "seed_facts": {"session_goal": "evaluate"},
                    "metadata": {"initiator": "review-surface"},
                },
            )
            assert replay.status_code == 200
            replay_payload = replay.json()
            assert replay_payload["simulation"]["final_step_id"] in {
                "collect_booking_details",
                "collect_booking_details",
                "submit_lead",
                "request_submitted",
            }
            assert replay_payload["metrics"]["trace_count"] >= 2
            replay_conversation = await client.get("/conversations/replay-demo-sales")
            assert replay_conversation.status_code == 200
            replay_conversation_payload = replay_conversation.json()
            assert replay_conversation_payload["mode"] == "simulation"
            assert replay_conversation_payload["metadata"]["simulation"]["source"] == "replay"
            assert replay_conversation_payload["metadata"]["simulation"]["starting_step_id"] == "discover"
            assert replay_conversation_payload["metadata"]["simulation"]["seed_facts"] == {"session_goal": "evaluate"}
            assert replay_conversation_payload["metadata"]["initiator"] == "review-surface"
            assert replay_conversation_payload["facts"]["session_goal"] == "evaluate"

            metrics = await client.get("/agents/sales/metrics")
            assert metrics.status_code == 200
            metrics_payload = metrics.json()
            assert metrics_payload["agent_id"] == "sales"
            assert metrics_payload["trace_count"] >= 2

            widget_config = await client.get("/public/widget/config", params={"agent_id": "sales"})
            assert widget_config.status_code == 200
            assert widget_config.json()["agent_id"] == "sales"

            widget_session = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "conversation_id": "widget-resume-demo",
                    "publishable_key": pk,
                },
            )
            assert widget_session.status_code == 200
            widget_payload = widget_session.json()
            assert widget_payload["conversation_id"] == "widget-resume-demo"
            assert widget_payload["session_token"]

            unauthorized_widget_resume = await client.get("/public/widget/sessions/widget-resume-demo")
            assert unauthorized_widget_resume.status_code == 401

            widget_resume = await client.get(
                "/public/widget/sessions/widget-resume-demo",
                headers=_widget_headers(widget_payload["session_token"]),
            )
            assert widget_resume.status_code == 200
            assert widget_resume.json()["resumed"] is True

            widget_projection = await client.get(
                "/public/widget/sessions/widget-resume-demo/projection",
                headers=_widget_headers(widget_payload["session_token"]),
            )
            assert widget_projection.status_code == 200
            assert widget_projection.json()["snapshot_id"]

            whatsapp = await client.post(
                "/channels/whatsapp/messages",
                json={
                    "agent_id": "sales",
                    "external_session_id": "wa-thread-1",
                    "text": "Can you explain what the product does?",
                },
            )
            assert whatsapp.status_code == 200
            whatsapp_payload = whatsapp.json()
            assert whatsapp_payload["channel"] == "whatsapp"
            assert whatsapp_payload["conversation_id"] == "whatsapp:wa-thread-1"
            assert whatsapp_payload["realtime_session_id"]
            assert whatsapp_payload["messages"]
            whatsapp_conversation = await client.get("/conversations/whatsapp:wa-thread-1")
            assert whatsapp_conversation.status_code == 200
            assert whatsapp_conversation.json()["channel"] == "whatsapp"
            assert whatsapp_conversation.json()["status"] == "active"

            phone_start = await client.post(
                "/channels/phone/calls/start",
                json={"agent_id": "sales", "external_session_id": "call-123"},
            )
            assert phone_start.status_code == 200
            assert phone_start.json()["conversation_id"] == "phone:call-123"
            assert phone_start.json()["realtime_session_id"]

            phone_turn = await client.post(
                "/channels/phone/calls/call-123/transcripts",
                json={"text": "I want to book a demo.", "is_final": True, "idempotency_key": "call-123:seg-1"},
            )
            assert phone_turn.status_code == 200
            phone_payload = phone_turn.json()
            assert phone_payload["channel"] == "phone"
            assert phone_payload["trace_id"]
            phone_conversation = await client.get("/conversations/phone:call-123")
            assert phone_conversation.status_code == 200
            assert phone_conversation.json()["channel"] == "phone"
            assert phone_conversation.json()["status"] == "active"

        projection_events = app.state.realtime_control_plane.events.replay(conversation_id="widget-resume-demo")
        projection_names = [(event.family, event.name) for event in projection_events]
        assert ("projection", "widget_snapshot_requested") in projection_names

    asyncio.run(run())


def test_api_exposes_simulation_fixtures_and_evaluation_runs(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="test-org",
        )
        runtime_database_url = str(app.state.runtime_settings.database_url or "")
        _seed_sync_crm_submit_lead_tool(
            app,
            runtime_database_url=runtime_database_url,
            organization_id="test-org",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            policy = await client.get("/agents/sales/evaluation-policy")
            assert policy.status_code == 200
            assert policy.json()["policy"]["minimum_pass_rate_ratio"] == 1.0

            updated_policy = await client.patch(
                "/agents/sales/evaluation-policy",
                json={
                    "minimum_pass_rate_ratio": 0.75,
                    "allow_warning_failures": False,
                    "max_qualified_run_age_hours": 4,
                },
            )
            assert updated_policy.status_code == 200
            assert updated_policy.json()["policy"] == {
                "minimum_pass_rate_ratio": 0.75,
                "allow_warning_failures": False,
                "max_qualified_run_age_hours": 4,
            }

            settings = await client.get("/agents/sales/settings")
            assert settings.status_code == 200
            assert settings.json()["settings"] == {
                "description": "",
                "agent_type": "voice",
                "system_prompt": "You are a helpful AI voice assistant.",
                "llm_config": {
                    "provider": "vertex",
                    "model": "gemini-3-flash-preview",
                    "temperature": 1.0,
                    "classifier": {"strategy": "main_llm"},
                },
                "voice_config": {"voice_id": "en-US-Chirp3-HD-Kore"},
                "knowledge_base_ids": [],
                "persona": None,
                "source_template_id": None,
            }

            updated_settings = await client.patch(
                "/agents/sales/settings",
                json={
                    "description": "Handles demo qualification",
                    "agent_type": "chat",
                    "system_prompt": "You qualify inbound demo requests.",
                    "llm_config": {
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "classifier": {"strategy": "main_llm"},
                    },
                    "knowledge_base_ids": ["doc-1", "doc-2"],
                },
            )
            assert updated_settings.status_code == 200
            assert updated_settings.json()["settings"] == {
                "description": "Handles demo qualification",
                "agent_type": "chat",
                "system_prompt": "You qualify inbound demo requests.",
                "llm_config": {
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "temperature": 1.0,
                    "classifier": {"strategy": "main_llm"},
                },
                "voice_config": {"voice_id": "en-US-Chirp3-HD-Kore"},
                "knowledge_base_ids": ["doc-1", "doc-2"],
                "persona": None,
                "source_template_id": None,
            }

            reread_settings = await client.get("/agents/sales/settings")
            assert reread_settings.status_code == 200
            assert reread_settings.json()["settings"]["knowledge_base_ids"] == ["doc-1", "doc-2"]

            reread_policy = await client.get("/agents/sales/evaluation-policy")
            assert reread_policy.status_code == 200
            assert reread_policy.json()["policy"] == {
                "minimum_pass_rate_ratio": 0.75,
                "allow_warning_failures": False,
                "max_qualified_run_age_hours": 4,
            }

            agents = await client.get("/agents")
            assert agents.status_code == 200
            sales = next(item for item in agents.json() if item["id"] == "sales")
            assert sales["description"] == "Handles demo qualification"
            assert sales["agent_type"] == "chat"
            assert sales["llm_provider"] == "openai"
            assert sales["llm_model"] == "gpt-4.1-mini"
            assert sales["knowledge_base_count"] == 2
            assert sales["has_draft_version"] is True
            assert sales["has_published_version"] is True

            fixture = await _create_simple_simulation_fixture(client, "sales", name="api-eval")

            fixtures = await client.get("/agents/sales/simulation-fixtures")
            assert fixtures.status_code == 200
            assert [item["fixture_id"] for item in fixtures.json()] == [fixture["fixture_id"]]

            fixture_detail = await client.get(f"/simulation-fixtures/{fixture['fixture_id']}")
            assert fixture_detail.status_code == 200
            assert fixture_detail.json()["name"] == "api-eval"

            patched = await client.patch(
                f"/simulation-fixtures/{fixture['fixture_id']}",
                json={
                    "name": "api-eval-updated",
                    "starting_step_id": "discover",
                    "seed_facts": {"session_goal": "qualify"},
                },
            )
            assert patched.status_code == 200
            patched_payload = patched.json()
            assert patched_payload["name"] == "api-eval-updated"
            assert patched_payload["starting_step_id"] == "discover"
            assert patched_payload["seed_facts"] == {"session_goal": "qualify"}

            exported = await client.get("/agents/sales/simulation-fixtures/export")
            assert exported.status_code == 200
            exported_payload = exported.json()
            assert exported_payload["schema_version"] == "simulation_fixture_bundle.v1"
            assert len(exported_payload["fixtures"]) == 1

            imported = await client.post(
                "/agents/sales/simulation-fixtures/import",
                json={
                    "bundle": exported_payload,
                    "replace_existing": False,
                    "assign_new_ids": True,
                    "activate_imported": False,
                },
            )
            assert imported.status_code == 200
            assert imported.json()["created_count"] == 1
            imported_fixture_id = imported.json()["imported_fixtures"][0]["fixture_id"]

            evaluation_run = await _run_gate_evaluation(
                client,
                "sales",
                fixture_ids=[fixture["fixture_id"]],
            )
            assert evaluation_run["status"] == "completed"

            runs = await client.get("/agents/sales/evaluation-runs")
            assert runs.status_code == 200
            assert runs.json()[0]["evaluation_run_id"] == evaluation_run["evaluation_run_id"]

            run_detail = await client.get(f"/evaluation-runs/{evaluation_run['evaluation_run_id']}")
            assert run_detail.status_code == 200
            assert run_detail.json()["qualified_at"] is not None

            run_results = await client.get(f"/evaluation-runs/{evaluation_run['evaluation_run_id']}/results")
            assert run_results.status_code == 200
            results_payload = run_results.json()
            assert len(results_payload) == 1
            assert results_payload[0]["status"] == "passed"

            result_detail = await client.get(
                f"/evaluation-runs/{evaluation_run['evaluation_run_id']}/results/{results_payload[0]['case_result_id']}"
            )
            assert result_detail.status_code == 200
            assert result_detail.json()["case_result_id"] == results_payload[0]["case_result_id"]

            result_review = await client.get(
                f"/evaluation-runs/{evaluation_run['evaluation_run_id']}/results/{results_payload[0]['case_result_id']}/review"
            )
            assert result_review.status_code == 200
            review_payload = result_review.json()
            assert review_payload["case_result"]["case_result_id"] == results_payload[0]["case_result_id"]
            assert review_payload["conversation"]["metadata"]["simulation"]["source"] == "evaluation"
            assert isinstance(review_payload["traces"], list)

            latest_qualified = await client.get("/agents/sales/latest-qualified-run")
            assert latest_qualified.status_code == 200
            assert latest_qualified.json()["evaluation_run_id"] == evaluation_run["evaluation_run_id"]

            async_run = await client.post(
                "/agents/sales/evaluation-runs",
                json={
                    "fixture_ids": [fixture["fixture_id"]],
                    "gate_eligible": False,
                    "mode": "manual_batch",
                    "source": "api",
                    "execution_mode": "async",
                },
            )
            assert async_run.status_code == 200
            async_payload = async_run.json()
            assert async_payload["status"] == "queued"
            completed_async_run = await _wait_for_evaluation_run(client, async_payload["evaluation_run_id"])
            assert completed_async_run["status"] == "completed"

            runtime_status = await client.get("/evaluation-runtime/status")
            assert runtime_status.status_code == 200
            assert runtime_status.json()["max_workers"] >= 1

            stop = await client.post(f"/evaluation-runs/{evaluation_run['evaluation_run_id']}/stop")
            assert stop.status_code == 200
            assert stop.json()["status"] == "completed"

            publish_review = await client.get("/agents/sales/publish-review")
            assert publish_review.status_code == 200
            review_payload = publish_review.json()
            assert review_payload["qualification"]["latest_qualified_run_id"] == evaluation_run["evaluation_run_id"]
            assert review_payload["qualification"]["minimum_pass_rate_ratio"] == 0.75
            assert review_payload["qualification"]["allow_warning_failures"] is False
            assert review_payload["qualification"]["max_qualified_run_age_hours"] == 4
            assert review_payload["can_publish"] is True

            deleted = await client.delete(f"/simulation-fixtures/{fixture['fixture_id']}")
            assert deleted.status_code == 204

            deleted_imported = await client.delete(f"/simulation-fixtures/{imported_fixture_id}")
            assert deleted_imported.status_code == 204

            active_fixtures = await client.get(
                "/agents/sales/simulation-fixtures",
                params={"is_active": "true"},
            )
            assert active_fixtures.status_code == 200
            assert active_fixtures.json() == []

    asyncio.run(run())


def test_agent_template_clone_applies_classifier_defaults_by_workflow(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
            runtime_settings=_auth_runtime_settings(),
        )
        transport = httpx.ASGITransport(app=app)
        auth_service = app.state.auth_service

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            sales_clone = await client.post(
                "/agent-templates/gtpl_sales/clone",
                json={"agent_name": "Sales Template Copy"},
            )
            assert sales_clone.status_code == 200
            sales_settings = await client.get(f"/agents/{sales_clone.json()["agent_id"]}/settings")
            assert sales_settings.status_code == 200
            assert sales_settings.json()["settings"]["llm_config"]["classifier"] == {
                "strategy": "main_llm",
            }

            healthcare_clone = await client.post(
                "/agent-templates/gtpl_healthcare_triage/clone",
                json={"agent_name": "Healthcare Template Copy"},
            )
            assert healthcare_clone.status_code == 200
            healthcare_settings = await client.get(
                f"/agents/{healthcare_clone.json()['agent_id']}/settings"
            )
            assert healthcare_settings.status_code == 200
            assert healthcare_settings.json()["settings"]["llm_config"]["classifier"] == {
                "strategy": "main_llm",
            }

    asyncio.run(run())

def test_api_exposes_journey_definition_review_and_publish(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
        )
        runtime_database_url = str(app.state.runtime_settings.database_url or "")
        _seed_sync_crm_submit_lead_tool(app, runtime_database_url=runtime_database_url)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            definition = await _create_journey_definition(client)
            definition_id = definition["definition_id"]

            listing = await client.get("/journey-definitions")
            assert listing.status_code == 200
            assert listing.json()["definitions"][0]["definition_id"] == definition_id

            version_response = await client.post(
                f"/journey-definitions/{definition_id}/versions",
                json={
                    "rules": {
                        "entry_rules": [{"kind": "conversation_started"}],
                        "milestones": [
                            {
                                "milestone_id": "discover",
                                "name": "Discover",
                                "order_index": 1,
                                "enter_when": [{"kind": "step_entered", "value": "discover"}],
                            }
                        ],
                        "outcome_rules": {
                            "completed": [{"kind": "fact_present", "value": "booking_id"}],
                        },
                    }
                },
            )
            assert version_response.status_code == 200
            version_payload = version_response.json()
            assert version_payload["version_number"] == 1

            version_detail = await client.get(
                f"/journey-definition-versions/{version_payload['definition_version_id']}"
            )
            assert version_detail.status_code == 200
            assert version_detail.json()["definition_version_id"] == version_payload["definition_version_id"]

            review = await client.get(f"/journey-definitions/{definition_id}/review")
            assert review.status_code == 200
            review_payload = review.json()
            assert review_payload["readiness"]["can_publish"] is True
            assert review_payload["draft_version"]["definition_version_id"] == version_payload["definition_version_id"]
            assert any(
                item["code"] == "journey.definition.first_publish_pending"
                for item in review_payload["readiness"]["warnings"]
            )

            published = await client.post(f"/journey-definitions/{definition_id}/publish", json={})
            assert published.status_code == 200
            published_payload = published.json()
            assert published_payload["status"] == "published"
            assert published_payload["published_at"] is not None

            definition_detail = await client.get(f"/journey-definitions/{definition_id}")
            assert definition_detail.status_code == 200
            definition_payload = definition_detail.json()
            assert definition_payload["current_published_version_id"] == published_payload["definition_version_id"]
            assert definition_payload["current_draft_version_id"] is None

    asyncio.run(run())


def test_api_updates_journey_definition_slug(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
        )
        runtime_database_url = str(app.state.runtime_settings.database_url or "")
        _seed_sync_crm_submit_lead_tool(app, runtime_database_url=runtime_database_url)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            definition = await _create_journey_definition(client, slug="demo-booking")
            definition_id = definition["definition_id"]

            updated = await client.patch(
                f"/journey-definitions/{definition_id}",
                json={"slug": "enterprise-demo-booking", "name": "Enterprise demo booking"},
            )
            assert updated.status_code == 200
            payload = updated.json()
            assert payload["slug"] == "enterprise-demo-booking"
            assert payload["name"] == "Enterprise demo booking"

    asyncio.run(run())


def test_api_duplicates_and_archives_journey_definition(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            definition = await _create_journey_definition(client, slug="demo-booking")
            definition_id = definition["definition_id"]
            await _publish_journey_version(client, definition_id=definition_id)

            duplicated = await client.post(f"/journey-definitions/{definition_id}/duplicate")
            assert duplicated.status_code == 200
            duplicate_payload = duplicated.json()
            assert duplicate_payload["definition_id"] != definition_id
            assert duplicate_payload["slug"] == "demo-booking-copy"
            assert duplicate_payload["current_draft_version_id"] is not None

            duplicate_versions = await client.get(
                f"/journey-definitions/{duplicate_payload['definition_id']}/versions"
            )
            assert duplicate_versions.status_code == 200
            assert len(duplicate_versions.json()["versions"]) == 1

            archived = await client.post(f"/journey-definitions/{definition_id}/archive")
            assert archived.status_code == 200
            assert archived.json()["status"] == "archived"

    asyncio.run(run())

def test_api_rejects_blocked_journey_publish(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            definition = await _create_journey_definition(client, slug="blocked-booking")
            definition_id = definition["definition_id"]

            version_response = await client.post(
                f"/journey-definitions/{definition_id}/versions",
                json={
                    "rules": {
                        "entry_rules": [{"kind": "conversation_started"}],
                        "milestones": [
                            {
                                "milestone_id": "discover",
                                "name": "Discover",
                                "order_index": 1,
                                "enter_when": [{"kind": "step_entered", "value": "discover"}],
                            }
                        ],
                        "outcome_rules": {
                            "unexpected": [{"kind": "fact_present", "value": "booking_id"}],
                        },
                    }
                },
            )
            assert version_response.status_code == 200

            review = await client.get(f"/journey-definitions/{definition_id}/review")
            assert review.status_code == 200
            review_payload = review.json()
            assert review_payload["readiness"]["can_publish"] is False
            assert any(
                item["code"] == "journey.outcome_rules.invalid_key"
                for item in review_payload["readiness"]["blockers"]
            )

            publish = await client.post(f"/journey-definitions/{definition_id}/publish", json={})
            assert publish.status_code == 409
            assert publish.json()["detail"]["message"] == "journey definition publish blocked by review errors"

    asyncio.run(run())


def test_api_tracks_journey_instances_and_exposes_analytics(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            definition = await _create_journey_definition(client, slug="tracked-booking")
            await _publish_journey_version(
                client,
                definition_id=definition["definition_id"],
                outcome_rules={
                    "transferred": [{"kind": "realtime_event", "value": "handoff:transferred"}],
                },
            )

            started_one = await client.post(
                "/conversations",
                json={
                    "agent_id": "sales",
                    "seed_facts": {"customer_id": "subject-1"},
                },
            )
            assert started_one.status_code == 200
            conversation_one = started_one.json()["conversation"]["conversation_id"]

            started_two = await client.post(
                "/conversations",
                json={
                    "agent_id": "sales",
                    "seed_facts": {"customer_id": "subject-2"},
                },
            )
            assert started_two.status_code == 200

            app.state.realtime_control_plane.events.append(
                conversation_id=conversation_one,
                organization_id=None,
                family="handoff",
                name="transferred",
                payload={"reason": "operator"},
            )

            journeys = await client.get("/journeys")
            assert journeys.status_code == 200
            journeys_payload = journeys.json()
            assert journeys_payload["total_count"] == 2
            first_journey_id = journeys_payload["journeys"][0]["journey_id"]

            detail = await client.get(f"/journeys/{first_journey_id}")
            assert detail.status_code == 200
            detail_payload = detail.json()
            assert detail_payload["definition"]["definition_id"] == definition["definition_id"]
            assert detail_payload["touchpoints"]
            assert detail_payload["events"]

            touchpoints = await client.get(f"/journeys/{first_journey_id}/touchpoints")
            assert touchpoints.status_code == 200
            assert len(touchpoints.json()["touchpoints"]) == 1

            events = await client.get(f"/journeys/{first_journey_id}/events")
            assert events.status_code == 200
            assert any(item["event_type"] == "journey_opened" for item in events.json()["events"])

            annotation = await client.post(
                f"/journeys/{first_journey_id}/annotations",
                json={"note": "Operator reviewed"},
            )
            assert annotation.status_code == 200
            assert annotation.json()["event_type"] == "manual_annotation"

            evidence = await client.get(f"/journeys/{first_journey_id}/evidence")
            assert evidence.status_code == 200
            evidence_payload = evidence.json()
            assert evidence_payload["conversations"]
            assert any(evidence_payload["traces_by_conversation"].values())
            assert any(evidence_payload["realtime_events_by_conversation"].values())

            funnel = await client.get(
                "/journey-analytics/funnel",
                params={"definition_id": definition["definition_id"]},
            )
            assert funnel.status_code == 200
            funnel_payload = funnel.json()
            assert funnel_payload["total_journeys"] == 2
            assert funnel_payload["stages"][0]["entered_count"] == 2

            drop_off = await client.get(
                "/journey-analytics/drop-off",
                params={"definition_id": definition["definition_id"]},
            )
            assert drop_off.status_code == 200
            assert drop_off.json()["rows"][0]["milestone_id"] == "discover"

            paths = await client.get(
                "/journey-analytics/paths",
                params={"definition_id": definition["definition_id"]},
            )
            assert paths.status_code == 200
            assert paths.json()["rows"]

            trends = await client.get(
                "/journey-analytics/trends",
                params={"definition_id": definition["definition_id"]},
            )
            assert trends.status_code == 200
            assert trends.json()["points"]

            channel_mix = await client.get(
                "/journey-analytics/channel-mix",
                params={"definition_id": definition["definition_id"]},
            )
            assert channel_mix.status_code == 200
            assert channel_mix.json()["rows"][0]["channel"] == "web_chat"

    asyncio.run(run())


def test_api_replays_journeys_and_rebuilds_journey_analytics(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            definition = await _create_journey_definition(client, slug="repair-booking")
            await _publish_journey_version(client, definition_id=definition["definition_id"])
            tracker = app.state.journey_tracker
            now = datetime.now(timezone.utc)
            conversation = ConversationState(
                conversation_id="conv-repair",
                organization_id="public",
                agent_id="sales",
                agent_version_id="agent-version-1",
                channel="web_chat",
                step_id="discover",
                facts={"customer_id": "subject-repair", "booking_id": "book-1"},
                started_at=now,
                updated_at=now,
            )
            trace = TurnTrace(
                trace_id="trace-repair",
                conversation_id=conversation.conversation_id,
                organization_id="public",
                turn_id="turn-repair",
                agent_id=conversation.agent_id,
                agent_version_id=conversation.agent_version_id,
                step_before="entry",
                step_after="discover",
                chosen_action=ActionRecord(type="reply", reason="hello"),
                recorded_at=now,
            )
            tracker._conversation_store.save(conversation)  # noqa: SLF001
            tracker._trace_store.append(trace)  # noqa: SLF001
            tracker.process_turn_trace(trace, conversation=conversation)

            journeys = await client.get("/journeys", params={"definition_id": definition["definition_id"]})
            assert journeys.status_code == 200
            journey_id = journeys.json()["journeys"][0]["journey_id"]

            annotation = await client.post(
                f"/journeys/{journey_id}/annotations",
                json={"note": "Keep this note"},
            )
            assert annotation.status_code == 200

            journey_store = app.state.journey_instance_store
            corrupted = journey_store.load_instance(journey_id, organization_id="public")
            assert corrupted is not None
            journey_store.save_instance(
                corrupted.model_copy(
                    update={
                        "status": "open",
                        "outcome": None,
                        "current_milestone_id": None,
                        "current_milestone_order": None,
                        "milestone_path": [],
                        "ended_at": None,
                    }
                )
            )

            replay = await client.post(f"/journeys/{journey_id}/replay", json={})
            assert replay.status_code == 200
            replay_payload = replay.json()
            assert replay_payload["journey_id"] == journey_id
            assert replay_payload["preserved_event_count"] == 1

            detail = await client.get(f"/journeys/{journey_id}")
            assert detail.status_code == 200
            detail_payload = detail.json()
            assert detail_payload["instance"]["status"] == "completed"
            assert detail_payload["instance"]["outcome"] == "completed"

            events = await client.get(f"/journeys/{journey_id}/events")
            assert events.status_code == 200
            event_types = [item["event_type"] for item in events.json()["events"]]
            assert "manual_annotation" in event_types
            assert "journey_opened" in event_types

            definition_replay = await client.post(
                f"/journey-definitions/{definition['definition_id']}/replay",
                json={},
            )
            assert definition_replay.status_code == 200
            definition_replay_payload = definition_replay.json()
            assert definition_replay_payload["replayed_journey_ids"] == [journey_id]
            assert definition_replay_payload["failures"] == []

            queued_journey_replay = await client.post(
                f"/journeys/{journey_id}/replay",
                json={"execution_mode": "async"},
            )
            assert queued_journey_replay.status_code == 200
            queued_journey_replay_payload = queued_journey_replay.json()
            assert queued_journey_replay_payload["kind"] == "journey_replay"
            completed_journey_replay = await _wait_for_journey_job(client, queued_journey_replay_payload["job_id"], app=app)
            assert completed_journey_replay["status"] == "completed"

            queued_definition_replay = await client.post(
                f"/journey-definitions/{definition['definition_id']}/replay",
                json={"execution_mode": "async"},
            )
            assert queued_definition_replay.status_code == 200
            queued_definition_replay_payload = queued_definition_replay.json()
            assert queued_definition_replay_payload["kind"] == "definition_replay"
            completed_definition_replay = await _wait_for_journey_job(
                client,
                queued_definition_replay_payload["job_id"],
                app=app,
            )
            assert completed_definition_replay["status"] == "completed"

            rebuild = await client.post(
                "/journey-analytics/rebuild",
                json={"definition_id": definition["definition_id"]},
            )
            assert rebuild.status_code == 200
            rebuild_payload = rebuild.json()
            assert rebuild_payload["definition_id"] == definition["definition_id"]
            assert rebuild_payload["rebuilt_views"] == [
                "funnel",
                "drop_off",
                "paths",
                "trends",
                "channel_mix",
            ]
            assert rebuild_payload["snapshot_count"] == 5

            runtime_status = await client.get("/journey-runtime/status")
            assert runtime_status.status_code == 200
            runtime_status_payload = runtime_status.json()
            metric_by_kind = {item["kind"]: item for item in runtime_status_payload["job_metrics"]}
            assert metric_by_kind["journey_replay"]["completed_jobs"] >= 1
            assert metric_by_kind["definition_replay"]["completed_jobs"] >= 1
            assert runtime_status_payload["alerts"] == []

    asyncio.run(run())


def test_api_supports_journey_rebuild_runtime_and_definition_import_export(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            definition = await _create_journey_definition(client, slug="backfill-api-booking")
            await _publish_journey_version(client, definition_id=definition["definition_id"])
            tracker = app.state.journey_tracker
            now = datetime.now(timezone.utc)
            conversation = ConversationState(
                conversation_id="conv-backfill-api",
                organization_id="public",
                agent_id="sales",
                agent_version_id="agent-version-1",
                channel="web_chat",
                step_id="discover",
                facts={"customer_id": "subject-backfill-api", "booking_id": "book-1"},
                started_at=now,
                updated_at=now,
            )
            trace = TurnTrace(
                trace_id="trace-backfill-api",
                conversation_id=conversation.conversation_id,
                organization_id="public",
                turn_id="turn-backfill-api",
                agent_id=conversation.agent_id,
                agent_version_id=conversation.agent_version_id,
                step_before="entry",
                step_after="discover",
                chosen_action=ActionRecord(type="reply", reason="hello"),
                recorded_at=now,
            )
            tracker._conversation_store.save(conversation)  # noqa: SLF001
            tracker._trace_store.append(trace)  # noqa: SLF001

            rebuild = await client.post(
                f"/journey-definitions/{definition['definition_id']}/rebuild",
                json={"execution_mode": "async"},
            )
            assert rebuild.status_code == 200
            rebuild_job = rebuild.json()
            assert rebuild_job["kind"] == "definition_rebuild"

            job = await _wait_for_journey_job(client, rebuild_job["job_id"], app=app)
            assert job["status"] == "completed"
            assert job["result"]["discovered_conversation_count"] == 1

            runtime = await client.get("/journey-runtime/status")
            assert runtime.status_code == 200
            assert runtime.json()["completed_jobs"] >= 1

            exported = await client.get(
                "/journey-definitions/export",
                params={"definition_id": definition["definition_id"]},
            )
            assert exported.status_code == 200
            exported_payload = exported.json()
            assert exported_payload["definitions"][0]["definition"]["definition_id"] == definition["definition_id"]

            imported = await client.post(
                "/journey-definitions/import",
                json={"bundle": exported_payload},
            )
            assert imported.status_code == 200
            imported_payload = imported.json()
            assert len(imported_payload["imported_definition_ids"]) == 1
            assert imported_payload["imported_definition_ids"][0] != definition["definition_id"]

            abandonment = await client.post(
                "/journey-runtime/abandonment-sweep",
                json={"definition_id": definition["definition_id"], "execution_mode": "async"},
            )
            assert abandonment.status_code == 200
            abandonment_job = await _wait_for_journey_job(client, abandonment.json()["job_id"], app=app)
            assert abandonment_job["status"] == "completed"

    asyncio.run(run())


def test_api_scopes_organization_management_and_conversations_when_auth_enabled(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
            runtime_settings=_auth_runtime_settings(),
        )
        transport = httpx.ASGITransport(app=app)
        auth_service = app.state.auth_service

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as anonymous_client:
            unauthorized = await anonymous_client.get("/organization")
            assert unauthorized.status_code == 401

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(
                admin_client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            organization = await admin_client.get("/organization")
            assert organization.status_code == 200
            assert organization.json()["organization_id"] == "org-1"
            assert organization.json()["name"] == "Acme"

            updated_org = await admin_client.patch(
                "/organization",
                json={"name": "Acme AI", "brand_color": "#112233"},
            )
            assert updated_org.status_code == 200
            assert updated_org.json()["name"] == "Acme AI"
            assert updated_org.json()["brand_color"] == "#112233"

            reserved_settings_update = await admin_client.patch(
                "/organization",
                json={"settings": {"auth_revoked_after_epoch": 123}},
            )
            assert reserved_settings_update.status_code == 400
            assert "reserved organization security settings" in reserved_settings_update.json()["detail"]

            members = await admin_client.get("/organization/members")
            assert members.status_code == 200
            assert {item["user_id"] for item in members.json()} == {"user-admin", "user-analyst"}

            created_member = await admin_client.post(
                "/organization/members",
                json={"email": "existing@example.com", "role": "developer"},
            )
            assert created_member.status_code == 200
            assert created_member.json()["user_id"] == "user-existing"
            assert created_member.json()["role"] == "developer"

            updated_member = await admin_client.patch(
                "/organization/members/user-existing",
                json={"role": "analyst"},
            )
            assert updated_member.status_code == 200
            assert updated_member.json()["role"] == "analyst"
            role_events = app.state.audit_store.list_events(
                organization_id="org-1",
                event_type=ADMIN_ROLE_CHANGED,
            )
            assert any(
                event.resource_id == "user-existing"
                and event.detail.get("old_role") == "developer"
                and event.detail.get("new_role") == "analyst"
                for event in role_events
            )

            started = await admin_client.post("/conversations", json={"agent_id": "sales"})
            assert started.status_code == 200
            payload = started.json()
            conversation_id = payload["conversation"]["conversation_id"]
            assert payload["conversation"]["organization_id"] == "org-1"

            conversations = await admin_client.get("/conversations")
            assert conversations.status_code == 200
            assert [item["conversation_id"] for item in conversations.json()] == [conversation_id]

            removed_member = await admin_client.delete("/organization/members/user-existing")
            assert removed_member.status_code == 204
            removal_events = app.state.audit_store.list_events(
                organization_id="org-1",
                event_type=ADMIN_USER_REMOVED,
            )
            assert any(
                event.resource_id == "user-existing"
                and event.detail.get("removed_role") == "analyst"
                for event in removal_events
            )

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as analyst_client:
            _authorize_client(
                analyst_client,
                auth_service=auth_service,
                user_id="user-analyst",
                organization_id="org-1",
            )

            analyst_members = await analyst_client.get("/organization/members")
            assert analyst_members.status_code == 200

            forbidden_update = await analyst_client.patch("/organization", json={"name": "Nope"})
            assert forbidden_update.status_code == 403

            forbidden_revoke = await analyst_client.post("/organization/auth/revoke-sessions")
            assert forbidden_revoke.status_code == 403

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as other_org_client:
            _authorize_client(
                other_org_client,
                auth_service=auth_service,
                user_id="user-org2-admin",
                organization_id="org-2",
            )

            other_conversations = await other_org_client.get("/conversations")
            assert other_conversations.status_code == 200
            assert other_conversations.json() == []

            hidden_conversation = await other_org_client.get(f"/conversations/{conversation_id}")
            assert hidden_conversation.status_code == 404

            hidden_traces = await other_org_client.get(f"/conversations/{conversation_id}/traces")
            assert hidden_traces.status_code == 404

            hidden_realtime_events = await other_org_client.get(
                f"/conversations/{conversation_id}/realtime-events"
            )
            assert hidden_realtime_events.status_code == 404

    asyncio.run(run())

def test_api_allows_analyst_review_and_simulation_workflows_when_auth_enabled(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
            runtime_settings=_auth_runtime_settings(),
        )
        transport = httpx.ASGITransport(app=app)
        auth_service = app.state.auth_service

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(
                admin_client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )
            definition = await _create_journey_definition(admin_client, slug="analyst-review-booking")
            await _publish_journey_version(admin_client, definition_id=definition["definition_id"])

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as analyst_client:
            _authorize_client(
                analyst_client,
                auth_service=auth_service,
                user_id="user-analyst",
                organization_id="org-1",
            )

            replay = await analyst_client.post(
                "/agents/sales/replay",
                json={
                    "channel": "web_chat",
                    "starting_step_id": "discover",
                    "seed_facts": {"session_goal": "review"},
                    "metadata": {"initiator": "analyst-review"},
                    "turns": [
                        {
                            "turn_id": "review-turn-1",
                            "dedupe_key": "review-turn-1",
                            "event_type": "user_message",
                            "modality": "text",
                            "text": "I want to book a demo.",
                        }
                    ],
                },
            )
            assert replay.status_code == 200
            assert replay.json()["simulation"]["start"]["step_after"] == "discover"

            fixture = await _create_simple_simulation_fixture(analyst_client, "sales", name="analyst-eval")

            patched_fixture = await analyst_client.patch(
                f"/simulation-fixtures/{fixture['fixture_id']}",
                json={
                    "name": "analyst-eval-updated",
                    "starting_step_id": "discover",
                    "seed_facts": {"session_goal": "qualify"},
                },
            )
            assert patched_fixture.status_code == 200
            assert patched_fixture.json()["name"] == "analyst-eval-updated"

            exported = await analyst_client.get("/agents/sales/simulation-fixtures/export")
            assert exported.status_code == 200

            imported = await analyst_client.post(
                "/agents/sales/simulation-fixtures/import",
                json={
                    "bundle": exported.json(),
                    "replace_existing": False,
                    "assign_new_ids": True,
                    "activate_imported": False,
                },
            )
            assert imported.status_code == 200
            imported_fixture_id = imported.json()["imported_fixtures"][0]["fixture_id"]

            evaluation_run = await _run_gate_evaluation(
                analyst_client,
                "sales",
                fixture_ids=[fixture["fixture_id"]],
            )
            assert evaluation_run["status"] == "completed"

            stop = await analyst_client.post(f"/evaluation-runs/{evaluation_run['evaluation_run_id']}/stop")
            assert stop.status_code == 200
            assert stop.json()["status"] == "completed"

            started = await analyst_client.post(
                "/conversations",
                json={
                    "agent_id": "sales",
                    "seed_facts": {"customer_id": "subject-analyst-review"},
                },
            )
            assert started.status_code == 200

            journeys = await analyst_client.get("/journeys")
            assert journeys.status_code == 200
            journey_id = journeys.json()["journeys"][0]["journey_id"]

            annotation = await analyst_client.post(
                f"/journeys/{journey_id}/annotations",
                json={"note": "Analyst reviewed"},
            )
            assert annotation.status_code == 200
            assert annotation.json()["event_type"] == "manual_annotation"

            deleted_fixture = await analyst_client.delete(f"/simulation-fixtures/{fixture['fixture_id']}")
            assert deleted_fixture.status_code == 204

            deleted_imported_fixture = await analyst_client.delete(f"/simulation-fixtures/{imported_fixture_id}")
            assert deleted_imported_fixture.status_code == 204

    asyncio.run(run())


def test_api_requires_developer_role_for_intent_tags_authoring_and_allows_analyst_review_access(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        store = _seed_authenticated_api_store(auth_database_url)
        developer = store.save_user(
            User(
                user_id="user-developer",
                email="developer@example.com",
                display_name="Developer",
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=developer.user_id,
                organization_id="org-1",
                role="developer",
            )
        )

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
            runtime_settings=_auth_runtime_settings(),
        )
        transport = httpx.ASGITransport(app=app)
        auth_service = app.state.auth_service
        runtime = app.state.intent_tags_runtime

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(
                admin_client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )
            started = await admin_client.post("/conversations", json={"agent_id": "sales"})
            assert started.status_code == 200
            conversation_id = started.json()["conversation"]["conversation_id"]

            seeded_turn = await admin_client.post(
                f"/conversations/{conversation_id}/turns",
                json={
                    "turn_id": "intent_tags_review_seed",
                    "dedupe_key": "intent_tags_review_seed",
                    "channel": "web_chat",
                    "modality": "text",
                    "event_type": "user_message",
                    "text": "Can you explain what the product does?",
                },
            )
            assert seeded_turn.status_code == 200

        seeded_events = runtime.store.list_classification_events(
            "org-1",
            conversation_id=conversation_id,
            limit=10,
        )
        assert seeded_events
        review_item = runtime.review_service.create_review_item(
            organization_id="org-1",
            review_kind="manual_flag",
            classification_event_id=seeded_events[0].classification_event_id,
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as analyst_client:
            _authorize_client(
                analyst_client,
                auth_service=auth_service,
                user_id="user-analyst",
                organization_id="org-1",
            )

            taxonomy = await analyst_client.get("/intent-tags/taxonomy", params={"organization_id": "org-1"})
            assert taxonomy.status_code == 200

            analytics = await analyst_client.get("/intent-tags/analytics", params={"organization_id": "org-1"})
            assert analytics.status_code == 200

            forbidden_create = await analyst_client.post(
                "/intent-tags/intents",
                json={
                    "organization_id": "org-1",
                    "agent_id": "sales",
                    "name": "demo_request",
                    "display_name": "Demo request",
                },
            )
            assert forbidden_create.status_code == 403
            assert forbidden_create.json()["detail"] == "developer role required for organization access"

            claimed_review = await analyst_client.post(
                f"/intent-tags/reviews/{review_item.review_item_id}/claim",
                params={"organization_id": "org-1"},
                json={"user_id": "user-analyst"},
            )
            assert claimed_review.status_code == 200
            assert claimed_review.json()["claimed_by_user_id"] == "user-analyst"

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as developer_client:
            _authorize_client(
                developer_client,
                auth_service=auth_service,
                user_id="user-developer",
                organization_id="org-1",
            )

            created_intent = await developer_client.post(
                "/intent-tags/intents",
                json={
                    "organization_id": "org-1",
                    "agent_id": "sales",
                    "name": "demo_request",
                    "display_name": "Demo request",
                },
            )
            assert created_intent.status_code == 200

    asyncio.run(run())


def test_public_widget_classifier_strategy_prefill_routes_booking_request_from_state_hints(
    postgres_database_url_factory,
    monkeypatch,
) -> None:
    async def run() -> None:
        from ruhu.analytics_tagging.adapters import IntentTagsClassificationResult
        from ruhu.schemas import SemanticEventRecord

        def fake_classify(_self, request):
            assert request.resolved_profile.adapter_name == "gemma_local"
            assert any(item["name"] == "booking_request" for item in request.resolved_profile.effective_intent_catalog)
            # Edge-owned outcomes split: analytics (`intent_detected`) and
            # workflow routing (`routing.outcome_resolved`) are now distinct
            # writers. Production emits both; the test mock mirrors that.
            return IntentTagsClassificationResult(
                semantic_events=[
                    SemanticEventRecord(
                        family="routing",
                        name="outcome_resolved",
                        source="classifier",
                        confidence=0.95,
                        payload={"event": "booking_request"},
                    ),
                    SemanticEventRecord(
                        family="intent_detected",
                        name="booking_request",
                        source="classifier",
                        confidence=0.95,
                    ),
                ],
                adapter_name="gemma_local",
                model_version="gemma_local:test",
            )

        monkeypatch.setattr(
            "ruhu.analytics_tagging.adapters.IntentTagsClassifierRegistry.classify",
            fake_classify,
        )

        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(database_url)
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            interpreter_name="sales",
            bootstrap_organization_id="org-1",
            runtime_settings=RuntimeSettings(
                auth_database_url=database_url,
                auth_jwt_private_key_pem=_private_key_pem(),
                auth_jwt_active_kid="kid-widget-classifier",
                auth_allowed_redirect_origins=["http://testserver"],
            ),
        )
        auth_service = app.state.auth_service
        pk = make_widget_publishable_key(
            database_url,
            agent_id="sales",
            organization_id="org-1",
        )

        # ``strategy=prefill`` requires a production-status LoRA — seed one so
        # the patch isn't rejected by the backend gate.
        from ruhu.classifier.registry import (
            promote_to_production,
            register_candidate,
        )

        runtime_session_factory = build_session_factory(database_url)
        with runtime_session_factory.begin() as seed_session:
            record = register_candidate(
                seed_session,
                agent_id="sales",
                organization_id="org-1",
                lora_name="sales-test-lora-v1",
                model_uri="gs://test/sales-v1.safetensors",
                version="v1",
            )
            promote_to_production(seed_session, lora_id=record.lora_id)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )
            settings = await client.patch(
                "/agents/sales/settings",
                json={"llm_config": {"classifier": {"strategy": "prefill"}}},
            )
            assert settings.status_code == 200

            session = await client.post(
                "/public/widget/sessions",
                json={
                    "agent_id": "sales",
                    "channel": "web_widget",
                    "target": "draft",
                    "publishable_key": pk,
                },
            )
            assert session.status_code == 200
            session_payload = session.json()

            response = await client.post(
                f"/public/widget/sessions/{session_payload['conversation_id']}/messages",
                json={"text": "demo"},
                headers=_widget_headers(session_payload["session_token"]),
            )
            assert response.status_code == 200
            assert response.json()["step_after"] == "collect_booking_details"

    asyncio.run(run())

def test_api_uses_hosted_intent_tags_classifier_adapter_and_packages_context(
    postgres_database_url_factory,
) -> None:
    captured: dict[str, object] = {}

    class FakeHostedClassifierClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["client_kwargs"] = dict(kwargs)

        def __enter__(self) -> "FakeHostedClassifierClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url: str, *, json: dict | None = None, headers: dict | None = None) -> httpx.Response:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = dict(headers or {})
            request = httpx.Request("POST", url, json=json, headers=headers)
            return httpx.Response(
                200,
                json={
                    "intent": "demo_request",
                    "language": "en",
                    "response_language": "en",
                    "tool_route": "notify.sales",
                    "slots": {"lead_channel": "web"},
                    "confidence": 0.93,
                    "signals": {"uncertain_understanding": True},
                    "model": "gemma-hosted-v1",
                    "provider_cost_record": {
                        "amount_usd": 0.07,
                        "cost_type": "classifier_inference",
                        "reference_key": "clf-managed-1",
                    },
                },
                request=request,
            )

    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            intent_tags_classifier_base_url="http://classifier.internal:8011",
            intent_tags_classifier_api_key="classifier-api-key",
            intent_tags_classifier_timeout_seconds=1.25,
            intent_tags_classifier_max_retries=1,
        )
        with patch("ruhu.analytics_tagging.adapters.httpx.Client", FakeHostedClassifierClient):
            app = build_default_app(
                agent_root=agent_root_path,
                database_url=postgres_database_url_factory(),
                runtime_settings=runtime_settings,
                bootstrap_organization_id="public",
            )

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                created_intent = await client.post(
                    "/intent-tags/intents",
                    json={
                        "organization_id": "public",
                        "agent_id": "sales",
                        "name": "demo_request",
                        "display_name": "Demo request",
                    },
                )
                assert created_intent.status_code == 200

                created_profile = await client.post(
                    "/intent-tags/profiles",
                    json={
                        "organization_id": "public",
                        "agent_id": "sales",
                        "adapter_name": "managed_demo",
                    },
                )
                assert created_profile.status_code == 200

                started = await client.post("/conversations", json={"agent_id": "sales"})
                assert started.status_code == 200
                conversation_id = started.json()["conversation"]["conversation_id"]

                demo_turn = await client.post(
                    f"/conversations/{conversation_id}/turns",
                    json={
                        "turn_id": "turn_managed_classifier",
                        "dedupe_key": "turn_managed_classifier",
                        "channel": "web_chat",
                        "modality": "text",
                        "event_type": "user_message",
                        "text": "I want to book a demo.",
                    },
                )
                assert demo_turn.status_code == 200

                events = await client.get(
                    "/intent-tags/events",
                    params={"organization_id": "public", "conversation_id": conversation_id},
                )
                assert events.status_code == 200
                payload = events.json()
                assert [item["intent_name"] for item in payload] == ["demo_request"]
                assert [item["adapter_name"] for item in payload] == ["managed_demo"]
                assert [item["model_version"] for item in payload] == ["gemma-hosted-v1"]

                provider_costs = await client.get(f"/conversations/{conversation_id}/provider-cost-records")
                assert provider_costs.status_code == 200
                provider_cost_payload = provider_costs.json()["items"]
                assert len(provider_cost_payload) == 1
                assert provider_cost_payload[0]["provider"] == "intent_tags_classifier"
                assert provider_cost_payload[0]["cost_type"] == "classifier_inference"
                assert provider_cost_payload[0]["amount_usd"] == 0.07
                assert provider_cost_payload[0]["reference_key"] == "clf-managed-1"

        realtime_events = app.state.realtime_control_plane.events.replay(conversation_id=conversation_id)
        assert ("provider", "cost_recorded") in {(event.family, event.name) for event in realtime_events}

        assert captured["url"] == "http://classifier.internal:8011/v1/classifier/decision"
        assert captured["headers"] == {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer classifier-api-key",
        }
        assert captured["client_kwargs"] == {
            "timeout": 1.25,
            "follow_redirects": False,
        }
        request_payload = captured["json"]
        assert isinstance(request_payload, dict)
        assert request_payload["adapter"] == "managed_demo"
        context = request_payload["context"]
        assert context["stable"]["current_channel"] == "web_chat"
        assert [item["name"] for item in context["stable"]["valid_intents"]] == ["demo_request"]
        assert context["dynamic"]["current_utterance"] == "I want to book a demo."
        assert context["dynamic"]["transcript_window"][0]["role"] == "user"

    asyncio.run(run())


def test_provider_webhook_dispatcher_fans_out_and_delivers_semantic_summary_publications(
    postgres_database_url_factory,
) -> None:
    captured: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["client_kwargs"] = dict(kwargs)

        def __enter__(self) -> "FakeWebhookClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url: str, *, content: bytes | None = None, headers: dict | None = None) -> httpx.Response:
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = dict(headers or {})
            request = httpx.Request("POST", url, content=content, headers=headers)
            return httpx.Response(200, request=request)

    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            interpreter_name="sales",
            provider_shared_secret="provider-webhook-secret",
        )
        with patch("ruhu.analytics_tagging.webhooks.httpx.Client", FakeWebhookClient):
            app = build_default_app(
                agent_root=agent_root_path,
                database_url=postgres_database_url_factory(),
                runtime_settings=runtime_settings,
                bootstrap_organization_id="public",
            )
            runtime_database_url = str(app.state.runtime_settings.database_url or "")
            _seed_sync_crm_submit_lead_tool(app, runtime_database_url=runtime_database_url)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                target = await client.post(
                    "/intent-tags/webhook-targets",
                    json={
                        "organization_id": "public",
                        "name": "Ops Webhook",
                        "url": "https://hooks.example.com/semantic",
                        "agent_ids": ["sales"],
                        "channels": ["web_chat"],
                        "signing_secret_ref": "super-secret",
                    },
                )
                assert target.status_code == 200

                started = await client.post("/conversations", json={"agent_id": "sales"})
                assert started.status_code == 200
                conversation_id = started.json()["conversation"]["conversation_id"]

                demo_turn = await client.post(
                    f"/conversations/{conversation_id}/turns",
                    json={
                        "turn_id": "turn_webhook_demo",
                        "dedupe_key": "turn_webhook_demo",
                        "channel": "web_chat",
                        "modality": "text",
                        "event_type": "user_message",
                        "text": "I want to book a demo.",
                    },
                )
                assert demo_turn.status_code == 200

                _, _, invocation_id = await _advance_sales_booking_to_invocation(
                    client,
                    conversation_id,
                    email="person@example.com",
                    turn_prefix="turn_webhook_booking",
                )

                confirmed = await client.post(f"/conversations/{conversation_id}/tool-invocations/{invocation_id}/confirm")
                assert confirmed.status_code == 200

                dispatched = await client.post(
                    "/providers/intent-tags/webhooks/dispatch",
                    headers={"X-Ruhu-Provider-Secret": "provider-webhook-secret"},
                )
                assert dispatched.status_code == 200
                payload = dispatched.json()
                assert payload["publication_attempted"] == 1
                assert payload["publication_fanned_out"] == 1
                assert payload["delivery_attempted"] == 1
                assert payload["delivery_delivered"] == 1

                targets = await client.get(
                    "/intent-tags/webhook-targets",
                    params={"organization_id": "public"},
                )
                assert targets.status_code == 200
                assert targets.json()[0]["last_success_at"] is not None

        assert captured["url"] == "https://hooks.example.com/semantic"
        assert captured["client_kwargs"] == {
            "timeout": 5.0,
            "follow_redirects": False,
        }
        body = captured["content"]
        assert isinstance(body, bytes)
        delivery_payload = json.loads(body)
        assert delivery_payload["delivery"]["event_name"] == "semantic_summary.finalized"
        assert delivery_payload["summary"]["primary_intent_name"] == "demo_request"
        headers = captured["headers"]
        assert isinstance(headers, dict)
        timestamp = headers["X-Ruhu-Timestamp"]
        expected_signature = hmac.new(
            b"super-secret",
            timestamp.encode("utf-8") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        assert headers["X-Ruhu-Signing-Version"] == "v1"
        assert headers["X-Ruhu-Signature"] == f"sha256={expected_signature}"

    asyncio.run(run())


def test_semantic_summary_webhook_worker_autodelivers_publications(
    postgres_database_url_factory,
) -> None:
    captured: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["client_kwargs"] = dict(kwargs)

        def __enter__(self) -> "FakeWebhookClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url: str, *, content: bytes | None = None, headers: dict | None = None) -> httpx.Response:
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = dict(headers or {})
            request = httpx.Request("POST", url, content=content, headers=headers)
            return httpx.Response(200, request=request)

    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            interpreter_name="sales",
            semantic_summary_webhook_worker_enabled=True,
            semantic_summary_webhook_interval_seconds=0.05,
            semantic_summary_webhook_batch_size=25,
        )
        with patch("ruhu.analytics_tagging.webhooks.httpx.Client", FakeWebhookClient):
            app = build_default_app(
                agent_root=agent_root_path,
                database_url=postgres_database_url_factory(),
                runtime_settings=runtime_settings,
                bootstrap_organization_id="public",
            )
            runtime_database_url = str(app.state.runtime_settings.database_url or "")
            _seed_sync_crm_submit_lead_tool(app, runtime_database_url=runtime_database_url)

            if True:
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    target = await client.post(
                        "/intent-tags/webhook-targets",
                        json={
                            "organization_id": "public",
                            "name": "Auto Worker Webhook",
                            "url": "https://hooks.example.com/auto",
                            "agent_ids": ["sales"],
                            "channels": ["web_chat"],
                            "signing_secret_ref": "auto-secret",
                        },
                    )
                    assert target.status_code == 200

                    started = await client.post("/conversations", json={"agent_id": "sales"})
                    assert started.status_code == 200
                    conversation_id = started.json()["conversation"]["conversation_id"]

                    demo_turn = await client.post(
                        f"/conversations/{conversation_id}/turns",
                        json={
                            "turn_id": "turn_auto_worker_demo",
                            "dedupe_key": "turn_auto_worker_demo",
                            "channel": "web_chat",
                            "modality": "text",
                            "event_type": "user_message",
                            "text": "I want to book a demo.",
                        },
                    )
                    assert demo_turn.status_code == 200

                    _, _, invocation_id = await _advance_sales_booking_to_invocation(
                        client,
                        conversation_id,
                        email="person@example.com",
                        turn_prefix="turn_auto_worker_booking",
                    )

                    confirmed = await client.post(
                        f"/conversations/{conversation_id}/tool-invocations/{invocation_id}/confirm"
                    )
                    assert confirmed.status_code == 200

                    # Dispatch now happens in the worker process: drive the
                    # registered handler over the same database via the jobs
                    # runtime (one keyless job per cycle to bypass slot dedupe).
                    from ruhu.db import build_session_factory as _bsf
                    from ruhu.analytics_tagging.webhooks import WEBHOOK_DISPATCH_JOB_TYPE
                    from ruhu.jobs import Job, JobRuntime, SQLAlchemyJobStore
                    from ruhu.worker import build_handler_registry

                    _wsf = _bsf(runtime_database_url)
                    registry, _ = build_handler_registry(
                        session_factory=_wsf, settings=runtime_settings
                    )
                    jobs_store = SQLAlchemyJobStore(_wsf)
                    job_runtime = JobRuntime(jobs_store, registry, worker_id="w-test")

                    delivered = False
                    for _ in range(5):
                        jobs_store.enqueue(Job(job_type=WEBHOOK_DISPATCH_JOB_TYPE))
                        job_runtime.run_once()
                        targets = await client.get(
                            "/intent-tags/webhook-targets",
                            params={"organization_id": "public"},
                        )
                        assert targets.status_code == 200
                        target_payload = targets.json()[0]
                        if target_payload["last_success_at"] is not None:
                            delivered = True
                            break
                        await asyncio.sleep(0.05)

                    assert delivered is True

        assert captured["url"] == "https://hooks.example.com/auto"
        body = captured["content"]
        assert isinstance(body, bytes)
        headers = captured["headers"]
        assert isinstance(headers, dict)
        timestamp = headers["X-Ruhu-Timestamp"]
        expected_signature = hmac.new(
            b"auto-secret",
            timestamp.encode("utf-8") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        assert headers["X-Ruhu-Signature"] == f"sha256={expected_signature}"

    asyncio.run(run())


def test_hosted_intent_tags_classifier_falls_back_to_runtime_semantics_on_managed_failure(
    postgres_database_url_factory,
) -> None:
    class FailingHostedClassifierClient:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def __enter__(self) -> "FailingHostedClassifierClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url: str, *, json: dict | None = None, headers: dict | None = None) -> httpx.Response:
            request = httpx.Request("POST", url, json=json, headers=headers)
            return httpx.Response(503, json={"detail": "classifier unavailable"}, request=request)

    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            interpreter_name="sales",
            intent_tags_classifier_base_url="http://classifier.internal:8011",
            intent_tags_classifier_max_retries=0,
        )
        with patch("ruhu.analytics_tagging.adapters.httpx.Client", FailingHostedClassifierClient):
            app = build_default_app(
                agent_root=agent_root_path,
                database_url=postgres_database_url_factory(),
                runtime_settings=runtime_settings,
                bootstrap_organization_id="public",
            )

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                created_intent = await client.post(
                    "/intent-tags/intents",
                    json={
                        "organization_id": "public",
                        "agent_id": "sales",
                        "name": "demo_request",
                        "display_name": "Demo request",
                    },
                )
                assert created_intent.status_code == 200

                created_profile = await client.post(
                    "/intent-tags/profiles",
                    json={
                        "organization_id": "public",
                        "agent_id": "sales",
                        "adapter_name": "managed_demo",
                    },
                )
                assert created_profile.status_code == 200

                started = await client.post("/conversations", json={"agent_id": "sales"})
                assert started.status_code == 200
                conversation_id = started.json()["conversation"]["conversation_id"]

                demo_turn = await client.post(
                    f"/conversations/{conversation_id}/turns",
                    json={
                        "turn_id": "turn_managed_fallback",
                        "dedupe_key": "turn_managed_fallback",
                        "channel": "web_chat",
                        "modality": "text",
                        "event_type": "user_message",
                        "text": "I want to book a demo.",
                    },
                )
                assert demo_turn.status_code == 200

                events = await client.get(
                    "/intent-tags/events",
                    params={"organization_id": "public", "conversation_id": conversation_id},
                )
                assert events.status_code == 200
                payload = events.json()
                assert [item["intent_name"] for item in payload] == ["demo_request"]
                assert [item["adapter_name"] for item in payload] == ["sales"]
                classifier_metadata = payload[0]["context_payload"]["classifier_metadata"]
                assert classifier_metadata["fallback_applied"] is True
                assert classifier_metadata["requested_adapter_name"] == "managed_demo"
                assert classifier_metadata["fallback_reason"]["category"] == "http_retryable"

    asyncio.run(run())


def test_internal_intent_tags_classifier_diagnostics_reports_config_activity_and_costs(
    postgres_database_url_factory,
) -> None:
    class FakeHostedClassifierClient:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def __enter__(self) -> "FakeHostedClassifierClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url: str, *, json: dict | None = None, headers: dict | None = None) -> httpx.Response:
            request = httpx.Request("POST", url, json=json, headers=headers)
            return httpx.Response(
                200,
                json={
                    "intent": "demo_request",
                    "language": "en",
                    "response_language": "en",
                    "confidence": 0.91,
                    "model": "gemma-hosted-v2",
                    "provider_cost_record": {
                        "amount_usd": 0.04,
                        "cost_type": "classifier_inference",
                    },
                },
                request=request,
            )

    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url, admin_is_superuser=True)
        runtime_settings = RuntimeSettings(
            auth_database_url=auth_database_url,
            auth_jwt_private_key_pem=_private_key_pem(),
            auth_jwt_active_kid="kid-intent-tags",
            interpreter_name="sales",
            intent_tags_classifier_base_url="http://classifier.internal:8011",
            intent_tags_classifier_api_key="classifier-api-key",
        )
        with patch("ruhu.analytics_tagging.adapters.httpx.Client", FakeHostedClassifierClient):
            app = build_default_app(
                agent_root=agent_root_path,
                database_url=postgres_database_url_factory(),
                runtime_settings=runtime_settings,
            )

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                _authorize_client(
                    client,
                    auth_service=app.state.auth_service,
                    user_id="user-admin",
                    organization_id="org-1",
                )

                created_intent = await client.post(
                    "/intent-tags/intents",
                    json={
                        "agent_id": "sales",
                        "name": "demo_request",
                        "display_name": "Demo request",
                    },
                )
                assert created_intent.status_code == 200

                created_profile = await client.post(
                    "/intent-tags/profiles",
                    json={
                        "agent_id": "sales",
                        "adapter_name": "managed_demo",
                    },
                )
                assert created_profile.status_code == 200

                started = await client.post("/conversations", json={"agent_id": "sales"})
                assert started.status_code == 200
                conversation_id = started.json()["conversation"]["conversation_id"]

                demo_turn = await client.post(
                    f"/conversations/{conversation_id}/turns",
                    json={
                        "turn_id": "turn_internal_diagnostics",
                        "dedupe_key": "turn_internal_diagnostics",
                        "channel": "web_chat",
                        "modality": "text",
                        "event_type": "user_message",
                        "text": "I want to book a demo.",
                    },
                )
                assert demo_turn.status_code == 200

                diagnostics = await client.get("/internal/intent-tags/classifier/diagnostics")
                assert diagnostics.status_code == 200
                payload = diagnostics.json()
                assert payload["runtime_enabled"] is True
                assert payload["hosted_classifier_enabled"] is True
                assert payload["hosted_base_url"] == "http://classifier.internal:8011"
                assert payload["hosted_api_key_source"] == "env"
                assert payload["active_profile_count"] >= 1
                assert payload["active_profile_adapter_counts"]["managed_demo"] >= 1
                assert payload["recent_event_count"] >= 1
                assert payload["recent_hosted_event_count"] >= 1
                assert payload["recent_fallback_count"] == 0
                assert payload["recent_model_counts"]["gemma-hosted-v2"] >= 1
                assert payload["recent_cost_record_count"] >= 1
                assert payload["recent_cost_total_usd"] >= 0.04
                assert payload["recent_cost_type_counts"]["classifier_inference"] >= 1
                assert payload["semantic_summary_webhook_worker_enabled"] is False
                assert payload["semantic_summary_webhook_worker_running"] is False
                # Status now projects from the jobs table (worker process owns
                # dispatch); disabled => no tick scheduled, no history.
                webhook_last_result = payload["semantic_summary_webhook_worker_last_result"]
                assert webhook_last_result["scheduled"] is False
                assert webhook_last_result["last_tick_at"] is None
                assert webhook_last_result["last_error"] is None

    asyncio.run(run())


def test_api_uses_explicit_intent_tags_profile_adapter_even_without_kernel_interpreter(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            bootstrap_organization_id="public",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created_intent = await client.post(
                "/intent-tags/intents",
                json={
                    "organization_id": "public",
                    "agent_id": "sales",
                    "name": "demo_request",
                    "display_name": "Demo request",
                },
            )
            assert created_intent.status_code == 200

            created_profile = await client.post(
                "/intent-tags/profiles",
                json={
                    "organization_id": "public",
                    "agent_id": "sales",
                    "adapter_name": "sales",
                },
            )
            assert created_profile.status_code == 200

            started = await client.post("/conversations", json={"agent_id": "sales"})
            assert started.status_code == 200
            conversation_id = started.json()["conversation"]["conversation_id"]

            demo_turn = await client.post(
                f"/conversations/{conversation_id}/turns",
                json={
                    "turn_id": "turn_profile_adapter",
                    "dedupe_key": "turn_profile_adapter",
                    "channel": "web_chat",
                    "modality": "text",
                    "event_type": "user_message",
                    "text": "I want to book a demo.",
                },
            )
            assert demo_turn.status_code == 200

            events = await client.get(
                "/intent-tags/events",
                params={"organization_id": "public", "conversation_id": conversation_id},
            )
            assert events.status_code == 200
            payload = events.json()
            assert [item["intent_name"] for item in payload] == ["demo_request"]
            assert [item["adapter_name"] for item in payload] == ["sales"]
            assert [item["source_kind"] for item in payload] == ["runtime"]

    asyncio.run(run())


def test_api_scopes_tool_invocations_to_authenticated_organization(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
            runtime_settings=_auth_runtime_settings(),
        )
        _seed_sync_crm_submit_lead_tool(
            app,
            runtime_database_url=runtime_database_url,
            organization_id="org-1",
        )
        transport = httpx.ASGITransport(app=app)
        auth_service = app.state.auth_service

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(
                admin_client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            started = await admin_client.post("/conversations", json={"agent_id": "sales"})
            assert started.status_code == 200
            conversation_id = started.json()["conversation"]["conversation_id"]

            demo_turn = await admin_client.post(
                f"/conversations/{conversation_id}/turns",
                json={
                    "turn_id": "turn_demo",
                    "dedupe_key": "turn_demo",
                    "channel": "web_chat",
                    "modality": "text",
                    "event_type": "user_message",
                    "text": "I want to book a demo.",
                },
            )
            assert demo_turn.status_code == 200
            assert demo_turn.json()["step_after"] == "collect_booking_details"

            _, _, legit_invocation_id = await _advance_sales_booking_to_invocation(
                admin_client,
                conversation_id,
                email="tenant@example.com",
                turn_prefix="turn_org_scope_booking",
            )
            assert legit_invocation_id is not None

            now = datetime.now(timezone.utc)
            app.state.tool_runtime.store.save(
                ToolInvocation(
                    invocation_id="inv-rogue-org-2",
                    tool_ref="knowledge.lookup",
                    executor_kind="builtin",
                    status="waiting_confirmation",
                    caller=ToolCaller(
                        channel="web_chat",
                        conversation_id=conversation_id,
                        tenant_id="org-2",
                    ),
                    args={"email": "rogue@example.com"},
                    decision="confirm",
                    decision_reason="needs confirmation",
                    metadata={"confirmation_prompt": "Confirm rogue invocation"},
                    created_at=now,
                    updated_at=now,
                )
            )

            tool_invocations = await admin_client.get(f"/conversations/{conversation_id}/tool-invocations")
            assert tool_invocations.status_code == 200
            invocation_ids = [item["invocation_id"] for item in tool_invocations.json()]
            assert legit_invocation_id in invocation_ids
            assert "inv-rogue-org-2" not in invocation_ids

            confirm_rogue = await admin_client.post(
                f"/conversations/{conversation_id}/tool-invocations/inv-rogue-org-2/confirm"
            )
            assert confirm_rogue.status_code == 404

    asyncio.run(run())


def test_api_reconciles_deferred_tool_webhook_completion(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        private_key_pem = _private_key_pem()
        _seed_authenticated_api_store(auth_database_url)

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_private_key_pem=private_key_pem,
            auth_jwt_active_kid="kid-tool-webhook",
            interpreter_name="sales",
            runtime_settings=RuntimeSettings(
                auth_database_url=auth_database_url,
                auth_jwt_private_key_pem=private_key_pem,
                auth_jwt_active_kid="kid-tool-webhook",
                auth_allowed_redirect_origins=["http://testserver"],
                provider_shared_secret="provider-secret",
            ),
        )
        _seed_deferred_crm_submit_lead_tool(app, runtime_database_url=runtime_database_url)
        app.state.tool_integration_worker.embedded_worker_enabled = False

        transport = httpx.ASGITransport(app=app)
        auth_service = app.state.auth_service

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(
                admin_client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            started = await admin_client.post("/conversations", json={"agent_id": "sales"})
            assert started.status_code == 200
            conversation_id = started.json()["conversation"]["conversation_id"]

            demo_turn = await admin_client.post(
                f"/conversations/{conversation_id}/turns",
                json={"turn_id": "turn_demo", "dedupe_key": "turn_demo", "channel": "web_chat", "modality": "text", "event_type": "user_message", "text": "I want to book a demo."},
            )
            assert demo_turn.status_code == 200

            _, action_payload, invocation_id = await _advance_sales_booking_to_invocation(
                admin_client,
                conversation_id,
                email="tenant@example.com",
                turn_prefix="turn_deferred_booking",
            )
            assert invocation_id is not None
            assert action_payload["tool_calls"][0]["status"] == "confirmation_required"

            confirm = await admin_client.post(f"/conversations/{conversation_id}/tool-invocations/{invocation_id}/confirm")
            assert confirm.status_code == 200
            assert confirm.json()["tool_calls"][0]["status"] == "requested"

            processed = app.state.tool_integration_worker.process_available_jobs_once(max_jobs=1)
            assert len(processed) == 1
            job = app.state.tool_integration_runtime.load_job_for_invocation(invocation_id)
            assert job is not None
            assert job.status == "waiting_webhook"
            assert job.callback_correlation_id == f"demo-booking-{invocation_id}"

            progress_conversation = await admin_client.get(f"/conversations/{conversation_id}")
            assert progress_conversation.status_code == 200
            assert progress_conversation.json()["control_state"]["pending_action"]["status"] == "waiting_webhook"

            realtime_events = await admin_client.get(f"/conversations/{conversation_id}/realtime-events")
            assert realtime_events.status_code == 200
            progress_event_names = {(item["family"], item["name"]) for item in realtime_events.json()}
            assert ("interaction", "activity_progressed") in progress_event_names
            assert ("interaction", "status_trail_updated") in progress_event_names

            jobs = await admin_client.get("/tool-integration/jobs")
            assert jobs.status_code == 200
            jobs_payload = jobs.json()
            assert jobs_payload["counts_by_status"]["waiting_webhook"] >= 1
            listed_job = next(item for item in jobs_payload["items"] if item["job_id"] == job.job_id)
            assert listed_job["provider"] == "demo_http"
            assert listed_job["conversation_id"] == conversation_id

            job_detail = await admin_client.get(f"/tool-integration/jobs/{job.job_id}")
            assert job_detail.status_code == 200
            assert job_detail.json()["args"]["attendee_email"] == "tenant@example.com"

            webhook = await admin_client.post(
                f"/providers/tools/integration-webhooks/{job.callback_correlation_id}",
                json={"payload": {"status": "completed", "result": {"booking": {"email": "tenant@example.com"}}}},
                headers={"X-Ruhu-Provider-Secret": "provider-secret"},
            )
            assert webhook.status_code == 200
            payload = webhook.json()
            assert payload["job_status"] == "completed"
            assert payload["invocation_id"] == invocation_id
            assert payload["kernel_turn_applied"] is True
            assert payload["conversation_id"] == conversation_id
            assert payload["replayed"] is False

            replay = await admin_client.post(
                f"/providers/tools/integration-webhooks/{job.callback_correlation_id}",
                json={"payload": {"status": "completed", "result": {"booking": {"email": "tenant@example.com"}}}},
                headers={"X-Ruhu-Provider-Secret": "provider-secret"},
            )
            assert replay.status_code == 200
            replay_payload = replay.json()
            assert replay_payload["replayed"] is True
            assert replay_payload["kernel_turn_applied"] is False

            invocation = app.state.tool_runtime.store.load(invocation_id)
            assert invocation is not None
            assert invocation.status == "completed"
            assert invocation.output["booking"]["email"] == "tenant@example.com"

            conversation = await admin_client.get(f"/conversations/{conversation_id}")
            assert conversation.status_code == 200
            assert conversation.json()["control_state"]["pending_action"] is None

    asyncio.run(run())


def test_api_lists_dead_lettered_tool_integration_jobs(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        private_key_pem = _private_key_pem()
        _seed_authenticated_api_store(auth_database_url)

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_private_key_pem=private_key_pem,
            auth_jwt_active_kid="kid-tool-jobs",
            interpreter_name="sales",
            runtime_settings=RuntimeSettings(
                auth_database_url=auth_database_url,
                auth_jwt_private_key_pem=private_key_pem,
                auth_jwt_active_kid="kid-tool-jobs",
                auth_allowed_redirect_origins=["http://testserver"],
                provider_shared_secret="provider-secret",
            ),
        )
        _seed_deferred_crm_submit_lead_tool(app, runtime_database_url=runtime_database_url)
        app.state.tool_integration_worker.embedded_worker_enabled = False

        transport = httpx.ASGITransport(app=app)
        auth_service = app.state.auth_service

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(
                admin_client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            started = await admin_client.post("/conversations", json={"agent_id": "sales"})
            assert started.status_code == 200
            conversation_id = started.json()["conversation"]["conversation_id"]

            await admin_client.post(
                f"/conversations/{conversation_id}/turns",
                json={"turn_id": "turn_demo", "dedupe_key": "turn_demo", "channel": "web_chat", "modality": "text", "event_type": "user_message", "text": "I want to book a demo."},
            )
            _, _, invocation_id = await _advance_sales_booking_to_invocation(
                admin_client,
                conversation_id,
                email="stale@example.com",
                turn_prefix="turn_dead_letter_booking",
            )
            confirm = await admin_client.post(f"/conversations/{conversation_id}/tool-invocations/{invocation_id}/confirm")
            assert confirm.status_code == 200

            processed = app.state.tool_integration_worker.process_available_jobs_once(max_jobs=1)
            assert len(processed) == 1
            job = app.state.tool_integration_runtime.load_job_for_invocation(invocation_id)
            assert job is not None
            job.last_progress_at = datetime.now(timezone.utc) - timedelta(seconds=90)
            job.metadata["deferred_timeout_seconds"] = 30
            app.state.tool_integration_runtime.store.save(job)

            swept = app.state.tool_integration_worker.sweep_stuck_jobs_once(limit=1)
            assert len(swept) == 1
            assert swept[0].status == "dead_lettered"

            listing = await admin_client.get("/tool-integration/jobs", params={"status": "dead_lettered"})
            assert listing.status_code == 200
            listing_payload = listing.json()
            assert listing_payload["counts_by_status"]["dead_lettered"] >= 1
            item = next(row for row in listing_payload["items"] if row["job_id"] == job.job_id)
            assert item["status"] == "dead_lettered"
            assert item["metadata"]["dead_letter_reason"] == "stuck_timeout"

            detail = await admin_client.get(f"/tool-integration/jobs/{job.job_id}")
            assert detail.status_code == 200
            assert detail.json()["conversation_id"] == conversation_id
            assert detail.json()["status"] == "dead_lettered"

    asyncio.run(run())


def test_api_manages_user_sessions_and_invitation_acceptance(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
            runtime_settings=_auth_runtime_settings(),
        )
        transport = httpx.ASGITransport(app=app)
        auth_service = app.state.auth_service

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={
                "User-Agent": "admin-browser-a",
                "X-Forwarded-For": "203.0.113.10",
            },
        ) as admin_client:
            admin_session = _authorize_client(
                admin_client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
                ip="203.0.113.10",
                user_agent="admin-browser-a",
            )
            current_session_id = admin_session.session.session_id

            sessions_response = await admin_client.get("/auth/sessions")
            assert sessions_response.status_code == 200
            sessions_payload = sessions_response.json()
            assert len(sessions_payload) == 1
            assert sessions_payload[0]["session_id"] == current_session_id
            assert sessions_payload[0]["is_current"] is True
            assert sessions_payload[0]["created_ip"] == "203.0.113.10"
            assert sessions_payload[0]["user_agent"] == "admin-browser-a"

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={
                    "User-Agent": "admin-browser-b",
                    "X-Forwarded-For": "203.0.113.20",
                },
            ) as admin_client_two:
                second_session = _authorize_client(
                    admin_client_two,
                    auth_service=auth_service,
                    user_id="user-admin",
                    organization_id="org-1",
                    ip="203.0.113.20",
                    user_agent="admin-browser-b",
                )
                second_session_id = second_session.session.session_id
                assert second_session_id != current_session_id

                sessions_response = await admin_client.get("/auth/sessions")
                assert sessions_response.status_code == 200
                session_ids = {item["session_id"] for item in sessions_response.json()}
                assert session_ids == {current_session_id, second_session_id}

                revoke_peer_session = await admin_client.delete(f"/auth/sessions/{second_session_id}")
                assert revoke_peer_session.status_code == 204

                revoked_me = await admin_client_two.get("/auth/me")
                assert revoked_me.status_code == 401

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={"User-Agent": "analyst-browser"},
            ) as analyst_client:
                _authorize_client(
                    analyst_client,
                    auth_service=auth_service,
                    user_id="user-analyst",
                    organization_id="org-1",
                    user_agent="analyst-browser",
                )

                analyst_sessions = await admin_client.get("/organization/members/user-analyst/sessions")
                assert analyst_sessions.status_code == 200
                assert len(analyst_sessions.json()) == 1
                assert analyst_sessions.json()[0]["user_id"] == "user-analyst"

                revoke_analyst_sessions = await admin_client.delete("/organization/members/user-analyst/sessions")
                assert revoke_analyst_sessions.status_code == 204

                analyst_me = await analyst_client.get("/auth/me")
                assert analyst_me.status_code == 401

            created_invitation = await admin_client.post(
                "/organization/invitations",
                json={
                    "email": "invitee@example.com",
                    "role": "developer",
                },
            )
            assert created_invitation.status_code == 200
            invitation_payload = created_invitation.json()
            assert invitation_payload["status"] == "pending"
            assert invitation_payload["delivery"]["transport"] == "dev_outbox"
            invitation_token = _extract_token_from_dev_outbox(
                app,
                path="/accept-invitation",
            )

            listed_invitations = await admin_client.get("/organization/invitations")
            assert listed_invitations.status_code == 200
            assert any(item["email"] == "invitee@example.com" for item in listed_invitations.json())

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={"User-Agent": "invitee-browser"},
            ) as invited_client:
                accepted_invitation = await invited_client.post(
                    "/auth/invitations/accept",
                    json={
                        "invitation_token": invitation_token,
                        "display_name": "Invited User",
                        "timezone": "Africa/Lagos",
                        "language": "en-NG",
                    },
                )
                assert accepted_invitation.status_code == 200
                accepted_payload = accepted_invitation.json()
                assert accepted_payload["organization"]["organization_id"] == "org-1"
                assert accepted_payload["user"]["email"] == "invitee@example.com"
                invitation_events = app.state.audit_store.list_events(
                    organization_id="org-1",
                    event_type=ADMIN_INVITATION_ACCEPTED,
                )
                assert any(
                    event.resource_id == invitation_payload["invitation_id"]
                    and event.actor_id == accepted_payload["user"]["user_id"]
                    and event.detail.get("email") == "invitee@example.com"
                    and event.detail.get("role") == "developer"
                    for event in invitation_events
                )

                invited_sessions = await invited_client.get("/auth/sessions")
                assert invited_sessions.status_code == 200
                assert len(invited_sessions.json()) == 1
                assert invited_sessions.json()[0]["user_agent"] == "invitee-browser"

            members = await admin_client.get("/organization/members")
            assert members.status_code == 200
            assert any(item["email"] == "invitee@example.com" for item in members.json())

            revoked_invitation = await admin_client.post(
                "/organization/invitations",
                json={
                    "email": "revoked@example.com",
                    "role": "analyst",
                },
            )
            assert revoked_invitation.status_code == 200
            revoked_invitation_payload = revoked_invitation.json()

            revoke_invitation = await admin_client.delete(
                f"/organization/invitations/{revoked_invitation_payload['invitation_id']}"
            )
            assert revoke_invitation.status_code == 204

            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as revoked_client:
                revoked_invitation_token = _extract_token_from_dev_outbox(
                    app,
                    path="/accept-invitation",
                )
                revoked_accept = await revoked_client.post(
                    "/auth/invitations/accept",
                    json={
                        "invitation_token": revoked_invitation_token,
                    },
                )
                assert revoked_accept.status_code == 409
                assert revoked_accept.json()["detail"] == "invitation is no longer active"

            revoke_current = await admin_client.delete("/auth/sessions/current")
            assert revoke_current.status_code == 204

            admin_me = await admin_client.get("/auth/me")
            assert admin_me.status_code == 401

    asyncio.run(run())


def test_api_key_creation_returns_metadata_only_and_accepts_client_supplied_hash(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            interpreter_name="sales",
            runtime_settings=_auth_runtime_settings(),
        )
        transport = httpx.ASGITransport(app=app)
        auth_service = app.state.auth_service
        plaintext = "sk_live_test_issue_flow_123456789"
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        key_prefix = plaintext[:16]

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(
                admin_client,
                auth_service=auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            created = await admin_client.post(
                "/api-keys",
                json={
                    "name": "CI key",
                    "key_hash": key_hash,
                    "key_prefix": key_prefix,
                },
            )
            assert created.status_code == 201, created.text
            payload = created.json()
            assert payload["name"] == "CI key"
            assert payload["key_prefix"] == key_prefix
            assert "key" not in payload

            listed = await admin_client.get("/api-keys")
            assert listed.status_code == 200
            assert any(item["key_id"] == payload["key_id"] for item in listed.json())
            assert all("key" not in item for item in listed.json())

            duplicate = await admin_client.post(
                "/api-keys",
                json={
                    "name": "duplicate",
                    "key_hash": key_hash,
                    "key_prefix": key_prefix,
                },
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["detail"] == "api key already exists"

        from ruhu.db_models import ApiKeyRecord

        auth_session_factory = build_session_factory(auth_database_url)
        with auth_session_factory() as session:
            record = session.get(ApiKeyRecord, payload["key_id"])
            assert record is not None
            assert record.key_hash == key_hash
            assert record.key_prefix == key_prefix

    asyncio.run(run())


def test_api_supports_invite_validation_and_magic_link_passwordless_signin(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        store = _seed_authenticated_api_store(auth_database_url)
        service = AuthService(identity_store=store, jwt_codec=JWTCodec(secret=TEST_HS256_SECRET))
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            auth_resolver=AuthContextResolver(auth_service=service),
            runtime_settings=_auth_runtime_settings(),
        )
        transport = httpx.ASGITransport(app=app)
        admin_session = service.issue_browser_session(user_id="user-admin", organization_id="org-1")

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            admin_client.headers["Authorization"] = f"Bearer {admin_session.access_token}"
            created_invitation = await admin_client.post(
                "/organization/invitations",
                json={"email": "magic@example.com", "role": "developer"},
            )
            assert created_invitation.status_code == 200
            assert created_invitation.json()["delivery"]["transport"] == "dev_outbox"
            invitation_token = _extract_token_from_dev_outbox(
                app,
                path="/accept-invitation",
            )

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as invited_client:
            validate = await invited_client.get(
                "/auth/invite/validate",
                params={"token": invitation_token},
            )
            assert validate.status_code == 200
            assert validate.json()["valid"] is True
            assert validate.json()["email"] == "magic@example.com"

            requested_magic_link = await invited_client.post(
                "/auth/magic-link/request",
                json={
                    "email": "magic@example.com",
                    "invitation_token": invitation_token,
                },
            )
            assert requested_magic_link.status_code == 200
            assert requested_magic_link.json()["delivery"]["transport"] == "dev_outbox"
            magic_link_token = _extract_token_from_dev_outbox(
                app,
                path="/auth/magic-link",
            )

            verified_magic_link = await invited_client.post(
                "/auth/magic-link/verify",
                json={"token": magic_link_token},
            )
            assert verified_magic_link.status_code == 200
            payload = verified_magic_link.json()
            assert payload["user"]["email"] == "magic@example.com"
            assert payload["organization"]["organization_id"] == "org-1"

            me_response = await invited_client.get("/auth/me")
            assert me_response.status_code == 200
            assert me_response.json()["user"]["email"] == "magic@example.com"

    asyncio.run(run())


def test_api_supports_google_and_enterprise_sso_passwordless_signin(
    postgres_database_url_factory,
    monkeypatch,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        store = _seed_authenticated_api_store(auth_database_url)
        service = AuthService(identity_store=store, jwt_codec=JWTCodec(secret=TEST_HS256_SECRET))

        monkeypatch.setenv("RUHU_SSO_CLIENT_SECRET__ACME_OIDC", "enterprise-secret")

        async def fake_fetch_discovery(_issuer_url: str) -> dict[str, str]:
            return {
                "authorization_endpoint": "https://idp.example.com/authorize",
                "token_endpoint": "https://idp.example.com/token",
                "userinfo_endpoint": "https://idp.example.com/userinfo",
            }

        async def fake_exchange_code_for_tokens(**_kwargs) -> dict[str, str]:
            return {"access_token": "provider-access-token"}

        async def fake_fetch_userinfo(*, access_token: str, userinfo_endpoint: str) -> dict[str, object]:
            assert access_token == "provider-access-token"
            assert userinfo_endpoint == "https://idp.example.com/userinfo"
            current_email = fake_fetch_userinfo.email
            return {
                "sub": f"subject:{current_email}",
                "email": current_email,
                "email_verified": True,
                "name": current_email.split("@", 1)[0].title(),
                "picture": "https://cdn.example.com/avatar.png",
            }

        fake_fetch_userinfo.email = "googleinvite@example.com"  # type: ignore[attr-defined]

        monkeypatch.setattr("ruhu.routes.auth_sessions.fetch_discovery", fake_fetch_discovery)
        monkeypatch.setattr("ruhu.routes.organization.fetch_discovery", fake_fetch_discovery)
        monkeypatch.setattr("ruhu.routes.auth_sessions.exchange_code_for_tokens", fake_exchange_code_for_tokens)
        monkeypatch.setattr("ruhu.routes.auth_sessions.fetch_userinfo", fake_fetch_userinfo)

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            auth_resolver=AuthContextResolver(auth_service=service),
            runtime_settings=RuntimeSettings(
                frontend_url="http://app.example.com",
                auth_allowed_redirect_origins=[
                    "http://app.example.com",
                    "http://testserver",
                ],
                google_client_id="google-client-id",
                google_client_secret="google-client-secret",
            ),
        )
        transport = httpx.ASGITransport(app=app)
        admin_session = service.issue_browser_session(user_id="user-admin", organization_id="org-1")

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            admin_client.headers["Authorization"] = f"Bearer {admin_session.access_token}"
            created_invitation = await admin_client.post(
                "/organization/invitations",
                json={"email": "googleinvite@example.com", "role": "developer"},
            )
            assert created_invitation.status_code == 200
            assert created_invitation.json()["delivery"]["transport"] == "dev_outbox"
            google_invitation_token = _extract_token_from_dev_outbox(
                app,
                path="/accept-invitation",
            )

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as google_client:
            google_start = await google_client.post(
                "/auth/oauth/google/start",
                json={
                    "invitation_token": google_invitation_token,
                    "redirect_uri": "http://testserver/auth/callback",
                },
            )
            assert google_start.status_code == 200
            google_state = parse_qs(urlparse(google_start.json()["authorization_url"]).query)["state"][0]

            google_callback = await google_client.post(
                "/auth/oauth/callback",
                json={
                    "code": "google-code",
                    "state": google_state,
                    "redirect_uri": "http://testserver/auth/callback",
                },
            )
            assert google_callback.status_code == 200
            assert google_callback.json()["user"]["email"] == "googleinvite@example.com"

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            admin_client.headers["Authorization"] = f"Bearer {admin_session.access_token}"
            created_sso_config = await admin_client.put(
                "/auth/sso/config",
                json={
                    "issuer_url": "https://sso.example.com",
                    "client_id": "enterprise-client-id",
                    "client_secret_ref": "env:RUHU_SSO_CLIENT_SECRET__ACME_OIDC",
                    "allowed_domains": ["acme.com"],
                    "scopes": ["openid", "profile", "email"],
                    "is_active": True,
                    "enforce_sso": True,
                    "jit_provisioning_enabled": True,
                },
            )
            assert created_sso_config.status_code == 200
            assert created_sso_config.json()["allowed_domains"] == ["acme.com"]

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as sso_client:
            fake_fetch_userinfo.email = "jit-user@acme.com"  # type: ignore[attr-defined]
            sso_start = await sso_client.post(
                "/auth/oauth/sso/start",
                json={
                    "email": "jit-user@acme.com",
                    "redirect_uri": "http://testserver/auth/callback",
                },
            )
            assert sso_start.status_code == 200
            sso_state = parse_qs(urlparse(sso_start.json()["authorization_url"]).query)["state"][0]

            sso_callback = await sso_client.post(
                "/auth/oauth/callback",
                json={
                    "code": "sso-code",
                    "state": sso_state,
                    "redirect_uri": "http://testserver/auth/callback",
                },
            )
            assert sso_callback.status_code == 200
            sso_payload = sso_callback.json()
            assert sso_payload["organization"]["organization_id"] == "org-1"
            assert sso_payload["user"]["email"] == "jit-user@acme.com"
            outbox_size_before = len(getattr(app.state, "email_outbox", []))

            blocked_magic_link = await sso_client.post(
                "/auth/magic-link/request",
                json={"email": "jit-user@acme.com", "organization_id": "org-1"},
            )
            assert blocked_magic_link.status_code == 200
            assert blocked_magic_link.json() == {
                "message": "If the sign-in request is valid, a sign-in link has been issued.",
                "delivery": {
                    "transport": "dev_outbox",
                    "delivery_id": None,
                    "status": "queued",
                    "dev_outbox_entry_id": None,
                },
            }
            assert len(getattr(app.state, "email_outbox", [])) == outbox_size_before

    asyncio.run(run())


def test_api_executes_tool_backed_sales_flow_and_confirmation(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        knowledge_seed_path = Path(__file__).resolve().parents[1] / "tests" / "_fixtures" / "data" / "knowledge" / "sales.json"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
            runtime_settings=RuntimeSettings(
                knowledge_default_organization_id="public",
                knowledge_seed_path=knowledge_seed_path,
                knowledge_auto_seed=True,
                knowledge_auto_reindex_on_startup=False,
            ),
        )
        runtime_database_url = str(app.state.runtime_settings.database_url or "")
        _seed_sync_crm_submit_lead_tool(app, runtime_database_url=runtime_database_url)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            started = await client.post("/conversations", json={"agent_id": "sales"})
            assert started.status_code == 200
            conversation_id = started.json()["conversation"]["conversation_id"]

            product_turn = await client.post(
                f"/conversations/{conversation_id}/turns",
                json={
                    "turn_id": "turn_1",
                    "dedupe_key": "turn_1",
                    "channel": "web_chat",
                    "modality": "text",
                    "event_type": "user_message",
                    "text": "Can you explain how the workflow builder works?",
                },
            )
            assert product_turn.status_code == 200
            product_payload = product_turn.json()
            assert product_payload["step_after"] == "product_qa"
            assert product_payload["tool_calls"][0]["tool_ref"] == "knowledge.lookup"
            assert product_payload["tool_calls"][0]["status"] == "success"
            assert "workflow" in product_payload["emitted_messages"][0]["text"].lower()

            tool_invocations = await client.get(f"/conversations/{conversation_id}/tool-invocations")
            assert tool_invocations.status_code == 200
            assert len(tool_invocations.json()) == 1
            assert tool_invocations.json()[0]["status"] == "completed"

            demo_turn = await client.post(
                f"/conversations/{conversation_id}/turns",
                json={
                    "turn_id": "turn_2",
                    "dedupe_key": "turn_2",
                    "channel": "web_chat",
                    "modality": "text",
                    "event_type": "user_message",
                    "text": "I want to book a demo.",
                },
            )
            assert demo_turn.status_code == 200
            assert demo_turn.json()["step_after"] == "collect_booking_details"

            _, booking_payload, invocation_id = await _advance_sales_booking_to_invocation(
                client,
                conversation_id,
                email="person@example.com",
                turn_prefix="turn_3",
            )
            assert booking_payload["tool_calls"][0]["status"] == "confirmation_required"

            confirmed = await client.post(
                f"/conversations/{conversation_id}/tool-invocations/{invocation_id}/confirm"
            )
            assert confirmed.status_code == 200
            confirmed_payload = confirmed.json()
            assert confirmed_payload["step_after"] == "request_submitted"
            assert confirmed_payload["tool_calls"][0]["status"] == "success"
            assert "demo" in confirmed_payload["emitted_messages"][0]["text"].lower()

            tool_invocations_after = await client.get(f"/conversations/{conversation_id}/tool-invocations")
            assert tool_invocations_after.status_code == 200
            assert len(tool_invocations_after.json()) == 2
            assert tool_invocations_after.json()[-1]["status"] == "completed"

    asyncio.run(run())


def test_api_rejects_cancelling_completed_tool_invocation(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            bootstrap_organization_id="public",
        )
        runtime_database_url = str(app.state.runtime_settings.database_url or "")
        _seed_sync_crm_submit_lead_tool(app, runtime_database_url=runtime_database_url)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            started = await client.post("/conversations", json={"agent_id": "sales"})
            assert started.status_code == 200
            conversation_id = started.json()["conversation"]["conversation_id"]

            await client.post(
                f"/conversations/{conversation_id}/turns",
                json={
                    "turn_id": "turn_1",
                    "dedupe_key": "turn_1",
                    "channel": "web_chat",
                    "modality": "text",
                    "event_type": "user_message",
                    "text": "I want to book a demo.",
                },
            )
            _, _, invocation_id = await _advance_sales_booking_to_invocation(
                client,
                conversation_id,
                email="person@example.com",
                turn_prefix="turn_cancel_booking",
            )

            confirmed = await client.post(
                f"/conversations/{conversation_id}/tool-invocations/{invocation_id}/confirm"
            )
            assert confirmed.status_code == 200

            cancelled = await client.post(
                f"/conversations/{conversation_id}/tool-invocations/{invocation_id}/cancel"
            )
            assert cancelled.status_code == 409
            assert cancelled.json()["detail"] == "invocation cannot be cancelled from current state"

    asyncio.run(run())
