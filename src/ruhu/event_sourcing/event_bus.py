"""Event Bus: Dispatches domain events to handlers (projections, webhooks, etc.).

The event bus receives events and distributes them to interested subscribers.
Subscribers (handlers) react to events and update read models (projections).

Pattern:
  1. Append event to event store
  2. Publish event to bus
  3. Handlers process event and update projections
  4. Commit both event and projections
"""

import logging
from typing import Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ruhu.db_sqlmodel import DomainEvent


logger = logging.getLogger(__name__)

EventHandler = Callable[[AsyncSession, DomainEvent], None]


class EventBus:
    """In-memory event bus for synchronous event dispatching."""

    def __init__(self):
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._global_subscribers: list[EventHandler] = []

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe a handler to events of a specific type.

        Args:
            event_type: Event class name (e.g., 'GoalDefinitionCreated')
            handler: Async callable(session, event)
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe a handler to all events."""
        self._global_subscribers.append(handler)

    async def publish(self, session: AsyncSession, event: DomainEvent) -> None:
        """Publish an event to all subscribers.

        Subscribers are called synchronously (blocking).
        In production, consider async message queue (RabbitMQ, Kafka) for decoupling.

        Args:
            session: AsyncSession for handlers to use
            event: DomainEvent to publish
        """
        handlers = self._global_subscribers + self._subscribers.get(event.event_type, [])

        for handler in handlers:
            try:
                await handler(session, event)
            except Exception:
                logger.exception(f"Event handler failed for {event.event_type}", exc_info=True)
                # In production: dead-letter queue, alerting, retry logic
                raise


class InMemoryEventBus(EventBus):
    """Simple in-memory event bus. Suitable for single-process deployments.

    For distributed systems, use a message broker (RabbitMQ, Kafka, etc.).
    """

    pass


# Singleton instance (would be dependency-injected in production)
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = InMemoryEventBus()
    return _event_bus
