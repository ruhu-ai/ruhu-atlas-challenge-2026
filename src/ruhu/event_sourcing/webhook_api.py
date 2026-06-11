"""Webhook management API.

Endpoints:
- POST   /webhooks — Register a webhook
- GET    /webhooks — List all webhooks
- DELETE /webhooks/{webhook_id} — Unregister a webhook
- PATCH  /webhooks/{webhook_id}/activate — Activate webhook
- PATCH  /webhooks/{webhook_id}/deactivate — Deactivate webhook
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl
from typing import Optional, Dict, List

from ruhu.event_sourcing.webhook_config import (
    get_webhook_registry,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class RegisterWebhookRequest(BaseModel):
    """Request to register a webhook."""
    event_type: str
    url: HttpUrl
    headers: Optional[Dict[str, str]] = None
    max_retries: int = 3


class WebhookResponse(BaseModel):
    """Webhook response."""
    webhook_id: str
    event_type: str
    url: str
    headers: Dict[str, str]
    active: bool
    max_retries: int


@router.post("", response_model=WebhookResponse, status_code=201)
async def register_webhook(request: RegisterWebhookRequest) -> WebhookResponse:
    """Register a new webhook for an event type.

    The webhook will receive POST requests when matching events occur.
    Payload includes event_id, event_type, aggregate_id, payload, timestamp, etc.
    """
    registry = get_webhook_registry()

    webhook_id = registry.register(
        event_type=request.event_type,
        url=str(request.url),
        headers=request.headers,
        max_retries=request.max_retries,
    )

    config = registry.all_webhooks[webhook_id]
    return WebhookResponse(
        webhook_id=webhook_id,
        event_type=config.event_type,
        url=str(config.url),
        headers=config.headers,
        active=config.active,
        max_retries=config.max_retries,
    )


@router.get("", response_model=List[WebhookResponse])
async def list_webhooks() -> List[WebhookResponse]:
    """List all registered webhooks."""
    registry = get_webhook_registry()
    return [
        WebhookResponse(
            webhook_id=config.webhook_id,
            event_type=config.event_type,
            url=str(config.url),
            headers=config.headers,
            active=config.active,
            max_retries=config.max_retries,
        )
        for config in registry.list_all()
    ]


@router.delete("/{webhook_id}")
async def unregister_webhook(webhook_id: str) -> dict:
    """Unregister a webhook."""
    registry = get_webhook_registry()

    if not registry.unregister(webhook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")

    return {"status": "deleted", "webhook_id": webhook_id}


@router.patch("/{webhook_id}/activate")
async def activate_webhook(webhook_id: str) -> dict:
    """Activate a webhook."""
    registry = get_webhook_registry()

    if not registry.activate(webhook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")

    return {"status": "activated", "webhook_id": webhook_id}


@router.patch("/{webhook_id}/deactivate")
async def deactivate_webhook(webhook_id: str) -> dict:
    """Deactivate a webhook."""
    registry = get_webhook_registry()

    if not registry.deactivate(webhook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")

    return {"status": "deactivated", "webhook_id": webhook_id}


def install_webhook_api(app) -> None:
    """Install webhook management endpoints into FastAPI app."""
    app.include_router(router, prefix="/internal")
