"""Persona voice library/cloning + avatar routes — extracted from api.py (RP-3.1 step 8b).

Covers the voice catalog (/persona/voices/library, /persona/voices/{voice_id}/
preview), voice cloning (/persona/voices/clone, /persona/voices/clones/
{clone_id}) and the persona avatar pair (/agents/{agent_id}/persona/avatar —
upload is admin-gated, GET is deliberately unauthenticated for the customer
widget). Mounted under the same guard as the organization router (``if
auth_enabled and effective_tenant_identity_repositories is not None and
effective_identity_store is not None:``), between the widget enable/disable
and widget-config routers — the exact position the inline block occupied
(hazard H2: /persona/voices/library and /persona/voices/clone register
before /persona/voices/{voice_id}/preview keeps working because "library"
and "clone" are static segments).

The lazily-constructed voice provider and clone store move here with the
block — they were handler-local caches, not composition state. The voice
DTOs still live in ``ruhu.api``, so this module is imported by
``create_app()`` AT THE MOUNT SITE rather than at api.py's module top
(hazard H7: DTO imports stay at this module's top for PEP 563). No
``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

import logging
from typing import Callable

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse

# DTOs at module top (hazard H7: PEP 563 annotations resolve against this
# module's globals).
from ..api import (
    VoiceCatalogEntryResponse,
    VoiceCatalogPageResponse,
    VoiceCloneCreatedResponse,
)
from ..api_auth import RequestAuthContext
from ..policy import require_organization_role

logger = logging.getLogger(__name__)


def build_persona_router(
    *,
    agent_registry,
    runtime_session_factory,
    provider_cost_store,
    emit_semantic_audit_event: Callable[..., None],
) -> APIRouter:
    """Build the persona voices + avatar router."""
    router = APIRouter()

    # ── Voice library (Phase 2a-base) ──────────────────────────────
    # Lazy-construct the voice provider once on first request — the
    # Vertex provider is stateless after init so this is safe to
    # share across handlers.
    _voice_provider_cache: dict[str, object] = {}

    def _get_voice_provider():
        if "provider" not in _voice_provider_cache:
            from ..voice import build_voice_provider_from_env
            _voice_provider_cache["provider"] = build_voice_provider_from_env()
        return _voice_provider_cache["provider"]

    # ── Voice cloning (Phase 2a-cloning) ───────────────────────────
    _voice_clone_store_cache: dict[str, object] = {}

    def _get_voice_clone_store():
        """Voice clone store, lazy-constructed. Reuses the existing
        credential cipher (FernetCipher) that the OAuth subsystem
        already requires — no new key material to manage."""
        if "store" in _voice_clone_store_cache:
            return _voice_clone_store_cache["store"]
        if runtime_session_factory is None:
            _voice_clone_store_cache["store"] = None
            return None
        try:
            from ..tools.cipher import FernetCipher
            cipher = FernetCipher.from_env()
        except Exception:
            # Same fallback shape as build_default_app for environments
            # without an explicit cipher key (dev / tests). The store
            # still functions; a fresh dev key just means clones can't
            # be decrypted across restarts in dev mode, which is fine.
            from cryptography.fernet import Fernet as _DevFernet
            from ..tools.cipher import FernetCipher
            cipher = FernetCipher(primary=_DevFernet.generate_key().decode())
        from ..voice_cloning import VoiceCloneStore
        _voice_clone_store_cache["store"] = VoiceCloneStore(
            runtime_session_factory,
            cipher=cipher,
        )
        return _voice_clone_store_cache["store"]

    @router.get("/persona/voices/library", response_model=VoiceCatalogPageResponse)
    def get_voice_library(
        response: Response,
        language: str | None = Query(default=None, max_length=16),
        gender: str | None = Query(default=None, max_length=16),
        accent: str | None = Query(default=None, max_length=64),
        cursor: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=50, ge=1, le=200),
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> VoiceCatalogPageResponse:
        """List voices from the configured provider, filterable by
        language/gender/accent. Auth-scoped (analyst+) and cached for
        5 minutes — the catalog changes when we deploy new providers,
        not per-tenant, so a short edge cache is safe."""
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            from ..voice import VoiceGender as _VoiceGender
            gender_enum = _VoiceGender(gender) if gender else None
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"invalid gender filter: {gender!r} (must be male/female/neutral)",
            )
        provider = _get_voice_provider()
        page = provider.list_voices(
            language=language,
            gender=gender_enum,
            accent=accent,
            cursor=cursor,
            limit=limit,
        )
        # Phase 2a-cloning: splice in tenant clones BEFORE serialising,
        # so they appear at the top of the response and inherit the
        # same filter semantics. Catalog edge-cache stays at 5 min;
        # clones change rarely enough that a stale read for a minute
        # is acceptable, and the wizard invalidates the picker query
        # locally on success.
        store = _get_voice_clone_store()
        if store is not None:
            from ..voice_cloning import merge_catalog_with_clones
            clones = store.list_active(
                organization_id=principal.organization.organization_id,
            )
            page = merge_catalog_with_clones(
                catalog_page=page,
                clones=clones,
                language_filter=language,
            )

        response.headers["Cache-Control"] = "private, max-age=300"
        return VoiceCatalogPageResponse(
            voices=[
                VoiceCatalogEntryResponse(
                    voice_id=entry.voice_id,
                    provider=entry.provider,
                    display_name=entry.display_name,
                    language=entry.language,
                    gender=entry.gender.value,
                    accent=entry.accent,
                    description=entry.description,
                    sample_text=entry.sample_text,
                )
                for entry in page.voices
            ],
            next_cursor=page.next_cursor,
            total_count=page.total_count,
        )

    @router.get("/persona/voices/{voice_id}/preview")
    def get_voice_preview(
        voice_id: str,
        response: Response,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> Response:
        """Synthesize a short preview clip of a voice. The preview
        audio is cacheable for 24h via Cache-Control because catalog
        voices are stable and the script is deterministic."""
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        provider = _get_voice_provider()
        entry = provider.get_voice(voice_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown voice_id")
        # Provider may return a redirect URL (CDN sample). Vertex
        # currently returns None; fall through to synthesis.
        redirect = provider.preview_url(voice_id)
        if redirect is not None:
            return RedirectResponse(redirect, status_code=302)
        sample_text = entry.sample_text or "Hi, this is a voice preview from Ruhu."
        try:
            result = provider.synthesize(sample_text, voice_id=voice_id)
        except RuntimeError as exc:
            # Provider raised — typically because google-cloud-texttospeech
            # isn't installed or ADC isn't configured. Surface a 503 so
            # the picker UI can show a clear "previews unavailable" state
            # rather than a generic 500.
            raise HTTPException(status_code=503, detail=str(exc))
        # Best-effort cost record — if provider_cost_store is wired,
        # we land a row. Failures here MUST NOT break the preview.
        try:
            if provider_cost_store is not None and result.estimated_cost_usd > 0:
                from ..provider_costs import ProviderCostRecord
                provider_cost_store.save(
                    ProviderCostRecord(
                        organization_id=principal.organization.organization_id,
                        provider=entry.provider,
                        cost_type="tts_preview",
                        amount_usd=result.estimated_cost_usd,
                        reference_key=voice_id,
                        metadata={
                            "character_count": str(result.character_count),
                            **result.provider_metadata,
                        },
                    )
                )
        except Exception:  # pragma: no cover - cost recording is best-effort
            logger.exception("voice_preview.cost_record_failed")
        response.headers["Cache-Control"] = "private, max-age=86400"
        return Response(
            content=result.audio_bytes,
            media_type=result.audio_mime_type,
        )

    # ── Voice cloning endpoints (Phase 2a-cloning) ─────────────────

    # Hard size cap on consent audio uploads. 10 seconds * 48kHz *
    # 16-bit mono ≈ 960KB; 1MB leaves headroom for slightly higher
    # bitrates without permitting upload-bomb DoS. Defence in depth —
    # operators should ALSO configure their reverse proxy to reject
    # >1MB request bodies on this route.
    _CLONE_AUDIO_MAX_BYTES = 1_000_000
    _CLONE_AUDIO_ALLOWED_MIMES: frozenset[str] = frozenset(
        {"audio/wav", "audio/x-wav", "audio/mp3", "audio/mpeg", "audio/webm"}
    )

    async def _read_audio_with_cap(
        upload: UploadFile,
        *,
        field_name: str,
    ) -> tuple[bytes, str]:
        """Read an upload with a hard size cap + MIME allowlist.

        Reads up to ``_CLONE_AUDIO_MAX_BYTES + 1`` bytes; if more
        are available we reject without buffering the whole payload.
        Returns ``(bytes, mime)`` — the MIME is normalised and
        validated against the allowlist."""
        mime = (upload.content_type or "").strip().lower()
        if mime not in _CLONE_AUDIO_ALLOWED_MIMES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{field_name}: unsupported audio MIME {mime!r}; "
                    f"use one of {sorted(_CLONE_AUDIO_ALLOWED_MIMES)}"
                ),
            )
        payload = await upload.read(_CLONE_AUDIO_MAX_BYTES + 1)
        if len(payload) > _CLONE_AUDIO_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{field_name}: exceeds {_CLONE_AUDIO_MAX_BYTES} bytes "
                    "(max ~10 seconds at 48kHz). Re-record at lower "
                    "quality or shorter duration."
                ),
            )
        if not payload:
            raise HTTPException(
                status_code=422,
                detail=f"{field_name}: empty upload",
            )
        return payload, mime

    @router.post(
        "/persona/voices/clone",
        response_model=VoiceCloneCreatedResponse,
        status_code=201,
    )
    async def clone_voice(
        request: Request,
        display_name: str = Form(min_length=1, max_length=64),
        language: str = Form(min_length=2, max_length=16),
        agent_id: str | None = Form(default=None, max_length=255),
        consent_audio: UploadFile = File(...),
        reference_audio: UploadFile | None = File(default=None),
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> VoiceCloneCreatedResponse:
        """Clone a custom voice via Chirp 3 HD Instant Custom Voice.

        Admin role required because cloning is a high-impact action
        (creates a tenant-scoped credential the agent will speak
        with). The wizard UI captures Google's mandated consent
        statement; the bytes are forwarded as-is to the provider
        and then retained server-side for the seven-year compliance
        window.

        Production posture (each backed by tests):
        - 1MB hard cap on audio uploads (defence-in-depth — proxy
          should also enforce).
        - MIME allowlist (audio/wav, audio/mp3, audio/webm).
        - ``agent_id`` ownership verified against the caller's org
          (cross-tenant agent_id spoofing rejected with 404).
        - Audit emission on success ('resource.created') and on
          consent rejection ('security.suspicious') so abuse
          attempts are visible.
        - Plaintext cloning key never logged.
        """
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        provider = _get_voice_provider()
        if not getattr(provider, "supports_cloning", False):
            raise HTTPException(
                status_code=501,
                detail="configured voice provider does not support cloning",
            )
        store = _get_voice_clone_store()
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="voice clone store is not available in this environment",
            )

        org_id = principal.organization.organization_id

        # Verify agent_id ownership BEFORE we read megabytes of audio.
        # Spoofing an agent_id from another organization is the only
        # cross-tenant escape hatch on this endpoint; closing it
        # before any expensive work prevents both data leakage AND
        # consumes the smallest possible amount of capacity on a
        # malicious request.
        if agent_id is not None:
            try:
                agent_registry.get_agent_registration(
                    agent_id, organization_id=org_id,
                )
            except KeyError:
                raise HTTPException(
                    status_code=404,
                    detail=f"unknown agent_id {agent_id!r} in this organization",
                )

        # Read + validate audio payloads with hard caps.
        consent_bytes, consent_mime = await _read_audio_with_cap(
            consent_audio, field_name="consent_audio",
        )
        reference_bytes: bytes | None = None
        reference_mime: str | None = None
        if reference_audio is not None:
            reference_bytes, reference_mime = await _read_audio_with_cap(
                reference_audio, field_name="reference_audio",
            )

        try:
            from ..voice import VoiceCloningConsentError
            clone_result = provider.clone_voice(
                consent_audio=consent_bytes,
                consent_audio_mime=consent_mime,
                reference_audio=reference_bytes,
                reference_audio_mime=reference_mime,
                display_name=display_name,
                language=language,
                organization_id=org_id,
                actor_id=principal.user.user_id,
            )
        except VoiceCloningConsentError as exc:
            # Audit consent rejections — repeated rejections by the
            # same actor is a signal of abuse (someone trying to
            # clone a voice without genuine consent). Audit captures
            # the actor, not the audio bytes.
            emit_semantic_audit_event(
                request=request,
                event_type="security.suspicious",
                organization_id=org_id,
                actor_id=principal.user.user_id,
                actor_session_id=None,
                resource_type="voice_clone",
                resource_id=None,
                detail={
                    "reason": "voice_clone_consent_rejected",
                    "language": language,
                },
            )
            raise HTTPException(status_code=422, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        try:
            clone = store.create(
                organization_id=org_id,
                agent_id=agent_id,
                provider=clone_result.provider_metadata.get(
                    "provider_key", provider.name,
                ),
                display_name=clone_result.display_name,
                language=clone_result.language,
                voice_cloning_key=clone_result.voice_cloning_key,
                consent_audio=consent_bytes,
                consent_audio_mime=consent_mime,
                created_by=principal.user.user_id,
            )
        except Exception:
            # Don't let the unencrypted cloning key leak into the
            # exception message — log without it. The provider has
            # already issued the key; persistence failure means the
            # author needs operator help to recover (they can't
            # retry — Google may not let them re-clone the same
            # voice without rate limiting).
            logger.exception(
                "voice_clone.persist_failed",
                extra={"organization_id": org_id, "actor_id": principal.user.user_id},
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "voice cloning succeeded at provider but persistence "
                    "failed; contact support with this request id"
                ),
            )

        # Audit successful clone create.
        emit_semantic_audit_event(
            request=request,
            event_type="resource.created",
            organization_id=org_id,
            actor_id=principal.user.user_id,
            actor_session_id=None,
            resource_type="voice_clone",
            resource_id=clone.clone_id,
            detail={
                "display_name": clone.display_name,
                "language": clone.language,
                "agent_id": agent_id,
            },
        )

        # Best-effort cost record for the clone-creation fee.
        try:
            if (
                provider_cost_store is not None
                and clone_result.estimated_cost_usd > 0
            ):
                from ..provider_costs import ProviderCostRecord
                provider_cost_store.save(
                    ProviderCostRecord(
                        organization_id=org_id,
                        provider=f"{provider.name}_clone",
                        cost_type="voice_clone_create",
                        amount_usd=clone_result.estimated_cost_usd,
                        reference_key=clone.clone_id,
                        metadata={"language": clone.language},
                    )
                )
        except Exception:  # pragma: no cover - cost recording is best-effort
            logger.exception("voice_clone.cost_record_failed")

        return VoiceCloneCreatedResponse(
            clone_id=clone.clone_id,
            provider=clone.provider,
            display_name=clone.display_name,
            language=clone.language,
            created_at=clone.created_at,
            estimated_cost_usd=clone_result.estimated_cost_usd,
        )

    @router.delete(
        "/persona/voices/clones/{clone_id}",
        status_code=204,
    )
    def delete_voice_clone(
        clone_id: str,
        request: Request,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> Response:
        """Soft-delete a tenant clone. Idempotent; returns 204
        whether or not the row existed (matching how the OAuth
        connection delete works elsewhere in the API). The clone's
        consent audio + encrypted key remain in the DB for the
        seven-year retention window.

        Audit emission only fires when an actual row was deleted —
        spurious 204s on already-deleted clones don't pollute the
        audit log."""
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        store = _get_voice_clone_store()
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="voice clone store is not available in this environment",
            )
        org_id = principal.organization.organization_id
        deleted = store.soft_delete(clone_id, organization_id=org_id)
        if deleted:
            emit_semantic_audit_event(
                request=request,
                event_type="resource.deleted",
                organization_id=org_id,
                actor_id=principal.user.user_id,
                actor_session_id=None,
                resource_type="voice_clone",
                resource_id=clone_id,
                detail={"soft_delete": True},
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ── Persona avatar upload (Phase 2d) ───────────────────────────

    @router.post(
        "/agents/{agent_id}/persona/avatar",
        status_code=200,
    )
    async def upload_persona_avatar(
        agent_id: str,
        request: Request,
        file: UploadFile = File(...),
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> dict:
        """Upload a persona avatar image. Replaces any existing
        avatar in-place (cosmetic, not compliance-relevant — the
        audit trail is what's preserved).

        Production posture (each backed by tests):

        * Format allowlist (jpeg/png/webp). SVG is REJECTED at the
          MIME check — would be an XSS vector when served from the
          customer widget.
        * 2MB hard cap on bytes (defence-in-depth — proxy should
          also enforce).
        * Dimension validation: square (within 5%), 256x256 to
          1024x1024.
        * EXIF strip + re-encode — the persisted bytes are NOT the
          user-supplied bytes. GPS / camera metadata is dropped.
        * Polyglot rejection — Pillow decodes the actual content
          and a magic-bytes-vs-MIME mismatch returns 422.
        * Agent_id ownership verified before reading bytes.
        * Audit emission on every successful upload.
        """
        from ..persona_avatar import (
            AvatarValidationError,
            MAX_AVATAR_BYTES,
            process_avatar_upload,
        )

        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        org_id = principal.organization.organization_id

        # Verify agent ownership BEFORE reading bytes — same
        # pattern as the voice-clone endpoint.
        try:
            agent_registry.get_agent_registration(
                agent_id, organization_id=org_id,
            )
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown agent_id {agent_id!r} in this organization",
            )

        # Bounded read — read up to MAX+1, reject if anything
        # bigger arrived.
        raw_bytes = await file.read(MAX_AVATAR_BYTES + 1)
        if len(raw_bytes) > MAX_AVATAR_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"avatar exceeds {MAX_AVATAR_BYTES} bytes "
                    "(2MB max). Use a smaller image."
                ),
            )

        declared_mime = (file.content_type or "").strip().lower()
        try:
            processed = process_avatar_upload(
                raw_bytes=raw_bytes,
                declared_mime=declared_mime,
            )
        except AvatarValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        # Persist. Replacement is in-place because avatars are
        # cosmetic; the audit event captures the change. Use the
        # runtime session factory (tenant-scoped). No async
        # cleanup needed because there's no separate object
        # storage — the bytes live on the row itself.
        from datetime import datetime, timezone
        from ..db_models import PersonaAvatarBlobRecord
        now = datetime.now(timezone.utc)
        if runtime_session_factory is None:
            raise HTTPException(
                status_code=503,
                detail="persona avatar store is not available in this environment",
            )
        with runtime_session_factory() as session:
            record = session.get(PersonaAvatarBlobRecord, agent_id)
            if record is None:
                record = PersonaAvatarBlobRecord(
                    agent_id=agent_id,
                    organization_id=org_id,
                    created_at=now,
                    created_by=principal.user.user_id,
                    content_type=processed.mime,
                    width=processed.width,
                    height=processed.height,
                    data=processed.bytes,
                    updated_at=now,
                )
                session.add(record)
            else:
                if record.organization_id != org_id:
                    # Tenant-scoping defence — should not happen
                    # because RLS is enforced, but never hurts to
                    # check before clobbering.
                    raise HTTPException(status_code=404, detail="unknown agent")
                record.content_type = processed.mime
                record.width = processed.width
                record.height = processed.height
                record.data = processed.bytes
                record.updated_at = now
            session.commit()

        avatar_url = f"/agents/{agent_id}/persona/avatar"
        emit_semantic_audit_event(
            request=request,
            event_type="resource.updated",
            organization_id=org_id,
            actor_id=principal.user.user_id,
            actor_session_id=None,
            resource_type="persona_avatar",
            resource_id=agent_id,
            detail={
                "content_type": processed.mime,
                "width": processed.width,
                "height": processed.height,
                "bytes": len(processed.bytes),
            },
        )
        return {
            "agent_id": agent_id,
            "avatar_url": avatar_url,
            "content_type": processed.mime,
            "width": processed.width,
            "height": processed.height,
            "updated_at": now,
        }

    @router.get("/agents/{agent_id}/persona/avatar")
    def get_persona_avatar(
        agent_id: str,
        response: Response,
    ) -> Response:
        """Serve the persona avatar bytes. UNAUTHENTICATED on
        purpose — the customer widget is the primary consumer and
        it doesn't carry session credentials. Caching: 60 seconds
        so a persona-edit propagates within a minute (matching the
        existing public widget config Cache-Control)."""
        from ..db_models import PersonaAvatarBlobRecord
        if runtime_session_factory is None:
            raise HTTPException(status_code=503, detail="not available")
        with runtime_session_factory() as session:
            record = session.get(PersonaAvatarBlobRecord, agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no avatar")
        response.headers["Cache-Control"] = "public, max-age=60"
        return Response(
            content=record.data,
            media_type=record.content_type,
        )

    return router
