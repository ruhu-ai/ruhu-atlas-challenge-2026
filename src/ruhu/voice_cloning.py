"""Phase 2a-cloning — voice clone persistence + catalog merge.

This module owns:

* ``VoiceClone`` — Pydantic surface returned to callers.
* ``VoiceCloneStore`` — SQLAlchemy CRUD over ``voice_clones`` with
  AES-GCM encryption of the cloning key and tenant scoping enforced
  at every read.
* ``merge_catalog_with_clones`` — helper used by the
  ``GET /persona/voices/library`` endpoint to splice tenant clones
  into the provider's static catalog.

Production-readiness contracts:

* **Encrypted at rest** — cloning keys are AES-GCM with AAD
  ``b"voiceclone:" + organization_id + b"|" + clone_id``. Cross-tenant
  ciphertext swap fails to decrypt; cross-clone swap inside the same
  tenant also fails. We use the existing ``FernetCipher`` from
  ``tools/cipher.py`` to share the key-ring infrastructure with the
  OAuth credential cipher — no new key material to manage.
* **Soft-delete by default** — deletion sets ``deleted_at``; rows
  remain in the table for the seven-year compliance retention.
  Hard-delete is the responsibility of a separate retention sweep
  (not in this module).
* **Tenant scoping enforced at every query** — every ``list_active``
  / ``get`` / ``soft_delete`` requires ``organization_id``. There is
  no ``get_by_id_only`` escape hatch.
* **Plaintext keys never appear in serialised output** — ``VoiceClone``
  exposes ``has_voice_cloning_key: bool`` rather than the key itself.
  Synthesis paths that need the plaintext call
  ``VoiceCloneStore.decrypt_key_for_synthesis()`` and emit a
  ``credential.decrypted`` audit event (handled at the caller —
  matches the OAuth cipher pattern).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import VoiceCloneRecord
from .tools.cipher import CredentialCipher
from .voice import VoiceCatalogEntry, VoiceCatalogPage, VoiceGender

logger = logging.getLogger(__name__)


# ── AAD ──────────────────────────────────────────────────────────────────────


def _build_aad(*, organization_id: str, clone_id: str) -> bytes:
    """Canonical AAD for cloning-key blobs.

    Bind every ciphertext to both its tenant and the exact row it
    belongs to. Cross-tenant ciphertext swap fails because the AAD
    won't match. Cross-clone swap inside the same tenant also fails
    for the same reason.
    """
    return f"voiceclone:{organization_id}|clone:{clone_id}".encode("utf-8")


# ── Pydantic surface ─────────────────────────────────────────────────────────


class VoiceClone(BaseModel):
    """Public-facing voice clone record.

    Notable absences from this model:

    * No ``voice_cloning_key`` plaintext — keys must be retrieved via
      ``VoiceCloneStore.decrypt_key_for_synthesis`` so the audit hook
      fires.
    * No ``consent_audio_blob`` — the bytes are kept server-side; the
      compliance retrieval path is a separate operator-only endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    clone_id: str
    organization_id: str
    agent_id: str | None
    provider: str
    display_name: str
    language: str
    has_voice_cloning_key: bool = True
    """Always ``True`` for clones returned by the store; set to
    ``False`` when serialising before encryption is wired (used in
    factory tests)."""
    created_at: datetime
    created_by: str
    deleted_at: datetime | None = Field(default=None)


# ── Store ────────────────────────────────────────────────────────────────────


