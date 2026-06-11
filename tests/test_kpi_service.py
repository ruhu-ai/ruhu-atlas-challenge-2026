from __future__ import annotations

from datetime import timedelta

import pytest

from ruhu.db import RUNTIME_TENANT_RLS_TABLES
from ruhu.kpi import KPIReadService, KPIService, InMemoryKPIStore, InsightSignal, RecommendationCandidate
from ruhu.kpi.models import utc_now


def test_kpi_service_creates_scope_goal_evaluation_and_read_models() -> None:
    store = InMemoryKPIStore()
    service = KPIService(store)
    read_service = KPIReadService(store)

    scope = service.ensure_scope(
        organization_id="org-kpi",
        scope_kind="channel",
        channel="web_widget",
        display_name="Website widget",
    )
    duplicate_scope = service.ensure_scope(
        organization_id="org-kpi",
        scope_kind="channel",
        channel="web_widget",
    )
    assert duplicate_scope.scope_id == scope.scope_id

    baseline_observation = service.record_observation(
        organization_id="org-kpi",
        metric_key="deflection_rate",
        scope_id=scope.scope_id,
        value=55.0,
        sample_size=40,
        confidence=0.92,
        period_start=utc_now() - timedelta(days=30),
        period_end=utc_now() - timedelta(days=1),
        source_summary={"sources": ["conversations", "realtime_events"]},
    )

    goal = service.create_goal(
        organization_id="org-kpi",
        metric_key="deflection_rate",
        scope_id=scope.scope_id,
        name="Improve web deflection",
        target_value=70.0,
        target_at=utc_now() + timedelta(days=45),
    )
    assert goal.status == "active"

    current_observation = service.record_observation(
        organization_id="org-kpi",
        metric_key="deflection_rate",
        scope_id=scope.scope_id,
        value=66.0,
        sample_size=55,
        confidence=0.95,
        period_start=utc_now() - timedelta(days=7),
        period_end=utc_now(),
        source_summary={"sources": ["conversations"]},
    )

    evaluation = service.evaluate_goal(goal.goal_id, observation_id=current_observation.observation_id)
    assert evaluation.status == "on_track"
    assert evaluation.progress_ratio > 0.7

    summaries = read_service.list_goal_summaries("org-kpi")
    assert len(summaries) == 1
    assert summaries[0].baseline_value == baseline_observation.value
    assert summaries[0].current_value == current_observation.value
    assert summaries[0].status == "on_track"

    detail = read_service.get_goal_detail(goal.goal_id)
    assert detail is not None
    assert detail.scope.channel == "web_widget"
    assert detail.latest_evaluation is not None
    assert detail.latest_evaluation.evaluation_id == evaluation.evaluation_id


