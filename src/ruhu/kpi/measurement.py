from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..agent_document import AgentDocument
from ..db_models import ConversationRecord, RealtimeEventRecord, ToolInvocationRecord, TurnTraceRecord
from ..provider_costs import ProviderCostRecord as CanonicalProviderCostRecord
from ..registry import SQLAlchemyAgentRegistry
from .models import MetricObservation, MetricScope, utc_now
from .service import KPIService

SupportedMeasuredMetric = Literal[
    "deflection_rate",
    "resolution_rate",
    "transfer_rate",
    "containment_rate",
    "average_handle_time",
    "abandonment_rate",
    "cost_per_conversation",
]

_TERMINAL_OUTCOME_ALIASES = {
    "resolved": "resolved",
    "completed": "resolved",
    "closed": "resolved",
    "transferred": "transferred",
    "handoff": "transferred",
    "transfer": "transferred",
    "abandoned": "abandoned",
    "failed": "failed",
    "voicemail": "voicemail",
    "callback_scheduled": "callback_scheduled",
    "follow_up_required": "follow_up_required",
}


@dataclass(slots=True)
class ConversationSample:
    conversation_id: str
    organization_id: str | None
    agent_id: str
    agent_version_id: str
    channel: str | None
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None
    ended: bool
    had_handoff: bool
    tool_invocation_count: int
    tool_failure_count: int


