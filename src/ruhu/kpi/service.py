from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Sequence

from .execution import AdapterExecutionOutcome, KPIExecutionAdapterRegistry
from .models import (
    AttributionConfidence,
    AttributionMode,
    BaselineSnapshot,
    ExecutionIntent,
    ExecutionResult,
    ExecutionReversibility,
    ExecutionSafetyLevel,
    Goal,
    GoalEvaluation,
    GoalStatus,
    ImpactAssessment,
    InsightItem,
    InsightSignal,
    KPIExperiment,
    MetricDefinition,
    MetricObservation,
    MetricScope,
    RecommendationCandidate,
    normalize_channel,
    utc_now,
)
from .read_models import GoalDetailReadModel, GoalSummaryReadModel
from .registry import default_metric_registry
from .store import KPIStore

_ACTIVE_GOAL_STATUSES = {"active", "on_track", "at_risk", "stalled"}
_ALLOWED_INSIGHT_TRANSITIONS: dict[str, set[str]] = {
    "open": {"open", "accepted", "dismissed", "superseded"},
    "accepted": {"accepted", "superseded"},
    "dismissed": {"dismissed", "open", "superseded"},
    "superseded": {"superseded"},
}
_ALLOWED_RECOMMENDATION_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"draft", "ready_for_review", "rejected", "superseded"},
    "ready_for_review": {"ready_for_review", "approved", "rejected", "superseded"},
    "approved": {"approved", "execution_requested", "rejected", "superseded"},
    "rejected": {"rejected", "ready_for_review", "superseded"},
    "execution_requested": {"execution_requested", "executed", "execution_failed", "superseded"},
    "execution_failed": {"execution_failed", "approved", "rejected", "superseded"},
    "executed": {"executed", "superseded"},
    "superseded": {"superseded"},
}


