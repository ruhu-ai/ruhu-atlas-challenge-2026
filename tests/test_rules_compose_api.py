"""Compose API tests: compile, explain, and save (Doc 04).

Save is deliberately a draft-only path. Bindings are not created at this
step \u2014 Doc 04 \u00a73 keeps publish a separate, reviewed action and
``RuleBinding`` already requires a published revision.
"""
from __future__ import annotations

from pathlib import Path

import anyio
import httpx
import pytest

from ruhu.api import build_default_app
from ruhu.runtime_config import RuntimeSettings
from tests.conftest import TEST_JWT_SECRET


class _HTTPClient:
    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        async def _inner() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
                return await ac.request(method, path, **kwargs)
        return anyio.run(_inner)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)


@pytest.fixture
def client(test_db_urls):
    runtime_db_url, auth_db_url = test_db_urls
    agent_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
    app = build_default_app(
        agent_root=agent_root,
        database_url=runtime_db_url,
        interpreter_name="sales",
        bootstrap_organization_id="public",
        runtime_settings=RuntimeSettings(
            auth_database_url=auth_db_url,
            auth_jwt_secret=TEST_JWT_SECRET,
        ),
    )
    return _HTTPClient(app)


def test_compose_compile_returns_ready_proposal_for_clear_policy(
    client, superuser_auth_headers: dict
) -> None:
    response = client.post(
        "/api/rules/compose/compile",
        json={"text": "Block any message containing credit card numbers"},
        headers=superuser_auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["outcome"] == "ready"
    assert data["rule_body"]["effect"]["kind"] == "block"
    assert data["rule_body"]["stage"] == "turn_ingress"
    assert "credit card" in data["expression"]


def test_compose_compile_returns_clarification_when_underspecified(
    client, superuser_auth_headers: dict
) -> None:
    response = client.post(
        "/api/rules/compose/compile",
        json={"text": "be careful with refunds"},
        headers=superuser_auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["outcome"] == "needs_clarification"
    codes = {item["code"] for item in data["ambiguities"]}
    assert "effect_unclear" in codes


def test_compose_save_persists_draft_with_suggested_scope_metadata(
    client, superuser_auth_headers: dict
) -> None:
    compile_response = client.post(
        "/api/rules/compose/compile",
        json={
            "text": "Require approval for transactions over $5000 when calling process_transaction tool"
        },
        headers=superuser_auth_headers,
    )
    assert compile_response.status_code == 200, compile_response.text
    proposal = compile_response.json()
    assert proposal["outcome"] == "ready"

    save_response = client.post(
        "/api/rules/compose/save",
        json={
            "rule_id": "rule.compose.test_save",
            "organization_scope": "system",
            "rule_body": proposal["rule_body"],
            "suggested_binding_scope": proposal["binding_scope"],
        },
        headers=superuser_auth_headers,
    )
    assert save_response.status_code == 201, save_response.text
    saved = save_response.json()
    assert saved["rule_id"] == "rule.compose.test_save"
    assert saved["status"] == "draft"
    assert saved["revision"] == 1
    metadata = saved["metadata"]
    assert metadata["compose_source"] == "natural_language"
    assert "compose_suggested_scope" in metadata
    suggested = metadata["compose_suggested_scope"]
    assert suggested["tool_refs"] == ["process_transaction"]


def test_compose_save_persists_scenario_scope_as_advisory_metadata(
    client, superuser_auth_headers: dict
) -> None:
    compile_response = client.post(
        "/api/rules/compose/compile",
        json={
            "text": 'Block "credit card" in scenario refunds',
        },
        headers=superuser_auth_headers,
    )
    assert compile_response.status_code == 200, compile_response.text
    proposal = compile_response.json()
    assert proposal["binding_scope"]["scenario_ids"] == ["refunds"]

    save_response = client.post(
        "/api/rules/compose/save",
        json={
            "rule_id": "rule.compose.test_scenario_advisory",
            "organization_scope": "system",
            "rule_body": proposal["rule_body"],
            "suggested_binding_scope": proposal["binding_scope"],
        },
        headers=superuser_auth_headers,
    )
    assert save_response.status_code == 201, save_response.text
    saved = save_response.json()
    assert saved["metadata"]["compose_suggested_scope"]["scenario_ids"] == ["refunds"]
    assert saved["metadata"]["compose_scope_advisory"]["scenario_ids"] == ["refunds"]


def test_compose_save_rejects_system_scope_for_non_superuser(
    client, user_auth_headers: dict
) -> None:
    compile_response = client.post(
        "/api/rules/compose/compile",
        json={"text": "Block any message containing credit card numbers"},
        headers=user_auth_headers,
    )
    assert compile_response.status_code == 200, compile_response.text
    proposal = compile_response.json()

    response = client.post(
        "/api/rules/compose/save",
        json={
            "rule_id": "rule.compose.test_org_scope",
            "organization_scope": "system",
            "rule_body": proposal["rule_body"],
            "suggested_binding_scope": proposal["binding_scope"],
        },
        headers=user_auth_headers,
    )
    assert response.status_code == 403, response.text


def test_compose_explain_round_trips_saved_rule_body(
    client, superuser_auth_headers: dict
) -> None:
    compile_response = client.post(
        "/api/rules/compose/compile",
        json={"text": "Block any message containing credit card numbers"},
        headers=superuser_auth_headers,
    )
    assert compile_response.status_code == 200
    proposal = compile_response.json()

    explain_response = client.post(
        "/api/rules/compose/explain",
        json={
            "rule_body": proposal["rule_body"],
            "binding_scope": proposal["binding_scope"],
        },
        headers=superuser_auth_headers,
    )
    assert explain_response.status_code == 200, explain_response.text
    body = explain_response.json()
    assert "Block" in body["explanation"]
    assert "credit card" in body["explanation"]
