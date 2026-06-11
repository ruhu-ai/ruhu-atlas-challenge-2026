"""Phase 2a-cloning — voice clone provider + store + catalog merge tests.

Tests are organised by surface:

* Provider extension (`VertexGeminiVoiceProvider.clone_voice`) — input
  validation, MIME allowlist, size cap, error wrapping.
* Store (`VoiceCloneStore`) — CRUD + AES-GCM AAD binding contract +
  soft-delete semantics + tenant scoping.
* Catalog merge — clones appear at the top, language filter applies,
  empty / no-match cases.

No tests hit Google's real API; cloning calls are monkey-patched. No
tests hit a live Postgres; the store uses an in-memory SQLite session
factory built from the existing ``Base.metadata``.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ruhu.db_models import Base, VoiceCloneRecord
from ruhu.tools.cipher import DecryptionFailed, FernetCipher
from ruhu.voice import (
    VertexGeminiVoiceProvider,
    VoiceCatalogEntry,
    VoiceCatalogPage,
    VoiceCloningConsentError,
    VoiceCloningResult,
    VoiceGender,
)
from ruhu.voice_cloning import (
    VoiceClone,
    VoiceCloneStore,
    merge_catalog_with_clones,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_session_factory() -> sessionmaker:
    """In-memory SQLite session factory with ONLY the voice_clones table.

    The project's full schema includes Postgres-specific ARRAY columns
    (e.g. on rule_bindings) that SQLite can't compile. We only need the
    one table the store touches, so we create exactly that one. This
    keeps tests fast (no Postgres) and isolated."""
    engine = create_engine("sqlite:///:memory:")
    VoiceCloneRecord.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def cipher() -> FernetCipher:
    return FernetCipher(primary=Fernet.generate_key().decode())


@pytest.fixture()
def store(cipher: FernetCipher) -> VoiceCloneStore:
    return VoiceCloneStore(_make_session_factory(), cipher=cipher)


# ── Provider: clone_voice contract ───────────────────────────────────────────


class TestVertexCloneVoiceContract:
    def test_supports_cloning_flag_true(self):
        assert VertexGeminiVoiceProvider.supports_cloning is True

    def test_consent_audio_required(self):
        provider = VertexGeminiVoiceProvider()
        with pytest.raises(ValueError, match="non-empty"):
            provider.clone_voice(
                consent_audio=b"",
                consent_audio_mime="audio/wav",
                display_name="Test",
                language="en-US",
                organization_id="org-1",
                actor_id="user-1",
            )

    def test_consent_audio_size_cap(self):
        provider = VertexGeminiVoiceProvider()
        with pytest.raises(VoiceCloningConsentError, match="exceeds"):
            provider.clone_voice(
                consent_audio=b"x" * 1_500_000,
                consent_audio_mime="audio/wav",
                display_name="Test",
                language="en-US",
                organization_id="org-1",
                actor_id="user-1",
            )

    def test_unsupported_mime_rejected(self):
        provider = VertexGeminiVoiceProvider()
        with pytest.raises(VoiceCloningConsentError, match="unsupported"):
            provider.clone_voice(
                consent_audio=b"x" * 100,
                consent_audio_mime="application/octet-stream",
                display_name="Test",
                language="en-US",
                organization_id="org-1",
                actor_id="user-1",
            )

    def test_returns_cloning_result(self, monkeypatch):
        def fake_clone(*, consent_audio, consent_audio_mime, reference_audio,
                       reference_audio_mime, language, organization_id, actor_id):
            return ("opaque-cloning-key-from-google", 0.50)

        monkeypatch.setattr(
            "ruhu.voice.vertex_gemini_provider._clone_via_chirp3",
            fake_clone,
        )
        provider = VertexGeminiVoiceProvider()
        result = provider.clone_voice(
            consent_audio=b"audio-bytes",
            consent_audio_mime="audio/wav",
            display_name="Maya",
            language="en-US",
            organization_id="org-1",
            actor_id="user-1",
        )
        assert isinstance(result, VoiceCloningResult)
        assert result.voice_cloning_key == "opaque-cloning-key-from-google"
        assert result.display_name == "Maya"
        assert result.language == "en-US"
        assert result.estimated_cost_usd == 0.50

    def test_consent_error_propagates_unchanged(self, monkeypatch):
        def fake_clone(**_kw):
            raise VoiceCloningConsentError("Google rejected: consent statement not detected")

        monkeypatch.setattr(
            "ruhu.voice.vertex_gemini_provider._clone_via_chirp3",
            fake_clone,
        )
        provider = VertexGeminiVoiceProvider()
        with pytest.raises(VoiceCloningConsentError, match="consent statement"):
            provider.clone_voice(
                consent_audio=b"x" * 100,
                consent_audio_mime="audio/wav",
                display_name="Test",
                language="en-US",
                organization_id="org-1",
                actor_id="user-1",
            )

    def test_generic_exception_wrapped_to_runtime_error(self, monkeypatch):
        """Provider exceptions other than consent errors must wrap to a
        generic RuntimeError so the API layer doesn't leak Google's
        exception types in 5xx responses."""
        def fake_clone(**_kw):
            raise ConnectionError("Google API unreachable")

        monkeypatch.setattr(
            "ruhu.voice.vertex_gemini_provider._clone_via_chirp3",
            fake_clone,
        )
        provider = VertexGeminiVoiceProvider()
        with pytest.raises(RuntimeError, match="voice cloning failed"):
            provider.clone_voice(
                consent_audio=b"x" * 100,
                consent_audio_mime="audio/wav",
                display_name="Test",
                language="en-US",
                organization_id="org-1",
                actor_id="user-1",
            )


# ── Store: encryption + AAD ──────────────────────────────────────────────────


class TestVoiceCloneStoreEncryption:
    def test_create_persists_clone_and_returns_summary(self, store: VoiceCloneStore):
        clone = store.create(
            organization_id="org-1",
            agent_id=None,
            provider="vertex_gemini",
            display_name="CEO Voice",
            language="en-US",
            voice_cloning_key="secret-key-from-google",
            consent_audio=b"consent-audio-bytes",
            consent_audio_mime="audio/wav",
            created_by="user-1",
        )
        assert clone.clone_id.startswith("vc_")
        assert clone.organization_id == "org-1"
        assert clone.display_name == "CEO Voice"
        assert clone.has_voice_cloning_key is True
        # Plaintext key MUST NOT be on the public surface.
        assert not hasattr(clone, "voice_cloning_key")

    def test_cloning_key_encrypted_at_rest(self, store: VoiceCloneStore):
        """Critical: the raw bytes in voice_cloning_key_enc must NOT
        contain the plaintext key. Anyone with DB access shouldn't be
        able to grep for cloning credentials."""
        clone = store.create(
            organization_id="org-1",
            agent_id=None,
            provider="vertex_gemini",
            display_name="Test",
            language="en-US",
            voice_cloning_key="distinctive-plaintext-needle",
            consent_audio=b"audio",
            consent_audio_mime="audio/wav",
            created_by="user-1",
        )
        # Use the same session_factory to read the raw bytes.
        with store._session_factory() as session:
            row = session.get(VoiceCloneRecord, clone.clone_id)
            assert row is not None
            assert b"distinctive-plaintext-needle" not in row.voice_cloning_key_enc
            # And the encrypted blob is non-trivial (not just empty).
            assert len(row.voice_cloning_key_enc) > 16

    def test_decrypt_round_trips(self, store: VoiceCloneStore):
        clone = store.create(
            organization_id="org-1",
            agent_id=None,
            provider="vertex_gemini",
            display_name="Test",
            language="en-US",
            voice_cloning_key="round-trip-key",
            consent_audio=b"audio",
            consent_audio_mime="audio/wav",
            created_by="user-1",
        )
        plaintext = store.decrypt_key_for_synthesis(
            clone.clone_id, organization_id="org-1",
        )
        assert plaintext == "round-trip-key"

    def test_decrypt_requires_correct_organization_aad(
        self, store: VoiceCloneStore,
    ):
        """Cross-tenant ciphertext swap defence: decrypting a clone with
        the WRONG organization_id must fail because the AAD won't match."""
        clone = store.create(
            organization_id="org-1",
            agent_id=None,
            provider="vertex_gemini",
            display_name="Test",
            language="en-US",
            voice_cloning_key="secret",
            consent_audio=b"audio",
            consent_audio_mime="audio/wav",
            created_by="user-1",
        )
        # Move the row's organization_id to org-2 directly in the DB so
        # we exercise the AAD mismatch path. (In production this can't
        # happen via the API — but if a manual DB edit ever happens, the
        # AAD binding is what catches it.)
        with store._session_factory() as session:
            row = session.get(VoiceCloneRecord, clone.clone_id)
            row.organization_id = "org-2"
            session.commit()
        # Now even using the (now-correct) org_id, the AAD doesn't match
        # what was used at encryption time → DecryptionFailed.
        with pytest.raises(DecryptionFailed):
            store.decrypt_key_for_synthesis(
                clone.clone_id, organization_id="org-2",
            )


