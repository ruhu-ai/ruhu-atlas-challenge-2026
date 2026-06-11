"""Wire webhook dispatcher into event bus.

This module connects the webhook registry with the event bus,
so registered webhooks receive events.
"""

import logging
from sqlalchemy.ext.asyncio import AsyncSession

from ruhu.db_sqlmodel import DomainEvent
from ruhu.event_sourcing.webhook_config import get_webhook_registry
from ruhu.event_sourcing.webhooks import WebhookDispatcher

logger = logging.getLogger(__name__)


async def webhook_dispatch_handler(session: AsyncSession, event: DomainEvent) -> None:
    """Event handler that dispatches to registered webhooks.

    Called for every event published to the bus.
    Checks if any webhooks are registered for this event type,
    and dispatches the event to them.
    """
    registry = get_webhook_registry()
    webhooks = registry.get_webhooks(event.event_type)

    if not webhooks:
        return  # No webhooks registered for this event type

    # Create a dispatcher and send to each webhook
    dispatcher = WebhookDispatcher(session)

    for webhook_config in webhooks:
        # Register the webhook with the dispatcher temporarily
        dispatcher.register_webhook(
            event_type=webhook_config.event_type,
            url=str(webhook_config.url),
            headers=webhook_config.headers,
            max_retries=webhook_config.max_retries,
        )

    # Dispatch to all registered webhooks
    await dispatcher.dispatch(event)


def wire_webhook_dispatcher(event_bus) -> None:
    """Register webhook dispatcher as a global event handler.

    Call during app startup to wire webhooks into the event bus.
    """
    event_bus.subscribe_all(webhook_dispatch_handler)
    logger.info("Webhook dispatcher wired into event bus")
