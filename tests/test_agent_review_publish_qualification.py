from __future__ import annotations

from datetime import datetime, timezone

from ruhu.agent_review import AgentPublishReadiness, PublishQualificationSummary, PublishReviewItem, apply_publish_qualification
from ruhu.schemas import AgentDefinitionValidationReport


def test_apply_publish_qualification_merges_blockers_and_warnings() -> None:
    readiness = AgentPublishReadiness(
        agent_id="agent-1",
        draft_version_id="version-1",
        can_publish=True,
        validation=AgentDefinitionValidationReport(
            agent_id="agent-1",
            agent_name="Agent One",
            valid=True,
            error_count=0,
            warning_count=0,
        ),
    )
    qualification = PublishQualificationSummary(
        latest_run_id="run-1",
        latest_qualified_run_id="run-1",
        latest_qualified_at=datetime.now(timezone.utc),
        evaluation_blockers=[
            PublishReviewItem(
                severity="error",
                code="evaluation.required_fixture_coverage_missing",
                message="Missing coverage.",
            )
        ],
        fixture_reference_warnings=[
            PublishReviewItem(
                severity="warning",
                code="fixture.assertion_state_missing",
                message="Stale state reference.",
            )
        ],
    )

    enriched = apply_publish_qualification(readiness, qualification)

    assert enriched.can_publish is False
    assert any(item.code == "evaluation.required_fixture_coverage_missing" for item in enriched.blockers)
    assert any(item.code == "fixture.assertion_state_missing" for item in enriched.warnings)
    assert enriched.qualification.latest_run_id == "run-1"


def test_apply_publish_qualification_preserves_publishability_when_only_warnings_exist() -> None:
    readiness = AgentPublishReadiness(
        agent_id="agent-1",
        draft_version_id="version-1",
        can_publish=True,
        validation=AgentDefinitionValidationReport(
            agent_id="agent-1",
            agent_name="Agent One",
            valid=True,
            error_count=0,
            warning_count=0,
        ),
    )
    qualification = PublishQualificationSummary(
        fixture_reference_warnings=[
            PublishReviewItem(
                severity="warning",
                code="fixture.assertion_state_missing",
                message="Stale state reference.",
            )
        ]
    )

    enriched = apply_publish_qualification(readiness, qualification)

    assert enriched.can_publish is True
    assert any(item.code == "fixture.assertion_state_missing" for item in enriched.warnings)