# ── Store: tenant scoping ────────────────────────────────────────────────────


class TestVoiceCloneStoreTenantScoping:
    def test_get_returns_none_for_different_org(self, store: VoiceCloneStore):
        clone = store.create(
            organization_id="org-1",
            agent_id=None,
            provider="vertex_gemini",
            display_name="Test",
            language="en-US",
            voice_cloning_key="key",
            consent_audio=b"audio",
            consent_audio_mime="audio/wav",
            created_by="user-1",
        )
        assert store.get(clone.clone_id, organization_id="org-2") is None
        # And the legitimate org sees it.
        assert store.get(clone.clone_id, organization_id="org-1") is not None

    def test_list_active_only_returns_caller_org(self, store: VoiceCloneStore):
        store.create(
            organization_id="org-1", agent_id=None, provider="vertex_gemini",
            display_name="Org-1 Voice", language="en-US",
            voice_cloning_key="k1", consent_audio=b"a", consent_audio_mime="audio/wav",
            created_by="user-1",
        )
        store.create(
            organization_id="org-2", agent_id=None, provider="vertex_gemini",
            display_name="Org-2 Voice", language="en-US",
            voice_cloning_key="k2", consent_audio=b"a", consent_audio_mime="audio/wav",
            created_by="user-2",
        )
        org1_clones = store.list_active(organization_id="org-1")
        assert len(org1_clones) == 1
        assert org1_clones[0].display_name == "Org-1 Voice"

    def test_decrypt_rejects_wrong_org(self, store: VoiceCloneStore):
        clone = store.create(
            organization_id="org-1", agent_id=None, provider="vertex_gemini",
            display_name="Test", language="en-US",
            voice_cloning_key="key", consent_audio=b"audio",
            consent_audio_mime="audio/wav", created_by="user-1",
        )
        # The wrong-org caller can't even find the row — KeyError, not
        # DecryptionFailed (we don't want to leak that the row exists).
        with pytest.raises(KeyError):
            store.decrypt_key_for_synthesis(
                clone.clone_id, organization_id="org-2",
            )


