"""Webhook dispatcher for domain events.

Sends domain events to registered webhooks (external systems, analytics, etc.).
Implements retry logic and logging for reliability.

Example usage:
  dispatcher = WebhookDispatcher(store)
  dispatcher.register_webhook(
    "GoalDefinitionCreated",
    "https://analytics.example.com/events",
    headers={"Authorization": "Bearer token"}
  )
"""

import asyncio
import logging
from typing import Optional
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ruhu.db_sqlmodel import DomainEvent


logger = logging.getLogger(__name__)


class Webhook:
    """Represents a registered webhook endpoint."""

    def __init__(
        self,
        webhook_id: str,
        event_type: str,
        url: str,
        headers: Optional[dict[str, str]] = None,
        max_retries: int = 3,
        timeout_seconds: int = 10,
    ):
        self.webhook_id = webhook_id
        self.event_type = event_type
        self.url = url
        self.headers = headers or {}
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.active = True


class WebhookDispatcher:
    """Dispatches domain events to registered webhooks."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self._webhooks: dict[str, list[Webhook]] = {}
        self._retry_backoff_seconds = [1, 2, 5]  # Exponential backoff

    def register_webhook(
        self,
        event_type: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        max_retries: int = 3,
    ) -> str:
        """Register a webhook for an event type.

        Args:
            event_type: Event class name to subscribe to
            url: Webhook endpoint URL
            headers: Optional HTTP headers to include
            max_retries: Number of retries on failure

        Returns:
            Webhook ID for later reference
        """
        if event_type not in self._webhooks:
            self._webhooks[event_type] = []

        webhook_id = str(uuid4())
        webhook = Webhook(
            webhook_id=webhook_id,
            event_type=event_type,
            url=url,
            headers=headers,
            max_retries=max_retries,
        )

        self._webhooks[event_type].append(webhook)
        logger.info(f"Registered webhook {webhook_id} for {event_type} → {url}")

        return webhook_id

    async def dispatch(self, event: DomainEvent) -> None:
        """Dispatch an event to all registered webhooks for that event type.

        Args:
            event: DomainEvent to dispatch
        """
        webhooks = self._webhooks.get(event.event_type, [])

        for webhook in webhooks:
            if not webhook.active:
                continue

            # Fire and forget (with retries)
            asyncio.create_task(self._send_with_retry(webhook, event))

    async def _send_with_retry(
        self,
        webhook: Webhook,
        event: DomainEvent,
    ) -> None:
        """Send webhook with exponential backoff retry.

        Args:
            webhook: Webhook to send to
            event: Event to send
        """
        payload = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "payload": event.payload,
            "timestamp": event.timestamp.isoformat(),
            "organization_id": event.organization_id,
        }

        for attempt in range(webhook.max_retries):
            try:
                async with httpx.AsyncClient(timeout=webhook.timeout_seconds) as client:
                    response = await client.post(
                        webhook.url,
                        json=payload,
                        headers=webhook.headers,
                    )
                    response.raise_for_status()

                logger.info(f"Webhook {webhook.webhook_id} succeeded ({response.status_code})")
                return

            except httpx.TimeoutException:
                logger.warning(
                    f"Webhook {webhook.webhook_id} timeout (attempt {attempt + 1}/{webhook.max_retries})"
                )
            except httpx.HTTPError as e:
                logger.warning(
                    f"Webhook {webhook.webhook_id} failed (attempt {attempt + 1}/{webhook.max_retries}): {e}"
                )
            except Exception:
                logger.exception(
                    f"Webhook {webhook.webhook_id} error (attempt {attempt + 1}/{webhook.max_retries})",
                    exc_info=True,
                )

            # Backoff before retry
            if attempt < webhook.max_retries - 1:
                backoff = self._retry_backoff_seconds[min(attempt, len(self._retry_backoff_seconds) - 1)]
                await asyncio.sleep(backoff)

        # All retries exhausted
        logger.error(f"Webhook {webhook.webhook_id} failed after {webhook.max_retries} retries")
