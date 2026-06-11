"""Phase 2: ``ToolSpecCompiler`` routes OAuth2 decrypts through the
audited path when the catalog resolver is invoked with a ``caller``.

The contract we pin here:
  - invoke path  (caller present)  → decrypt + emit audit + bake auth header
  - list path    (caller is None)  → skip decrypt + no audit + no auth header

Miss either arm and phase 2 regresses: either we over-audit the UI
catalog-list path (noise), or we under-audit the real invoke path
(compliance gap).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from ruhu.audit.events import AuditEvent
from ruhu.db import build_session_factory
from ruhu.db_models import APIConnectionRecord, ToolDefinitionRecord
from ruhu.tools.compiler import ToolSpecCompiler
from ruhu.tools.db_catalog import SQLAlchemyCatalogResolver
from ruhu.tools.management import APIConnectionStore
from ruhu.tools.types import ToolCaller


class _CapturingAuditRouter:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def route(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
def resolver_with_seeded_oauth_tool(postgres_database_url_factory, credential_cipher):
    """Build a resolver + store + compiler wired to the same cipher, with one
    seeded OAuth2 connection and tool definition pre-encrypted by the store."""
    def _build():
        url = postgres_database_url_factory()
        sf = build_session_factory(url)
        router = _CapturingAuditRouter()
        store = APIConnectionStore(sf, blob_cipher=credential_cipher, audit_router=router)

        # Seed via the store so oauth_token_ct is populated.
        record = store.create(
            organization_id="org-A",
            display_name="gh",
            provider="github",
            auth_type="oauth2",
            base_url="https://api.github.com",
            oauth_token={"access_token": "gh-secret-xyz"},
        )

        now = datetime.now(timezone.utc)
        with sf.begin() as session:
            session.add(
                ToolDefinitionRecord(
                    tool_definition_id="td_gh_user",
                    organization_id="org-A",
                    tool_ref="gh_user",
                    connection_id=record.connection_id,
                    display_name="Fetch GitHub user",
                    description="Return /user for the authenticated token.",
                    endpoint_path="/user",
                    http_method="GET",
                    input_schema_json={"type": "object", "properties": {}, "required": []},
                    output_schema_json={"type": "object", "additionalProperties": True},
                    timeout_ms=5000,
                    enabled=True,
                    metadata_json={
                        "purpose": "Retrieve the authenticated GitHub user profile before making account-specific decisions.",
                        "when_to_use": [
                            "Use when the workflow needs the current GitHub account profile for read-only inspection."
                        ],
                        "when_not_to_use": [
                            "Do not use for repository writes, destructive mutations, or unauthenticated preview flows."
                        ],
                        "input_examples": [
                            {
                                "name": "fetch_profile",
                                "description": "Loads the current GitHub profile for downstream read-only logic.",
                                "args": {},
                            }
                        ],
                        "failure_modes": [
                            {
                                "kind": "transient_upstream_error",
                                "description": "GitHub API is temporarily unavailable or returns a retryable failure.",
                                "retryable": True,
                            }
                        ],
                        "output_validation_mode": "strict",
                    },
                    created_at=now,
                    updated_at=now,
                )
            )

        compiler = ToolSpecCompiler(cipher=None, connection_store=store)
        resolver = SQLAlchemyCatalogResolver(sf, compiler)
        return resolver, store, router

    return _build


class TestCompileInvokePath:
    """With a ``caller`` provided, the compiler decrypts via the store so one
    audit event fires and the Authorization header is populated."""

    def test_resolve_with_caller_emits_one_audit_event(
        self, resolver_with_seeded_oauth_tool
    ) -> None:
        resolver, _store, router = resolver_with_seeded_oauth_tool()
        caller = ToolCaller(
            channel="web_chat",
            conversation_id="conv-1",
            tenant_id="org-A",
            user_id="user-42",
            agent_id="agent-1",
        )
        router.events.clear()  # ignore create()'s events from the fixture setup

        spec = resolver.resolve("gh_user", organization_id="org-A", caller=caller)

        assert spec is not None
        headers = dict(spec.executor_config.get("headers") or {})
        assert headers.get("Authorization") == "Bearer gh-secret-xyz"

        decrypts = [e for e in router.events if e.event_type == "credential.decrypted"]
        assert len(decrypts) == 1
        evt = decrypts[0]
        assert evt.actor_id == "user-42"
        assert evt.detail["purpose"] == "http_tool_call"
        assert evt.detail["actor_type"] == "user"
        assert spec.purpose is not None
        assert spec.when_to_use
        assert spec.when_not_to_use
        assert spec.input_examples[0].name == "fetch_profile"
        assert spec.failure_modes[0].kind == "transient_upstream_error"
        assert spec.output_validation_mode == "strict"

    def test_agent_driven_call_audits_as_tool_runtime(
        self, resolver_with_seeded_oauth_tool
    ) -> None:
        """When no user_id is attached (agent acting on its own) the audit
        actor is the agent under actor_type=tool_runtime."""
        resolver, _store, router = resolver_with_seeded_oauth_tool()
        caller = ToolCaller(
            channel="phone",
            conversation_id="conv-2",
            tenant_id="org-A",
            agent_id="agent-7",
        )
        router.events.clear()

        resolver.resolve("gh_user", organization_id="org-A", caller=caller)

        decrypts = [e for e in router.events if e.event_type == "credential.decrypted"]
        assert len(decrypts) == 1
        assert decrypts[0].actor_id == "agent-7"
        assert decrypts[0].detail["actor_type"] == "tool_runtime"


class TestCompileListPath:
    """Without a ``caller`` the compiler must NOT decrypt — list/preview is
    for UI-facing catalog rendering, not tool execution."""

    def test_list_for_organization_skips_decrypt_and_audit(
        self, resolver_with_seeded_oauth_tool
    ) -> None:
        resolver, _store, router = resolver_with_seeded_oauth_tool()
        router.events.clear()

        specs = resolver.list_for_organization(organization_id="org-A")

        assert len(specs) == 1
        headers = dict(specs[0].executor_config.get("headers") or {})
        # No Authorization header — spec is safe to show but not to execute.
        assert "Authorization" not in headers
        # No audit noise.
        decrypts = [e for e in router.events if e.event_type == "credential.decrypted"]
        assert decrypts == []

    def test_resolve_without_caller_still_skips_decrypt(
        self, resolver_with_seeded_oauth_tool
    ) -> None:
        """Backwards-compat: old callers that don't pass ``caller`` keep
        working (no crash) but get a no-credentials spec."""
        resolver, _store, router = resolver_with_seeded_oauth_tool()
        router.events.clear()

        spec = resolver.resolve("gh_user", organization_id="org-A")

        assert spec is not None
        assert "Authorization" not in dict(spec.executor_config.get("headers") or {})
        assert [e for e in router.events if e.event_type == "credential.decrypted"] == []


def test_compile_provider_template_tool_with_empty_schema_is_permissive(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    store = APIConnectionStore(sf, blob_cipher=credential_cipher)

    connection = store.create(
        organization_id="org-permissive",
        display_name="calendar",
        provider="google_calendar",
        auth_type="oauth2",
        base_url="https://www.googleapis.com/calendar/v3",
        oauth_token={"access_token": "calendar-token"},
    )

    now = datetime.now(timezone.utc)
    with sf.begin() as session:
        session.add(
            ToolDefinitionRecord(
                tool_definition_id="td_calendar_create_event",
                organization_id="org-permissive",
                tool_ref="calendar.create_event",
                connection_id=connection.connection_id,
                kind="integration",
                display_name="Create Event",
                description="Create a calendar event.",
                endpoint_path="/calendars/primary/events",
                http_method="POST",
                input_schema_json={},
                output_schema_json={},
                timeout_ms=5000,
                enabled=True,
                metadata_json={"template_slug": "google_calendar"},
                created_at=now,
                updated_at=now,
            )
        )

    resolver = SQLAlchemyCatalogResolver(
        sf,
        ToolSpecCompiler(cipher=None, connection_store=store),
    )
    spec = resolver.resolve(
        "calendar.create_event",
        organization_id="org-permissive",
        caller=ToolCaller(
            channel="phone",
            conversation_id="conv-permissive",
            tenant_id="org-permissive",
            user_id="user-1",
            agent_id="agent-1",
        ),
    )

    assert spec is not None
    assert spec.input_schema["type"] == "object"
    assert spec.input_schema["additionalProperties"] is True
    assert spec.output_schema["type"] == "object"
    assert spec.output_schema["additionalProperties"] is True


class TestCompileMetadataPolicy:
    def test_resolve_hydrates_confirmation_annotations_and_channels(
        self, postgres_database_url_factory, credential_cipher
    ) -> None:
        url = postgres_database_url_factory()
        sf = build_session_factory(url)
        store = APIConnectionStore(sf, blob_cipher=credential_cipher)

        connection = store.create(
            organization_id="org-A",
            display_name="crm",
            provider="demo_http",
            auth_type="none",
            base_url="https://api.example.com",
        )

        now = datetime.now(timezone.utc)
        with sf.begin() as session:
            session.add(
                ToolDefinitionRecord(
                    tool_definition_id="td_crm_submit",
                    organization_id="org-A",
                    tool_ref="crm.submit_lead",
                    connection_id=connection.connection_id,
                    display_name="Submit lead",
                    description="Submit a CRM lead and require explicit user confirmation before execution.",
                    endpoint_path="/leads",
                    http_method="POST",
                    input_schema_json={"type": "object", "properties": {}, "required": []},
                    output_schema_json={"type": "object", "additionalProperties": True},
                    timeout_ms=5000,
                    enabled=True,
                    metadata_json={
                        "annotations": {"destructive": True, "idempotent": True},
                        "confirmation": "always",
                        "confirmation_prompt": "Confirm and I’ll submit this lead.",
                        "allowed_channels": ["web_chat"],
                        "tags": ["crm", "sales"],
                        "executor_config": {"execution_mode": "deferred"},
                    },
                    created_at=now,
                    updated_at=now,
                )
            )

        resolver = SQLAlchemyCatalogResolver(sf, ToolSpecCompiler(cipher=None, connection_store=store))
        spec = resolver.resolve(
            "crm.submit_lead",
            organization_id="org-A",
            caller=ToolCaller(channel="web_chat", conversation_id="conv-1", tenant_id="org-A"),
        )

        assert spec is not None
        assert spec.confirmation == "always"
        assert spec.confirmation_prompt == "Confirm and I’ll submit this lead."
        assert spec.annotations.destructive is True
        assert spec.annotations.idempotent is True
        assert spec.allowed_channels == ["web_chat"]
        assert spec.tags == ["crm", "sales"]
        assert spec.executor_config["execution_mode"] == "deferred"


class TestBackfillParityWithStore:
    """A row backfilled by the script must decrypt identically to a row
    written fresh by the store.  Prevents format drift between the two
    write paths during phase 2."""

    def test_backfilled_row_decrypts_like_store_written_row(
        self, postgres_database_url_factory, credential_cipher
    ) -> None:
        from scripts import backfill_encrypt_credentials as backfill
        from ruhu.tools.cipher import build_aad

        url = postgres_database_url_factory()
        sf = build_session_factory(url)
        now = datetime.now(timezone.utc)

        # Seed a row the OLD way: oauth_token_json populated, ct column NULL.
        with sf.begin() as session:
            session.add(
                APIConnectionRecord(
                    connection_id="conn_legacy_001",
                    organization_id="org-A",
                    display_name="legacy",
                    provider="salesforce",
                    auth_type="oauth2",
                    oauth_token_json={"access_token": "legacy-abc"},
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )

        # Run one batch of the backfill under the same cipher.
        with sf() as session:
            n, _ = backfill._process_batch(
                session,
                credential_cipher,
                batch_size=10,
                dry_run=False,
                after_connection_id=None,
            )
        assert n == 1

        # Row should now decrypt to the original token under the correct AAD.
        with sf() as session:
            row = session.get(APIConnectionRecord, "conn_legacy_001")
            assert row is not None and row.oauth_token_ct is not None
            aad = build_aad(
                organization_id=row.organization_id,
                connection_id=row.connection_id,
            )
            decrypted = json.loads(
                credential_cipher.decrypt(bytes(row.oauth_token_ct), aad=aad)
            )
            assert decrypted == {"access_token": "legacy-abc"}