# ── Store: soft-delete ───────────────────────────────────────────────────────


class TestVoiceCloneStoreSoftDelete:
    def test_soft_delete_returns_true_on_first_call(self, store: VoiceCloneStore):
        clone = store.create(
            organization_id="org-1", agent_id=None, provider="vertex_gemini",
            display_name="Test", language="en-US",
            voice_cloning_key="key", consent_audio=b"audio",
            consent_audio_mime="audio/wav", created_by="user-1",
        )
        assert store.soft_delete(clone.clone_id, organization_id="org-1") is True

    def test_soft_delete_idempotent(self, store: VoiceCloneStore):
        clone = store.create(
            organization_id="org-1", agent_id=None, provider="vertex_gemini",
            display_name="Test", language="en-US",
            voice_cloning_key="key", consent_audio=b"audio",
            consent_audio_mime="audio/wav", created_by="user-1",
        )
        assert store.soft_delete(clone.clone_id, organization_id="org-1") is True
        # Second call returns False (already deleted).
        assert store.soft_delete(clone.clone_id, organization_id="org-1") is False

    def test_soft_delete_excludes_from_list_active(self, store: VoiceCloneStore):
        clone = store.create(
            organization_id="org-1", agent_id=None, provider="vertex_gemini",
            display_name="Test", language="en-US",
            voice_cloning_key="key", consent_audio=b"audio",
            consent_audio_mime="audio/wav", created_by="user-1",
        )
        store.soft_delete(clone.clone_id, organization_id="org-1")
        assert store.list_active(organization_id="org-1") == []

    def test_soft_delete_preserves_row_for_audit(self, store: VoiceCloneStore):
        """Compliance retention: the row stays in the DB after
        soft-delete with deleted_at set. Hard-delete is a separate
        retention sweep, not soft_delete."""
        clone = store.create(
            organization_id="org-1", agent_id=None, provider="vertex_gemini",
            display_name="Test", language="en-US",
            voice_cloning_key="key", consent_audio=b"audio",
            consent_audio_mime="audio/wav", created_by="user-1",
        )
        store.soft_delete(clone.clone_id, organization_id="org-1")
        with store._session_factory() as session:
            row = session.get(VoiceCloneRecord, clone.clone_id)
            assert row is not None
            assert row.deleted_at is not None
            # Encrypted key + consent audio still on the row.
            assert row.voice_cloning_key_enc
            assert row.consent_audio_blob == b"audio"

    def test_decrypt_excluded_after_soft_delete(self, store: VoiceCloneStore):
        clone = store.create(
            organization_id="org-1", agent_id=None, provider="vertex_gemini",
            display_name="Test", language="en-US",
            voice_cloning_key="key", consent_audio=b"audio",
            consent_audio_mime="audio/wav", created_by="user-1",
        )
        store.soft_delete(clone.clone_id, organization_id="org-1")
        # After soft-delete, decrypt fails closed — no synthesis can use
        # the cloned voice.
        with pytest.raises(KeyError):
            store.decrypt_key_for_synthesis(
                clone.clone_id, organization_id="org-1",
            )