class SQLAlchemyKPIMeasurementService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        agent_registry: SQLAlchemyAgentRegistry,
        kpi_service: KPIService,
    ) -> None:
        self._session_factory = session_factory
        self._agent_registry = agent_registry
        self._kpi_service = kpi_service
        self._agent_document_cache: dict[str, AgentDocument] = {}

    def list_measurement_support(self, scope: MetricScope) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for definition in self._kpi_service.list_metric_definitions():
            supported, reason = self._metric_support(definition.metric_key, scope)
            items.append(
                {
                    "metric_key": definition.metric_key,
                    "supported": supported,
                    "reason": reason,
                }
            )
        return items

    def refresh_observation(
        self,
        *,
        organization_id: str,
        metric_key: str,
        scope_id: str,
        lookback_days: int | None = None,
        period_end: datetime | None = None,
    ) -> MetricObservation:
        scope = self._kpi_service._require_scope(scope_id)
        definition = self._kpi_service._require_metric_definition(metric_key)
        supported, reason = self._metric_support(metric_key, scope)
        if not supported:
            raise ValueError(reason)
        effective_period_end = period_end or utc_now()
        effective_lookback_days = lookback_days or definition.default_lookback_days
        period_start = effective_period_end - timedelta(days=effective_lookback_days)
        samples = self.collect_samples(
            organization_id=organization_id,
            scope=scope,
            period_start=period_start,
            period_end=effective_period_end,
        )
        if metric_key == "cost_per_conversation":
            value, sample_size, eligibility_count, excluded_count, quality_flags, source_summary = self._compute_cost_metric(
                organization_id=organization_id,
                scope=scope,
                period_start=period_start,
                period_end=effective_period_end,
                samples=samples,
            )
        else:
            value, sample_size, eligibility_count, excluded_count, quality_flags, source_summary = self._compute_metric(
                metric_key=metric_key,
                samples=samples,
            )
        confidence = min(1.0, sample_size / max(definition.minimum_sample_size, 1))
        return self._kpi_service.record_observation(
            organization_id=organization_id,
            metric_key=metric_key,
            scope_id=scope.scope_id,
            value=value,
            sample_size=sample_size,
            confidence=round(confidence, 4),
            period_start=period_start,
            period_end=effective_period_end,
            observation_kind="scheduled_refresh",
            eligibility_count=eligibility_count,
            excluded_count=excluded_count,
            lookback_days=effective_lookback_days,
            quality_flags=quality_flags,
            source_summary=source_summary,
            calculation_version="runtime_v1",
        )

    def collect_samples(
        self,
        *,
        organization_id: str,
        scope: MetricScope,
        period_start: datetime,
        period_end: datetime,
    ) -> list[ConversationSample]:
        scope_supported, reason = self._scope_support(scope)
        if not scope_supported:
            raise ValueError(reason)
        with self._session_factory() as session:
            conversation_statement = (
                select(ConversationRecord)
                .where(ConversationRecord.mode == "live")
                .where(ConversationRecord.created_at <= period_end)
                .where(ConversationRecord.updated_at >= period_start)
                .order_by(ConversationRecord.updated_at.desc())
            )
            conversation_statement = _scope_conversation_statement(conversation_statement, organization_id)
            conversations = session.execute(conversation_statement).scalars().all()
            if not conversations:
                return []

            conversation_ids = [item.conversation_id for item in conversations]
            traces = self._load_traces(session, conversation_ids)
            channel_map = self._load_channels(session, conversation_ids)
            tool_counts = self._load_tool_counts(session, organization_id, period_start, period_end)

        samples: list[ConversationSample] = []
        for conversation in conversations:
            agent_id = conversation.agent_id
            agent_version_id = conversation.agent_version_id
            channel = conversation.channel or _resolve_channel(
                conversation_id=conversation.conversation_id,
                channel_map=channel_map,
            )
            if scope.scope_kind == "channel" and scope.channel is not None and channel != scope.channel:
                continue
            if scope.scope_kind == "workflow" and scope.workflow_id and agent_id != scope.workflow_id:
                continue
            if scope.scope_kind == "agent" and scope.agent_id and agent_id != scope.agent_id:
                continue

            latest_trace = traces.get(conversation.conversation_id, [])
            had_handoff = any(
                (trace.chosen_action_json or {}).get("type") == "handoff"
                for trace in latest_trace
            )
            agent_document = self._resolve_agent_document(agent_version_id)
            step_id = conversation.step_id
            outcome, ended = _resolve_outcome(
                agent_document=agent_document,
                step_id=step_id,
                had_handoff=had_handoff,
                explicit_outcome=conversation.outcome,
                explicit_status=conversation.status,
            )
            end_time = None
            if ended:
                if conversation.ended_at is not None:
                    end_time = conversation.ended_at
                elif latest_trace:
                    end_time = max(trace.recorded_at for trace in latest_trace)
                else:
                    end_time = conversation.updated_at

            tool_count = tool_counts.get(conversation.conversation_id, {}).get("count", 0)
            tool_failures = tool_counts.get(conversation.conversation_id, {}).get("failures", 0)
            samples.append(
                ConversationSample(
                    conversation_id=conversation.conversation_id,
                    organization_id=conversation.organization_id,
                    agent_id=agent_id,
                    agent_version_id=agent_version_id,
                    channel=channel,
                    started_at=conversation.started_at,
                    ended_at=end_time,
                    outcome=outcome,
                    ended=ended,
                    had_handoff=had_handoff,
                    tool_invocation_count=tool_count,
                    tool_failure_count=tool_failures,
                )
            )
        return samples

    def _metric_support(self, metric_key: str, scope: MetricScope) -> tuple[bool, str | None]:
        scope_supported, scope_reason = self._scope_support(scope)
        if not scope_supported:
            return False, scope_reason
        if metric_key in {
            "deflection_rate",
            "resolution_rate",
            "transfer_rate",
            "containment_rate",
            "average_handle_time",
            "abandonment_rate",
            "cost_per_conversation",
        }:
            return True, None
        return False, f"{metric_key} cannot be measured yet because required upstream data is not available"

    def _scope_support(self, scope: MetricScope) -> tuple[bool, str | None]:
        if scope.scope_kind in {"organization", "agent", "workflow", "channel"}:
            return True, None
        return False, f"{scope.scope_kind} KPI measurement is not supported yet by canonical runtime data"

    def _load_traces(self, session: Session, conversation_ids: list[str]) -> dict[str, list[TurnTraceRecord]]:
        if not conversation_ids:
            return {}
        statement = (
            select(TurnTraceRecord)
            .where(TurnTraceRecord.conversation_id.in_(conversation_ids))
            .order_by(TurnTraceRecord.recorded_at.desc())
        )
        traces = session.execute(statement).scalars().all()
        by_conversation: dict[str, list[TurnTraceRecord]] = {}
        for trace in traces:
            by_conversation.setdefault(trace.conversation_id, []).append(trace)
        return by_conversation

    def _load_channels(self, session: Session, conversation_ids: list[str]) -> dict[str, str]:
        if not conversation_ids:
            return {}
        statement = (
            select(RealtimeEventRecord)
            .where(RealtimeEventRecord.conversation_id.in_(conversation_ids))
            .where(
                or_(
                    RealtimeEventRecord.name == "user_accepted",
                    RealtimeEventRecord.name == "inbound_observed",
                    RealtimeEventRecord.name == "started",
                )
            )
            .order_by(RealtimeEventRecord.created_at.asc())
        )
        events = session.execute(statement).scalars().all()
        channels: dict[str, str] = {}
        for event in events:
            payload = dict(event.payload_json or {})
            candidate = payload.get("channel")
            if isinstance(candidate, str) and candidate and event.conversation_id not in channels:
                channels[event.conversation_id] = candidate
        return channels

    def _load_tool_counts(
        self,
        session: Session,
        organization_id: str,
        period_start: datetime,
        period_end: datetime,
    ) -> dict[str, dict[str, int]]:
        statement = (
            select(ToolInvocationRecord)
            .where(ToolInvocationRecord.created_at >= period_start)
            .where(ToolInvocationRecord.created_at <= period_end)
            .order_by(ToolInvocationRecord.created_at.asc())
        )
        statement = _scope_tool_statement(statement, organization_id)
        rows = session.execute(statement).scalars().all()
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            caller = dict(row.caller_json or {})
            conversation_id = caller.get("conversation_id")
            if not isinstance(conversation_id, str) or not conversation_id:
                continue
            bucket = counts.setdefault(conversation_id, {"count": 0, "failures": 0})
            bucket["count"] += 1
            if row.status in {"failed", "blocked", "timed_out", "cancelled"}:
                bucket["failures"] += 1
        return counts

    def _resolve_agent_document(self, agent_version_id: str) -> AgentDocument:
        cached = self._agent_document_cache.get(agent_version_id)
        if cached is not None:
            return cached
        snapshot = self._agent_registry.get_version_snapshot(agent_version_id)
        agent_document = snapshot.agent_document
        if agent_document is None:
            raise ValueError(f"agent version {agent_version_id!r} is missing canonical agent document")
        self._agent_document_cache[agent_version_id] = agent_document
        return agent_document

    def _compute_metric(
        self,
        *,
        metric_key: str,
        samples: list[ConversationSample],
    ) -> tuple[float, int, int, int, list[str], dict[str, object]]:
        quality_flags: list[str] = []
        source_summary: dict[str, object] = {
            "sources": ["conversations", "turn_traces", "realtime_events"],
            "conversation_count": len(samples),
        }
        ended = [sample for sample in samples if sample.ended]
        if metric_key == "average_handle_time":
            eligible = [
                sample
                for sample in ended
                if sample.ended_at is not None and sample.ended_at >= sample.started_at
            ]
            excluded = len(samples) - len(eligible)
            if not eligible:
                quality_flags.append("no_eligible_conversations")
                return 0.0, 0, 0, excluded, quality_flags, source_summary
            durations = [
                (sample.ended_at - sample.started_at).total_seconds()
                for sample in eligible
                if sample.ended_at is not None
            ]
            source_summary["ended_conversation_count"] = len(eligible)
            return round(sum(durations) / len(durations), 4), len(eligible), len(eligible), excluded, quality_flags, source_summary

        eligible = [sample for sample in ended if sample.outcome is not None]
        excluded = len(samples) - len(eligible)
        if excluded:
            quality_flags.append("unknown_outcomes_excluded")
        if not eligible:
            quality_flags.append("no_eligible_conversations")
            return 0.0, 0, 0, excluded, quality_flags, source_summary

        outcome_counts: dict[str, int] = {}
        for sample in eligible:
            assert sample.outcome is not None
            outcome_counts[sample.outcome] = outcome_counts.get(sample.outcome, 0) + 1
        source_summary["ended_conversation_count"] = len(eligible)
        source_summary["outcome_counts"] = outcome_counts

        if metric_key == "resolution_rate":
            numerator = sum(1 for sample in eligible if sample.outcome == "resolved")
        elif metric_key == "abandonment_rate":
            numerator = sum(1 for sample in eligible if sample.outcome == "abandoned")
        elif metric_key == "transfer_rate":
            numerator = sum(1 for sample in eligible if sample.outcome == "transferred" or sample.had_handoff)
        elif metric_key in {"deflection_rate", "containment_rate"}:
            numerator = sum(1 for sample in eligible if sample.outcome == "resolved" and not sample.had_handoff)
        else:  # pragma: no cover - guarded by support matrix
            raise ValueError(f"unsupported metric computation: {metric_key}")

        return round((numerator / len(eligible)) * 100.0, 4), len(eligible), len(eligible), excluded, quality_flags, source_summary

    def _compute_cost_metric(
        self,
        *,
        organization_id: str,
        scope: MetricScope,
        period_start: datetime,
        period_end: datetime,
        samples: list[ConversationSample],
    ) -> tuple[float, int, int, int, list[str], dict[str, object]]:
        quality_flags: list[str] = []
        with self._session_factory() as session:
            cost_rows = self._load_provider_costs(
                session,
                organization_id=organization_id,
                period_start=period_start,
                period_end=period_end,
                conversation_ids=[sample.conversation_id for sample in samples],
            )
        totals_by_conversation: dict[str, float] = {}
        counts_by_conversation: dict[str, int] = {}
        for row in cost_rows:
            if row.conversation_id is None:
                continue
            totals_by_conversation[row.conversation_id] = totals_by_conversation.get(row.conversation_id, 0.0) + row.amount_usd
            counts_by_conversation[row.conversation_id] = counts_by_conversation.get(row.conversation_id, 0) + 1

        eligible = [sample for sample in samples if sample.conversation_id in totals_by_conversation]
        excluded = len(samples) - len(eligible)
        source_summary: dict[str, object] = {
            "sources": ["provider_cost_records", "conversations"],
            "conversation_count": len(samples),
            "cost_record_count": len(cost_rows),
            "eligible_cost_conversation_count": len(eligible),
        }
        if not eligible:
            quality_flags.append("no_linked_provider_costs")
            return 0.0, 0, 0, excluded, quality_flags, source_summary

        total_cost = round(sum(totals_by_conversation[item.conversation_id] for item in eligible), 6)
        source_summary["total_cost_usd"] = total_cost
        source_summary["mean_cost_record_count"] = round(
            sum(counts_by_conversation[item.conversation_id] for item in eligible) / max(len(eligible), 1),
            4,
        )
        return round(total_cost / len(eligible), 6), len(eligible), len(eligible), excluded, quality_flags, source_summary

    def _load_provider_costs(
        self,
        session: Session,
        *,
        organization_id: str,
        period_start: datetime,
        period_end: datetime,
        conversation_ids: list[str],
    ) -> list[CanonicalProviderCostRecord]:
        from ..db_models import ProviderCostRecord as ProviderCostRecordModel

        if not conversation_ids:
            return []
        statement = (
            select(ProviderCostRecordModel)
            .where(ProviderCostRecordModel.occurred_at >= period_start)
            .where(ProviderCostRecordModel.occurred_at <= period_end)
            .where(ProviderCostRecordModel.conversation_id.in_(conversation_ids))
            .order_by(ProviderCostRecordModel.occurred_at.asc())
        )
        if organization_id is None:
            statement = statement.where(ProviderCostRecordModel.organization_id.is_(None))
        else:
            statement = statement.where(ProviderCostRecordModel.organization_id == organization_id)
        rows = session.execute(statement).scalars().all()
        return [
            CanonicalProviderCostRecord(
                cost_record_id=row.cost_record_id,
                organization_id=row.organization_id,
                conversation_id=row.conversation_id,
                realtime_session_id=row.realtime_session_id,
                turn_trace_id=row.turn_trace_id,
                tool_invocation_id=row.tool_invocation_id,
                provider=row.provider,
                cost_type=row.cost_type,
                amount_usd=row.amount_usd,
                reference_key=row.reference_key,
                metadata=dict(row.metadata_json or {}),
                occurred_at=row.occurred_at,
                created_at=row.created_at,
            )
            for row in rows
        ]


