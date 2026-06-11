from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    ClassificationReviewItem,
    ClassifierProfile,
    ConversationSemanticContext,
    ConversationSemanticSummary,
    EffectiveConversationSummary,
    EffectiveTurnClassification,
    IntentDefinition,
    TagAssignment,
    TagDefinition,
    TaxonomyVersion,
    TurnClassificationEvent,
)
from .service import ClassifierProfileService, ReviewQueueService, TaxonomyService
from .store import IntentTagsStore


class TaxonomySnapshotReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    taxonomy_versions: list[TaxonomyVersion] = Field(default_factory=list)
    intents: list[IntentDefinition] = Field(default_factory=list)
    tags: list[TagDefinition] = Field(default_factory=list)
    profiles: list[ClassifierProfile] = Field(default_factory=list)


class IntentAnalyticsRowReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    intent_name: str
    display_name: str
    summary_count: int = 0
    turn_event_count: int = 0
    corrected_turn_count: int = 0
    low_confidence_turn_count: int = 0
    review_count: int = 0
    human_followup_count: int = 0


class TagAnalyticsRowReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tag_definition_id: str
    tag_name: str
    display_name: str
    tag_kind: str
    assignment_count: int = 0
    validated_count: int = 0
    turn_assignment_count: int = 0
    conversation_assignment_count: int = 0
    assignment_source_counts: dict[str, int] = Field(default_factory=dict)


class SummaryOutcomeDistributionReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    channel: str
    outcome: str | None = None
    resolution_status: str | None = None
    count: int = 0


class SemanticInsightRowReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    insight_key: str
    blocker_kind: str
    title: str
    summary: str
    agent_id: str | None = None
    primary_intent_name: str | None = None
    tag_definition_id: str | None = None
    tag_name: str | None = None
    tag_kind: str | None = None
    resolution_status: str | None = None
    outcome: str | None = None
    requires_human_followup: bool = False
    occurrence_count: int = 0
    coverage_ratio: float = 0.0
    example_conversation_ids: list[str] = Field(default_factory=list)


class IntentTagsInsightsReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    totals: dict[str, int] = Field(default_factory=dict)
    rows: list[SemanticInsightRowReadModel] = Field(default_factory=list)


class ReviewQueueRowReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    review_item: ClassificationReviewItem
    conversation_id: str | None = None
    target_kind: str
    channel: str | None = None
    current_intent_name: str | None = None
    effective_intent_name: str | None = None
    summary_primary_intent_name: str | None = None
    resolution_status: str | None = None
    outcome: str | None = None


class SummaryListItemReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    summary: ConversationSemanticSummary
    effective_summary: ConversationSemanticSummary
    is_corrected: bool = False
    review_item: ClassificationReviewItem | None = None
    tag_assignments: list[TagAssignment] = Field(default_factory=list)
    tag_names: list[str] = Field(default_factory=list)


class TurnClassificationEvidenceReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event: TurnClassificationEvent
    effective_event: TurnClassificationEvent
    review_item: ClassificationReviewItem | None = None
    is_corrected: bool = False
    tag_assignments: list[TagAssignment] = Field(default_factory=list)


class ConversationSummaryDetailReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    conversation_context: ConversationSemanticContext | None = None
    effective_summary: EffectiveConversationSummary
    turn_evidence: list[TurnClassificationEvidenceReadModel] = Field(default_factory=list)


class IntentTagsAnalyticsReadModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    totals: dict[str, int] = Field(default_factory=dict)
    review_status_counts: dict[str, int] = Field(default_factory=dict)
    intent_rows: list[IntentAnalyticsRowReadModel] = Field(default_factory=list)
    tag_rows: list[TagAnalyticsRowReadModel] = Field(default_factory=list)
    outcome_rows: list[SummaryOutcomeDistributionReadModel] = Field(default_factory=list)
    insight_rows: list[SemanticInsightRowReadModel] = Field(default_factory=list)