# ── Catalog merge ────────────────────────────────────────────────────────────


def _empty_catalog(total: int = 0) -> VoiceCatalogPage:
    return VoiceCatalogPage(voices=[], next_cursor=None, total_count=total)


def _catalog_with(*entries: VoiceCatalogEntry) -> VoiceCatalogPage:
    return VoiceCatalogPage(
        voices=list(entries), next_cursor=None, total_count=len(entries),
    )


def _make_clone(
    *,
    clone_id: str = "vc_test",
    organization_id: str = "org-1",
    display_name: str = "Test Clone",
    language: str = "en-US",
    provider: str = "vertex_gemini",
    deleted_at: datetime | None = None,
) -> VoiceClone:
    return VoiceClone(
        clone_id=clone_id,
        organization_id=organization_id,
        agent_id=None,
        provider=provider,
        display_name=display_name,
        language=language,
        created_at=datetime.now(timezone.utc),
        created_by="user-1",
        deleted_at=deleted_at,
    )


class TestCatalogMerge:
    def test_no_clones_passes_through(self):
        original = _catalog_with(
            VoiceCatalogEntry(
                voice_id="en-US-Chirp3-HD-Kore",
                provider="vertex_gemini",
                display_name="Kore",
                language="en-US",
                gender=VoiceGender.neutral,
            ),
        )
        result = merge_catalog_with_clones(catalog_page=original, clones=[])
        assert result is original

    def test_clones_appear_at_top_of_response(self):
        catalog = _catalog_with(
            VoiceCatalogEntry(
                voice_id="en-US-Chirp3-HD-Kore",
                provider="vertex_gemini",
                display_name="Kore",
                language="en-US",
                gender=VoiceGender.neutral,
            ),
        )
        clone = _make_clone(display_name="Custom CEO Voice")
        result = merge_catalog_with_clones(
            catalog_page=catalog, clones=[clone],
        )
        assert result.voices[0].display_name == "Custom CEO Voice"
        assert result.voices[1].display_name == "Kore"

    def test_clone_provider_marker_added(self):
        """UI uses the provider field to render the 'Cloned' badge —
        clones must come back as `<provider>_clone`."""
        clone = _make_clone(provider="vertex_gemini")
        result = merge_catalog_with_clones(
            catalog_page=_empty_catalog(), clones=[clone],
        )
        assert result.voices[0].provider == "vertex_gemini_clone"

    def test_language_filter_prefix_match(self):
        """`en` matches `en-US` clones."""
        en_clone = _make_clone(clone_id="vc_en", language="en-US")
        yo_clone = _make_clone(clone_id="vc_yo", language="yo-NG")
        result = merge_catalog_with_clones(
            catalog_page=_empty_catalog(),
            clones=[en_clone, yo_clone],
            language_filter="en",
        )
        assert len(result.voices) == 1
        assert result.voices[0].voice_id == "vc_en"

    def test_language_filter_excludes_non_matching(self):
        en_clone = _make_clone(clone_id="vc_en", language="en-US")
        result = merge_catalog_with_clones(
            catalog_page=_empty_catalog(),
            clones=[en_clone],
            language_filter="yo",
        )
        assert result.voices == []

    def test_total_count_increments_with_clones(self):
        catalog = _catalog_with(
            VoiceCatalogEntry(
                voice_id="en-US-Chirp3-HD-Kore",
                provider="vertex_gemini",
                display_name="Kore",
                language="en-US",
                gender=VoiceGender.neutral,
            ),
        )
        result = merge_catalog_with_clones(
            catalog_page=catalog,
            clones=[_make_clone()],
        )
        assert result.total_count == 2
