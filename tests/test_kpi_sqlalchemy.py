from __future__ import annotations

from datetime import timedelta

from ruhu.db import build_session_factory
from ruhu.kpi import KPIReadService, KPIService, RecommendationCandidate, SQLAlchemyKPIStore, InsightSignal
from ruhu.kpi.models import utc_now


def test_sqlalchemy_kpi_store_round_trips_measurement_and_recommendation_domain(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyKPIStore(session_factory)
    service = KPIService(store)
    read_service = KPIReadService(store)

    scope = service.ensure_scope(
        organization_id="org-sql-kpi",
        scope_kind="channel",
        channel="web_chat",
        display_name="Web chat",
    )
    duplicate = service.ensure_scope(
        organization_id="org-sql-kpi",
        scope_kind="channel",
        channel="web_chat",
    )
    assert duplicate.scope_id == scope.scope_id

    baseline = service.record_observation(
        organization_id="org-sql-kpi",
        metric_key="cost_per_conversation",
        scope_id=scope.scope_id,
        value=2.4,
        sample_size=25,
        confidence=0.88,
        period_start=utc_now() - timedelta(days=30),
        period_end=utc_now() - timedelta(days=5),
        source_summary={"sources": ["provider_cost_records", "tool_invocations", "conversations"]},
    )
    goal = service.create_goal(
        organization_id="org-sql-kpi",
        metric_key="cost_per_conversation",
        scope_id=scope.scope_id,
        name="Lower web-chat cost",
        target_value=1.5,
        target_at=utc_now() + timedelta(days=40),
    )
    current = service.record_observation(
        organization_id="org-sql-kpi",
        metric_key="cost_per_conversation",
        scope_id=scope.scope_id,
        value=1.8,
        sample_size=28,
        confidence=0.9,
        period_start=utc_now() - timedelta(days=4),
        period_end=utc_now(),
        source_summary={"sources": ["provider_cost_records"]},
    )
    evaluation = service.evaluate_goal(goal.goal_id, observation_id=current.observation_id)
    assert evaluation.status == "on_track"

    insights = service.generate_insights(
        organization_id="org-sql-kpi",
        goal_id=goal.goal_id,
        signals=[
            InsightSignal(
                blocker_kind="knowledge_gap",
                title="Repeated unsupported refund questions",
                summary="Refund questions trigger unnecessary tool calls and longer conversations.",
                severity=1.3,
                occurrence_count=3,
                metric_relevance=1.1,
                freshness_score=1.0,
            )
        ],
    )
    recommendations = service.generate_recommendations(
        organization_id="org-sql-kpi",
        goal_id=goal.goal_id,
    )
    assert len(insights) == 1
    assert len(recommendations) == 1
    assert recommendations[0].category == "knowledge"
    assert recommendations[0].execution_template is not None

    approved = service.update_recommendation_status(recommendations[0].recommendation_id, status="approved")
    preview_intent = service.request_execution_intent(
        recommendation_id=approved.recommendation_id,
        execution_mode="preview",
        requested_via="test",
    )
    preview_result = service.preview_execution_intent(preview_intent.execution_intent_id)
    assert preview_result.status == "preview_failed"

    apply_intent = service.request_execution_intent(
        recommendation_id=approved.recommendation_id,
        execution_mode="apply",
        requested_via="test",
    )
    apply_result = service.apply_execution_intent(apply_intent.execution_intent_id)
    assert apply_result.status == "apply_failed"

    experiment = service.create_experiment(
        organization_id="org-sql-kpi",
        scope_id=scope.scope_id,
        primary_metric_key="cost_per_conversation",
        goal_id=goal.goal_id,
        recommendation_id=approved.recommendation_id,
        name="Refund knowledge pack canary",
        hypothesis="Adding vetted refund guidance reduces unnecessary provider spend.",
    )
    assert service.update_experiment_status(experiment.experiment_id, status="running").status == "running"

    assessment = service.record_impact_assessment(
        organization_id="org-sql-kpi",
        metric_key="cost_per_conversation",
        scope_id=scope.scope_id,
        goal_id=goal.goal_id,
        recommendation_id=approved.recommendation_id,
        execution_intent_id=apply_intent.execution_intent_id,
        experiment_id=experiment.experiment_id,
        baseline_observation_id=baseline.observation_id,
        comparison_observation_id=current.observation_id,
        attribution_mode="manual_judgment",
        attribution_confidence="moderate",
        attributed_change=-0.4,
    )
    assert assessment.attributed_change == -0.4

    summary = read_service.list_goal_summaries("org-sql-kpi")
    assert len(summary) == 1
    assert summary[0].status == "on_track"
    assert summary[0].pending_recommendation_count == 0

    detail = read_service.get_goal_detail(goal.goal_id)
    assert detail is not None
    assert detail.goal.goal_id == goal.goal_id
    assert detail.latest_observation is not None
    assert detail.latest_observation.observation_id == current.observation_id
    assert len(detail.recommendations) == 1
    assert len(detail.execution_intents) == 2
    assert len(detail.execution_results) == 2
    assert len(detail.experiments) == 1
    assert len(detail.impact_assessments) == 1

    org_scope = service.ensure_scope(
        organization_id="org-sql-kpi",
        scope_kind="organization",
        display_name="Org-wide costs",
    )
    org_recommendation = store.save_recommendation(
        RecommendationCandidate(
            organization_id="org-sql-kpi",
            goal_id=None,
            scope_id=org_scope.scope_id,
            metric_key="cost_per_conversation",
            category="knowledge",
            title="Vetted refund response pack",
            summary="Reduce repeated follow-up traffic with a curated refund knowledge pack.",
            rationale="The cost signal is concentrated in refund-related exchanges.",
            projected_impact_min=-0.15,
            projected_impact_max=-0.05,
            projected_confidence=0.55,
            status="approved",
        )
    )
    org_intent = service.request_execution_intent(
        recommendation_id=org_recommendation.recommendation_id,
        execution_mode="preview",
        requested_via="test",
    )
    assert org_intent.goal_id is None
