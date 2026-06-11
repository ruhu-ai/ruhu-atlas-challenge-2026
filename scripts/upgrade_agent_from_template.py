#!/usr/bin/env python3
"""Upgrade an existing agent's draft document from a template file.

Templates seed agents at first creation only — see
``registry.ensure_seeded_document`` which short-circuits when the agent
already exists. Once an agent is in the DB, edits to the template file
no longer affect it. This script closes the gap: it loads the latest
template document and overwrites the named agent's draft with it,
optionally publishing the draft so it's immediately live.

Identity, version chain, organization, and agent_settings are all
preserved — only the document body (scenarios, steps, transitions,
fact_schema, scenario_routes, capability manifest, metadata) is
replaced.

Examples
--------
Dry-run (show what would change without writing):

    python scripts/upgrade_agent_from_template.py \\
        --agent-id sales_agent_e784ba41 \\
        --template src/ruhu/templates/system/sales-agent.json \\
        --dry-run

Replace the draft and publish immediately:

    python scripts/upgrade_agent_from_template.py \\
        --agent-id sales_agent_e784ba41 \\
        --template src/ruhu/templates/system/sales-agent.json \\
        --publish
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ruhu.db import build_session_factory
from ruhu.loader import load_agent_document_source
from ruhu.registry import SQLAlchemyAgentRegistry


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Overwrite an agent's draft document with a template's content.",
    )
    parser.add_argument(
        "--agent-id",
        required=True,
        help="Agent id to upgrade (e.g. sales_agent_e784ba41).",
    )
    parser.add_argument(
        "--template",
        required=True,
        type=Path,
        help="Path to the template JSON (e.g. src/ruhu/templates/system/sales-agent.json).",
    )
    parser.add_argument(
        "--organization-id",
        default=None,
        help="Organization id the agent belongs to. Required if multiple agents share the id across tenants.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("RUHU_DATABASE_URL") or os.environ.get("DATABASE_URL", ""),
        help="DB URL. Defaults to RUHU_DATABASE_URL or DATABASE_URL env.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="After overwriting the draft, immediately publish it. Default is to leave as draft for review.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing to the DB.",
    )
    return parser.parse_args()


def _document_diff_summary(before: dict, after: dict) -> list[str]:
    """One-line summary per top-level field that changed."""
    notes: list[str] = []
    keys = set(before) | set(after)
    for key in sorted(keys):
        if before.get(key) != after.get(key):
            if isinstance(before.get(key), list) and isinstance(after.get(key), list):
                notes.append(f"  {key}: {len(before.get(key, []))} → {len(after.get(key, []))} items")
            else:
                notes.append(f"  {key}: changed")
    return notes


def main() -> int:
    args = _parse_args()
    if not args.database_url:
        print("error: DB URL not set (pass --database-url or set RUHU_DATABASE_URL).", file=sys.stderr)
        return 2
    if not args.template.exists():
        print(f"error: template file not found: {args.template}", file=sys.stderr)
        return 2

    template_document, template_agent_id, template_agent_name = load_agent_document_source(args.template)
    print(f"loaded template: {args.template} (template_agent_id={template_agent_id})")

    session_factory = build_session_factory(args.database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)

    try:
        existing = registry.get_agent_document(
            args.agent_id,
            target="draft",
            organization_id=args.organization_id,
        )
    except KeyError as exc:
        print(f"error: agent not found ({args.agent_id}): {exc}", file=sys.stderr)
        return 2

    before_json = existing.model_dump(mode="json")
    after_json = template_document.model_dump(mode="json")
    diff_notes = _document_diff_summary(before_json, after_json)

    print(f"\nupgrading agent {args.agent_id} draft document from template:")
    if not diff_notes:
        print("  (no field-level changes — template matches current draft)")
    else:
        for note in diff_notes:
            print(note)

    if args.dry_run:
        print("\ndry-run: no changes written.")
        return 0

    try:
        registry.update_draft_agent_document(
            args.agent_id,
            template_document,
            organization_id=args.organization_id,
        )
    except PermissionError as exc:
        print(f"error: cannot edit agent draft: {exc}", file=sys.stderr)
        return 2
    except (KeyError, ValueError) as exc:
        print(f"error: update failed: {exc}", file=sys.stderr)
        return 2

    print("\ndraft replaced.")

    if args.publish:
        try:
            published = registry.publish(
                args.agent_id,
                organization_id=args.organization_id,
            )
        except (KeyError, ValueError) as exc:
            print(f"warning: draft was replaced but publish failed: {exc}", file=sys.stderr)
            return 1
        print(f"published version {published.version_number} (version_id={published.version_id})")
    else:
        print("draft is staged. Publish via the canvas UI or rerun with --publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
