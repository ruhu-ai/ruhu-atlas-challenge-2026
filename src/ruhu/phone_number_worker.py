from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass

from .db import build_session_factory, resolve_database_url
from .notifications.store import SQLAlchemyNotificationStore
from .phone_number_audit import PhoneNumberAuditService
from .phone_number_operations import PhoneNumberOperationsService
from .phone_number_registry import PhoneNumberRegistryService
from .phone_number_service import PhoneNumberService
from .phone_provider_telnyx import TelnyxPhoneProvider
from .runtime_config import RuntimeSettings


@dataclass(slots=True)
class PhoneNumberWorkerStatus:
    active_bindings: int
    provider_counts: dict[str, int]
    health_counts: dict[str, int]

    def model_dump(self) -> dict[str, object]:
        return {
            "active_bindings": self.active_bindings,
            "provider_counts": dict(self.provider_counts),
            "health_counts": dict(self.health_counts),
        }


def _build_operations_service(*, database_url: str | None = None) -> PhoneNumberOperationsService:
    settings = RuntimeSettings.from_env()
    resolved_database_url = resolve_database_url(
        database_url=database_url if database_url is not None else settings.database_url,
    )
    session_factory = build_session_factory(resolved_database_url)
    registry = PhoneNumberRegistryService(session_factory)
    telnyx_provider = (
        None
        if not settings.telnyx_api_key
        else TelnyxPhoneProvider(
            api_key=settings.telnyx_api_key,
            base_url=settings.telnyx_api_base_url,
            timeout_seconds=settings.telnyx_timeout_seconds,
        )
    )
    phone_number_service = PhoneNumberService(
        registry=registry,
        telnyx_provider=telnyx_provider,
    )
    return PhoneNumberOperationsService(
        registry=registry,
        phone_number_service=phone_number_service,
        audit_service=PhoneNumberAuditService(session_factory),
        notification_store=SQLAlchemyNotificationStore(session_factory),
    )


def _build_registry(*, database_url: str | None = None) -> PhoneNumberRegistryService:
    settings = RuntimeSettings.from_env()
    resolved_database_url = resolve_database_url(
        database_url=database_url if database_url is not None else settings.database_url,
    )
    session_factory = build_session_factory(resolved_database_url)
    return PhoneNumberRegistryService(session_factory)


def _print(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    for key, value in payload.items():
        print(f"{key}={value}")


async def _reconcile_once(args: argparse.Namespace) -> dict[str, object]:
    operations = _build_operations_service(database_url=args.database_url)
    summary = await operations.reconcile_bindings(
        organization_id=args.organization_id,
        provider=args.provider,
        phone_number_id=args.phone_number_id,
        binding_id=args.binding_id,
        limit=args.limit,
        actor_type="system",
    )
    return {
        "organization_id": summary.organization_id,
        "processed_count": summary.processed_count,
        "changed_count": summary.changed_count,
        "failed_count": summary.failed_count,
    }


def _status(args: argparse.Namespace) -> dict[str, object]:
    registry = _build_registry(database_url=args.database_url)
    bindings = registry.list_bindings_for_organization(
        organization_id=args.organization_id,
        provider=args.provider,
        phone_number_id=args.phone_number_id,
        active_only=True,
        limit=max(args.limit, 500),
    )
    provider_counts: dict[str, int] = {}
    health_counts: dict[str, int] = {}
    for binding in bindings:
        provider_counts[binding.provider] = provider_counts.get(binding.provider, 0) + 1
        health_counts[binding.health_status] = health_counts.get(binding.health_status, 0) + 1
    return PhoneNumberWorkerStatus(
        active_bindings=len(bindings),
        provider_counts=provider_counts,
        health_counts=health_counts,
    ).model_dump()


def build_parser() -> argparse.ArgumentParser:
    settings = RuntimeSettings.from_env()
    parser = argparse.ArgumentParser(description="Operate phone-number reconciliation for Ruhu.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--database-url")
        subparser.add_argument("--organization-id", required=True)
        subparser.add_argument("--provider")
        subparser.add_argument("--phone-number-id")
        subparser.add_argument("--binding-id")
        subparser.add_argument(
            "--limit",
            type=int,
            default=settings.phone_number_reconciliation_batch_size,
        )
        subparser.add_argument("--json", action="store_true")

    reconcile_once = subparsers.add_parser("reconcile-once", help="Reconcile one batch of active phone bindings.")
    add_common(reconcile_once)
    reconcile_once.set_defaults(handler="reconcile-once")

    worker = subparsers.add_parser("worker", help="Run the reconciliation loop.")
    add_common(worker)
    worker.add_argument(
        "--interval-seconds",
        type=float,
        default=settings.phone_number_reconciliation_interval_seconds,
    )
    worker.add_argument("--max-runs", type=int)
    worker.set_defaults(handler="worker")

    status_parser = subparsers.add_parser("status", help="Report current active binding counts by provider and health.")
    add_common(status_parser)
    status_parser.set_defaults(handler="status")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.handler == "status":
        _print(_status(args), as_json=args.json)
        return 0

    if args.handler == "reconcile-once":
        _print(asyncio.run(_reconcile_once(args)), as_json=args.json)
        return 0

    runs = 0
    while args.max_runs is None or runs < args.max_runs:
        _print(asyncio.run(_reconcile_once(args)), as_json=args.json)
        runs += 1
        if args.max_runs is not None and runs >= args.max_runs:
            break
        if args.interval_seconds > 0:
            time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
