"""Phase 1.5: ``_persist_tokens`` dual-writes ``oauth_token_ct`` when a
``blob_cipher`` is supplied.

The refresh worker and the authorization-code exchange path both bottom out
in ``tools.oauth._persist_tokens``.  Phase 1 left this path plaintext-only;
phase 1.5 threads the phase-1 AEAD cipher through so refreshed tokens land
in both columns.  These tests pin that behaviour against a real Postgres
schema so a future refactor can't silently regress to plaintext-only.
"""
from __future__ import annotations

import pytest

from ruhu.db import build_session_factory
from ruhu.db_models import APIConnectionRecord
from ruhu.tools import oauth as oauth_module
from ruhu.tools.cipher import build_aad
from ruhu.tools.management import APIConnectionStore


@pytest.fixture
def seed_connection(postgres_database_url_factory, credential_cipher):
    """Create a connection via the phase-1 store so ``oauth_token_ct`` starts
    populated with a first-version blob we can compare against."""
    def _seed(initial_token: dict):
        url = postgres_database_url_factory()
        sf = build_session_factory(url)
        store = APIConnectionStore(sf, blob_cipher=credential_cipher)
        record = store.create(
            organization_id="org-A",
            display_name="gh",
            provider="github",
            auth_type="oauth2",
            oauth_token=initial_token,
        )
        return sf, record

    return _seed


def test_persist_tokens_without_cipher_only_writes_legacy_column(
    seed_connection,
) -> None:
    sf, record = seed_connection({"access_token": "v1", "refresh_token": "r"})

    # Grab the phase-1 ciphertext before the refresh to prove it's unchanged.
    with sf() as session:
        before = session.get(APIConnectionRecord, record.connection_id)
        assert before is not None
        pre_ct = before.oauth_token_ct

    oauth_module._persist_tokens(
        sf,
        connection_id=record.connection_id,
        organization_id=record.organization_id,
        token_data={"access_token": "v2", "refresh_token": "r2"},
        # No blob_cipher: legacy-only write path.
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.oauth_token_json == {"access_token": "v2", "refresh_token": "r2"}
        # Ciphertext column is untouched — phase-2 backfill will catch it.
        assert after.oauth_token_ct == pre_ct


def test_persist_tokens_with_cipher_dual_writes(seed_connection, credential_cipher) -> None:
    sf, record = seed_connection({"access_token": "v1", "refresh_token": "r"})

    oauth_module._persist_tokens(
        sf,
        connection_id=record.connection_id,
        organization_id=record.organization_id,
        token_data={"access_token": "v2", "refresh_token": "r2"},
        blob_cipher=credential_cipher,
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.oauth_token_json == {"access_token": "v2", "refresh_token": "r2"}

        # The new ciphertext must decrypt back to the new token, under the
        # correct AAD — proves both the encryption happened AND the AAD is
        # correct for the row we're writing.
        aad = build_aad(
            organization_id=record.organization_id,
            connection_id=record.connection_id,
        )
        assert after.oauth_token_ct is not None
        decrypted = credential_cipher.decrypt(after.oauth_token_ct, aad=aad)

    import json as _json
    assert _json.loads(decrypted) == {"access_token": "v2", "refresh_token": "r2"}


def test_persist_tokens_refresh_with_cipher_after_rotation_produces_new_key_id(
    seed_connection, credential_cipher
) -> None:
    """If a rotation just happened, the refreshed ciphertext must carry the
    new key_id — otherwise the rewrap worker has no way to know this row
    needs re-encrypting.  Pins the contract that every refresh writes a
    fresh blob rather than re-using the previous one."""
    sf, record = seed_connection({"access_token": "v1"})

    with sf() as session:
        before = session.get(APIConnectionRecord, record.connection_id)
        assert before is not None and before.oauth_token_ct is not None
        pre_key_id = before.oauth_token_ct[1:17]

    oauth_module._persist_tokens(
        sf,
        connection_id=record.connection_id,
        organization_id=record.organization_id,
        token_data={"access_token": "v2"},
        blob_cipher=credential_cipher,
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None and after.oauth_token_ct is not None
        # Same key → same key_id, different nonce → different ciphertext bytes.
        assert after.oauth_token_ct[1:17] == pre_key_id
        assert after.oauth_token_ct != before.oauth_token_ct  # nonce is fresh
