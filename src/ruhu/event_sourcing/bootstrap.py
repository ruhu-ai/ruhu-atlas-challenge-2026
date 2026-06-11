"""Event sourcing bootstrap: Register handlers and initialize infrastructure.

Called on app startup to wire up all event handlers to the event bus.
"""

from ruhu.event_sourcing.event_bus import EventBus
from ruhu.projections.kpi_event_handlers import process_kpi_event
from ruhu.projections.intent_tags_event_handlers import process_intent_tags_event
from ruhu.projections.attachments_event_handlers import process_attachments_event
from ruhu.event_sourcing.webhook_dispatch import wire_webhook_dispatcher


def bootstrap_event_handlers(event_bus: EventBus) -> None:
    """Register all event handlers to the event bus.

    Called during app initialization to set up event sourcing.

    Args:
        event_bus: The event bus to register handlers to
    """
    # KPI events
    event_bus.subscribe("GoalDefinitionCreated", process_kpi_event)
    event_bus.subscribe("GoalDefinitionUpdated", process_kpi_event)
    event_bus.subscribe("GoalObservationRecorded", process_kpi_event)

    # Intent Tags events
    event_bus.subscribe("TaxonomyVersionCreated", process_intent_tags_event)
    event_bus.subscribe("IntentDefinitionCreated", process_intent_tags_event)
    event_bus.subscribe("IntentDefinitionUpdated", process_intent_tags_event)

    # Attachments events
    event_bus.subscribe("AttachmentUploaded", process_attachments_event)
    event_bus.subscribe("AttachmentProcessingCompleted", process_attachments_event)

    # Wire webhook dispatcher for external integrations
    wire_webhook_dispatcher(event_bus)
