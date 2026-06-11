from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..db_models import ProviderCostRecord, ToolInvocationRecord, TurnTraceRecord
from ..analytics_tagging.sqlalchemy_models import IntentTagAssignmentRecord, IntentTagConversationSummaryRecord, TagDefinitionRecord
from .measurement import ConversationSample, SQLAlchemyKPIMeasurementService
from .models import Goal, InsightSignal, MetricScope, utc_now
from .service import KPIService


class SQLAlchemyKPIInsightAnalyzer:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        measurement_service: SQLAlchemyKPIMeasurementService,
        kpi_service: KPIService,
    ) -> None:
        self._session_factory = session_factory
        self._measurement_service = measurement_service
        self._kpi_service = kpi_service

    def build_signals_for_goal(self, goal_id: str) -> list[InsightSignal]:
        goal = self._kpi_service._require_goal(goal_id)
        scope = self._kpi_service._require_scope(goal.scope_id)
        definition = self._kpi_service._require_metric_definition(goal.metric_key)
        latest_observation = self._kpi_service._store.get_latest_observation(goal.organization_id, goal.metric_key, goal.scope_id)
        now = utc_now()
        period_end = latest_observation.period_end if latest_observation is not None else now
        period_start = period_end - timedelta(days=definition.default_lookback_days)
        samples = self._measurement_service.collect_samples(
            organization_id=goal.organization_id,
            scope=scope,
            period_start=period_start,
            period_end=period_end,
        )
        tool_failures = self._top_tool_failures(
            organization_id=goal.organization_id,
            conversation_ids=[sample.conversation_id for sample in samples],
            period_start=period_start,
            period_end=period_end,
        )
        signals: list[InsightSignal] = []
        signals.extend(self._signals_from_samples(goal, scope, samples))
        signals.extend(tool_failures)
        signals.extend(
            self._summary_assignment_signals(
                organization_id=goal.organization_id,
                samples=samples,
            )
        )
        signals.extend(
            self._cost_signals(
                goal=goal,
                samples=samples,
                period_start=period_start,
                period_end=period_end,
            )
        )
        if latest_observation is not None and latest_observation.sample_size < definition.minimum_sample_size:
            signals.append(
                InsightSignal(
                    blocker_kind="data_quality",
                    title="Measurement sample size is below the decision threshold",
                    summary=(
                        f"{goal.metric_key} currently has sample size {latest_observation.sample_size}, "
                        f"below the recommended threshold of {definition.minimum_sample_size}."
                    ),
                    severity=1.0,
                    occurrence_count=max(latest_observation.sample_size, 1),
                    metric_relevance=1.0,
                    freshness_score=1.0,
                    evidence_bundle={
                        "sample_size": latest_observation.sample_size,
                        "minimum_sample_size": definition.minimum_sample_size,
                    },
                )
            )
        if not signals:
            signals.append(
                InsightSignal(
                    blocker_kind="performance_gap",
                    title=f"{goal.metric_key.replace('_', ' ').title()} needs explicit follow-up",
                    summary="The current KPI gap lacks a stronger blocker cluster, so operator review should inspect representative conversations directly.",
                    severity=1.0,
                    occurrence_count=max(len(samples), 1),
                    metric_relevance=1.0,
                    freshness_score=1.0,
                    evidence_bundle={"conversation_count": len(samples)},
                )
            )
        return signals

    def generate_for_goal(self, goal_id: str) -> list:
        goal = self._kpi_service._require_goal(goal_id)
        return self._kpi_service.generate_insights(
            organization_id=goal.organization_id,
            goal_id=goal.goal_id,
            signals=self.build_signals_for_goal(goal_id),
        )

    def _signals_from_samples(self, goal: Goal, scope: MetricScope, samples: list) -> list[InsightSignal]:
        signals: list[InsightSignal] = []
        ended = [sample for sample in samples if sample.ended]
        transferred = [sample for sample in ended if sample.had_handoff or sample.outcome == "transferred"]
        if goal.metric_key in {"transfer_rate", "deflection_rate", "containment_rate", "resolution_rate"} and transferred:
            examples = [sample.conversation_id for sample in transferred[:5]]
            signals.append(
                InsightSignal(
                    blocker_kind="transfer_escalation",
                    title="Repeat handoffs are suppressing self-service completion",
                    summary="A meaningful share of eligible conversations reached a handoff path instead of finishing inside the AI workflow.",
                    severity=1.4,
                    occurrence_count=len(transferred),
                    metric_relevance=1.3,
                    freshness_score=1.0,
                    evidence_bundle={
                        "conversation_ids": examples,
                        "transferred_count": len(transferred),
                        "ended_count": len(ended),
                        "scope_kind": scope.scope_kind,
                    },
                    examples=examples,
                )
            )
            workflow_hotspot = self._workflow_hotspot_signal(
                blocker_kind="workflow_hotspot",
                title="One workflow is driving most transfer outcomes",
                summary="The current KPI drag is concentrated in a single workflow, which makes the remediation path narrower and easier to validate.",
                samples=transferred,
                metric_relevance=1.2,
            )
            if workflow_hotspot is not None:
                signals.append(workflow_hotspot)
        unresolved_followups = [
            sample
            for sample in ended
            if sample.outcome in {"follow_up_required", "callback_scheduled", "failed"}
        ]
        if goal.metric_key in {"resolution_rate", "deflection_rate", "containment_rate"} and unresolved_followups:
            examples = [sample.conversation_id for sample in unresolved_followups[:5]]
            counts: dict[str, int] = {}
            for sample in unresolved_followups:
                if sample.outcome is None:
                    continue
                counts[sample.outcome] = counts.get(sample.outcome, 0) + 1
            signals.append(
                InsightSignal(
                    blocker_kind="follow_up_backlog",
                    title="Follow-up and callback outcomes are blocking clean resolution",
                    summary="A non-trivial share of conversations are ending in follow-up-required or callback-needed states instead of reaching a clean resolved outcome.",
                    severity=1.2,
                    occurrence_count=len(unresolved_followups),
                    metric_relevance=1.2,
                    freshness_score=1.0,
                    evidence_bundle={
                        "conversation_ids": examples,
                        "outcome_breakdown": counts,
                        "ended_count": len(ended),
                    },
                    examples=examples,
                )
            )
        if goal.metric_key == "abandonment_rate":
            abandoned = [sample for sample in ended if sample.outcome == "abandoned"]
            if abandoned:
                examples = [sample.conversation_id for sample in abandoned[:5]]
                signals.append(
                    InsightSignal(
                        blocker_kind="abandonment_dropoff",
                        title="Conversations are dropping before completion",
                        summary="A noticeable share of started conversations ended abandoned before reaching resolution or transfer.",
                        severity=1.3,
                        occurrence_count=len(abandoned),
                        metric_relevance=1.3,
                        freshness_score=1.0,
                        evidence_bundle={
                            "conversation_ids": examples,
                            "abandoned_count": len(abandoned),
                            "ended_count": len(ended),
                        },
                        examples=examples,
                    )
                )
                workflow_hotspot = self._workflow_hotspot_signal(
                    blocker_kind="workflow_abandonment_hotspot",
                    title="One workflow accounts for most abandonment",
                    summary="Abandonment is concentrated in a single workflow, suggesting the drop-off is not evenly distributed across the product.",
                    samples=abandoned,
                    metric_relevance=1.2,
                )
                if workflow_hotspot is not None:
                    signals.append(workflow_hotspot)
        if goal.metric_key == "average_handle_time":
            long_samples = sorted(
                (
                    sample
                    for sample in ended
                    if sample.ended_at is not None and sample.ended_at >= sample.started_at
                ),
                key=lambda sample: (sample.ended_at - sample.started_at).total_seconds() if sample.ended_at else 0,
                reverse=True,
            )[:5]
            if long_samples:
                examples = [sample.conversation_id for sample in long_samples]
                mean_tool_invocations = round(
                    sum(sample.tool_invocation_count for sample in long_samples) / max(len(long_samples), 1),
                    2,
                )
                signals.append(
                    InsightSignal(
                        blocker_kind="latency_overhead",
                        title="Long conversations are driving handle-time inflation",
                        summary="The slowest completed conversations show elevated duration and often include multiple tool steps or repeated fallback paths.",
                        severity=1.3,
                        occurrence_count=len(long_samples),
                        metric_relevance=1.2,
                        freshness_score=1.0,
                        evidence_bundle={
                            "conversation_ids": examples,
                            "mean_tool_invocation_count": mean_tool_invocations,
                        },
                        examples=examples,
                    )
                )
                workflow_hotspot = self._workflow_hotspot_signal(
                    blocker_kind="workflow_latency_hotspot",
                    title="One workflow is responsible for the slowest conversations",
                    summary="The longest conversations cluster inside a single workflow, which points to a localized latency problem instead of a global system slowdown.",
                    samples=long_samples,
                    metric_relevance=1.1,
                )
                if workflow_hotspot is not None:
                    signals.append(workflow_hotspot)
        tool_heavy = [sample for sample in ended if sample.tool_invocation_count > 0 or sample.tool_failure_count > 0]
        if goal.metric_key in {"average_handle_time", "cost_per_conversation"} and tool_heavy:
            average_tool_invocations = round(
                sum(sample.tool_invocation_count for sample in tool_heavy) / max(len(tool_heavy), 1),
                2,
            )
            total_failures = sum(sample.tool_failure_count for sample in tool_heavy)
            if average_tool_invocations >= 2.0 or total_failures >= 2:
                ranked = sorted(
                    tool_heavy,
                    key=lambda sample: (sample.tool_failure_count, sample.tool_invocation_count, sample.conversation_id),
                    reverse=True,
                )[:5]
                examples = [sample.conversation_id for sample in ranked]
                signals.append(
                    InsightSignal(
                        blocker_kind="tool_overhead",
                        title="Tool overhead is inflating cost or latency",
                        summary="Conversations linked to this KPI show elevated tool-call density or repeated tool failures, which suggests the remediation path should start with the tool chain rather than the prompt layer.",
                        severity=1.15,
                        occurrence_count=len(tool_heavy),
                        metric_relevance=1.15,
                        freshness_score=1.0,
                        evidence_bundle={
                            "conversation_ids": examples,
                            "average_tool_invocations": average_tool_invocations,
                            "total_tool_failures": total_failures,
                        },
                        examples=examples,
                    )
                )
        return signals

    def _cost_signals(
        self,
        *,
        goal: Goal,
        samples: list[ConversationSample],
        period_start,
        period_end,
    ) -> list[InsightSignal]:
        conversation_ids = [sample.conversation_id for sample in samples]
        if goal.metric_key != "cost_per_conversation" or not conversation_ids:
            return []
        with self._session_factory() as session:
            statement = (
                select(ProviderCostRecord)
                .where(ProviderCostRecord.occurred_at >= period_start)
                .where(ProviderCostRecord.occurred_at <= period_end)
                .where(ProviderCostRecord.conversation_id.in_(conversation_ids))
            )
            if goal.organization_id is None:
                statement = statement.where(ProviderCostRecord.organization_id.is_(None))
            else:
                statement = statement.where(ProviderCostRecord.organization_id == goal.organization_id)
            records = session.execute(statement).scalars().all()
        if not records:
            return []
        totals_by_conversation: dict[str, float] = {}
        totals_by_cost_type: dict[str, float] = {}
        for record in records:
            if record.conversation_id is not None:
                totals_by_conversation[record.conversation_id] = totals_by_conversation.get(record.conversation_id, 0.0) + record.amount_usd
            totals_by_cost_type[record.cost_type] = totals_by_cost_type.get(record.cost_type, 0.0) + record.amount_usd
        if not totals_by_conversation:
            return []
        top_conversations = sorted(totals_by_conversation.items(), key=lambda item: item[1], reverse=True)[:5]
        top_types = sorted(totals_by_cost_type.items(), key=lambda item: item[1], reverse=True)[:3]
        dominant_type = top_types[0][0]
        dominant_share = round(top_types[0][1] / max(sum(totals_by_cost_type.values()), 0.000001), 4)
        signals = [
            InsightSignal(
                blocker_kind="cost_inflation",
                title=f"{dominant_type} costs are dominating conversation spend",
                summary="Provider cost records show a concentrated cost driver inside the current KPI window.",
                severity=1.2,
                occurrence_count=len(top_conversations),
                metric_relevance=1.3,
                freshness_score=1.0,
                evidence_bundle={
                    "top_conversation_costs": [
                        {"conversation_id": conversation_id, "amount_usd": round(amount, 6)}
                        for conversation_id, amount in top_conversations
                    ],
                    "cost_type_breakdown": [
                        {"cost_type": cost_type, "amount_usd": round(amount, 6)}
                        for cost_type, amount in top_types
                    ],
                    "dominant_cost_type": dominant_type,
                    "dominant_cost_share": dominant_share,
                },
                examples=[conversation_id for conversation_id, _ in top_conversations],
            )
        ]
        sample_by_conversation = {sample.conversation_id: sample for sample in samples}
        totals_by_agent: dict[str, float] = {}
        agent_examples: dict[str, list[dict[str, object]]] = {}
        for conversation_id, amount in top_conversations:
            sample = sample_by_conversation.get(conversation_id)
            if sample is None:
                continue
            totals_by_agent[sample.agent_id] = totals_by_agent.get(sample.agent_id, 0.0) + amount
            agent_examples.setdefault(sample.agent_id, []).append(
                {"conversation_id": conversation_id, "amount_usd": round(amount, 6)}
            )
        if totals_by_agent:
            dominant_agent, dominant_agent_amount = max(
                totals_by_agent.items(),
                key=lambda item: (item[1], item[0]),
            )
            total_amount = sum(totals_by_agent.values())
            agent_share = round(dominant_agent_amount / max(total_amount, 0.000001), 4)
            if agent_share >= 0.5 and len(agent_examples.get(dominant_agent, [])) >= 2:
                signals.append(
                    InsightSignal(
                        blocker_kind="workflow_cost_hotspot",
                        title="One workflow is driving most provider spend",
                        summary="Conversation cost is not evenly distributed. A single workflow owns most of the spend in the current KPI window.",
                        severity=1.15,
                        occurrence_count=len(agent_examples[dominant_agent]),
                        metric_relevance=1.25,
                        freshness_score=1.0,
                        evidence_bundle={
                            "agent_id": dominant_agent,
                            "cost_share": agent_share,
                            "conversation_costs": agent_examples[dominant_agent],
                        },
                        examples=[item["conversation_id"] for item in agent_examples[dominant_agent]],
                    )
                )
        return signals

    def _top_tool_failures(
        self,
        *,
        organization_id: str,
        conversation_ids: list[str],
        period_start,
        period_end,
    ) -> list[InsightSignal]:
        if not conversation_ids:
            return []
        with self._session_factory() as session:
            statement = (
                select(ToolInvocationRecord)
                .where(ToolInvocationRecord.created_at >= period_start)
                .where(ToolInvocationRecord.created_at <= period_end)
            )
            if organization_id is None:
                statement = statement.where(ToolInvocationRecord.organization_id.is_(None))
            else:
                statement = statement.where(ToolInvocationRecord.organization_id == organization_id)
            invocations = session.execute(statement).scalars().all()
        failing_by_tool: dict[str, list[str]] = {}
        for invocation in invocations:
            caller = dict(invocation.caller_json or {})
            conversation_id = caller.get("conversation_id")
            if conversation_id not in conversation_ids:
                continue
            if invocation.status not in {"failed", "blocked", "timed_out", "cancelled"}:
                continue
            failing_by_tool.setdefault(invocation.tool_ref, []).append(conversation_id)
        signals: list[InsightSignal] = []
        for tool_ref, failures in sorted(failing_by_tool.items(), key=lambda item: len(item[1]), reverse=True)[:3]:
            signals.append(
                InsightSignal(
                    blocker_kind="tool_failures",
                    title=f"{tool_ref} failures are degrading the conversation path",
                    summary=f"{tool_ref} recorded repeated blocked, failed, or timed-out executions during the current measurement window.",
                    severity=1.2,
                    occurrence_count=len(failures),
                    metric_relevance=1.1,
                    freshness_score=1.0,
                    evidence_bundle={
                        "tool_ref": tool_ref,
                        "conversation_ids": failures[:5],
                        "failure_count": len(failures),
                    },
                    examples=failures[:5],
                )
            )
        return signals

    def _summary_assignment_signals(
        self,
        *,
        organization_id: str,
        samples: list[ConversationSample],
    ) -> list[InsightSignal]:
        conversation_ids = [sample.conversation_id for sample in samples]
        if not conversation_ids:
            return []
        with self._session_factory() as session:
            summaries = session.execute(
                select(IntentTagConversationSummaryRecord)
                .where(IntentTagConversationSummaryRecord.organization_id == organization_id)
                .where(IntentTagConversationSummaryRecord.conversation_id.in_(conversation_ids))
                .where(IntentTagConversationSummaryRecord.status.in_(("final", "corrected")))
            ).scalars().all()
            if not summaries:
                return []

            effective_summaries: dict[str, IntentTagConversationSummaryRecord] = {}
            for summary in sorted(
                summaries,
                key=lambda item: (_summary_status_priority(item.status), item.updated_at, item.conversation_summary_id),
                reverse=True,
            ):
                effective_summaries.setdefault(summary.conversation_id, summary)
            summary_ids = [item.conversation_summary_id for item in effective_summaries.values()]
            if not summary_ids:
                return []

            assignment_rows = session.execute(
                select(IntentTagAssignmentRecord, TagDefinitionRecord)
                .join(
                    TagDefinitionRecord,
                    IntentTagAssignmentRecord.tag_definition_id == TagDefinitionRecord.tag_definition_id,
                )
                .where(IntentTagAssignmentRecord.organization_id == organization_id)
                .where(IntentTagAssignmentRecord.conversation_summary_id.in_(summary_ids))
                .where(IntentTagAssignmentRecord.assignment_scope == "conversation")
                .where(TagDefinitionRecord.tag_kind.in_(("blocker", "failure_reason", "risk")))
            ).all()
        if not assignment_rows:
            return []

        summary_by_id = {
            item.conversation_summary_id: item
            for item in effective_summaries.values()
        }
        grouped: dict[str, dict[str, object]] = {}
        for assignment, tag in assignment_rows:
            summary = summary_by_id.get(assignment.conversation_summary_id or "")
            if summary is None:
                continue
            bucket = grouped.setdefault(
                tag.tag_definition_id,
                {
                    "tag_name": tag.name,
                    "display_name": tag.display_name,
                    "tag_kind": tag.tag_kind,
                    "conversation_ids": set(),
                    "primary_intent_counts": {},
                },
            )
            conversation_ids_bucket = bucket["conversation_ids"]
            if isinstance(conversation_ids_bucket, set):
                conversation_ids_bucket.add(summary.conversation_id)
            intent_counts = bucket["primary_intent_counts"]
            if isinstance(intent_counts, dict):
                intent_name = summary.primary_intent_name or "unknown"
                intent_counts[intent_name] = int(intent_counts.get(intent_name, 0)) + 1

        total_summary_count = len(effective_summaries)
        signals: list[InsightSignal] = []
        ranked = sorted(
            grouped.values(),
            key=lambda item: (
                len(item["conversation_ids"]) if isinstance(item["conversation_ids"], set) else 0,
                str(item["tag_name"]),
            ),
            reverse=True,
        )
        for item in ranked[:3]:
            conversation_ids_bucket = item["conversation_ids"]
            if not isinstance(conversation_ids_bucket, set):
                continue
            conversation_ids_for_tag = sorted(conversation_ids_bucket)
            if len(conversation_ids_for_tag) < 2:
                continue
            tag_name = str(item["tag_name"])
            display_name = str(item["display_name"])
            tag_kind = str(item["tag_kind"])
            coverage_ratio = round(len(conversation_ids_for_tag) / max(total_summary_count, 1), 4)
            signals.append(
                InsightSignal(
                    blocker_kind="summary_tag_pattern",
                    title=f"{display_name} is recurring in final semantic summaries",
                    summary=(
                        f"Final semantic summaries repeatedly applied the {display_name} {tag_kind.replace('_', ' ')} "
                        "tag across conversations in the current KPI window."
                    ),
                    severity=1.15,
                    occurrence_count=len(conversation_ids_for_tag),
                    metric_relevance=1.15,
                    freshness_score=1.0,
                    evidence_bundle={
                        "tag_name": tag_name,
                        "tag_kind": tag_kind,
                        "coverage_ratio": coverage_ratio,
                        "summary_count": len(conversation_ids_for_tag),
                        "conversation_ids": conversation_ids_for_tag[:5],
                        "primary_intent_counts": dict(item["primary_intent_counts"]),
                    },
                    examples=conversation_ids_for_tag[:5],
                )
            )
        return signals

    def _workflow_hotspot_signal(
        self,
        *,
        blocker_kind: str,
        title: str,
        summary: str,
        samples: list[ConversationSample],
        metric_relevance: float,
    ) -> InsightSignal | None:
        if len(samples) < 2:
            return None
        conversations_by_agent: dict[str, list[str]] = {}
        for sample in samples:
            conversations_by_agent.setdefault(sample.agent_id, []).append(sample.conversation_id)
        dominant_agent, agent_conversations = max(
            conversations_by_agent.items(),
            key=lambda item: (len(item[1]), item[0]),
        )
        share = round(len(agent_conversations) / max(len(samples), 1), 4)
        if share < 0.5 and len(agent_conversations) < 3:
            return None
        return InsightSignal(
            blocker_kind=blocker_kind,
            title=title,
            summary=summary,
            severity=1.1,
            occurrence_count=len(agent_conversations),
            metric_relevance=metric_relevance,
            freshness_score=1.0,
            evidence_bundle={
                "agent_id": dominant_agent,
                "agent_share": share,
                "conversation_ids": agent_conversations[:5],
            },
            examples=agent_conversations[:5],
        )


def _summary_status_priority(status: str) -> int:
    if status == "corrected":
        return 2
    if status == "final":
        return 1
    return 0