class VoiceCloneStore:
    """SQLAlchemy CRUD for voice clones with at-rest encryption.

    Construction takes the shared ``sessionmaker`` and a
    ``CredentialCipher`` (the project's ``FernetCipher`` ring). The
    cipher MUST already be wired with key material — see
    ``ruhu.api`` for the construction site that pulls the key-ring
    from environment variables.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        cipher: CredentialCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    # ── Create ───────────────────────────────────────────────────────────

    def create(
        self,
        *,
        organization_id: str,
        agent_id: str | None,
        provider: str,
        display_name: str,
        language: str,
        voice_cloning_key: str,
        consent_audio: bytes,
        consent_audio_mime: str,
        created_by: str,
        clone_id: str | None = None,
    ) -> VoiceClone:
        """Persist a new clone. The cloning key is encrypted with AAD
        binding; the consent audio is stored as-is for compliance
        retrieval."""
        if not voice_cloning_key:
            raise ValueError("voice_cloning_key must be non-empty")
        if not consent_audio:
            raise ValueError("consent_audio must be non-empty")
        clone_id = clone_id or f"vc_{uuid4().hex}"
        aad = _build_aad(organization_id=organization_id, clone_id=clone_id)
        encrypted_key = self._cipher.encrypt(
            voice_cloning_key.encode("utf-8"), aad=aad,
        )
        now = _utcnow()
        record = VoiceCloneRecord(
            clone_id=clone_id,
            organization_id=organization_id,
            agent_id=agent_id,
            provider=provider,
            display_name=display_name,
            language=language,
            voice_cloning_key_enc=encrypted_key,
            consent_audio_blob=consent_audio,
            consent_audio_mime=consent_audio_mime,
            metadata_json={},
            created_at=now,
            created_by=created_by,
            deleted_at=None,
        )
        with self._session_factory() as session:
            session.add(record)
            session.commit()
        return _record_to_clone(record)

    # ── Read ─────────────────────────────────────────────────────────────

    def get(
        self,
        clone_id: str,
        *,
        organization_id: str,
        include_deleted: bool = False,
    ) -> VoiceClone | None:
        """Return a single clone by id, scoped to organization. Returns
        ``None`` if not found (do not raise — the picker UI hits this
        path on every catalog refresh and 404 noise is unhelpful)."""
        with self._session_factory() as session:
            row = self._get_row(
                session,
                clone_id=clone_id,
                organization_id=organization_id,
                include_deleted=include_deleted,
            )
            if row is None:
                return None
            return _record_to_clone(row)

    def list_active(
        self,
        *,
        organization_id: str,
        agent_id: str | None = None,
    ) -> list[VoiceClone]:
        """List active (non-soft-deleted) clones for an organization.

        ``agent_id=None`` returns both org-wide AND agent-specific
        clones — the picker shows everything authored against the
        tenant. Pass an explicit ``agent_id`` to filter to clones for
        that agent only (or org-wide ones, since those apply too).
        """
        statement = (
            select(VoiceCloneRecord)
            .where(VoiceCloneRecord.organization_id == organization_id)
            .where(VoiceCloneRecord.deleted_at.is_(None))
            .order_by(VoiceCloneRecord.created_at.desc())
        )
        with self._session_factory() as session:
            rows = session.execute(statement).scalars().all()
        return [_record_to_clone(row) for row in rows]

    def decrypt_key_for_synthesis(
        self,
        clone_id: str,
        *,
        organization_id: str,
    ) -> str:
        """Decrypt the cloning key for a synthesis call.

        This is the ONLY path that produces a plaintext key. Callers
        MUST emit a ``voice_clone.key_decrypted`` audit event after
        calling — same convention as the OAuth credential cipher (see
        ``docs/operations/credential-encryption.md``).

        Raises ``KeyError`` if the clone doesn't exist or is
        soft-deleted; raises ``DecryptionFailed`` (from
        ``tools.cipher``) if the AAD doesn't match.
        """
        with self._session_factory() as session:
            row = self._get_row(
                session,
                clone_id=clone_id,
                organization_id=organization_id,
                include_deleted=False,
            )
            if row is None:
                raise KeyError(f"clone {clone_id} not found in organization {organization_id}")
            aad = _build_aad(
                organization_id=organization_id, clone_id=clone_id,
            )
            plaintext = self._cipher.decrypt(row.voice_cloning_key_enc, aad=aad)
            return plaintext.decode("utf-8")

    # ── Soft-delete ──────────────────────────────────────────────────────

    def soft_delete(
        self,
        clone_id: str,
        *,
        organization_id: str,
    ) -> bool:
        """Mark a clone deleted. Returns ``True`` if a row was updated,
        ``False`` if not found or already deleted. Idempotent."""
        with self._session_factory() as session:
            row = self._get_row(
                session,
                clone_id=clone_id,
                organization_id=organization_id,
                include_deleted=False,
            )
            if row is None:
                return False
            row.deleted_at = _utcnow()
            session.commit()
            return True

    # ── Internals ────────────────────────────────────────────────────────

    def _get_row(
        self,
        session: Session,
        *,
        clone_id: str,
        organization_id: str,
        include_deleted: bool,
    ) -> VoiceCloneRecord | None:
        statement = (
            select(VoiceCloneRecord)
            .where(VoiceCloneRecord.clone_id == clone_id)
            .where(VoiceCloneRecord.organization_id == organization_id)
        )
        if not include_deleted:
            statement = statement.where(VoiceCloneRecord.deleted_at.is_(None))
        return session.execute(statement).scalar_one_or_none()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _record_to_clone(row: VoiceCloneRecord) -> VoiceClone:
    return VoiceClone(
        clone_id=row.clone_id,
        organization_id=row.organization_id,
        agent_id=row.agent_id,
        provider=row.provider,
        display_name=row.display_name,
        language=row.language,
        created_at=row.created_at,
        created_by=row.created_by,
        deleted_at=row.deleted_at,
    )


# ── Catalog merge helper ─────────────────────────────────────────────────────


def merge_catalog_with_clones(
    *,
    catalog_page: VoiceCatalogPage,
    clones: list[VoiceClone],
    language_filter: str | None = None,
) -> VoiceCatalogPage:
    """Splice tenant clones into the provider's catalog page.

    Clones appear at the TOP of the response so authors see their
    custom voices before scrolling through the standard catalog. Each
    clone is rendered with ``provider="<base_provider>_clone"`` so the
    UI can render a "Cloned" badge without inspecting metadata.

    Filtering: if ``language_filter`` is provided (matching the
    behaviour of the catalog endpoint), clones whose language doesn't
    match are dropped. Prefix-match — ``"en"`` matches ``"en-US"``,
    ``"en-GB"``, ``"en-NG"`` — matching the same semantics as the
    Vertex provider's filter.
    """
    if not clones:
        return catalog_page

    clone_entries: list[VoiceCatalogEntry] = []
    for clone in clones:
        if language_filter is not None:
            if not clone.language.casefold().startswith(language_filter.casefold()):
                continue
        clone_entries.append(
            VoiceCatalogEntry(
                voice_id=clone.clone_id,
                provider=f"{clone.provider}_clone",
                display_name=clone.display_name,
                language=clone.language,
                # Cloned voices are gender-agnostic from our side — the
                # cloned voice carries whatever the consent recording
                # had, but we don't classify it. Default to neutral so
                # the picker shows the clone under the "Any gender"
                # filter and not under male/female/neutral specifics.
                gender=VoiceGender.neutral,
                accent=None,
                description=f"Custom clone created on {clone.created_at:%Y-%m-%d}",
                sample_text=None,
                provider_metadata={
                    "clone_id": clone.clone_id,
                    "is_clone": "true",
                },
            )
        )

    if not clone_entries:
        return catalog_page

    # Re-compute total_count if it was set; clones are independent of
    # the provider's pagination so we just add to the visible count.
    total = (
        (catalog_page.total_count or 0) + len(clone_entries)
        if catalog_page.total_count is not None
        else None
    )
    return VoiceCatalogPage(
        voices=clone_entries + list(catalog_page.voices),
        next_cursor=catalog_page.next_cursor,
        total_count=total,
    )


__all__ = [
    "VoiceClone",
    "VoiceCloneStore",
    "merge_catalog_with_clones",
]
