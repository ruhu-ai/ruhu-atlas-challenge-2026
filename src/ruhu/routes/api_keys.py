"""API-key management routes — extracted from api.py (RP-3.1 step 8a).

Secret org-scoped API keys (/api-keys) plus browser-embeddable publishable
keys (/api-keys/publishable and /api-keys/{key_id}/allowed-origins). Mounted
under the same guard as the organization router — ``if auth_enabled and
effective_tenant_identity_repositories is not None and
effective_identity_store is not None:`` — immediately after it, the exact
position the inline block occupied (hazard H2: /api-keys/publishable
registers before /api-keys/{key_id}/allowed-origins).

The DTOs (ApiKeyPublicResponse, PublishableKey*) still live in ``ruhu.api``,
so this module is imported by ``create_app()`` AT THE MOUNT SITE rather than
at api.py's module top (hazard H7: DTO imports stay at this module's top for
PEP 563). No ``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError

# DTOs at module top (hazard H7: PEP 563 annotations resolve against this
# module's globals).
from ..api import (
    ApiKeyPublicResponse,
    CreateApiKeyRequest,
    CreatePublishableKeyRequest,
    PublishableKeyCreatedResponse,
    PublishableKeyPublicResponse,
    UpdateAllowedOriginsRequest,
)
from ..api_auth import RequestAuthContext
from ..policy import require_organization_role


def build_api_keys_router(
    *,
    auth_session_factory,
    agent_registry,
) -> APIRouter:
    """Build the /api-keys router (secret + publishable keys)."""
    router = APIRouter()

    def _build_api_key_response(record: object) -> ApiKeyPublicResponse:
        return ApiKeyPublicResponse(
            key_id=record.key_id,  # type: ignore[attr-defined]
            name=record.name,  # type: ignore[attr-defined]
            key_prefix=record.key_prefix,  # type: ignore[attr-defined]
            is_active=record.is_active,  # type: ignore[attr-defined]
            created_at=record.created_at,  # type: ignore[attr-defined]
            last_used_at=record.last_used_at,  # type: ignore[attr-defined]
        )

    @router.get("/api-keys", response_model=list[ApiKeyPublicResponse])
    def list_api_keys(
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> list[ApiKeyPublicResponse]:
        from ..db_models import ApiKeyRecord
        from sqlalchemy import select as sa_select
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        with auth_session_factory() as session:
            records = session.scalars(
                sa_select(ApiKeyRecord)
                .where(ApiKeyRecord.organization_id == principal.organization.organization_id)
                .order_by(ApiKeyRecord.created_at.desc())
            ).all()
        return [_build_api_key_response(r) for r in records]

    @router.post("/api-keys", response_model=ApiKeyPublicResponse, status_code=201)
    def create_api_key(
        payload: CreateApiKeyRequest = Body(...),
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> ApiKeyPublicResponse:
        from ..db_models import ApiKeyRecord
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        key_id = str(uuid4())
        now = datetime.now(timezone.utc)
        record = ApiKeyRecord(
            key_id=key_id,
            organization_id=principal.organization.organization_id,
            name=payload.name,
            key_hash=payload.key_hash,
            key_prefix=payload.key_prefix,
            is_active=True,
            created_at=now,
        )
        try:
            with auth_session_factory.begin() as session:
                session.add(record)
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="api key already exists") from exc
        return ApiKeyPublicResponse(
            key_id=key_id,
            name=payload.name,
            key_prefix=payload.key_prefix,
            is_active=True,
            created_at=now,
        )

    @router.delete("/api-keys/{key_id}", status_code=204)
    def revoke_api_key(
        key_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> Response:
        from ..db_models import ApiKeyRecord
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        with auth_session_factory.begin() as session:
            record = session.get(ApiKeyRecord, key_id)
            if record is None or record.organization_id != principal.organization.organization_id:
                raise HTTPException(status_code=404, detail="unknown api key")
            record.is_active = False
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ── Publishable API keys ───────────────────────────────────────────
    # Browser-embeddable keys bound to a specific agent. Unlike secret
    # keys they carry an allowed_origins list for CORS-style validation.
    # Schema classes (PublishableKeyPublicResponse, etc.) are defined at module level.

    def _build_publishable_key_response(record: object) -> PublishableKeyPublicResponse:
        origins = record.allowed_origins  # type: ignore[attr-defined]
        return PublishableKeyPublicResponse(
            key_id=record.key_id,  # type: ignore[attr-defined]
            name=record.name,  # type: ignore[attr-defined]
            key_prefix=record.key_prefix,  # type: ignore[attr-defined]
            key_type=record.key_type,  # type: ignore[attr-defined]
            agent_id=record.agent_id,  # type: ignore[attr-defined]
            allowed_origins=list(origins) if isinstance(origins, list) else [],
            environment=record.environment,  # type: ignore[attr-defined]
            is_active=record.is_active,  # type: ignore[attr-defined]
            created_at=record.created_at,  # type: ignore[attr-defined]
            last_used_at=record.last_used_at,  # type: ignore[attr-defined]
        )

    @router.get("/api-keys/publishable", response_model=list[PublishableKeyPublicResponse])
    def list_publishable_keys(
        agent_id: str | None = Query(default=None),
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> list[PublishableKeyPublicResponse]:
        from ..db_models import ApiKeyRecord
        from sqlalchemy import select as sa_select
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        with auth_session_factory() as session:
            q = (
                sa_select(ApiKeyRecord)
                .where(
                    ApiKeyRecord.organization_id == principal.organization.organization_id,
                    ApiKeyRecord.key_type == "publishable",
                )
                .order_by(ApiKeyRecord.created_at.desc())
            )
            if agent_id:
                q = q.where(ApiKeyRecord.agent_id == agent_id)
            records = session.scalars(q).all()
        return [_build_publishable_key_response(r) for r in records]

    @router.post("/api-keys/publishable", response_model=PublishableKeyCreatedResponse, status_code=201)
    def create_publishable_key(
        payload: CreatePublishableKeyRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PublishableKeyCreatedResponse:
        from ..db_models import ApiKeyRecord
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        # Verify the agent belongs to this organisation.
        try:
            agent_registry.get_agent_registration(payload.agent_id, organization_id=principal.organization.organization_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent id")
        env_prefix = "test" if payload.environment == "test" else "live"
        plaintext = f"pk_{env_prefix}_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        key_prefix = plaintext[:16]
        key_id = str(uuid4())
        now = datetime.now(timezone.utc)
        record = ApiKeyRecord(
            key_id=key_id,
            organization_id=principal.organization.organization_id,
            name=payload.name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            is_active=True,
            created_at=now,
            key_type="publishable",
            agent_id=payload.agent_id,
            allowed_origins=payload.allowed_origins,
            environment=payload.environment,
        )
        with auth_session_factory.begin() as session:
            session.add(record)
        resp = _build_publishable_key_response(record)
        return PublishableKeyCreatedResponse(**resp.model_dump(), key=plaintext)

    @router.get("/api-keys/publishable/{key_id}", response_model=PublishableKeyPublicResponse)
    def get_publishable_key(
        key_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PublishableKeyPublicResponse:
        from ..db_models import ApiKeyRecord
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        with auth_session_factory() as session:
            record = session.get(ApiKeyRecord, key_id)
            if (
                record is None
                or record.organization_id != principal.organization.organization_id
                or record.key_type != "publishable"
            ):
                raise HTTPException(status_code=404, detail="unknown publishable key")
        return _build_publishable_key_response(record)

    @router.put("/api-keys/{key_id}/allowed-origins", response_model=PublishableKeyPublicResponse)
    def update_allowed_origins(
        key_id: str,
        payload: UpdateAllowedOriginsRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PublishableKeyPublicResponse:
        from ..db_models import ApiKeyRecord
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        with auth_session_factory.begin() as session:
            record = session.get(ApiKeyRecord, key_id)
            if record is None or record.organization_id != principal.organization.organization_id:
                raise HTTPException(status_code=404, detail="unknown api key")
            record.allowed_origins = payload.allowed_origins
        return _build_publishable_key_response(record)

    return router
