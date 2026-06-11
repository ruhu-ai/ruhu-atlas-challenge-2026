from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from cryptography.fernet import Fernet
from fastapi import FastAPI

from ruhu.db import build_session_factory
from ruhu.tools.management import APIConnectionStore, CredentialCipher, ToolAgentAssignmentStore, ToolDefinitionStore
from ruhu.tools.reference import build_reference_tool_runtime
from ruhu.tools_api import install_tools_router


def test_callable_catalog_includes_builtin_system_tools(
    postgres_database_url_factory,
    credential_cipher,
    monkeypatch,
) -> None:
    async def run() -> None:
        session_factory = build_session_factory(postgres_database_url_factory())
        tool_runtime, _backend = build_reference_tool_runtime()
        app = FastAPI()

        fake_context = SimpleNamespace(
            principal=SimpleNamespace(
                organization=SimpleNamespace(organization_id="org_tools_catalog"),
            )
        )
        monkeypatch.setattr("ruhu.tools_api.require_authenticated_context", lambda _request: fake_context)

        install_tools_router(
            app,
            connection_store=APIConnectionStore(
                session_factory,
                blob_cipher=credential_cipher,
                legacy_cipher=CredentialCipher(Fernet.generate_key()),
            ),
            definition_store=ToolDefinitionStore(session_factory),
            assignment_store=ToolAgentAssignmentStore(session_factory),
            tool_runtime=tool_runtime,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/agents/agent_sales/callable-catalog")

        assert response.status_code == 200
        payload = response.json()
        builtin_refs = {item["ref"]: item for item in payload["builtin"]}
        assert "knowledge.lookup" in builtin_refs
        assert builtin_refs["knowledge.lookup"]["tool_definition_id"] == "builtin:knowledge.lookup"
        assert builtin_refs["knowledge.lookup"]["kind"] == "builtin"
        assert builtin_refs["knowledge.lookup"]["function_name"] == "lookup"
        assert builtin_refs["knowledge.lookup"]["callable_name"] == "knowledge_lookup"
        assert builtin_refs["knowledge.lookup"]["read_only"] is True

    asyncio.run(run())