class KPIService:
    def __init__(
        self,
        store: KPIStore,
        *,
        metric_definitions: Sequence[MetricDefinition] | None = None,
        execution_registry: KPIExecutionAdapterRegistry | None = None,
    ) -> None:
        self._store = store
        self._metric_definitions = {
            definition.metric_key: definition
            for definition in (metric_definitions or default_metric_registry())
        }
        self._execution_registry = execution_registry or KPIExecutionAdapterRegistry()

    def list_metric_definitions(self) -> list[MetricDefinition]:
        return sorted(
            (definition.model_copy(deep=True) for definition in self._metric_definitions.values()),
            key=lambda definition: definition.metric_key,
        )

    def get_metric_definition(self, metric_key: str) -> MetricDefinition | None:
        definition = self._metric_definitions.get(metric_key)
        return None if definition is None else definition.model_copy(deep=True)

    def ensure_scope(
        self,
        *,
        organization_id: str,
        scope_kind: str,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        channel: str | None = None,
        segment_key: str | None = None,
        campaign_key: str | None = None,
        custom_scope: dict[str, object] | None = None,
        display_name: str | None = None,
    ) -> MetricScope:
        normalized_scope = {
            "scope_kind": scope_kind,
            "agent_id": agent_id or None,
            "workflow_id": workflow_id or None,
            "channel": normalize_channel(channel),
            "segment_key": segment_key or None,
            "campaign_key": campaign_key or None,
            "custom_scope": dict(custom_scope or {}),
        }
        fingerprint = _scope_fingerprint(organization_id=organization_id, payload=normalized_scope)
        existing = self._store.get_scope_by_fingerprint(organization_id, fingerprint)
        if existing is not None:
            if display_name and display_name != existing.display_name:
                existing = existing.model_copy(update={"display_name": display_name})
                return self._store.save_scope(existing)
            return existing
        scope = MetricScope(
            organization_id=organization_id,
            scope_kind=scope_kind,  # type: ignore[arg-type]
            agent_id=agent_id,
            workflow_id=workflow_id,
            channel=normalized_scope["channel"],
            segment_key=segment_key,
            campaign_key=campaign_key,
            custom_scope=dict(custom_scope or {}),
            display_name=display_name,
            fingerprint=fingerprint,
        )
        return self._store.save_scope(scope)

    def record_observation(
        self,
        *,
        organization_id: str,
        metric_key: str,
        scope_id: str,
        value: float,
        sample_size: int,
        confidence: float,
        period_start: datetime,
        period_end: datetime,
        observation_kind: str = "scheduled_refresh",
        eligibility_count: int | None = None,
        excluded_count: int | None = None,
        lookback_days: int | None = None,
        quality_flags: Sequence[str] | None = None,
        source_summary: dict[str, object] | None = None,
        calculation_version: str = "v1",
    ) -> MetricObservation:
        definition = self._require_metric_definition(metric_key)
        scope = self._require_scope(scope_id)
        if scope.organization_id != organization_id:
            raise ValueError("scope does not belong to organization")
        if definition.min_value is not None and value < definition.min_value:
            raise ValueError(f"{metric_key} value {value} is below minimum {definition.min_value}")
        if definition.max_value is not None and value > definition.max_value:
            raise ValueError(f"{metric_key} value {value} is above maximum {definition.max_value}")
        observation = MetricObservation(
            organization_id=organization_id,
            metric_key=metric_key,
            metric_definition_version=definition.version,
            scope_id=scope_id,
            observation_kind=observation_kind,  # type: ignore[arg-type]
            value=value,
            sample_size=sample_size,
            confidence=confidence,
            eligibility_count=eligibility_count,
            excluded_count=excluded_count,
            period_start=period_start,
            period_end=period_end,
            lookback_days=lookback_days or definition.default_lookback_days,
            quality_flags=list(quality_flags or []),
            source_summary=dict(source_summary or {}),
            calculation_version=calculation_version,
        )
        return self._store.save_observation(observation)

    def create_baseline_snapshot(
        self,
        *,
        organization_id: str,
        metric_key: str,
        scope_id: str,
        goal_id: str | None = None,
        observation_id: str | None = None,
        manual_value: float | None = None,
        manual_sample_size: int | None = None,
        manual_confidence: float | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        reason: str | None = None,
        provenance: dict[str, object] | None = None,
    ) -> BaselineSnapshot:
        if observation_id and manual_value is not None:
            raise ValueError("baseline snapshot must be created from observation or manual value, not both")
        self._require_scope(scope_id)
        definition = self._require_metric_definition(metric_key)
        if observation_id:
            observation = self._require_observation(observation_id)
        else:
            observation = self._store.get_latest_observation(organization_id, metric_key, scope_id)
        if observation is None and manual_value is None:
            raise ValueError("no measured observation available for baseline snapshot")
        if observation is not None and (
            observation.organization_id != organization_id
            or observation.metric_key != metric_key
            or observation.scope_id != scope_id
        ):
            raise ValueError("baseline observation does not match requested organization, metric, or scope")

        if manual_value is not None:
            value = manual_value
            sample_size = manual_sample_size if manual_sample_size is not None else 1
            confidence = manual_confidence if manual_confidence is not None else 0.5
            baseline_source = "manual_override"
            snapshot_period_start = period_start or utc_now()
            snapshot_period_end = period_end or snapshot_period_start
            source_observation_id = None
        else:
            assert observation is not None
            value = observation.value
            sample_size = observation.sample_size
            confidence = observation.confidence
            baseline_source = "measured"
            snapshot_period_start = observation.period_start
            snapshot_period_end = observation.period_end
            source_observation_id = observation.observation_id

        if definition.min_value is not None and value < definition.min_value:
            raise ValueError(f"{metric_key} baseline {value} is below minimum {definition.min_value}")
        if definition.max_value is not None and value > definition.max_value:
            raise ValueError(f"{metric_key} baseline {value} is above maximum {definition.max_value}")

        snapshot = BaselineSnapshot(
            organization_id=organization_id,
            goal_id=goal_id,
            metric_key=metric_key,
            scope_id=scope_id,
            source_observation_id=source_observation_id,
            value=value,
            sample_size=sample_size,
            confidence=confidence,
            period_start=snapshot_period_start,
            period_end=snapshot_period_end,
            baseline_source=baseline_source,  # type: ignore[arg-type]
            baseline_reason=reason,
            provenance=dict(provenance or {}),
        )
        return self._store.save_baseline_snapshot(snapshot)

    def create_goal(
        self,
        *,
        organization_id: str,
        metric_key: str,
        scope_id: str,
        name: str,
        target_value: float,
        target_at: datetime,
        description: str | None = None,
        owner_user_id: str | None = None,
        baseline_snapshot_id: str | None = None,
        start_at: datetime | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Goal:
        self._require_scope(scope_id)
        definition = self._require_metric_definition(metric_key)
        baseline = (
            self._require_baseline_snapshot(baseline_snapshot_id)
            if baseline_snapshot_id
            else self.create_baseline_snapshot(
                organization_id=organization_id,
                metric_key=metric_key,
                scope_id=scope_id,
            )
        )
        if baseline.organization_id != organization_id or baseline.metric_key != metric_key or baseline.scope_id != scope_id:
            raise ValueError("baseline snapshot does not match goal metric or scope")
        _validate_target_direction(definition, baseline.value, target_value)
        if target_at <= (start_at or utc_now()):
            raise ValueError("target_at must be later than start_at")
        goal = Goal(
            organization_id=organization_id,
            metric_key=metric_key,
            scope_id=scope_id,
            name=name,
            description=description,
            baseline_snapshot_id=baseline.baseline_snapshot_id,
            target_value=target_value,
            status="active",
            start_at=start_at or utc_now(),
            target_at=target_at,
            owner_user_id=owner_user_id,
            metadata=dict(metadata or {}),
        )
        stored_goal = self._store.save_goal(goal)
        if baseline.goal_id != stored_goal.goal_id:
            self._store.save_baseline_snapshot(baseline.model_copy(update={"goal_id": stored_goal.goal_id}))
        return stored_goal

    def pause_goal(self, goal_id: str) -> Goal:
        goal = self._require_goal(goal_id)
        updated = goal.model_copy(update={"status": "paused", "updated_at": utc_now()})
        return self._store.save_goal(updated)

    def resume_goal(self, goal_id: str) -> Goal:
        goal = self._require_goal(goal_id)
        updated = goal.model_copy(update={"status": "active", "updated_at": utc_now()})
        return self._store.save_goal(updated)

    def abandon_goal(self, goal_id: str) -> Goal:
        goal = self._require_goal(goal_id)
        updated = goal.model_copy(update={"status": "abandoned", "updated_at": utc_now()})
        return self._store.save_goal(updated)

    def complete_goal(self, goal_id: str) -> Goal:
        goal = self._require_goal(goal_id)
        updated = goal.model_copy(update={"status": "completed", "updated_at": utc_now()})
        return self._store.save_goal(updated)

    def evaluate_goal(self, goal_id: str, *, observation_id: str | None = None) -> GoalEvaluation:
        goal = self._require_goal(goal_id)
        definition = self._require_metric_definition(goal.metric_key)
        baseline = self._require_baseline_snapshot(goal.baseline_snapshot_id)
        observation = (
            self._require_observation(observation_id)
            if observation_id
            else self._store.get_latest_observation(goal.organization_id, goal.metric_key, goal.scope_id)
        )
        if observation is None:
            raise ValueError("no observation available to evaluate goal")
        if observation.scope_id != goal.scope_id or observation.metric_key != goal.metric_key:
            raise ValueError("goal evaluation observation does not match goal metric or scope")

        now = utc_now()
        raw_gap = goal.target_value - baseline.value
        if definition.direction == "higher_is_better":
            progress_ratio = _safe_progress(numerator=observation.value - baseline.value, denominator=raw_gap)
            distance_to_target = goal.target_value - observation.value
        else:
            progress_ratio = _safe_progress(numerator=baseline.value - observation.value, denominator=baseline.value - goal.target_value)
            distance_to_target = observation.value - goal.target_value
        delta_from_baseline = observation.value - baseline.value
        freshness_seconds = max(int((now - observation.period_end).total_seconds()), 0)
        sample_size_sufficient = observation.sample_size >= definition.minimum_sample_size

        if goal.status in {"paused", "abandoned"}:
            evaluation_status: GoalStatus = goal.status
            notes = f"goal remains {goal.status}"
        elif progress_ratio >= 1 and sample_size_sufficient:
            evaluation_status = "completed"
            notes = "latest observation meets target"
        elif freshness_seconds > definition.default_lookback_days * 2 * 86400:
            evaluation_status = "stalled"
            notes = "latest observation is stale relative to lookback window"
        elif progress_ratio >= 0.6:
            evaluation_status = "on_track"
            notes = None if sample_size_sufficient else "on track but sample size is below recommended threshold"
        else:
            evaluation_status = "at_risk"
            notes = None if sample_size_sufficient else "below target trajectory and sample size is below recommended threshold"

        evaluation = GoalEvaluation(
            organization_id=goal.organization_id,
            goal_id=goal.goal_id,
            observation_id=observation.observation_id,
            status=evaluation_status,
            progress_ratio=round(progress_ratio, 4),
            distance_to_target=round(distance_to_target, 4),
            delta_from_baseline=round(delta_from_baseline, 4),
            sample_size_sufficient=sample_size_sufficient,
            freshness_seconds=freshness_seconds,
            notes=notes,
        )
        stored = self._store.save_goal_evaluation(evaluation)
        next_goal_status = goal.status
        if goal.status in _ACTIVE_GOAL_STATUSES:
            next_goal_status = stored.status
        elif goal.status == "completed":
            next_goal_status = "completed"
        updated_goal = goal.model_copy(
            update={
                "latest_evaluation_id": stored.evaluation_id,
                "status": next_goal_status,
                "updated_at": utc_now(),
            }
        )
        self._store.save_goal(updated_goal)
        return stored

    def generate_insights(
        self,
        *,
        organization_id: str,
        goal_id: str,
        signals: Sequence[InsightSignal],
    ) -> list[InsightItem]:
        goal = self._require_goal(goal_id)
        created: list[InsightItem] = []
        existing_by_identity = {
            _insight_identity(item): item
            for item in self._store.list_insights(organization_id, goal_id=goal_id, limit=500)
        }
        for signal in signals:
            rank_score = round(
                max(signal.occurrence_count, 1) * max(signal.severity, 0.1) * max(signal.metric_relevance, 0.1) * max(signal.freshness_score, 0.1),
                4,
            )
            stale_after = (utc_now() + timedelta(days=7)).replace(microsecond=0)
            evidence_bundle = {
                "examples": list(signal.examples),
                "signal": signal.model_dump(mode="json"),
                **dict(signal.evidence_bundle),
            }
            identity = _insight_identity_from_parts(goal.metric_key, signal.blocker_kind, signal.title)
            existing = existing_by_identity.get(identity)
            if existing is None:
                candidate = InsightItem(
                    organization_id=organization_id,
                    goal_id=goal.goal_id,
                    scope_id=goal.scope_id,
                    metric_key=goal.metric_key,
                    blocker_kind=signal.blocker_kind,
                    title=signal.title,
                    summary=signal.summary,
                    severity=signal.severity,
                    occurrence_count=signal.occurrence_count,
                    rank_score=rank_score,
                    evidence_bundle=evidence_bundle,
                    status="open",
                    stale_after=stale_after,
                )
            else:
                candidate = existing.model_copy(
                    update={
                        "summary": signal.summary,
                        "severity": signal.severity,
                        "occurrence_count": signal.occurrence_count,
                        "rank_score": rank_score,
                        "evidence_bundle": evidence_bundle,
                        "stale_after": stale_after,
                        "updated_at": utc_now(),
                    }
                )
            stored = self._store.save_insight(candidate)
            created.append(stored)
            existing_by_identity[identity] = stored
        created.sort(key=lambda item: (item.rank_score, item.updated_at), reverse=True)
        return created

    def generate_recommendations(
        self,
        *,
        organization_id: str,
        goal_id: str,
        insight_ids: Sequence[str] | None = None,
    ) -> list[RecommendationCandidate]:
        goal = self._require_goal(goal_id)
        baseline = self._require_baseline_snapshot(goal.baseline_snapshot_id)
        definition = self._require_metric_definition(goal.metric_key)
        insight_pool = self._store.list_insights(organization_id, goal_id=goal_id, status="open", limit=500)
        if insight_ids is not None:
            allowed = set(insight_ids)
            insight_pool = [insight for insight in insight_pool if insight.insight_id in allowed]
        existing_by_identity = {
            _recommendation_identity(item): item
            for item in self._store.list_recommendations(organization_id, goal_id=goal_id, limit=500)
        }
        created: list[RecommendationCandidate] = []
        gap = abs(goal.target_value - baseline.value) or max(abs(baseline.value) * 0.05, 1.0)
        sign = 1.0 if definition.direction == "higher_is_better" else -1.0
        for insight in insight_pool:
            category, title, summary, rationale, execution_template = _recommendation_blueprint(insight)
            rank_factor = max(min(insight.rank_score / 4.0, 2.5), 0.5)
            projected_max = round(sign * gap * 0.25 * rank_factor, 4)
            projected_min = round(projected_max * 0.5, 4)
            projected_confidence = round(min(0.9, 0.35 + insight.rank_score * 0.05), 4)
            evidence_bundle = {
                "insight_id": insight.insight_id,
                "blocker_kind": insight.blocker_kind,
                "rank_score": insight.rank_score,
                **dict(insight.evidence_bundle),
            }
            identity = _recommendation_identity_from_parts(goal.goal_id, insight.insight_id, category)
            existing = existing_by_identity.get(identity)
            if existing is None:
                candidate = RecommendationCandidate(
                    organization_id=organization_id,
                    goal_id=goal.goal_id,
                    scope_id=goal.scope_id,
                    metric_key=goal.metric_key,
                    insight_id=insight.insight_id,
                    category=category,
                    title=title,
                    summary=summary,
                    rationale=rationale,
                    projected_impact_min=min(projected_min, projected_max),
                    projected_impact_max=max(projected_min, projected_max),
                    projected_confidence=projected_confidence,
                    evidence_bundle=evidence_bundle,
                    execution_template=execution_template,
                    status="ready_for_review",
                )
            else:
                next_status = (
                    existing.status if existing.status not in {"draft", "ready_for_review"} else "ready_for_review"
                )
                candidate = existing.model_copy(
                    update={
                        "title": title,
                        "summary": summary,
                        "rationale": rationale,
                        "projected_impact_min": min(projected_min, projected_max),
                        "projected_impact_max": max(projected_min, projected_max),
                        "projected_confidence": projected_confidence,
                        "evidence_bundle": evidence_bundle,
                        "execution_template": execution_template,
                        "status": next_status,
                        "updated_at": utc_now(),
                    }
                )
            stored = self._store.save_recommendation(candidate)
            created.append(stored)
            existing_by_identity[identity] = stored
        created.sort(key=lambda item: (item.updated_at, item.recommendation_id), reverse=True)
        return created

    def update_insight_status(self, insight_id: str, *, status: str) -> InsightItem:
        insight = self._require_insight(insight_id)
        allowed = _ALLOWED_INSIGHT_TRANSITIONS.get(insight.status, {insight.status})
        if status not in allowed:
            raise ValueError(f"cannot change insight from {insight.status} to {status}")
        updated = insight.model_copy(update={"status": status, "updated_at": utc_now()})
        return self._store.save_insight(updated)

    def update_recommendation_status(self, recommendation_id: str, *, status: str) -> RecommendationCandidate:
        recommendation = self._require_recommendation(recommendation_id)
        allowed = _ALLOWED_RECOMMENDATION_TRANSITIONS.get(recommendation.status, {recommendation.status})
        if status not in allowed:
            raise ValueError(f"cannot change recommendation from {recommendation.status} to {status}")
        updated = recommendation.model_copy(update={"status": status, "updated_at": utc_now()})
        return self._store.save_recommendation(updated)

    def request_execution_intent(
        self,
        *,
        recommendation_id: str,
        execution_mode: str,
        requested_via: str,
        requested_by: str | None = None,
        approved_payload: dict[str, object] | None = None,
    ) -> ExecutionIntent:
        recommendation = self._require_recommendation(recommendation_id)
        if execution_mode == "apply" and recommendation.status not in {"approved", "execution_requested"}:
            raise ValueError("recommendation must be approved before apply can be requested")
        if execution_mode == "apply":
            self._require_execution_dependencies(recommendation)
        template = dict(recommendation.execution_template or {})
        adapter_kind = str(template.get("adapter_kind") or "template_validation").strip() or "template_validation"
        action_type = str(template.get("action_type") or recommendation.category or "manual_review").strip()
        safety_level = _coerce_safety_level(template.get("safety_level"))
        reversibility = _coerce_reversibility(template.get("reversibility"))
        intent = ExecutionIntent(
            organization_id=recommendation.organization_id,
            recommendation_id=recommendation.recommendation_id,
            goal_id=recommendation.goal_id,
            adapter_kind=adapter_kind,
            action_type=action_type,
            execution_mode=execution_mode,  # type: ignore[arg-type]
            requested_by=requested_by,
            requested_via=requested_via,
            approved_payload=dict(approved_payload or template or {}),
            validation_snapshot={
                "recommendation_status": recommendation.status,
                "execution_template_present": recommendation.execution_template is not None,
            },
            safety_level=safety_level,
            reversibility=reversibility,
        )
        stored = self._store.save_execution_intent(intent)
        if execution_mode == "apply" and recommendation.status == "approved":
            self._store.save_recommendation(
                recommendation.model_copy(update={"status": "execution_requested", "updated_at": utc_now()})
            )
        return stored

    def preview_execution_intent(self, execution_intent_id: str) -> ExecutionResult:
        intent = self._require_execution_intent(execution_intent_id)
        adapter = self._execution_registry.get(intent.adapter_kind)
        if adapter is None:
            outcome = AdapterExecutionOutcome(
                status="preview_failed",
                changed_object_refs=[],
                before_state_summary={},
                after_state_summary={},
                diff_artifact_ref=None,
                adapter_diagnostics={},
                rollback_handle=None,
                error_code="adapter_unavailable",
                error_message=f"no execution adapter registered for {intent.adapter_kind}",
            )
        else:
            outcome = adapter.preview(intent)
        return self._store.save_execution_result(_outcome_to_result(intent=intent, outcome=outcome))

    def apply_execution_intent(self, execution_intent_id: str) -> ExecutionResult:
        intent = self._require_execution_intent(execution_intent_id)
        recommendation = self._require_recommendation(intent.recommendation_id)
        if intent.execution_mode != "apply":
            raise ValueError("execution intent was not created for apply")
        if recommendation.status != "execution_requested":
            raise ValueError("recommendation must be execution_requested before apply can be executed")
        self._require_execution_dependencies(recommendation)
        adapter = self._execution_registry.get(intent.adapter_kind)
        if adapter is None:
            outcome = AdapterExecutionOutcome(
                status="apply_failed",
                changed_object_refs=[],
                before_state_summary={},
                after_state_summary={},
                diff_artifact_ref=None,
                adapter_diagnostics={},
                rollback_handle=None,
                error_code="adapter_unavailable",
                error_message=f"no execution adapter registered for {intent.adapter_kind}",
            )
        else:
            outcome = adapter.apply(intent)
        result = self._store.save_execution_result(_outcome_to_result(intent=intent, outcome=outcome))
        next_status = "executed" if result.status == "apply_succeeded" else "execution_failed"
        self._store.save_recommendation(
            recommendation.model_copy(update={"status": next_status, "updated_at": utc_now()})
        )
        return result

    def _require_execution_dependencies(self, recommendation: RecommendationCandidate) -> None:
        if not recommendation.dependency_ids:
            return

        missing_dependency_ids: list[str] = []
        incomplete_dependency_ids: list[str] = []
        for dependency_id in recommendation.dependency_ids:
            dependency = self._store.get_recommendation(dependency_id)
            if dependency is None:
                missing_dependency_ids.append(dependency_id)
                continue
            if dependency_id == recommendation.recommendation_id:
                raise ValueError("recommendation cannot depend on itself")
            if dependency.organization_id != recommendation.organization_id:
                raise ValueError("recommendation dependencies must be within the same organization")
            if dependency.goal_id != recommendation.goal_id:
                raise ValueError("recommendation dependencies must be within the same goal")
            if dependency.status != "executed":
                incomplete_dependency_ids.append(dependency_id)

        if missing_dependency_ids:
            raise ValueError(f"missing recommendation dependencies: {', '.join(missing_dependency_ids)}")
        if incomplete_dependency_ids:
            raise ValueError(
                f"dependencies must be executed before application: {', '.join(incomplete_dependency_ids)}"
            )

    def create_experiment(
        self,
        *,
        organization_id: str,
        scope_id: str,
        primary_metric_key: str,
        name: str,
        hypothesis: str,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        notes: str | None = None,
    ) -> KPIExperiment:
        self._require_scope(scope_id)
        self._require_metric_definition(primary_metric_key)
        if goal_id is not None:
            self._require_goal(goal_id)
        if recommendation_id is not None:
            self._require_recommendation(recommendation_id)
        experiment = KPIExperiment(
            organization_id=organization_id,
            goal_id=goal_id,
            recommendation_id=recommendation_id,
            name=name,
            hypothesis=hypothesis,
            primary_metric_key=primary_metric_key,
            scope_id=scope_id,
            notes=notes,
        )
        return self._store.save_experiment(experiment)

    def update_experiment_status(
        self,
        experiment_id: str,
        *,
        status: str,
        notes: str | None = None,
    ) -> KPIExperiment:
        experiment = self._require_experiment(experiment_id)
        now = utc_now()
        update: dict[str, object] = {"status": status, "updated_at": now}
        if notes is not None:
            update["notes"] = notes
        if status == "running" and experiment.started_at is None:
            update["started_at"] = now
        if status in {"completed", "aborted"}:
            update["ended_at"] = now
        return self._store.save_experiment(experiment.model_copy(update=update))

    def record_impact_assessment(
        self,
        *,
        organization_id: str,
        metric_key: str,
        scope_id: str,
        baseline_observation_id: str,
        comparison_observation_id: str,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        execution_intent_id: str | None = None,
        experiment_id: str | None = None,
        attribution_mode: AttributionMode = "uncontrolled_observation",
        attribution_confidence: AttributionConfidence = "weak",
        attributed_change: float | None = None,
        competing_changes: Sequence[str] | None = None,
        notes: str | None = None,
    ) -> ImpactAssessment:
        baseline = self._require_observation(baseline_observation_id)
        comparison = self._require_observation(comparison_observation_id)
        if baseline.organization_id != organization_id or comparison.organization_id != organization_id:
            raise ValueError("impact assessment observations must belong to organization")
        if baseline.metric_key != metric_key or comparison.metric_key != metric_key:
            raise ValueError("impact assessment observations must match metric")
        if baseline.scope_id != scope_id or comparison.scope_id != scope_id:
            raise ValueError("impact assessment observations must match scope")
        recommendation = None if recommendation_id is None else self._require_recommendation(recommendation_id)
        if execution_intent_id is not None:
            intent = self._require_execution_intent(execution_intent_id)
            if recommendation is not None and intent.recommendation_id != recommendation.recommendation_id:
                raise ValueError("execution intent does not match recommendation")
        if experiment_id is not None:
            self._require_experiment(experiment_id)
        observed_change = round(comparison.value - baseline.value, 4)
        if attributed_change is None and attribution_confidence in {"strong", "experiment_validated"} and attribution_mode != "uncontrolled_observation":
            attributed_change = observed_change
        assessment = ImpactAssessment(
            organization_id=organization_id,
            goal_id=goal_id,
            recommendation_id=recommendation_id,
            execution_intent_id=execution_intent_id,
            experiment_id=experiment_id,
            metric_key=metric_key,
            scope_id=scope_id,
            baseline_observation_id=baseline_observation_id,
            comparison_observation_id=comparison_observation_id,
            attribution_mode=attribution_mode,
            attribution_confidence=attribution_confidence,
            observed_change=observed_change,
            attributed_change=attributed_change,
            projected_impact_min=None if recommendation is None else recommendation.projected_impact_min,
            projected_impact_max=None if recommendation is None else recommendation.projected_impact_max,
            attainment_fraction=_calculate_attainment_fraction(
                recommendation=recommendation,
                change=attributed_change if attributed_change is not None else observed_change,
            ),
            competing_changes=list(competing_changes or []),
            notes=notes,
        )
        return self._store.save_impact_assessment(assessment)

    def _require_metric_definition(self, metric_key: str) -> MetricDefinition:
        definition = self.get_metric_definition(metric_key)
        if definition is None:
            raise ValueError(f"unknown KPI metric: {metric_key}")
        return definition

    def _require_scope(self, scope_id: str) -> MetricScope:
        scope = self._store.get_scope(scope_id)
        if scope is None:
            raise ValueError(f"scope {scope_id} not found")
        return scope

    def _require_observation(self, observation_id: str) -> MetricObservation:
        observation = self._store.get_observation(observation_id)
        if observation is None:
            raise ValueError(f"observation {observation_id} not found")
        return observation

    def _require_baseline_snapshot(self, baseline_snapshot_id: str) -> BaselineSnapshot:
        snapshot = self._store.get_baseline_snapshot(baseline_snapshot_id)
        if snapshot is None:
            raise ValueError(f"baseline snapshot {baseline_snapshot_id} not found")
        return snapshot

    def _require_goal(self, goal_id: str) -> Goal:
        goal = self._store.get_goal(goal_id)
        if goal is None:
            raise ValueError(f"goal {goal_id} not found")
        return goal

    def _require_insight(self, insight_id: str) -> InsightItem:
        insight = self._store.get_insight(insight_id)
        if insight is None:
            raise ValueError(f"insight {insight_id} not found")
        return insight

    def _require_recommendation(self, recommendation_id: str) -> RecommendationCandidate:
        recommendation = self._store.get_recommendation(recommendation_id)
        if recommendation is None:
            raise ValueError(f"recommendation {recommendation_id} not found")
        return recommendation

    def _require_execution_intent(self, execution_intent_id: str) -> ExecutionIntent:
        intent = self._store.get_execution_intent(execution_intent_id)
        if intent is None:
            raise ValueError(f"execution intent {execution_intent_id} not found")
        return intent

    def _require_experiment(self, experiment_id: str) -> KPIExperiment:
        experiment = self._store.get_experiment(experiment_id)
        if experiment is None:
            raise ValueError(f"experiment {experiment_id} not found")
        return experiment


class KPIReadService:
    def __init__(self, store: KPIStore) -> None:
        self._store = store

    def list_goal_summaries(
        self,
        organization_id: str,
        *,
        scope_id: str | None = None,
        status: str | None = None,
    ) -> list[GoalSummaryReadModel]:
        goals = self._store.list_goals(organization_id, scope_id=scope_id, status=status)
        summaries: list[GoalSummaryReadModel] = []
        for goal in goals:
            baseline = self._store.get_baseline_snapshot(goal.baseline_snapshot_id)
            latest_observation = self._store.get_latest_observation(goal.organization_id, goal.metric_key, goal.scope_id)
            latest_evaluation = (
                self._store.get_latest_goal_evaluation(goal.goal_id)
                if goal.latest_evaluation_id is None
                else next(
                    (
                        evaluation
                        for evaluation in self._store.list_goal_evaluations(goal.goal_id, limit=25)
                        if evaluation.evaluation_id == goal.latest_evaluation_id
                    ),
                    self._store.get_latest_goal_evaluation(goal.goal_id),
                )
            )
            open_insights = self._store.list_insights(goal.organization_id, goal_id=goal.goal_id, status="open", limit=500)
            reviewable_recommendations = self._store.list_recommendations(
                goal.organization_id,
                goal_id=goal.goal_id,
                limit=500,
            )
            pending_recommendation_count = sum(
                1
                for recommendation in reviewable_recommendations
                if recommendation.status in {"draft", "ready_for_review", "approved", "execution_requested"}
            )
            summaries.append(
                GoalSummaryReadModel(
                    goal_id=goal.goal_id,
                    name=goal.name,
                    metric_key=goal.metric_key,
                    scope_id=goal.scope_id,
                    status=goal.status,
                    target_value=goal.target_value,
                    baseline_value=baseline.value if baseline is not None else 0.0,
                    current_value=None if latest_observation is None else latest_observation.value,
                    progress_ratio=None if latest_evaluation is None else latest_evaluation.progress_ratio,
                    latest_observation_at=None if latest_observation is None else latest_observation.period_end,
                    latest_evaluation_at=None if latest_evaluation is None else latest_evaluation.created_at,
                    open_insight_count=len(open_insights),
                    pending_recommendation_count=pending_recommendation_count,
                )
            )
        return summaries

    def get_goal_detail(self, goal_id: str) -> GoalDetailReadModel | None:
        goal = self._store.get_goal(goal_id)
        if goal is None:
            return None
        scope = self._store.get_scope(goal.scope_id)
        baseline = self._store.get_baseline_snapshot(goal.baseline_snapshot_id)
        if scope is None or baseline is None:
            return None
        return GoalDetailReadModel(
            goal=goal,
            scope=scope,
            baseline_snapshot=baseline,
            latest_observation=self._store.get_latest_observation(goal.organization_id, goal.metric_key, goal.scope_id),
            latest_evaluation=self._store.get_latest_goal_evaluation(goal.goal_id),
            insights=self._store.list_insights(goal.organization_id, goal_id=goal.goal_id, limit=100),
            recommendations=self._store.list_recommendations(goal.organization_id, goal_id=goal.goal_id, limit=100),
            execution_intents=self._store.list_execution_intents(goal.organization_id, goal_id=goal.goal_id, limit=100),
            execution_results=_goal_execution_results(self._store, goal.organization_id, goal.goal_id),
            experiments=self._store.list_experiments(goal.organization_id, goal_id=goal.goal_id, limit=100),
            impact_assessments=self._store.list_impact_assessments(goal.organization_id, goal_id=goal.goal_id, limit=100),
        )


def _scope_fingerprint(*, organization_id: str, payload: dict[str, object]) -> str:
    normalized = {
        "organization_id": organization_id,
        **payload,
    }
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_target_direction(definition: MetricDefinition, baseline_value: float, target_value: float) -> None:
    if baseline_value == target_value:
        raise ValueError("target value must differ from baseline value")
    if definition.direction == "higher_is_better" and target_value <= baseline_value:
        raise ValueError("target value must be higher than baseline for this KPI")
    if definition.direction == "lower_is_better" and target_value >= baseline_value:
        raise ValueError("target value must be lower than baseline for this KPI")


def _safe_progress(*, numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 1.0
    return max(0.0, numerator / denominator)


def _insight_identity(item: InsightItem) -> str:
    return _insight_identity_from_parts(item.metric_key, item.blocker_kind, item.title)


def _insight_identity_from_parts(metric_key: str, blocker_kind: str, title: str) -> str:
    return f"{metric_key}:{blocker_kind.strip().lower()}:{title.strip().lower()}"


def _recommendation_identity(item: RecommendationCandidate) -> str:
    return _recommendation_identity_from_parts(item.goal_id, item.insight_id, item.category)


def _recommendation_identity_from_parts(goal_id: str | None, insight_id: str | None, category: str) -> str:
    return f"{goal_id or '-'}:{insight_id or '-'}:{category.strip().lower()}"


def _recommendation_blueprint(insight: InsightItem) -> tuple[str, str, str, str, dict[str, object] | None]:
    blocker = insight.blocker_kind.lower()
    if "knowledge" in blocker or "faq" in blocker or "gap" in blocker:
        return (
            "knowledge",
            f"Close the knowledge gap behind {insight.title}",
            "Package the missing knowledge or guidance causing repeat failures and attach it through the knowledge adapter.",
            f"This recommendation is grounded in the repeated knowledge gap signaled by '{insight.title}'.",
            {
                "adapter_kind": "knowledge_adapter",
                "action_type": "attach_knowledge_pack",
                "safety_level": "medium",
                "reversibility": "reversible",
            },
        )
    if "transfer" in blocker or "escalat" in blocker or "handoff" in blocker:
        return (
            "workflow",
            f"Reduce avoidable handoffs linked to {insight.title}",
            "Review the workflow branch or escalation trigger creating repeat human transfers and prepare a bounded workflow change.",
            f"The KPI gap appears to be driven by a repeated escalation pattern captured in '{insight.title}'.",
            None,
        )
    if "tool" in blocker or "error" in blocker or "failure" in blocker:
        return (
            "tooling",
            f"Stabilize tool behavior behind {insight.title}",
            "Harden the failing tool path, add fallback behavior, or reduce dependency on the error-prone call sequence.",
            f"The insight indicates repeated tool instability contributing to KPI drag in '{insight.title}'.",
            None,
        )
    if "latency" in blocker or "handle_time" in blocker or "duration" in blocker:
        return (
            "agent_config",
            f"Shorten response path contributing to {insight.title}",
            "Tighten bounded agent configuration, response policy, or prompt verbosity to remove avoidable delay.",
            f"The observed latency pattern in '{insight.title}' suggests a bounded config change can improve throughput.",
            {
                "adapter_kind": "agent_config_adapter",
                "action_type": "update_bounded_fields",
                "safety_level": "low",
                "reversibility": "reversible",
            },
        )
    return (
        "policy",
        f"Review operating policy around {insight.title}",
        "Translate the repeated blocker into an explicit operating rule, guardrail, or fallback policy before broader changes.",
        f"The repeated blocker described by '{insight.title}' warrants a controlled policy-level response.",
        None,
    )


def _coerce_safety_level(value: object) -> ExecutionSafetyLevel:
    candidate = str(value or "medium").strip().lower()
    if candidate not in {"low", "medium", "high"}:
        return "medium"
    return candidate  # type: ignore[return-value]


def _coerce_reversibility(value: object) -> ExecutionReversibility:
    candidate = str(value or "unknown").strip().lower()
    if candidate == "not_reversible":
        candidate = "irreversible"
    if candidate not in {"reversible", "irreversible", "unknown"}:
        return "unknown"
    return candidate  # type: ignore[return-value]


def _outcome_to_result(*, intent: ExecutionIntent, outcome: AdapterExecutionOutcome) -> ExecutionResult:
    return ExecutionResult(
        organization_id=intent.organization_id,
        execution_intent_id=intent.execution_intent_id,
        status=outcome.status,  # type: ignore[arg-type]
        changed_object_refs=list(outcome.changed_object_refs),
        before_state_summary=dict(outcome.before_state_summary),
        after_state_summary=dict(outcome.after_state_summary),
        diff_artifact_ref=outcome.diff_artifact_ref,
        adapter_diagnostics=dict(outcome.adapter_diagnostics),
        rollback_handle=None if outcome.rollback_handle is None else dict(outcome.rollback_handle),
        error_code=outcome.error_code,
        error_message=outcome.error_message,
    )


def _calculate_attainment_fraction(
    *,
    recommendation: RecommendationCandidate | None,
    change: float,
) -> float | None:
    if recommendation is None:
        return None
    midpoint = (recommendation.projected_impact_min + recommendation.projected_impact_max) / 2.0
    if midpoint == 0:
        return None
    return round(change / midpoint, 4)


def _goal_execution_results(store: KPIStore, organization_id: str, goal_id: str) -> list[ExecutionResult]:
    intents = store.list_execution_intents(organization_id, goal_id=goal_id, limit=200)
    results: list[ExecutionResult] = []
    for intent in intents:
        latest = store.get_latest_execution_result(intent.execution_intent_id)
        if latest is not None:
            results.append(latest)
    results.sort(key=lambda item: (item.created_at, item.execution_result_id), reverse=True)
    return results