def _latest_review_item(items: list[ClassificationReviewItem]) -> ClassificationReviewItem | None:
    if not items:
        return None
    ordered = sorted(items, key=lambda item: (item.updated_at, item.review_item_id), reverse=True)
    return ordered[0]


def _summary_rank(summary: ConversationSemanticSummary) -> tuple[int, Any, str]:
    status_priority = {
        "corrected": 4,
        "final": 3,
        "draft": 2,
        "superseded": 1,
    }
    return (
        status_priority.get(summary.status, 0),
        summary.updated_at,
        summary.conversation_summary_id,
    )


class IntentTagsReadService:
    def __init__(
        self,
        store: IntentTagsStore,
        *,
        taxonomy_service: TaxonomyService,
        profile_service: ClassifierProfileService,
        review_service: ReviewQueueService,
        low_confidence_threshold: float = 0.6,
    ) -> None:
        self.store = store
        self.taxonomy_service = taxonomy_service
        self.profile_service = profile_service
        self.review_service = review_service
        self.low_confidence_threshold = low_confidence_threshold

    def get_taxonomy_snapshot(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
    ) -> TaxonomySnapshotReadModel:
        versions = self.store.list_taxonomy_versions(organization_id)
        intents = self.taxonomy_service.list_effective_intents(
            organization_id,
            agent_id=agent_id,
            include_inactive=True,
        )
        tags = self.taxonomy_service.list_effective_tags(
            organization_id,
            agent_id=agent_id,
            include_inactive=True,
        )
        profiles = self.profile_service.list_profiles(organization_id, agent_id=agent_id)
        return TaxonomySnapshotReadModel(
            taxonomy_versions=versions,
            intents=intents,
            tags=tags,
            profiles=profiles,
        )

    def list_review_queue(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        status: str | None = None,
        review_kind: str | None = None,
        claimed_by_user_id: str | None = None,
        limit: int = 100,
    ) -> list[ReviewQueueRowReadModel]:
        rows: list[ReviewQueueRowReadModel] = []
        items = self.review_service.list_queue(
            organization_id,
            status=status,
            review_kind=review_kind,
            claimed_by_user_id=claimed_by_user_id,
            limit=limit,
        )
        for item in items:
            if item.classification_event_id is not None:
                effective = self.review_service.get_effective_turn_classification(item.classification_event_id)
                if agent_id is not None and effective.event.agent_id != agent_id:
                    continue
                rows.append(
                    ReviewQueueRowReadModel(
                        review_item=item,
                        conversation_id=effective.event.conversation_id,
                        target_kind="turn",
                        channel=effective.event.channel,
                        current_intent_name=effective.event.intent_name,
                        effective_intent_name=effective.effective_event.intent_name,
                    )
                )
                continue
            if item.conversation_summary_id is None:
                continue
            effective_summary = self.review_service.get_effective_summary(
                conversation_summary_id=item.conversation_summary_id
            )
            if agent_id is not None and effective_summary.summary.agent_id != agent_id:
                continue
            rows.append(
                ReviewQueueRowReadModel(
                    review_item=item,
                    conversation_id=effective_summary.summary.conversation_id,
                    target_kind="summary",
                    channel=effective_summary.effective_summary.channel,
                    current_intent_name=effective_summary.summary.primary_intent_name,
                    effective_intent_name=effective_summary.effective_summary.primary_intent_name,
                    summary_primary_intent_name=effective_summary.effective_summary.primary_intent_name,
                    resolution_status=effective_summary.effective_summary.resolution_status,
                    outcome=effective_summary.effective_summary.outcome,
                )
            )
        return rows

    def list_summaries(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SummaryListItemReadModel]:
        summaries = self.store.list_conversation_summaries(
            organization_id,
            conversation_id=conversation_id,
            status=status,
            limit=max(limit * 5, 200),
        )
        by_conversation: dict[str, list[ConversationSemanticSummary]] = defaultdict(list)
        for summary in summaries:
            if agent_id is not None and summary.agent_id != agent_id:
                continue
            by_conversation[summary.conversation_id].append(summary)

        items: list[SummaryListItemReadModel] = []
        for conversation_key in sorted(by_conversation):
            candidates = by_conversation[conversation_key]
            base_summary = max(candidates, key=_summary_rank)
            effective = self.review_service.get_effective_summary(
                conversation_summary_id=base_summary.conversation_summary_id
            )
            latest_review = effective.review_item
            tag_names = []
            for assignment in effective.tag_assignments:
                tag = self.store.get_tag_definition(assignment.tag_definition_id)
                tag_names.append(tag.display_name if tag is not None else assignment.tag_definition_id)
            items.append(
                SummaryListItemReadModel(
                    summary=effective.summary,
                    effective_summary=effective.effective_summary,
                    is_corrected=effective.is_corrected,
                    review_item=latest_review,
                    tag_assignments=effective.tag_assignments,
                    tag_names=sorted(tag_names),
                )
            )
        items.sort(key=lambda item: _summary_rank(item.effective_summary), reverse=True)
        return items[:limit]

    def get_summary_detail(
        self,
        organization_id: str,
        *,
        conversation_summary_id: str,
    ) -> ConversationSummaryDetailReadModel | None:
        base_summary = self.store.get_conversation_summary(conversation_summary_id)
        if base_summary is None or base_summary.organization_id != organization_id:
            return None
        effective_summary = self.review_service.get_effective_summary(
            conversation_summary_id=conversation_summary_id
        )
        context = self.store.get_conversation_context(base_summary.conversation_id)
        evidence_ids = list(
            effective_summary.effective_summary.evidence_payload.get("classification_event_ids")
            or base_summary.evidence_payload.get("classification_event_ids")
            or []
        )
        conversation_events = {
            item.classification_event_id: item
            for item in self.store.list_classification_events(
                organization_id,
                conversation_id=base_summary.conversation_id,
                limit=2000,
            )
        }
        turn_evidence: list[TurnClassificationEvidenceReadModel] = []
        ordered_event_ids = evidence_ids or list(conversation_events)
        for event_id in ordered_event_ids:
            event = conversation_events.get(event_id)
            if event is None:
                continue
            effective_turn = self.review_service.get_effective_turn_classification(event_id)
            tag_assignments = self.store.list_tag_assignments(
                organization_id,
                classification_event_id=event_id,
                assignment_scope="turn",
                limit=200,
            )
            turn_evidence.append(
                TurnClassificationEvidenceReadModel(
                    event=effective_turn.event,
                    effective_event=effective_turn.effective_event,
                    review_item=effective_turn.review_item,
                    is_corrected=effective_turn.is_corrected,
                    tag_assignments=tag_assignments,
                )
            )
        return ConversationSummaryDetailReadModel(
            conversation_context=context,
            effective_summary=effective_summary,
            turn_evidence=turn_evidence,
        )

    def analytics_snapshot(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 2500,
    ) -> IntentTagsAnalyticsReadModel:
        intents = self.taxonomy_service.list_effective_intents(
            organization_id,
            agent_id=agent_id,
            include_inactive=True,
        )
        tags = self.taxonomy_service.list_effective_tags(
            organization_id,
            agent_id=agent_id,
            include_inactive=True,
        )
        intent_names = {item.name: item.display_name for item in intents}
        tag_defs = {item.tag_definition_id: item for item in tags}
        events = [
            item
            for item in self.store.list_classification_events(organization_id, limit=limit)
            if agent_id is None or item.agent_id == agent_id
        ]
        review_items = self.store.list_review_items(organization_id, limit=limit)
        summaries = self.list_summaries(organization_id, agent_id=agent_id, limit=limit)
        assignments = [
            item
            for item in self.store.list_tag_assignments(organization_id, limit=limit)
            if agent_id is None or self._assignment_matches_agent(item, agent_id)
        ]

        intent_summary_counts: Counter[str] = Counter()
        human_followup_counts: Counter[str] = Counter()
        for item in summaries:
            effective = item.effective_summary
            if effective.primary_intent_name:
                intent_summary_counts[effective.primary_intent_name] += 1
                if effective.requires_human_followup:
                    human_followup_counts[effective.primary_intent_name] += 1

        turn_event_counts: Counter[str] = Counter()
        low_confidence_counts: Counter[str] = Counter()
        corrected_turn_counts: Counter[str] = Counter()
        review_counts: Counter[str] = Counter()
        event_by_id = {item.classification_event_id: item for item in events}

        for event in events:
            turn_event_counts[event.intent_name] += 1
            if event.confidence < self.low_confidence_threshold:
                low_confidence_counts[event.intent_name] += 1

        for review_item in review_items:
            if review_item.classification_event_id is not None:
                event = event_by_id.get(review_item.classification_event_id)
                if event is None:
                    continue
                review_counts[event.intent_name] += 1
                if review_item.review_disposition == "corrected":
                    corrected_turn_counts[event.intent_name] += 1
                continue
            if review_item.conversation_summary_id is None:
                continue
            summary = self.store.get_conversation_summary(review_item.conversation_summary_id)
            if summary is None or summary.primary_intent_name is None:
                continue
            if agent_id is not None and summary.agent_id != agent_id:
                continue
            review_counts[summary.primary_intent_name] += 1

        intent_rows: list[IntentAnalyticsRowReadModel] = []
        all_intent_names = sorted(
            {
                *intent_names.keys(),
                *intent_summary_counts.keys(),
                *turn_event_counts.keys(),
                *review_counts.keys(),
            }
        )
        for intent_name in all_intent_names:
            intent_rows.append(
                IntentAnalyticsRowReadModel(
                    intent_name=intent_name,
                    display_name=intent_names.get(intent_name, intent_name.replace("_", " ").title()),
                    summary_count=intent_summary_counts[intent_name],
                    turn_event_count=turn_event_counts[intent_name],
                    corrected_turn_count=corrected_turn_counts[intent_name],
                    low_confidence_turn_count=low_confidence_counts[intent_name],
                    review_count=review_counts[intent_name],
                    human_followup_count=human_followup_counts[intent_name],
                )
            )
        intent_rows.sort(
            key=lambda row: (
                -row.summary_count,
                -row.turn_event_count,
                row.display_name,
            )
        )

        assignment_source_counts: dict[str, Counter[str]] = defaultdict(Counter)
        assignment_counts: Counter[str] = Counter()
        validated_counts: Counter[str] = Counter()
        turn_assignment_counts: Counter[str] = Counter()
        conversation_assignment_counts: Counter[str] = Counter()
        for assignment in assignments:
            assignment_counts[assignment.tag_definition_id] += 1
            assignment_source_counts[assignment.tag_definition_id][assignment.assignment_source] += 1
            if assignment.is_validated:
                validated_counts[assignment.tag_definition_id] += 1
            if assignment.assignment_scope == "turn":
                turn_assignment_counts[assignment.tag_definition_id] += 1
            else:
                conversation_assignment_counts[assignment.tag_definition_id] += 1

        tag_rows: list[TagAnalyticsRowReadModel] = []
        for tag_definition_id, count in assignment_counts.items():
            tag = tag_defs.get(tag_definition_id)
            if tag is None:
                continue
            tag_rows.append(
                TagAnalyticsRowReadModel(
                    tag_definition_id=tag_definition_id,
                    tag_name=tag.name,
                    display_name=tag.display_name,
                    tag_kind=tag.tag_kind,
                    assignment_count=count,
                    validated_count=validated_counts[tag_definition_id],
                    turn_assignment_count=turn_assignment_counts[tag_definition_id],
                    conversation_assignment_count=conversation_assignment_counts[tag_definition_id],
                    assignment_source_counts=dict(assignment_source_counts[tag_definition_id]),
                )
            )
        tag_rows.sort(key=lambda row: (-row.assignment_count, row.display_name))

        outcome_counter: Counter[tuple[str, str | None, str | None]] = Counter()
        for item in summaries:
            effective = item.effective_summary
            outcome_counter[(effective.channel, effective.outcome, effective.resolution_status)] += 1
        outcome_rows = [
            SummaryOutcomeDistributionReadModel(
                channel=channel,
                outcome=outcome,
                resolution_status=resolution_status,
                count=count,
            )
            for (channel, outcome, resolution_status), count in sorted(
                outcome_counter.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1] or "", item[0][2] or ""),
            )
        ]

        review_status_counts: Counter[str] = Counter(item.status for item in review_items)
        totals = {
            "taxonomy_versions": len(self.store.list_taxonomy_versions(organization_id)),
            "intent_definitions": len(intents),
            "tag_definitions": len(tags),
            "classifier_profiles": len(self.profile_service.list_profiles(organization_id, agent_id=agent_id)),
            "turn_events": len(events),
            "conversation_summaries": len(summaries),
            "review_items": len(review_items),
            "tag_assignments": len(assignments),
        }
        return IntentTagsAnalyticsReadModel(
            totals=totals,
            review_status_counts=dict(review_status_counts),
            intent_rows=intent_rows,
            tag_rows=tag_rows,
            outcome_rows=outcome_rows,
            insight_rows=self.semantic_insights_snapshot(
                organization_id,
                agent_id=agent_id,
                limit=min(limit, 25),
            ).rows,
        )

    def semantic_insights_snapshot(
        self,
        organization_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> IntentTagsInsightsReadModel:
        summaries = self.list_summaries(organization_id, agent_id=agent_id, limit=max(limit * 10, 200))
        if not summaries:
            return IntentTagsInsightsReadModel(totals={"conversation_summaries": 0, "insight_rows": 0}, rows=[])

        tag_defs = {
            item.tag_definition_id: item
            for item in self.taxonomy_service.list_effective_tags(
                organization_id,
                agent_id=agent_id,
                include_inactive=True,
            )
        }
        blocker_buckets: dict[tuple[str | None, str, str], dict[str, Any]] = {}
        followup_buckets: dict[tuple[str | None, str, str | None, str | None, bool], dict[str, Any]] = {}

        for item in summaries:
            effective = item.effective_summary
            primary_intent_name = effective.primary_intent_name or "unknown"
            for assignment in item.tag_assignments:
                tag = tag_defs.get(assignment.tag_definition_id) or self.store.get_tag_definition(assignment.tag_definition_id)
                if tag is None or tag.tag_kind not in {"blocker", "failure_reason", "risk"}:
                    continue
                key = (effective.agent_id, primary_intent_name, tag.tag_definition_id)
                bucket = blocker_buckets.setdefault(
                    key,
                    {
                        "agent_id": effective.agent_id,
                        "primary_intent_name": primary_intent_name,
                        "tag_definition_id": tag.tag_definition_id,
                        "tag_name": tag.name,
                        "tag_kind": tag.tag_kind,
                        "display_name": tag.display_name,
                        "conversation_ids": set(),
                        "resolution_status": effective.resolution_status,
                        "outcome": effective.outcome,
                    },
                )
                conversation_ids = bucket["conversation_ids"]
                if isinstance(conversation_ids, set):
                    conversation_ids.add(effective.conversation_id)
            if effective.requires_human_followup or effective.resolution_status in {
                "follow_up_required",
                "escalated",
                "failed",
                "abandoned",
                "unresolved",
            }:
                key = (
                    effective.agent_id,
                    primary_intent_name,
                    effective.resolution_status,
                    effective.outcome,
                    effective.requires_human_followup,
                )
                bucket = followup_buckets.setdefault(
                    key,
                    {
                        "agent_id": effective.agent_id,
                        "primary_intent_name": primary_intent_name,
                        "resolution_status": effective.resolution_status,
                        "outcome": effective.outcome,
                        "requires_human_followup": effective.requires_human_followup,
                        "conversation_ids": set(),
                    },
                )
                conversation_ids = bucket["conversation_ids"]
                if isinstance(conversation_ids, set):
                    conversation_ids.add(effective.conversation_id)

        rows: list[SemanticInsightRowReadModel] = []
        total_summary_count = len(summaries)
        for item in blocker_buckets.values():
            conversation_ids = sorted(item["conversation_ids"])
            if len(conversation_ids) < 2:
                continue
            coverage_ratio = round(len(conversation_ids) / max(total_summary_count, 1), 4)
            display_name = str(item["display_name"])
            tag_kind = str(item["tag_kind"]).replace("_", " ")
            primary_intent_name = str(item["primary_intent_name"])
            rows.append(
                SemanticInsightRowReadModel(
                    insight_key=f"tag:{item['tag_definition_id']}:{primary_intent_name}:{item['agent_id'] or '-'}",
                    blocker_kind="summary_tag_pattern",
                    title=f"{display_name} is recurring in final semantic summaries",
                    summary=(
                        f"The {display_name} {tag_kind} tag is repeatedly attached to final summaries for "
                        f"{primary_intent_name.replace('_', ' ')} conversations."
                    ),
                    agent_id=item["agent_id"],
                    primary_intent_name=primary_intent_name,
                    tag_definition_id=str(item["tag_definition_id"]),
                    tag_name=str(item["tag_name"]),
                    tag_kind=str(item["tag_kind"]),
                    resolution_status=item["resolution_status"],
                    outcome=item["outcome"],
                    occurrence_count=len(conversation_ids),
                    coverage_ratio=coverage_ratio,
                    example_conversation_ids=conversation_ids[:5],
                )
            )
        for item in followup_buckets.values():
            conversation_ids = sorted(item["conversation_ids"])
            if len(conversation_ids) < 2:
                continue
            resolution_status = item["resolution_status"]
            outcome = item["outcome"]
            primary_intent_name = str(item["primary_intent_name"])
            coverage_ratio = round(len(conversation_ids) / max(total_summary_count, 1), 4)
            rows.append(
                SemanticInsightRowReadModel(
                    insight_key=(
                        f"followup:{primary_intent_name}:{item['agent_id'] or '-'}:"
                        f"{resolution_status or '-'}:{outcome or '-'}:{item['requires_human_followup']}"
                    ),
                    blocker_kind="summary_followup_pattern",
                    title="Final summaries show a repeated follow-up or escalation pattern",
                    summary=(
                        f"Final semantic summaries for {primary_intent_name.replace('_', ' ')} repeatedly end with "
                        f"resolution_status={resolution_status or 'unknown'} and outcome={outcome or 'unknown'}."
                    ),
                    agent_id=item["agent_id"],
                    primary_intent_name=primary_intent_name,
                    resolution_status=resolution_status,
                    outcome=outcome,
                    requires_human_followup=bool(item["requires_human_followup"]),
                    occurrence_count=len(conversation_ids),
                    coverage_ratio=coverage_ratio,
                    example_conversation_ids=conversation_ids[:5],
                )
            )
        rows.sort(
            key=lambda row: (
                row.occurrence_count,
                row.coverage_ratio,
                row.title,
                row.insight_key,
            ),
            reverse=True,
        )
        return IntentTagsInsightsReadModel(
            totals={
                "conversation_summaries": total_summary_count,
                "insight_rows": len(rows),
            },
            rows=rows[:limit],
        )

    def _assignment_matches_agent(self, assignment: TagAssignment, agent_id: str) -> bool:
        if assignment.classification_event_id is not None:
            event = self.store.get_classification_event(assignment.classification_event_id)
            return event is not None and event.agent_id == agent_id
        if assignment.conversation_summary_id is not None:
            summary = self.store.get_conversation_summary(assignment.conversation_summary_id)
            return summary is not None and summary.agent_id == agent_id
        return False
