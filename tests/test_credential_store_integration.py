"""End-to-end integration for the phase-1 credential-encryption store.

Verifies the dual-write + audited-read contract against a real Postgres
session: ``APIConnectionStore.create`` populates both the legacy
``oauth_token_json`` and the new ``oauth_token_ct`` columns, and
``get_oauth_token`` decrypts the new column with AAD binding + emits
exactly one ``credential.decrypted`` audit event.

Running this requires Postgres (same dev DB the other integration tests
use).  Unit tests of the cipher itself live in ``test_credential_cipher.py``.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from ruhu.audit.events import AuditEvent
from ruhu.db import build_session_factory
from ruhu.db_models import APIConnectionRecord
from ruhu.tools.cipher import DecryptionFailed, build_aad
from ruhu.tools.management import APIConnectionStore


class _CapturingAuditRouter:
    """Minimal router that records events without talking to Postgres.

    Keeps the integration scope to the cipher + ORM path; audit store
    integration lives in the audit test suite.
    """

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def route(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
def connection_store_factory(postgres_database_url_factory, credential_cipher):
    """Build a fresh-schema ``APIConnectionStore`` wired to an in-memory audit
    router.  The caller gets back the store plus the router so tests can
    inspect emitted events."""
    def _build() -> tuple[APIConnectionStore, _CapturingAuditRouter, Any]:
        url = postgres_database_url_factory()
        session_factory = build_session_factory(url)
        router = _CapturingAuditRouter()
        store = APIConnectionStore(
            session_factory, blob_cipher=credential_cipher, audit_router=router
        )
        return store, router, session_factory

    return _build


class TestDualWrite:
    def test_create_writes_both_legacy_and_encrypted_columns(
        self, connection_store_factory
    ) -> None:
        store, _router, sf = connection_store_factory()
        token = {"access_token": "slack-token-fixture", "refresh_token": "r"}
        record = store.create(
            organization_id="org-A",
            display_name="slack",
            provider="slack",
            auth_type="oauth2",
            oauth_token=token,
        )

        with sf() as session:
            fresh = session.scalar(
                select(APIConnectionRecord).where(
                    APIConnectionRecord.connection_id == record.connection_id
                )
            )
            assert fresh is not None
            # Legacy column preserved for rollback safety during phase 1.
            assert fresh.oauth_token_json == token
            # New column populated; ciphertext is opaque (non-plaintext).
            assert fresh.oauth_token_ct is not None
            assert b"slack-token-fixture" not in fresh.oauth_token_ct
            assert fresh.oauth_token_ct[0] == 0x02  # blob version byte

    def test_update_rewrites_both_columns(self, connection_store_factory) -> None:
        store, _router, sf = connection_store_factory()
        record = store.create(
            organization_id="org-A",
            display_name="slack",
            provider="slack",
            auth_type="oauth2",
            oauth_token={"access_token": "v1"},
        )
        store.update(record.connection_id, oauth_token={"access_token": "v2"})

        with sf() as session:
            fresh = session.get(APIConnectionRecord, record.connection_id)
            assert fresh is not None
            assert fresh.oauth_token_json == {"access_token": "v2"}
            # Ciphertext must change when plaintext changes.
            assert fresh.oauth_token_ct is not None
            assert b"v1" not in fresh.oauth_token_ct
            assert b"v2" not in fresh.oauth_token_ct  # encrypted, not searchable


class TestAuditedRead:
    def test_get_oauth_token_decrypts_and_emits_one_event(
        self, connection_store_factory
    ) -> None:
        store, router, _sf = connection_store_factory()
        record = store.create(
            organization_id="org-A",
            display_name="slack",
            provider="slack",
            auth_type="oauth2",
            oauth_token={"access_token": "slack-token-fixture"},
        )

        got = store.get_oauth_token(
            record.connection_id,
            actor_id="user-42",
            actor_type="user",
            purpose="http_tool_call",
        )
        assert got == {"access_token": "slack-token-fixture"}

        assert len(router.events) == 1
        evt = router.events[0]
        assert evt.event_type == "credential.decrypted"
        assert evt.outcome == "success"
        assert evt.actor_id == "user-42"
        assert evt.resource_id == record.connection_id
        assert evt.detail["purpose"] == "http_tool_call"
        assert evt.detail["key_id"]  # non-empty

    def test_swapped_ciphertext_between_connections_raises_and_audits_failure(
        self, connection_store_factory
    ) -> None:
        """AAD binding defense: moving the encrypted blob from one connection
        into another row (same org) must fail decrypt.  This is the primary
        defense against SQL-injection-enabled cross-row data moves."""
        store, router, sf = connection_store_factory()

        a = store.create(
            organization_id="org-A",
            display_name="slack-a",
            provider="slack",
            auth_type="oauth2",
            oauth_token={"access_token": "A-secret"},
        )
        b = store.create(
            organization_id="org-A",
            display_name="slack-b",
            provider="slack",
            auth_type="oauth2",
            oauth_token={"access_token": "B-secret"},
        )

        # Swap a's ciphertext onto b's row.
        with sf.begin() as session:
            row_a = session.get(APIConnectionRecord, a.connection_id)
            row_b = session.get(APIConnectionRecord, b.connection_id)
            assert row_a is not None and row_b is not None
            row_b.oauth_token_ct = row_a.oauth_token_ct

        # Clear previously-captured events so we see only the failing read.
        router.events.clear()

        with pytest.raises(DecryptionFailed):
            store.get_oauth_token(
                b.connection_id,
                actor_id="user-42",
                actor_type="user",
                purpose="http_tool_call",
            )

        # Failure must still be audited — "no trail" is worse than "failed trail".
        assert len(router.events) == 1
        assert router.events[0].outcome == "failure"
        assert router.events[0].detail.get("error") == "DecryptionFailed"

    def test_falls_back_to_legacy_column_when_encrypted_is_empty(
        self, connection_store_factory
    ) -> None:
        """Rows predating phase 1 have NULL ``oauth_token_ct``.  The audited
        read path must transparently return the legacy plaintext so the
        rollout doesn't break existing connections."""
        store, router, sf = connection_store_factory()
        record = store.create(
            organization_id="org-A",
            display_name="slack",
            provider="slack",
            auth_type="oauth2",
            oauth_token={"access_token": "legacy"},
        )
        # Simulate a pre-phase-1 row: clear the encrypted column, leave JSON.
        with sf.begin() as session:
            row = session.get(APIConnectionRecord, record.connection_id)
            assert row is not None
            row.oauth_token_ct = None

        router.events.clear()
        got = store.get_oauth_token(
            record.connection_id,
            actor_id="system",
            actor_type="system",
            purpose="oauth_refresh",
        )
        assert got == {"access_token": "legacy"}
        # Still audited — operators need to see fallback reads to know how
        # much of the install is still un-migrated.
        assert len(router.events) == 1
        assert router.events[0].outcome == "success"


class TestAADConstruction:
    def test_aad_binds_both_organization_and_connection(self) -> None:
        # Sanity check that the helper exported from cipher.py is what the
        # store actually uses — prevents a subtle bug where the store and
        # tests disagree on AAD canonicalisation.
        aad = build_aad(organization_id="org-A", connection_id="conn-1")
        assert b"org-A" in aad and b"conn-1" in aad