def test_kpi_service_generates_stable_insights_recommendations_and_impact_assessment() -> None:
    store = InMemoryKPIStore()
    service = KPIService(store)
    read_service = KPIReadService(store)

    scope = service.ensure_scope(
        organization_id="org-risk",
        scope_kind="agent",
        agent_id="agent-1",
        channel="web_chat",
    )
    baseline_observation = service.record_observation(
        organization_id="org-risk",
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        value=35.0,
        sample_size=60,
        confidence=0.91,
        period_start=utc_now() - timedelta(days=30),
        period_end=utc_now() - timedelta(days=20),
        source_summary={"sources": ["conversations", "turn_traces"]},
    )
    goal = service.create_goal(
        organization_id="org-risk",
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        name="Reduce transfers",
        target_value=20.0,
        target_at=utc_now() + timedelta(days=30),
    )

    first_insights = service.generate_insights(
        organization_id="org-risk",
        goal_id=goal.goal_id,
        signals=[
            InsightSignal(
                blocker_kind="transfer_escalation",
                title="Repeat escalation on billing questions",
                summary="Billing intents repeatedly escalate after the first fallback.",
                severity=1.6,
                occurrence_count=5,
                metric_relevance=1.2,
                freshness_score=1.0,
                evidence_bundle={"counts": {"billing_escalations": 5}},
                examples=["Need a human for billing", "Agent escalated after fallback"],
            )
        ],
    )
    second_insights = service.generate_insights(
        organization_id="org-risk",
        goal_id=goal.goal_id,
        signals=[
            InsightSignal(
                blocker_kind="transfer_escalation",
                title="Repeat escalation on billing questions",
                summary="Billing escalation remains the top blocker this week.",
                severity=1.4,
                occurrence_count=4,
                metric_relevance=1.2,
                freshness_score=0.9,
            )
        ],
    )
    assert len(first_insights) == 1
    assert first_insights[0].insight_id == second_insights[0].insight_id

    recommendations = service.generate_recommendations(
        organization_id="org-risk",
        goal_id=goal.goal_id,
    )
    assert len(recommendations) == 1
    assert recommendations[0].status == "ready_for_review"
    assert recommendations[0].category == "workflow"
    assert recommendations[0].execution_template is None

    approved = service.update_recommendation_status(
        recommendations[0].recommendation_id,
        status="approved",
    )
    preview_intent = service.request_execution_intent(
        recommendation_id=approved.recommendation_id,
        execution_mode="preview",
        requested_via="test",
    )
    preview_result = service.preview_execution_intent(preview_intent.execution_intent_id)
    assert preview_result.status == "preview_failed"
    assert preview_result.error_code == "adapter_unavailable"

    apply_intent = service.request_execution_intent(
        recommendation_id=approved.recommendation_id,
        execution_mode="apply",
        requested_via="test",
    )
    apply_result = service.apply_execution_intent(apply_intent.execution_intent_id)
    assert apply_result.status == "apply_failed"
    assert apply_result.error_code == "adapter_unavailable"

    experiment = service.create_experiment(
        organization_id="org-risk",
        scope_id=scope.scope_id,
        primary_metric_key="transfer_rate",
        goal_id=goal.goal_id,
        recommendation_id=approved.recommendation_id,
        name="Billing escalation canary",
        hypothesis="Tightening the billing fallback branch reduces transfers without harming completion.",
    )
    running_experiment = service.update_experiment_status(experiment.experiment_id, status="running")
    completed_experiment = service.update_experiment_status(running_experiment.experiment_id, status="completed")
    assert completed_experiment.status == "completed"
    assert completed_experiment.started_at is not None
    assert completed_experiment.ended_at is not None

    improved_observation = service.record_observation(
        organization_id="org-risk",
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        value=22.0,
        sample_size=58,
        confidence=0.9,
        period_start=utc_now() - timedelta(days=10),
        period_end=utc_now(),
        source_summary={"sources": ["conversations"]},
    )
    service.evaluate_goal(goal.goal_id, observation_id=improved_observation.observation_id)

    assessment = service.record_impact_assessment(
        organization_id="org-risk",
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        goal_id=goal.goal_id,
        recommendation_id=approved.recommendation_id,
        execution_intent_id=apply_intent.execution_intent_id,
        experiment_id=completed_experiment.experiment_id,
        baseline_observation_id=baseline_observation.observation_id,
        comparison_observation_id=improved_observation.observation_id,
        attribution_mode="sequential_rollout",
        attribution_confidence="strong",
        notes="Observed reduction after recommended workflow review.",
    )
    assert assessment.observed_change == -13.0
    assert assessment.attributed_change == -13.0
    assert assessment.execution_intent_id == apply_intent.execution_intent_id
    assert assessment.experiment_id == completed_experiment.experiment_id
    assert assessment.projected_impact_min is not None

    detail = read_service.get_goal_detail(goal.goal_id)
    assert detail is not None
    assert len(detail.insights) == 1
    assert len(detail.recommendations) == 1
    assert len(detail.execution_intents) == 2
    assert len(detail.execution_results) == 2
    assert len(detail.experiments) == 1
    assert len(detail.impact_assessments) == 1


def test_kpi_execution_intent_allows_goal_less_recommendations_and_rls_tables_cover_kpi_execution() -> None:
    store = InMemoryKPIStore()
    service = KPIService(store)

    scope = service.ensure_scope(
        organization_id="org-ops",
        scope_kind="organization",
        display_name="Org-wide KPI scope",
    )
    recommendation = store.save_recommendation(
        RecommendationCandidate(
            organization_id="org-ops",
            goal_id=None,
            scope_id=scope.scope_id,
            metric_key="cost_per_conversation",
            category="knowledge",
            title="Publish refund guidance",
            summary="Reduce repeated refund escalations with a vetted answer pack.",
            rationale="Observed cost growth comes from avoidable repeat lookup attempts.",
            projected_impact_min=-0.2,
            projected_impact_max=-0.1,
            projected_confidence=0.6,
            status="approved",
        )
    )

    intent = service.request_execution_intent(
        recommendation_id=recommendation.recommendation_id,
        execution_mode="preview",
        requested_via="test",
    )

    assert intent.goal_id is None
    assert "kpi_execution_intents" in RUNTIME_TENANT_RLS_TABLES
    assert "kpi_execution_results" in RUNTIME_TENANT_RLS_TABLES
    assert "kpi_experiments" in RUNTIME_TENANT_RLS_TABLES


