from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
import time

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db import build_session_factory, resolve_database_url
from .db_models import TicketingActivityRecord, TicketingConnectionRecord
from .runtime_config import RuntimeSettings
from .ticket_system import TicketSystemService
from .ticketing_providers import TicketingProviderError


@dataclass(slots=True)
class TicketingRetryRunSummary:
    processed_count: int = 0
    failed_count: int = 0
    organizations_scanned: int = 0

    def model_dump(self) -> dict[str, object]:
        return {
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "organizations_scanned": self.organizations_scanned,
        }


class TicketingRetryWorker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        service: TicketSystemService,
        interval_seconds: float = 60.0,
        batch_size: int = 25,
    ) -> None:
        self._session_factory = session_factory
        self._service = service
        self._interval_seconds = max(1.0, float(interval_seconds))
        self._batch_size = max(1, int(batch_size))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="ruhu-ticketing-retry",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self._interval_seconds + 1.0))
        self._thread = None

    def process_once(self) -> TicketingRetryRunSummary:
        summary = TicketingRetryRunSummary()
        organization_ids = self._organizations_with_pending_retries()
        summary.organizations_scanned = len(organization_ids)
        for organization_id in organization_ids:
            try:
                results = self._service.process_pending_retries(
                    organization_id=organization_id,
                    limit=self._batch_size,
                    force=False,
                )
            except TicketingProviderError:
                summary.failed_count += 1
                continue
            summary.processed_count += len(results)
            summary.failed_count += sum(1 for item in results if item.retry_status in {"pending", "exhausted"} and item.status == "error")
        return summary

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.process_once()
            except Exception:  # noqa: BLE001 — prevent daemon-thread death
                logger.exception("ticketing retry worker: unhandled exception in main loop")
                try:
                    from .observability.metrics import worker_unhandled_errors_total
                    worker_unhandled_errors_total.labels(worker="ticketing_retry").inc()
                except Exception:  # noqa: BLE001
                    pass
            self._stop_event.wait(self._interval_seconds)

    def _organizations_with_pending_retries(self) -> list[str]:
        with self._session_factory() as session:
            statement = (
                select(TicketingActivityRecord.organization_id)
                .where(TicketingActivityRecord.retry_status == "pending")
                .distinct()
            )
            rows = session.execute(statement).scalars().all()
        return [item for item in rows if item]


def _build_service(*, database_url: str | None = None) -> tuple[TicketSystemService, sessionmaker[Session]]:
    settings = RuntimeSettings.from_env()
    resolved_database_url = resolve_database_url(
        database_url=database_url if database_url is not None else settings.database_url,
    )
    session_factory = build_session_factory(resolved_database_url)
    return TicketSystemService(session_factory), session_factory


def _print(payload: dict[str, object] | list[dict[str, object]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    if isinstance(payload, list):
        for item in payload:
            print(json.dumps(item, default=str))
        return
    for key, value in payload.items():
        print(f"{key}={value}")


def _selected_connections(
    session_factory: sessionmaker[Session],
    *,
    organization_id: str | None = None,
    connection_id: str | None = None,
    include_disabled: bool = False,
) -> list[TicketingConnectionRecord]:
    with session_factory() as session:
        statement = select(TicketingConnectionRecord).order_by(
            TicketingConnectionRecord.organization_id.asc(),
            TicketingConnectionRecord.provider.asc(),
            TicketingConnectionRecord.display_name.asc(),
        )
        if organization_id:
            statement = statement.where(TicketingConnectionRecord.organization_id == organization_id)
        if connection_id:
            statement = statement.where(TicketingConnectionRecord.connection_id == connection_id)
        if not include_disabled:
            statement = statement.where(TicketingConnectionRecord.status != "disabled")
        return list(session.execute(statement).scalars().all())


def _run_verify_connections(args: argparse.Namespace) -> int:
    service, session_factory = _build_service(database_url=args.database_url)
    records = _selected_connections(
        session_factory,
        organization_id=args.organization_id,
        connection_id=args.connection_id,
        include_disabled=args.include_disabled,
    )
    results: list[dict[str, object]] = []
    exit_code = 0
    for record in records:
        try:
            connection = service.health_check_connection(
                organization_id=record.organization_id,
                connection_id=record.connection_id,
                queue_retry=False,
            )
            results.append(
                {
                    "organization_id": record.organization_id,
                    "connection_id": record.connection_id,
                    "provider": record.provider,
                    "display_name": record.display_name,
                    "status": None if connection is None else connection.status,
                }
            )
            if connection is None or connection.status not in {"active", "pending"}:
                exit_code = 1
        except Exception as exc:
            results.append(
                {
                    "organization_id": record.organization_id,
                    "connection_id": record.connection_id,
                    "provider": record.provider,
                    "display_name": record.display_name,
                    "status": "error",
                    "error": str(exc),
                }
            )
            exit_code = 1
    _print(results, as_json=args.json)
    return exit_code


def _run_retry_once(args: argparse.Namespace) -> int:
    service, session_factory = _build_service(database_url=args.database_url)
    worker = TicketingRetryWorker(
        session_factory=session_factory,
        service=service,
        interval_seconds=args.interval_seconds,
        batch_size=args.batch_size,
    )
    if args.organization_id:
        results = service.process_pending_retries(
            organization_id=args.organization_id,
            limit=args.batch_size,
            force=args.force,
        )
        payload = {"processed_count": len(results), "organization_id": args.organization_id}
    else:
        payload = worker.process_once().model_dump()
    _print(payload, as_json=args.json)
    return 0


def _run_worker(args: argparse.Namespace) -> int:
    service, session_factory = _build_service(database_url=args.database_url)
    worker = TicketingRetryWorker(
        session_factory=session_factory,
        service=service,
        interval_seconds=args.interval_seconds,
        batch_size=args.batch_size,
    )
    runs = 0
    try:
        while args.max_runs is None or runs < args.max_runs:
            if args.organization_id:
                results = service.process_pending_retries(
                    organization_id=args.organization_id,
                    limit=args.batch_size,
                    force=True,
                )
                payload = {
                    "organization_id": args.organization_id,
                    "processed_count": len(results),
                }
            else:
                payload = worker.process_once().model_dump()
            _print(payload, as_json=args.json)
            runs += 1
            if args.max_runs is not None and runs >= args.max_runs:
                break
            if args.interval_seconds > 0:
                time.sleep(args.interval_seconds)
        return 0
    finally:
        worker.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the Ruhu ticketing retry and verification runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--database-url")
        subparser.add_argument("--organization-id")
        subparser.add_argument("--json", action="store_true")

    verify = subparsers.add_parser("verify-connections", help="Run live health checks for configured ticketing connections.")
    add_common(verify)
    verify.add_argument("--connection-id")
    verify.add_argument("--include-disabled", action="store_true")
    verify.set_defaults(handler=_run_verify_connections)

    retry_once = subparsers.add_parser("retry-once", help="Process one batch of pending ticketing retries.")
    add_common(retry_once)
    retry_once.add_argument("--interval-seconds", type=float, default=60.0)
    retry_once.add_argument("--batch-size", type=int, default=25)
    retry_once.add_argument("--force", action="store_true")
    retry_once.set_defaults(handler=_run_retry_once)

    worker = subparsers.add_parser("worker", help="Run a standalone ticketing retry worker loop.")
    add_common(worker)
    worker.add_argument("--interval-seconds", type=float, default=60.0)
    worker.add_argument("--batch-size", type=int, default=25)
    worker.add_argument("--max-runs", type=int)
    worker.set_defaults(handler=_run_worker)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
