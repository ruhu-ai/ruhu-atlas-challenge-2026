#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from ruhu.db import build_session_factory, run_migrations
from ruhu.rules_migration import extract_legacy_policies, migrate_legacy_policies
from ruhu.rules_store import build_rules_runtime


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate legacy Ruhu policy exports into typed runtime rules.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a JSON export containing legacy policies (array, {items:[...]}, or {policies:[...]}).",
    )
    parser.add_argument(
        "--organization-id",
        required=True,
        help="Target organization id in the new Ruhu database.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Database URL for new Ruhu. Defaults to DATABASE_URL environment variable.",
    )
    parser.add_argument(
        "--actor-user-id",
        default="legacy-policy-migration",
        help="Audit actor user id recorded on created rules/bindings.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and convert records without writing to the database.",
    )
    parser.add_argument(
        "--skip-bindings",
        action="store_true",
        help="Create only rule definitions/revisions and skip binding creation.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the migration report JSON.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    args = _parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"input file not found: {input_path}")

    raw_payload = _load_json(input_path)
    policies = extract_legacy_policies(raw_payload)
    if not policies:
        report = {
            "total": 0,
            "migrated": 0,
            "skipped": 0,
            "failed": 0,
            "items": [],
            "detail": "no policy records were found in the provided input payload",
        }
    else:
        if not args.dry_run:
            if not args.database_url:
                raise ValueError("--database-url is required when not running with --dry-run")
            run_migrations(args.database_url)
            session_factory = build_session_factory(args.database_url)
            runtime = build_rules_runtime(session_factory)
            migration_report = migrate_legacy_policies(
                runtime=runtime,
                organization_id=args.organization_id,
                actor_user_id=args.actor_user_id,
                policies=policies,
                create_bindings=not args.skip_bindings,
                dry_run=False,
            )
        else:
            migration_report = migrate_legacy_policies(
                runtime=None,
                organization_id=args.organization_id,
                actor_user_id=args.actor_user_id,
                policies=policies,
                create_bindings=not args.skip_bindings,
                dry_run=True,
            )
        report = migration_report.as_dict()

    output = json.dumps(report, indent=2, sort_keys=True)
    print(output)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
