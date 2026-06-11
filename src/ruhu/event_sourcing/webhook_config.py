"""Webhook configuration and management.

Allows registering webhooks for specific event types.
Webhooks are dispatched via the WebhookDispatcher on event publish.

Example:
    webhook_config.register(
        event_type="GoalDefinitionCreated",
        url="https://analytics.example.com/events",
        headers={"Authorization": "Bearer token"},
    )
"""

from typing import Optional, Dict, List
from pydantic import BaseModel, Field, HttpUrl
import logging

logger = logging.getLogger(__name__)


class WebhookConfig(BaseModel):
    """Webhook registration configuration."""
    webhook_id: str = Field(description="Unique webhook ID")
    event_type: str = Field(description="Event type to subscribe to (e.g., 'GoalDefinitionCreated')")
    url: HttpUrl = Field(description="Webhook endpoint URL")
    headers: Dict[str, str] = Field(default_factory=dict, description="HTTP headers to send")
    active: bool = Field(default=True, description="Is this webhook active?")
    max_retries: int = Field(default=3, description="Max retries on failure")


class WebhookRegistry:
    """Registry for managing webhooks."""

    def __init__(self):
        self.webhooks: Dict[str, List[WebhookConfig]] = {}
        self.all_webhooks: Dict[str, WebhookConfig] = {}

    def register(
        self,
        event_type: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        webhook_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> str:
        """Register a webhook for an event type.

        Args:
            event_type: Event type to subscribe to
            url: Webhook endpoint URL
            headers: Optional HTTP headers
            webhook_id: Optional custom webhook ID
            max_retries: Max retries on failure

        Returns:
            Webhook ID
        """
        from uuid import uuid4
        webhook_id = webhook_id or str(uuid4())

        config = WebhookConfig(
            webhook_id=webhook_id,
            event_type=event_type,
            url=url,  # type: ignore
            headers=headers or {},
            max_retries=max_retries,
        )

        if event_type not in self.webhooks:
            self.webhooks[event_type] = []

        self.webhooks[event_type].append(config)
        self.all_webhooks[webhook_id] = config

        logger.info(f"Webhook registered: {webhook_id} for {event_type} → {url}")
        return webhook_id

    def unregister(self, webhook_id: str) -> bool:
        """Unregister a webhook.

        Returns:
            True if webhook was found and removed
        """
        if webhook_id not in self.all_webhooks:
            return False

        config = self.all_webhooks.pop(webhook_id)
        if config.event_type in self.webhooks:
            self.webhooks[config.event_type] = [
                w for w in self.webhooks[config.event_type]
                if w.webhook_id != webhook_id
            ]

        logger.info(f"Webhook unregistered: {webhook_id}")
        return True

    def get_webhooks(self, event_type: str) -> List[WebhookConfig]:
        """Get active webhooks for an event type."""
        return [
            w for w in self.webhooks.get(event_type, [])
            if w.active
        ]

    def list_all(self) -> List[WebhookConfig]:
        """List all registered webhooks."""
        return list(self.all_webhooks.values())

    def activate(self, webhook_id: str) -> bool:
        """Activate a webhook."""
        if webhook_id in self.all_webhooks:
            self.all_webhooks[webhook_id].active = True
            return True
        return False

    def deactivate(self, webhook_id: str) -> bool:
        """Deactivate a webhook."""
        if webhook_id in self.all_webhooks:
            self.all_webhooks[webhook_id].active = False
            return True
        return False


# Global registry instance
_registry: Optional[WebhookRegistry] = None


def get_webhook_registry() -> WebhookRegistry:
    """Get or create global webhook registry."""
    global _registry
    if _registry is None:
        _registry = WebhookRegistry()
    return _registry
