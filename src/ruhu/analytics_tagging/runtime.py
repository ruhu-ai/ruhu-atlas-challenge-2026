from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from .read_models import IntentTagsReadService
from .service import (
    ClassifierProfileService,
    ConversationSummaryService,
    DeterministicTaggingService,
    ReviewQueueService,
    TaxonomyService,
    TurnClassificationService,
)
from .store import SQLAlchemyIntentTagsStore
from .webhooks import SemanticSummaryWebhookService


@dataclass(slots=True)
class IntentTagsRuntime:
    store: SQLAlchemyIntentTagsStore
    taxonomy_service: TaxonomyService
    profile_service: ClassifierProfileService
    turn_service: TurnClassificationService
    summary_service: ConversationSummaryService
    tagging_service: DeterministicTaggingService
    review_service: ReviewQueueService
    read_service: IntentTagsReadService
    webhook_service: SemanticSummaryWebhookService | None = None


def build_intent_tags_runtime(
    *,
    session_factory: sessionmaker[Session],
    default_adapter_name: str = "ruhu-general",
) -> IntentTagsRuntime:
    store = SQLAlchemyIntentTagsStore(session_factory)
    taxonomy_service = TaxonomyService(store)
    profile_service = ClassifierProfileService(store, taxonomy_service, default_adapter_name=default_adapter_name)
    webhook_service = SemanticSummaryWebhookService(store)
    turn_service = TurnClassificationService(store)
    summary_service = ConversationSummaryService(store)
    tagging_service = DeterministicTaggingService(store, taxonomy_service)
    review_service = ReviewQueueService(store)
    read_service = IntentTagsReadService(
        store,
        taxonomy_service=taxonomy_service,
        profile_service=profile_service,
        review_service=review_service,
    )
    return IntentTagsRuntime(
        store=store,
        taxonomy_service=taxonomy_service,
        profile_service=profile_service,
        webhook_service=webhook_service,
        turn_service=turn_service,
        summary_service=summary_service,
        tagging_service=tagging_service,
        review_service=review_service,
        read_service=read_service,
    )