def test_generate_insights_and_recommendations_preserve_existing_terminal_statuses() -> None:
    store = InMemoryKPIStore()
    service = KPIService(store)

    scope = service.ensure_scope(
        organization_id="org-preserve",
        scope_kind="agent",
        agent_id="agent-1",
        channel="web_chat",
    )
    service.record_observation(
        organization_id="org-preserve",
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        value=40.0,
        sample_size=30,
        confidence=0.9,
        period_start=utc_now() - timedelta(days=30),
        period_end=utc_now() - timedelta(days=1),
        source_summary={"sources": ["conversations"]},
    )
    goal = service.create_goal(
        organization_id="org-preserve",
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        name="Reduce transfers",
        target_value=15.0,
        target_at=utc_now() + timedelta(days=20),
    )

    signals = [
        InsightSignal(
            blocker_kind="knowledge_gap",
            title="Recurring escalation pattern",
            summary="Pattern identified.",
            severity=1.2,
            occurrence_count=6,
            metric_relevance=1.1,
            freshness_score=0.9,
        ),
        InsightSignal(
            blocker_kind="transfer_escalation",
            title="Open transfer pattern",
            summary="Pattern still open.",
            severity=1.1,
            occurrence_count=4,
            metric_relevance=1.0,
            freshness_score=0.8,
        ),
    ]
    initial_insights = service.generate_insights(
        organization_id="org-preserve",
        goal_id=goal.goal_id,
        signals=signals,
    )
    assert len(initial_insights) == 2
    assert initial_insights[0].status == "open"
    accepted_target = next(item for item in initial_insights if item.title == "Recurring escalation pattern")
    open_target = next(item for item in initial_insights if item.title == "Open transfer pattern")

    accepted_insight = service.update_insight_status(accepted_target.insight_id, status="accepted")
    assert accepted_insight.status == "accepted"

    regenerated_insights = service.generate_insights(organization_id="org-preserve", goal_id=goal.goal_id, signals=signals)
    accepted_again = next(item for item in regenerated_insights if item.insight_id == accepted_insight.insight_id)
    open_again = next(item for item in regenerated_insights if item.insight_id == open_target.insight_id)
    assert accepted_again.status == "accepted"
    assert open_again.status == "open"

    first_recommendations = service.generate_recommendations(
        organization_id="org-preserve",
        goal_id=goal.goal_id,
        insight_ids=[open_again.insight_id],
    )
    assert len(first_recommendations) == 1

    rejected_recommendation = service.update_recommendation_status(first_recommendations[0].recommendation_id, status="rejected")
    assert rejected_recommendation.status == "rejected"

    regenerated_recommendations = service.generate_recommendations(organization_id="org-preserve", goal_id=goal.goal_id)
    assert regenerated_recommendations[0].recommendation_id == rejected_recommendation.recommendation_id
    assert regenerated_recommendations[0].status == "rejected"


def test_apply_execution_intent_requires_dependency_recommendations_to_be_executed() -> None:
    store = InMemoryKPIStore()
    service = KPIService(store)

    scope = service.ensure_scope(
        organization_id="org-deps",
        scope_kind="agent",
        agent_id="agent-1",
        channel="web_chat",
    )
    service.record_observation(
        organization_id="org-deps",
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        value=35.0,
        sample_size=40,
        confidence=0.9,
        period_start=utc_now() - timedelta(days=30),
        period_end=utc_now() - timedelta(days=1),
        source_summary={"sources": ["conversations"]},
    )
    goal = service.create_goal(
        organization_id="org-deps",
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        name="Reduce transfers",
        target_value=20.0,
        target_at=utc_now() + timedelta(days=20),
    )
    dependency = store.save_recommendation(
        RecommendationCandidate(
            organization_id="org-deps",
            goal_id=goal.goal_id,
            scope_id=scope.scope_id,
            metric_key="transfer_rate",
            category="knowledge",
            title="Base blocker",
            summary="A prerequisite optimization.",
            rationale="Executed baseline prerequisite.",
            projected_impact_min=-5.0,
            projected_impact_max=-3.0,
            projected_confidence=0.6,
            status="ready_for_review",
        )
    )
    dependent = store.save_recommendation(
        RecommendationCandidate(
            organization_id="org-deps",
            goal_id=goal.goal_id,
            scope_id=scope.scope_id,
            metric_key="transfer_rate",
            category="knowledge",
            title="Dependent blocker",
            summary="Runs after prerequisite.",
            rationale="Requires the base blocker to execute first.",
            projected_impact_min=-4.0,
            projected_impact_max=-2.0,
            projected_confidence=0.7,
            dependency_ids=[dependency.recommendation_id],
            status="approved",
        )
    )

    with pytest.raises(ValueError, match="dependencies must be executed before application"):
        service.request_execution_intent(recommendation_id=dependent.recommendation_id, execution_mode="apply", requested_via="test")

    store.save_recommendation(dependency.model_copy(update={"status": "executed"}))

    intent = service.request_execution_intent(recommendation_id=dependent.recommendation_id, execution_mode="apply", requested_via="test")
    updated_dependent = store.get_recommendation(dependent.recommendation_id)
    assert updated_dependent is not None
    assert updated_dependent.status == "execution_requested"
    assert intent.execution_intent_id != ""
