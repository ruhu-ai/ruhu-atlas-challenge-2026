"""Integration tests for GET /conversations/{id}/citations."""

from __future__ import annotations

import httpx
import pytest

from ruhu.api import build_default_app


@pytest.mark.asyncio
async def test_citations_endpoint_returns_grounded_email(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
        interpreter_name="sales",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        started = await client.post("/conversations", json={"agent_id": "sales"})
        assert started.status_code == 200
        conversation_id = started.json()["conversation"]["conversation_id"]

        # Advance into the booking step.
        demo_turn = await client.post(
            f"/conversations/{conversation_id}/turns",
            json={
                "turn_id": "citations_demo",
                "dedupe_key": "citations_demo",
                "channel": "web_chat",
                "modality": "text",
                "event_type": "user_message",
                "text": "I want to book a demo.",
            },
        )
        assert demo_turn.status_code == 200

        email_text = "Please use jane@example.com for booking."
        email_turn = await client.post(
            f"/conversations/{conversation_id}/turns",
            json={
                "turn_id": "citations_email",
                "dedupe_key": "citations_email",
                "channel": "web_chat",
                "modality": "text",
                "event_type": "user_message",
                "text": email_text,
            },
        )
        assert email_turn.status_code == 200

        citations = await client.get(f"/conversations/{conversation_id}/citations")
        assert citations.status_code == 200
        payload = citations.json()
        assert payload["conversation_id"] == conversation_id

        emails = [item for item in payload["citations"] if item["fact_name"] == "email"]
        assert emails, f"expected an email citation; got {payload['citations']}"
        email_citation = emails[0]
        assert email_citation["source_utterance"] == "jane@example.com"
        assert email_citation["confidence"] == 1.0
        assert email_citation["source"] == "deterministic"
        assert email_citation["turn_id"]


@pytest.mark.asyncio
async def test_citations_endpoint_empty_for_fresh_conversation(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
        interpreter_name="sales",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        started = await client.post("/conversations", json={"agent_id": "sales"})
        assert started.status_code == 200
        conversation_id = started.json()["conversation"]["conversation_id"]

        citations = await client.get(f"/conversations/{conversation_id}/citations")
        assert citations.status_code == 200
        payload = citations.json()
        assert payload == {"conversation_id": conversation_id, "citations": []}


@pytest.mark.asyncio
async def test_citations_endpoint_404_for_unknown_conversation(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
        interpreter_name="sales",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/conversations/does-not-exist/citations")
        assert response.status_code == 404
