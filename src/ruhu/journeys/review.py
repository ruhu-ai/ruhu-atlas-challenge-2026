from __future__ import annotations

from collections.abc import Iterable, Sequence

from ruhu.agent_document import AgentDocument

from .models import (
    JourneyDefinition,
    JourneyDefinitionReview,
    JourneyDefinitionVersion,
    JourneyPublishReadiness,
    JourneyReviewItem,
)
from .rules import validate_definition_version


def build_definition_review(
    definition: JourneyDefinition,
    version: JourneyDefinitionVersion,
    *,
    scoped_agent_documents: Sequence[AgentDocument] | None = None,
    missing_agent_ids: Sequence[str] | None = None,
    available_tool_refs: Iterable[str] | None = None,
) -> JourneyDefinitionReview:
    issues = validate_definition_version(
        definition,
        version,
        scoped_agent_documents=scoped_agent_documents,
        missing_agent_ids=missing_agent_ids,
        available_tool_refs=available_tool_refs,
    )
    blockers = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    return JourneyDefinitionReview(
        definition_id=definition.definition_id,
        definition_version_id=version.definition_version_id,
        can_publish=not blockers,
        blockers=blockers,
        warnings=warnings,
    )


def build_review_summary(
    definition: JourneyDefinition,
    version: JourneyDefinitionVersion,
    *,
    scoped_agent_documents: Sequence[AgentDocument] | None = None,
    missing_agent_ids: Sequence[str] | None = None,
    available_tool_refs: Iterable[str] | None = None,
) -> dict[str, object]:
    return build_definition_review(
        definition,
        version,
        scoped_agent_documents=scoped_agent_documents,
        missing_agent_ids=missing_agent_ids,
        available_tool_refs=available_tool_refs,
    ).model_dump(mode="json")


def build_publish_readiness(
    definition: JourneyDefinition,
    *,
    draft_version: JourneyDefinitionVersion | None,
    published_version: JourneyDefinitionVersion | None,
    scoped_agent_documents: Sequence[AgentDocument] | None = None,
    missing_agent_ids: Sequence[str] | None = None,
    available_tool_refs: Iterable[str] | None = None,
) -> JourneyPublishReadiness:
    if draft_version is None:
        blocker = JourneyReviewItem(
            severity="error",
            code="journey.definition.no_draft",
            message="Journey definition has no draft version to publish.",
        )
        warnings: list[JourneyReviewItem] = []
        if published_version is None:
            warnings.append(
                JourneyReviewItem(
                    severity="warning",
                    code="journey.definition.first_publish_pending",
                    message="Journey definition has never been published.",
                )
            )
        return JourneyPublishReadiness(
            definition_id=definition.definition_id,
            draft_version_id=None,
            published_version_id=None if published_version is None else published_version.definition_version_id,
            can_publish=False,
            blockers=[blocker],
            warnings=warnings,
            draft_review=None,
        )

    review = build_definition_review(
        definition,
        draft_version,
        scoped_agent_documents=scoped_agent_documents,
        missing_agent_ids=missing_agent_ids,
        available_tool_refs=available_tool_refs,
    )
    warnings = list(review.warnings)
    if published_version is None:
        warnings.append(
            JourneyReviewItem(
                severity="warning",
                code="journey.definition.first_publish_pending",
                message="Journey definition has never been published.",
            )
        )
    return JourneyPublishReadiness(
        definition_id=definition.definition_id,
        draft_version_id=draft_version.definition_version_id,
        published_version_id=None if published_version is None else published_version.definition_version_id,
        can_publish=review.can_publish,
        blockers=list(review.blockers),
        warnings=warnings,
        draft_review=review,
    )
