from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from pydantic import BaseModel, Field

from ruhu.agent_review import PublishQualificationSummary, PublishReviewItem
from ruhu.registry import AgentVersionSnapshot

from .assertions import collect_fixture_validation_issues
from .models import EvaluationPolicyConfig, EvaluationRun, FixtureValidationIssue, SimulationFixture


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


EvaluationPolicy = EvaluationPolicyConfig


class RunQualificationDecision(BaseModel):
    qualifies: bool
    blocker_failure_count: int = 0
    warning_failure_count: int = 0
    evaluation_blockers: list[PublishReviewItem] = Field(default_factory=list)


def active_fixtures(fixtures: Iterable[SimulationFixture]) -> list[SimulationFixture]:
    return [fixture for fixture in fixtures if fixture.is_active]


def required_fixtures(fixtures: Iterable[SimulationFixture]) -> list[SimulationFixture]:
    return [fixture for fixture in fixtures if fixture.is_active and fixture.gate_required]


def qualification_policy(
    *,
    minimum_pass_rate_ratio: float = 1.0,
    allow_warning_failures: bool = True,
    max_qualified_run_age_hours: int | None = None,
) -> EvaluationPolicy:
    return EvaluationPolicy(
        minimum_pass_rate_ratio=minimum_pass_rate_ratio,
        allow_warning_failures=allow_warning_failures,
        max_qualified_run_age_hours=max_qualified_run_age_hours,
    )


def run_qualifies(run: EvaluationRun, *, policy: EvaluationPolicy) -> bool:
    return qualification_decision(run, policy=policy).qualifies


def qualification_decision(run: EvaluationRun, *, policy: EvaluationPolicy) -> RunQualificationDecision:
    blocker_failure_count = sum(result.blocker_failures for result in run.results)
    warning_failure_count = sum(result.warning_failures for result in run.results)
    blockers: list[PublishReviewItem] = []
    if run.status != "completed":
        blockers.append(
            PublishReviewItem(
                severity="error",
                code="evaluation.run_not_completed",
                message=f"Evaluation run finished in status {run.status!r} instead of 'completed'.",
            )
        )
    if blocker_failure_count:
        blockers.append(
            PublishReviewItem(
                severity="error",
                code="evaluation.blocker_failures_present",
                message="The evaluation run contains blocker assertion failures.",
            )
        )
    if not policy.allow_warning_failures and warning_failure_count:
        blockers.append(
            PublishReviewItem(
                severity="error",
                code="evaluation.warning_failures_present",
                message="The evaluation run contains warning assertion failures and policy disallows them.",
            )
        )
    if run.pass_rate_ratio is None or run.pass_rate_ratio < policy.minimum_pass_rate_ratio:
        blockers.append(
            PublishReviewItem(
                severity="error",
                code="evaluation.pass_rate_below_threshold",
                message="The evaluation run does not meet the required pass rate threshold.",
            )
        )
    return RunQualificationDecision(
        qualifies=not blockers,
        blocker_failure_count=blocker_failure_count,
        warning_failure_count=warning_failure_count,
        evaluation_blockers=blockers,
    )


def build_publish_qualification_summary(
    *,
    snapshot: AgentVersionSnapshot,
    fixtures: Iterable[SimulationFixture],
    latest_run: EvaluationRun | None,
    latest_qualified: EvaluationRun | None,
    policy: EvaluationPolicy,
) -> PublishQualificationSummary:
    active = active_fixtures(fixtures)
    required = required_fixtures(fixtures)
    issues = collect_fixture_validation_issues(snapshot, active, active_only=False)

    blockers: list[PublishReviewItem] = []
    warnings: list[PublishReviewItem] = []
    for issue in issues:
        item = PublishReviewItem(
            severity="error" if issue.severity == "blocker" else "warning",
            code=issue.code,
            message=issue.message,
        )
        if issue.severity == "blocker" and _fixture_is_required(issue.fixture_id, required):
            blockers.append(item)
        else:
            warnings.append(item.model_copy(update={"severity": "warning"}))

    covered_required_count = 0
    blocker_failure_count = 0
    warning_failure_count = 0

    if latest_qualified is None:
        warnings.append(
            PublishReviewItem(
                severity="warning",
                code="evaluation.missing_qualified_run",
                message="No qualified evaluation run exists for the current draft version.",
            )
        )
    else:
        covered_required = {
            result.fixture_id
            for result in latest_qualified.results
            if result.fixture_id is not None and result.status == "passed"
        }
        covered_required_count = sum(1 for fixture in required if fixture.fixture_id in covered_required)
        decision = qualification_decision(latest_qualified, policy=policy)
        blocker_failure_count = decision.blocker_failure_count
        warning_failure_count = decision.warning_failure_count
        blockers.extend(decision.evaluation_blockers)
        if covered_required_count != len(required):
            blockers.append(
                PublishReviewItem(
                    severity="error",
                    code="evaluation.required_fixture_coverage_missing",
                    message="The latest qualified run does not cover all active gate-required fixtures.",
                )
            )
        if policy.max_qualified_run_age_hours is not None and latest_qualified.qualified_at is not None:
            freshness_deadline = _utcnow() - timedelta(hours=policy.max_qualified_run_age_hours)
            if latest_qualified.qualified_at < freshness_deadline:
                blockers.append(
                    PublishReviewItem(
                        severity="error",
                        code="evaluation.qualified_run_stale",
                        message="The latest qualified evaluation run is older than the configured freshness threshold.",
                    )
                )

    return PublishQualificationSummary(
        minimum_pass_rate_ratio=policy.minimum_pass_rate_ratio,
        allow_warning_failures=policy.allow_warning_failures,
        max_qualified_run_age_hours=policy.max_qualified_run_age_hours,
        latest_run_id=None if latest_run is None else latest_run.evaluation_run_id,
        latest_run_status=None if latest_run is None else latest_run.status,
        latest_run_pass_rate_ratio=None if latest_run is None else latest_run.pass_rate_ratio,
        latest_qualified_run_id=None if latest_qualified is None else latest_qualified.evaluation_run_id,
        latest_qualified_at=None if latest_qualified is None else latest_qualified.qualified_at,
        required_fixture_count=len(required),
        required_fixture_covered_count=covered_required_count,
        blocker_failure_count=blocker_failure_count,
        warning_failure_count=warning_failure_count,
        evaluation_blockers=blockers,
        fixture_reference_warnings=warnings,
    )


def summarize_fixture_issues(issues: Iterable[FixtureValidationIssue]) -> str:
    return "; ".join(issue.message for issue in issues)


def _fixture_is_required(fixture_id: str, fixtures: list[SimulationFixture]) -> bool:
    return any(fixture.fixture_id == fixture_id for fixture in fixtures)