def _resolve_channel(*, conversation_id: str, channel_map: dict[str, str]) -> str | None:
    explicit = channel_map.get(conversation_id)
    if explicit:
        return explicit
    prefix, separator, _ = conversation_id.partition(":")
    if separator and prefix in {"phone", "whatsapp", "web_chat", "web_widget", "browser"}:
        return prefix
    return None


def _resolve_outcome(
    *,
    agent_document: AgentDocument,
    step_id: str,
    had_handoff: bool,
    explicit_outcome: str | None = None,
    explicit_status: str | None = None,
) -> tuple[str | None, bool]:
    if explicit_outcome:
        return explicit_outcome, explicit_status == "ended"
    try:
        step = agent_document.step_by_id(step_id)
    except KeyError:
        return ("transferred", True) if had_handoff else (None, False)
    if step.handoff is not None:
        return "transferred", True
    if step.completion is not None:
        raw = (step.completion.disposition or "").strip().lower()
        return _TERMINAL_OUTCOME_ALIASES.get(raw), True
    if had_handoff:
        return "transferred", True
    return None, False


def _scope_conversation_statement(statement, organization_id: str | None):  # type: ignore[no-untyped-def]
    if organization_id is None:
        return statement.where(ConversationRecord.organization_id.is_(None))
    return statement.where(ConversationRecord.organization_id == organization_id)


def _scope_tool_statement(statement, organization_id: str | None):  # type: ignore[no-untyped-def]
    if organization_id is None:
        return statement.where(ToolInvocationRecord.organization_id.is_(None))
    return statement.where(ToolInvocationRecord.organization_id == organization_id)
