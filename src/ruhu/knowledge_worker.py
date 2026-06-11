from __future__ import annotations

import argparse
import json
import sys
import time

from .db import build_session_factory, resolve_database_url
from .knowledge import build_knowledge_runtime
from .knowledge.vector_index import run_weaviate_smoke_check
from .runtime_config import RuntimeSettings


def _build_runtime(*, database_url: str | None = None):
    settings = RuntimeSettings.from_env()
    resolved_database_url = resolve_database_url(
        database_url=database_url if database_url is not None else settings.database_url,
    )
    session_factory = build_session_factory(resolved_database_url)
    runtime = build_knowledge_runtime(
        session_factory=session_factory,
        runtime_settings=settings,
        default_seed_path=None,
    )
    runtime.auto_reindex_on_startup = False
    return runtime


def _print(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    for key, value in payload.items():
        print(f"{key}={value}")


def _run_status(args: argparse.Namespace) -> int:
    runtime = _build_runtime(database_url=args.database_url)
    runtime.startup()
    try:
        status = runtime.status(organization_id=args.organization_id)
        _print(status.model_dump(mode="json"), as_json=args.json)
        return 0
    finally:
        runtime.shutdown()


def _run_reindex_organization(args: argparse.Namespace) -> int:
    runtime = _build_runtime(database_url=args.database_url)
    runtime.startup()
    try:
        job = runtime.run_organization_reindex(
            organization_id=args.organization_id,
            force=args.force,
            timeout_seconds=args.timeout_seconds,
        )
        _print(job.model_dump(mode="json"), as_json=args.json)
        return 0 if job.status == "completed" else 1
    finally:
        runtime.shutdown()


def _run_reindex_document(args: argparse.Namespace) -> int:
    runtime = _build_runtime(database_url=args.database_url)
    runtime.startup()
    try:
        job = runtime.run_document_reindex(
            organization_id=args.organization_id,
            document_id=args.document_id,
            force=args.force,
            timeout_seconds=args.timeout_seconds,
        )
        _print(job.model_dump(mode="json"), as_json=args.json)
        return 0 if job.status == "completed" else 1
    finally:
        runtime.shutdown()


def _run_worker(args: argparse.Namespace) -> int:
    runtime = _build_runtime(database_url=args.database_url)
    runtime.startup()
    runs = 0
    exit_code = 0
    try:
        while args.max_runs is None or runs < args.max_runs:
            job = runtime.run_organization_reindex(
                organization_id=args.organization_id,
                force=args.force,
                timeout_seconds=args.timeout_seconds,
            )
            _print(job.model_dump(mode="json"), as_json=args.json)
            runs += 1
            if job.status != "completed":
                exit_code = 1
            if args.max_runs is not None and runs >= args.max_runs:
                break
            if args.interval_seconds > 0:
                time.sleep(args.interval_seconds)
        return exit_code
    finally:
        runtime.shutdown()


def _run_smoke(args: argparse.Namespace) -> int:
    runtime = _build_runtime(database_url=args.database_url)
    runtime.startup()
    try:
        vector_index = runtime.service.vector_index
        if vector_index is None:
            payload = {
                "ok": False,
                "reason": "knowledge vector index is not configured",
            }
            _print(payload, as_json=args.json)
            return 1
        query = args.query or "workflow automation smoke check"
        vector = runtime.service.embedding_provider.embed_query(query)
        try:
            payload = run_weaviate_smoke_check(
                index=vector_index,
                organization_id=runtime.resolve_organization_id(args.organization_id),
                model_key=runtime.service.embedding_provider.model_key,
                query=query,
                vector=vector,
            )
        except Exception as exc:
            diagnostics = vector_index.diagnostics()
            payload = {
                "ok": False,
                "reason": str(exc),
                "diagnostics": None if diagnostics is None else diagnostics.model_dump(mode="json"),
            }
        _print(payload, as_json=args.json)
        return 0 if payload.get("ok") else 1
    finally:
        runtime.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the Ruhu knowledge-base indexing runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--database-url")
        subparser.add_argument("--organization-id")
        subparser.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="Print knowledge indexing status.")
    add_common(status)
    status.set_defaults(handler=_run_status)

    reindex_org = subparsers.add_parser("reindex-organization", help="Reindex published knowledge for one organization.")
    add_common(reindex_org)
    reindex_org.add_argument("--force", action="store_true")
    reindex_org.add_argument("--timeout-seconds", type=float, default=300.0)
    reindex_org.set_defaults(handler=_run_reindex_organization)

    reindex_doc = subparsers.add_parser("reindex-document", help="Reindex one knowledge document.")
    add_common(reindex_doc)
    reindex_doc.add_argument("--document-id", required=True)
    reindex_doc.add_argument("--force", action="store_true")
    reindex_doc.add_argument("--timeout-seconds", type=float, default=300.0)
    reindex_doc.set_defaults(handler=_run_reindex_document)

    worker = subparsers.add_parser("worker", help="Run a standalone reindex worker loop.")
    add_common(worker)
    worker.add_argument("--force", action="store_true")
    worker.add_argument("--timeout-seconds", type=float, default=300.0)
    worker.add_argument("--interval-seconds", type=float, default=60.0)
    worker.add_argument("--max-runs", type=int)
    worker.set_defaults(handler=_run_worker)

    smoke = subparsers.add_parser("smoke-weaviate", help="Run a live Weaviate smoke check.")
    add_common(smoke)
    smoke.add_argument("--query")
    smoke.set_defaults(handler=_run_smoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
