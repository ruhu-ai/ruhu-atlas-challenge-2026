"""Per-agent widget admin routes — extracted from api.py (RP-3.1 step 8b).

Two builders mirror the original (non-contiguous) layout inside
``create_app()`` — the persona/voice block sat between them, so each mounts
at the exact position its inline block occupied, under the same guard
(``if auth_enabled and effective_tenant_identity_repositories is not None
and effective_identity_store is not None:``):

- ``build_widget_admin_router`` — POST /agents/{agent_id}/widget/enable and
  /widget/disable (mounted right after the api-keys router).
- ``build_widget_config_router`` — GET/PATCH /agents/{agent_id}/widget-config
  and GET /agents/{agent_id}/embed-code (mounted after the persona router).

Hazard H6: ``_widget_config`` (the public widget-config projection shared
with turn enrichment) does NOT move here — it stays in ``create_app()``
until the conversation-turns service lands. Likewise the embed-code snippet
builder ``_build_widget_embed_code`` stays in ``create_app()`` and is
threaded into BOTH builders as the explicit ``build_widget_embed_code``
kwarg.

The widget DTOs (WidgetEnableResponse, WidgetConfig*) still live in
``ruhu.api``, so this module is imported by ``create_app()`` AT THE MOUNT
SITE rather than at api.py's module top (hazard H7: DTO imports stay at this
module's top for PEP 563). No ``tags=`` / ``prefix=`` and unchanged handler
names (hazard H1).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

# DTOs at module top (hazard H7: PEP 563 annotations resolve against this
# module's globals).
from ..api import (
    EmbedCodeResponse,
    WidgetConfigFields,
    WidgetConfigReadResponse,
    WidgetConfigUpdateRequest,
    WidgetEnableResponse,
)
from ..api_auth import RequestAuthContext
from ..policy import require_organization_role


def build_widget_admin_router(
    *,
    agent_registry,
    runtime_session_factory,
    auth_session_factory,
    settings,
    build_widget_embed_code: Callable[..., str],
) -> APIRouter:
    """Build the widget enable/disable router."""
    router = APIRouter()

    @router.post("/agents/{agent_id}/widget/enable", response_model=WidgetEnableResponse, status_code=200)
    def enable_agent_widget(
        agent_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> WidgetEnableResponse:
        from ..db_models import AgentRecord as _WAgentRecord, ApiKeyRecord as _WApiKeyRecord
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        org_id = principal.organization.organization_id
        # Validate agent exists and belongs to this org
        try:
            reg = agent_registry.get_agent_registration(agent_id, organization_id=org_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent")
        if reg.current_published_version_id is None:
            raise HTTPException(status_code=400, detail="agent must have a published version before enabling widget")
        # Enable the widget flag
        _now = datetime.now(timezone.utc)
        with runtime_session_factory.begin() as _ws:
            _rec = _ws.get(_WAgentRecord, agent_id)
            if _rec is None:
                raise HTTPException(status_code=404, detail="unknown agent")
            _rec.is_widget_enabled = True
            _rec.updated_at = _now
        # Check for existing active publishable key
        from sqlalchemy import select as sa_select
        pk_plain: str | None = None
        pk_prefix: str | None = None
        with auth_session_factory() as _ks:
            existing_pk = _ks.scalars(
                sa_select(_WApiKeyRecord).where(
                    _WApiKeyRecord.organization_id == org_id,
                    _WApiKeyRecord.agent_id == agent_id,
                    _WApiKeyRecord.key_type == "publishable",
                    _WApiKeyRecord.is_active.is_(True),
                ).limit(1)
            ).first()
            if existing_pk is not None:
                pk_prefix = existing_pk.key_prefix
        # Auto-create a publishable key if none exists
        if existing_pk is None:
            import secrets as _pk_secrets
            env_prefix = "pk_live_" if (settings.environment or "").lower() != "test" else "pk_test_"
            pk_plain = env_prefix + _pk_secrets.token_urlsafe(32)
            pk_prefix = pk_plain[:16]
            pk_hash = hashlib.sha256(pk_plain.encode()).hexdigest()
            with auth_session_factory.begin() as _ks2:
                _ks2.add(_WApiKeyRecord(
                    key_id=str(uuid4()),
                    organization_id=org_id,
                    name=f"Widget key for {reg.name}",
                    key_hash=pk_hash,
                    key_prefix=pk_prefix,
                    key_type="publishable",
                    agent_id=agent_id,
                    allowed_origins=[],
                    is_active=True,
                    created_at=_now,
                ))
        embed_code = build_widget_embed_code(pk_prefix or "pk_live_...", agent_id)
        return WidgetEnableResponse(
            agent_id=agent_id,
            is_widget_enabled=True,
            widget_mode=reg.widget_mode,
            embed_code=embed_code,
            widget_url="/widget/widget.js",
            publishable_key=pk_plain,
            publishable_key_prefix=pk_prefix,
            message="Widget enabled" + (". Publishable key created — save it now, it will not be shown again." if pk_plain else "."),
        )

    @router.post("/agents/{agent_id}/widget/disable", response_model=WidgetEnableResponse, status_code=200)
    def disable_agent_widget(
        agent_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> WidgetEnableResponse:
        from ..db_models import AgentRecord as _WAgentRecord
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        org_id = principal.organization.organization_id
        try:
            agent_registry.get_agent_registration(agent_id, organization_id=org_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent")
        with runtime_session_factory.begin() as _ws:
            _rec = _ws.get(_WAgentRecord, agent_id)
            if _rec is None:
                raise HTTPException(status_code=404, detail="unknown agent")
            _rec.is_widget_enabled = False
            _rec.updated_at = datetime.now(timezone.utc)
        return WidgetEnableResponse(
            agent_id=agent_id,
            is_widget_enabled=False,
            message="Widget disabled.",
        )

    return router


def build_widget_config_router(
    *,
    agent_registry,
    runtime_session_factory,
    auth_session_factory,
    build_widget_embed_code: Callable[..., str],
) -> APIRouter:
    """Build the per-agent widget-config + embed-code router."""
    router = APIRouter()

    @router.get("/agents/{agent_id}/widget-config", response_model=WidgetConfigReadResponse)
    def get_agent_widget_config(
        agent_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> WidgetConfigReadResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        org_id = principal.organization.organization_id
        try:
            reg = agent_registry.get_agent_registration(agent_id, organization_id=org_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent")
        return WidgetConfigReadResponse(
            agent_id=agent_id,
            is_widget_enabled=reg.is_widget_enabled,
            widget_mode=reg.widget_mode,
            widget_config=dict(reg.widget_config or {}),
        )

    @router.patch("/agents/{agent_id}/widget-config", response_model=WidgetConfigReadResponse)
    def update_agent_widget_config(
        agent_id: str,
        payload: WidgetConfigUpdateRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> WidgetConfigReadResponse:
        from ..db_models import AgentRecord as _WAgentRecord
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        org_id = principal.organization.organization_id
        try:
            agent_registry.get_agent_registration(agent_id, organization_id=org_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent")
        # Validate widget_config blob through the strict schema
        if payload.widget_config is not None:
            from pydantic import ValidationError as _PydanticValidationError
            try:
                WidgetConfigFields.model_validate(payload.widget_config)
            except _PydanticValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors())
        with runtime_session_factory.begin() as _ws:
            _rec = _ws.get(_WAgentRecord, agent_id)
            if _rec is None:
                raise HTTPException(status_code=404, detail="unknown agent")
            if payload.widget_mode is not None:
                _rec.widget_mode = payload.widget_mode
            if payload.widget_config is not None:
                merged = dict(_rec.widget_config or {})
                for k, v in payload.widget_config.items():
                    if v is not None:
                        merged[k] = v
                _rec.widget_config = merged
            _rec.updated_at = datetime.now(timezone.utc)
            _final_mode = _rec.widget_mode
            _final_config = dict(_rec.widget_config or {})
            _final_enabled = _rec.is_widget_enabled
        return WidgetConfigReadResponse(
            agent_id=agent_id,
            is_widget_enabled=_final_enabled,
            widget_mode=_final_mode,
            widget_config=_final_config,
        )

    @router.get("/agents/{agent_id}/embed-code", response_model=EmbedCodeResponse)
    def get_agent_embed_code(
        agent_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> EmbedCodeResponse:
        from ..db_models import ApiKeyRecord as _WApiKeyRecord
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        org_id = principal.organization.organization_id
        try:
            reg = agent_registry.get_agent_registration(agent_id, organization_id=org_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent")
        if not reg.is_widget_enabled:
            raise HTTPException(status_code=400, detail="widget is not enabled for this agent")
        from sqlalchemy import select as sa_select
        with auth_session_factory() as _ks:
            pk_record = _ks.scalars(
                sa_select(_WApiKeyRecord).where(
                    _WApiKeyRecord.organization_id == org_id,
                    _WApiKeyRecord.agent_id == agent_id,
                    _WApiKeyRecord.key_type == "publishable",
                    _WApiKeyRecord.is_active.is_(True),
                ).order_by(_WApiKeyRecord.created_at.desc()).limit(1)
            ).first()
        pk_prefix = pk_record.key_prefix if pk_record is not None else "YOUR_PUBLISHABLE_KEY"
        embed_code = build_widget_embed_code(pk_prefix, agent_id)
        return EmbedCodeResponse(
            agent_id=agent_id,
            embed_code=embed_code,
            widget_url="/widget/widget.js",
            publishable_key_prefix=pk_prefix,
            message="Copy this snippet into your website's HTML.",
        )

    return router
