from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import sys
import time

from .agent_document import AgentDocument
from .db import build_session_factory, resolve_database_url
from .journeys import (
    JourneyRuntime,
    JourneyService,
    JourneyTracker,
    SQLAlchemyJourneyDefinitionStore,
    SQLAlchemyJourneyInstanceStore,
    SQLAlchemyJourneyRuntimeJobStore,
)
from .realtime import (
    RealtimeControlPlane,
    SQLAlchemyRealtimeEventStore,
    SQLAlchemyRealtimeIdempotencyStore,
    SQLAlchemyRealtimeOutboxStore,
    SQLAlchemyRealtimeSessionStore,
)
from .registry import SQLAlchemyAgentRegistry
from .runtime_config import RuntimeSettings
from .stores import SQLAlchemyConversationStore, SQLAlchemyTraceStore


JOURNEYS_JOB_TYPE = "journey_runtime.tick"


def _default_agent_root() -> Path:
    return Path(__file__).resolve().parents[2] / "examples" / "agents"


@dataclass(slots=True)
class JourneyWorkerRunSummary:
    processed_count: int = 0
    completed_count: int = 0
    failed_count: int = 0

    def model_dump(self) -> dict[str, int]:
        return {
            "processed_count": self.processed_count,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
        }


def build_journey_runtime(
    *,
    database_url: str | None = None,
    agent_root: str | Path | None = None,
    run_scheduler: bool = False,
) -> JourneyRuntime:
    settings = RuntimeSettings.from_env()
    resolved_database_url = resolve_database_url(
        database_url=database_url if database_url is not None else settings.database_url,
    )
    session_factory = build_session_factory(resolved_database_url)

    agent_registry = SQLAlchemyAgentRegistry(session_factory)
    # Bootstrap is opt-in: the CLI passes its --agent-root default explicitly;
    # ruhu.worker passes None so a worker process never seeds demo agents.
    if agent_root is not None:
        agent_registry.bootstrap_from_directory(Path(agent_root).resolve())
    definition_store = SQLAlchemyJourneyDefinitionStore(session_factory)
    instance_store = SQLAlchemyJourneyInstanceStore(session_factory)
    job_store = SQLAlchemyJourneyRuntimeJobStore(session_factory)
    realtime_control_plane = RealtimeControlPlane(
        sessions=SQLAlchemyRealtimeSessionStore(session_factory),
        events=SQLAlchemyRealtimeEventStore(session_factory),
        idempotency=SQLAlchemyRealtimeIdempotencyStore(session_factory),
        outbox=SQLAlchemyRealtimeOutboxStore(session_factory),
    )

    def journey_review_agent_documents(definition, organization_id):  # type: ignore[no-untyped-def]
        agent_documents: list[AgentDocument] = []
        missing_agent_ids: list[str] = []
        for agent_id in definition.scope.agent_ids:
            try:
                registration = agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
            except KeyError:
                missing_agent_ids.append(agent_id)
                continue
            version_id = registration.current_draft_version_id or registration.current_published_version_id
            if version_id is None:
                missing_agent_ids.append(agent_id)
                continue
            snapshot = agent_registry.get_version_snapshot(
                version_id,
                organization_id=organization_id,
            )
            agent_documents.append(
                snapshot.agent_document.model_copy(
                    update={
                        "metadata": {
                            **dict(snapshot.agent_document.metadata),
                            "agent_id": snapshot.agent_id,
                            "agent_name": snapshot.name,
                        }
                    }
                )
            )
        return agent_documents, missing_agent_ids

    service = JourneyService(
        definition_store,
        instance_store,
        agent_resolver=journey_review_agent_documents,
        available_tool_refs_provider=lambda: [],
    )
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=SQLAlchemyConversationStore(session_factory),
        trace_store=SQLAlchemyTraceStore(session_factory),
        realtime_event_store=realtime_control_plane.events,
    )
    return JourneyRuntime(
        service=service,
        tracker=tracker,
        max_workers=settings.journey_runtime_workers,
        job_store=job_store,
        embedded_worker_enabled=False,
        poll_interval_seconds=settings.journey_runtime_poll_interval_seconds,
        job_lease_seconds=settings.journey_runtime_job_lease_seconds,
        job_heartbeat_interval_seconds=settings.journey_runtime_job_heartbeat_interval_seconds,
        failure_alert_threshold=settings.journey_runtime_failure_alert_threshold,
        failure_alert_window_seconds=settings.journey_runtime_failure_alert_window_seconds,
        abandonment_sweep_enabled=run_scheduler and settings.journey_abandonment_sweep_enabled,
        abandonment_sweep_interval_seconds=settings.journey_abandonment_sweep_interval_seconds,
    )


