"""Integration tests for POST /conversations/{id}/analysis-sweep."""

from __future__ import annotations

import httpx
import pytest

from ruhu.agent_document import (
    AgentDocument,
    AnalysisVariableDef,
    Scenario,
    Step,
)
from ruhu.api import build_default_app
from ruhu.db import build_session_factory
from ruhu.registry import SQLAlchemyAgentRegistry


def _seed_agent_with_analysis_schema(
    database_url: str,
    *,
    agent_id: str,
    organization_id: str = "public",
) -> None:
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    registry.create_agent_document(
        agent_id=agent_id,
        agent_name="Sweep Agent",
        organization_id=organization_id,
        document=AgentDocument(
            start_scenario_id="main",
            scenarios=[
                Scenario(
                    id="main",
                    name="Main",
                    start_step_id="start",
                    steps=[
                        Step(
                            id="start",
                            name="Start",
                            say="Hello! How can I help?",
                        )
                    ],
                )
            ],
            analysis_schema=[
                AnalysisVariableDef(
                    name="email",
                    type="string",
                    description="customer email",
                ),
            ],
        ),
    )
    registry.publish(agent_id, organization_id=organization_id)


@pytest.mark.asyncio
async def test_analysis_sweep_extracts_email_from_transcript(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database_url = postgres_database_url_factory()
    _seed_agent_with_analysis_schema(database_url, agent_id="sweep_agent")
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        started = await client.post("/conversations", json={"agent_id": "sweep_agent"})
        assert started.status_code == 200
        conversation_id = started.json()["conversation"]["conversation_id"]

        email_text = "Hi, my email is jane@example.com — can you follow up?"
        turn = await client.post(
            f"/conversations/{conversation_id}/turns",
            json={
                "turn_id": "sweep_email_turn",
                "dedupe_key": "sweep_email_turn",
                "channel": "web_chat",
                "modality": "text",
                "event_type": "user_message",
                "text": email_text,
            },
        )
        assert turn.status_code == 200

        sweep = await client.post(
            f"/conversations/{conversation_id}/analysis-sweep"
        )
        assert sweep.status_code == 200
        sweep_payload = sweep.json()
        assert sweep_payload["conversation_id"] == conversation_id
        assert sweep_payload["variables_total"] == 1
        assert "email" in sweep_payload["variables_filled"]

        citations = await client.get(
            f"/conversations/{conversation_id}/citations"
        )
        assert citations.status_code == 200
        payload = citations.json()
        emails = [item for item in payload["citations"] if item["fact_name"] == "email"]
        assert emails, f"expected an email citation; got {payload['citations']}"
        email_citation = emails[0]
        assert email_citation["source_utterance"] == "jane@example.com"
        assert email_citation["source"] == "deterministic"


@pytest.mark.asyncio
async def test_analysis_sweep_returns_zero_when_schema_is_empty(
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
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # The sales fixture has no analysis_schema.
        started = await client.post("/conversations", json={"agent_id": "sales"})
        assert started.status_code == 200
        conversation_id = started.json()["conversation"]["conversation_id"]

        sweep = await client.post(
            f"/conversations/{conversation_id}/analysis-sweep"
        )
        assert sweep.status_code == 200
        payload = sweep.json()
        assert payload["variables_total"] == 0
        assert payload["variables_filled"] == []


@pytest.mark.asyncio
async def test_analysis_sweep_404_for_unknown_conversation(
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
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/conversations/does-not-exist/analysis-sweep")
        assert response.status_code == 404
