from __future__ import annotations

from ruhu.db import build_session_factory, tenant_db_context
from ruhu.journeys import JourneyAnalyticsRebuildRequest, JourneyRuntimeJob, SQLAlchemyJourneyRuntimeJobStore
from ruhu.journey_worker import main


def test_journey_worker_process_once_executes_queued_jobs(postgres_database_url_factory, capsys) -> None:
    database_url = postgres_database_url_factory()
    session_factory = build_session_factory(database_url)
    job_store = SQLAlchemyJourneyRuntimeJobStore(session_factory)

    with tenant_db_context(organization_id="org-1"):
        job_store.create_or_get_live_job(
            JourneyRuntimeJob(
                job_id="journey-worker-job-1",
                organization_id="org-1",
                kind="analytics_rebuild",
                payload=JourneyAnalyticsRebuildRequest().model_dump(mode="json"),
            )
        )

    exit_code = main(
        [
            "process-once",
            "--database-url",
            database_url,
            "--max-jobs",
            "1",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    completed = job_store.load_job("journey-worker-job-1", organization_id="org-1")

    assert exit_code == 0
    assert '"processed_count": 1' in output
    assert completed is not None
    assert completed.status == "completed"
