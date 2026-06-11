from __future__ import annotations

from cryptography.fernet import Fernet

from ruhu.db import build_session_factory
from ruhu.tools.ingestion import OpenAPIToolIngestionService
from ruhu.tools.management import (
    APIConnectionStore,
    AgentToolBindingStore,
    CredentialCipher,
    ToolAgentAssignmentStore,
    ToolDefinitionStore,
)


def test_openapi_ingestion_creates_connection_and_tools(postgres_database_url_factory, credential_cipher) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    service = OpenAPIToolIngestionService(
        connection_store=APIConnectionStore(
            session_factory,
            blob_cipher=credential_cipher,
            legacy_cipher=CredentialCipher(Fernet.generate_key()),
        ),
        definition_store=ToolDefinitionStore(session_factory),
        assignment_store=ToolAgentAssignmentStore(session_factory),
    )

    result = service.ingest(
        organization_id="org_123",
        spec={
            "openapi": "3.0.3",
            "info": {"title": "CRM API"},
            "servers": [{"url": "https://crm.example.com"}],
            "paths": {
                "/contacts/{contact_id}": {
                    "get": {
                        "operationId": "get_contact",
                        "summary": "Get contact",
                        "description": "Fetch a single contact by identifier from the remote CRM.",
                        "parameters": [
                            {
                                "name": "contact_id",
                                "in": "path",
                                "required": True,
                                "description": "CRM contact identifier.",
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Contact found",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "string"}},
                                            "additionalProperties": True,
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        },
        tool_ref_prefix="crm",
        agent_id="agent_1",
    )

    assert result.connection_id
    assert len(result.created_tool_ids) == 1
    assert len(result.assigned_tool_ids) == 1


def test_openapi_ingestion_updates_existing_tool(postgres_database_url_factory, credential_cipher) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    connection_store = APIConnectionStore(
        session_factory,
        blob_cipher=credential_cipher,
        legacy_cipher=CredentialCipher(Fernet.generate_key()),
    )
    definition_store = ToolDefinitionStore(session_factory)
    service = OpenAPIToolIngestionService(
        connection_store=connection_store,
        definition_store=definition_store,
    )

    first = service.ingest(
        organization_id="org_123",
        spec={
            "openapi": "3.0.3",
            "info": {"title": "CRM API"},
            "servers": [{"url": "https://crm.example.com"}],
            "paths": {
                "/contacts": {
                    "post": {
                        "operationId": "create_contact",
                        "summary": "Create contact",
                        "description": "Create a new contact in the external CRM system.",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        },
        tool_ref_prefix="crm",
    )
    second = service.ingest(
        organization_id="org_123",
        spec={
            "openapi": "3.0.3",
            "info": {"title": "CRM API"},
            "servers": [{"url": "https://crm.example.com"}],
            "paths": {
                "/contacts": {
                    "post": {
                        "operationId": "create_contact",
                        "summary": "Create contact",
                        "description": "Create a new contact in the external CRM system with the latest schema.",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        },
        connection_id=first.connection_id,
        tool_ref_prefix="crm",
    )

    assert len(first.created_tool_ids) == 1
    assert len(second.updated_tool_ids) == 1
