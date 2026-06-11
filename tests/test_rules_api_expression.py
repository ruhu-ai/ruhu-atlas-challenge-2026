"""Tests for rules API endpoints with expression field support."""
from pathlib import Path

import anyio
import httpx
import pytest

from ruhu.api import build_default_app
from ruhu.runtime_config import RuntimeSettings
from tests.conftest import TEST_JWT_SECRET


class _HTTPClient:
    """Synchronous HTTP client wrapper around httpx.AsyncClient.

    Works around starlette.testclient.TestClient's incompatibility with httpx>=0.27.
    """
    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make a synchronous request to the ASGI app."""
        async def _inner() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as async_client:
                return await async_client.request(method, path, **kwargs)
        return anyio.run(_inner)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def put(self, path: str, **kwargs) -> httpx.Response:
        return self.request("PUT", path, **kwargs)


@pytest.fixture
def client(test_db_urls):
    """Create test client with rules runtime and auth config."""
    runtime_db_url, auth_db_url = test_db_urls
    agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
    app = build_default_app(
        agent_root=agent_root_path,
        database_url=runtime_db_url,
        interpreter_name="sales",
        bootstrap_organization_id="public",
        runtime_settings=RuntimeSettings(
            auth_database_url=auth_db_url,
            auth_jwt_secret=TEST_JWT_SECRET,
        ),
    )
    return _HTTPClient(app)


def test_create_rule_with_expression(client, superuser_auth_headers: dict) -> None:
    """Test creating a rule via API with expression field."""
    payload = {
        "rule_id": "test.create_with_expression",
        "name": "Test Rule with Expression",
        "summary": "Created via API with DSL expression",
        "stage": "turn_ingress",
        "expression": "facts.is_vip == true and turn.text contains \"refund\"",
        "effect": {
            "kind": "block",
            "code": "test_block",
            "message": "Test block message",
        },
        "tags": ["test", "expression"],
        "organization_scope": "system",
    }
    response = client.post(
        "/api/rules/definitions",
        json=payload,
        headers=superuser_auth_headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["rule_id"] == "test.create_with_expression"
    assert data["expression"] == payload["expression"]
    assert data["predicate"]["kind"] == "all"  # AllPredicate for "and"


def test_create_rule_with_predicate_still_works(client, superuser_auth_headers: dict) -> None:
    """Rules can be created with a structured predicate payload."""
    payload = {
        "rule_id": "test.create_with_predicate",
        "name": "Test Rule with Predicate",
        "summary": "Created via API with structured predicate",
        "stage": "turn_ingress",
        "predicate": {
            "kind": "match",
            "path": "facts.is_vip",
            "operator": "eq",
            "value": True,
        },
        "effect": {
            "kind": "block",
            "code": "test_block",
            "message": "Test block message",
        },
        "organization_scope": "system",
    }
    response = client.post(
        "/api/rules/definitions",
        json=payload,
        headers=superuser_auth_headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["rule_id"] == "test.create_with_predicate"
    assert data["predicate"]["kind"] == "match"
    assert data["expression"] is None


def test_rule_with_or_expression(client, superuser_auth_headers: dict) -> None:
    """Test creating a rule with OR expression."""
    payload = {
        "rule_id": "test.or_expression",
        "name": "OR Expression Test",
        "summary": "Test OR operator in expression",
        "stage": "turn_ingress",
        "expression": "turn.text contains \"urgent\" or turn.text contains \"ASAP\"",
        "effect": {
            "kind": "warn",
            "code": "urgent_detected",
            "message": "Urgent request detected",
        },
        "organization_scope": "system",
    }
    response = client.post(
        "/api/rules/definitions",
        json=payload,
        headers=superuser_auth_headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["expression"] == payload["expression"]
    assert data["predicate"]["kind"] == "any"  # AnyPredicate for "or"


def test_publish_rule_with_expression(client, superuser_auth_headers: dict) -> None:
    """Test publishing a rule that was created with expression."""
    # Create rule with expression
    create_payload = {
        "rule_id": "test.publish_expression",
        "name": "Publish Test",
        "summary": "Rule to publish",
        "stage": "before_tool",
        "expression": "metadata.amount > 1000",
        "effect": {
            "kind": "require_confirmation",
            "code": "confirm_large_amount",
            "message": "Confirm large amount",
        },
        "organization_scope": "system",
    }
    create_response = client.post(
        "/api/rules/definitions",
        json=create_payload,
        headers=superuser_auth_headers,
    )
    assert create_response.status_code == 201
    created = create_response.json()
    revision = created["revision"]

    # Publish the rule
    publish_response = client.post(
        f"/api/rules/definitions/test.publish_expression/revisions/{revision}/publish",
        headers=superuser_auth_headers,
    )
    assert publish_response.status_code == 200, publish_response.text
    published = publish_response.json()
    assert published["status"] == "published"
    assert published["expression"] == create_payload["expression"]
    assert published["predicate"]["kind"] == "match"


def test_expression_with_operators(client, superuser_auth_headers: dict) -> None:
    """Test expressions using various supported operators."""
    test_cases = [
        ("facts.flag exists", "exists"),
        ("turn.text_length between [10, 100]", "between"),
        ("turn.channel in [\"phone\", \"whatsapp\"]", "in"),
        ("metadata.score >= 3", "gte"),
    ]

    for idx, (expression, expected_op) in enumerate(test_cases):
        payload = {
            "rule_id": f"test.operator_{idx}",
            "name": f"Operator Test {idx}",
            "summary": f"Testing {expected_op}",
            "stage": "turn_ingress",
            "expression": expression,
            "effect": {
                "kind": "trace",
                "code": f"op_test_{idx}",
            },
            "organization_scope": "system",
        }
        response = client.post(
            "/api/rules/definitions",
            json=payload,
            headers=superuser_auth_headers,
        )
        assert response.status_code == 201, f"Failed for {expected_op}: {response.text}"
        data = response.json()
        assert data["predicate"]["operator"] == expected_op


def test_invalid_expression_rejected(client, superuser_auth_headers: dict) -> None:
    """Test that invalid expressions are rejected during rule creation."""
    payload = {
        "rule_id": "test.invalid_expression",
        "name": "Invalid Rule",
        "summary": "Rule with bad expression",
        "stage": "turn_ingress",
        "expression": "facts.flag == ",  # Incomplete expression
        "effect": {
            "kind": "block",
            "code": "test",
            "message": "Test",
        },
        "organization_scope": "system",
    }
    response = client.post(
        "/api/rules/definitions",
        json=payload,
        headers=superuser_auth_headers,
    )
    # Should fail validation
    assert response.status_code in (400, 422), response.text


def test_expression_in_response(client, superuser_auth_headers: dict) -> None:
    """Test that expression field is included in API responses."""
    original_expr = "facts.is_vip == true and turn.text contains \"refund\""
    payload = {
        "rule_id": "test.response_field",
        "name": "Response Field Test",
        "summary": "Test expression in response",
        "stage": "turn_ingress",
        "expression": original_expr,
        "effect": {
            "kind": "block",
            "code": "test",
            "message": "Test",
        },
        "organization_scope": "system",
    }
    create_response = client.post(
        "/api/rules/definitions",
        json=payload,
        headers=superuser_auth_headers,
    )
    assert create_response.status_code == 201
    data = create_response.json()

    # Expression should be preserved in response
    assert data["expression"] == original_expr
    # Predicate should be properly compiled
    assert data["predicate"]["kind"] == "all"

