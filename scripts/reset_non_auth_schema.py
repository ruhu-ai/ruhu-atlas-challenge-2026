#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys

from ruhu.db import reset_non_auth_schema, resolve_database_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drop and recreate all non-auth Ruhu tables while preserving auth data."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("RUHU_DATABASE_URL", ""),
        help="Database URL to reset. Defaults to RUHU_DATABASE_URL.",
    )
    parser.add_argument(
        "--yes-i-understand",
        action="store_true",
        help="Required confirmation flag for the destructive reset.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the reset summary as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.yes_i_understand:
        parser.error("--yes-i-understand is required")
    try:
        database_url = resolve_database_url(database_url=args.database_url)
    except ValueError as exc:
        parser.error(str(exc))
    summary = reset_non_auth_schema(database_url)
    if args.json:
        print(json.dumps(summary, sort_keys=True, default=list))
    else:
        print("non-auth schema reset complete")
        print(f"database_url={summary['database_url']}")
        print(f"preserved_tables={','.join(summary['preserved_tables'])}")
        print(f"dropped_tables={','.join(summary['dropped_tables'])}")
        print(f"recreated_non_auth_tables={len(summary['non_auth_schema_tables'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