def _print(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    for key, value in payload.items():
        print(f"{key}={value}")


def _process_once(
    *,
    runtime: JourneyRuntime,
    organization_id: str | None,
    max_jobs: int,
) -> JourneyWorkerRunSummary:
    jobs = runtime.process_available_jobs_once(
        max_jobs=max_jobs,
        organization_id=organization_id,
    )
    return JourneyWorkerRunSummary(
        processed_count=len(jobs),
        completed_count=sum(1 for job in jobs if job.status == "completed"),
        failed_count=sum(1 for job in jobs if job.status == "failed"),
    )


def _run_process_once(args: argparse.Namespace) -> int:
    runtime = build_journey_runtime(
        database_url=args.database_url,
        agent_root=args.agent_root,
        run_scheduler=args.run_scheduler,
    )
    runtime.startup()
    try:
        _print(
            _process_once(
                runtime=runtime,
                organization_id=args.organization_id,
                max_jobs=args.max_jobs,
            ).model_dump(),
            as_json=args.json,
        )
        return 0
    finally:
        runtime.shutdown()


def _run_worker(args: argparse.Namespace) -> int:
    runtime = build_journey_runtime(
        database_url=args.database_url,
        agent_root=args.agent_root,
        run_scheduler=args.run_scheduler,
    )
    runtime.startup()
    runs = 0
    try:
        while args.max_runs is None or runs < args.max_runs:
            summary = _process_once(
                runtime=runtime,
                organization_id=args.organization_id,
                max_jobs=args.max_jobs,
            )
            _print(summary.model_dump(), as_json=args.json)
            runs += 1
            if args.max_runs is not None and runs >= args.max_runs:
                break
            if args.interval_seconds > 0:
                time.sleep(args.interval_seconds)
        return 0
    finally:
        runtime.shutdown()


def _run_status(args: argparse.Namespace) -> int:
    runtime = build_journey_runtime(
        database_url=args.database_url,
        agent_root=args.agent_root,
        run_scheduler=args.run_scheduler,
    )
    try:
        status = runtime.status(organization_id=args.organization_id)
        _print(status.model_dump(mode="json"), as_json=args.json)
        return 0
    finally:
        runtime.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the Ruhu journey runtime queue and external worker.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--database-url")
        subparser.add_argument("--agent-root", default=str(_default_agent_root()))
        subparser.add_argument("--organization-id")
        subparser.add_argument("--max-jobs", type=int, default=10)
        subparser.add_argument("--run-scheduler", action="store_true")
        subparser.add_argument("--json", action="store_true")

    process_once = subparsers.add_parser("process-once", help="Claim and execute one batch of queued journey jobs.")
    add_common(process_once)
    process_once.set_defaults(handler=_run_process_once)

    worker = subparsers.add_parser("worker", help="Run the external journey runtime worker loop.")
    add_common(worker)
    worker.add_argument("--interval-seconds", type=float, default=2.0)
    worker.add_argument("--max-runs", type=int)
    worker.set_defaults(handler=_run_worker)

    status = subparsers.add_parser("status", help="Print current journey runtime queue status and alerts.")
    add_common(status)
    status.set_defaults(handler=_run_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
