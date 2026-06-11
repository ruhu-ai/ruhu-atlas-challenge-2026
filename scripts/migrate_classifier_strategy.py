#!/usr/bin/env python3
"""One-time migration: resolve legacy ``classifier.mode`` per-agent.

The Phase A schema introduces ``agent_settings.llm_config.classifier.strategy``
("off" / "main_llm" / "prefill"). The legacy field is ``mode`` ("off" /
"always"). Naively mapping ``mode = "always"`` → ``strategy = "prefill"`` would
preserve today's broken state for agents that never had a LoRA: the small
classifier was running, just not accurately.

This script resolves each agent individually:

- ``mode = "off"``    → ``strategy = "off"``
- ``mode = "always"`` AND a production-status LoRA exists for the agent
                      → ``strategy = "prefill"``
- ``mode = "always"`` AND no production LoRA
                      → ``strategy = "main_llm"`` (the new safe default)
- already has ``strategy``
                      → leave alone

Examples
--------
Dry-run against the dev DB:

    python scripts/migrate_classifier_strategy.py \\
        --database-url "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev" \\
        --dry-run

Apply (per-tenant safe — the script touches every agent in the table):

    python scripts/migrate_classifier_strategy.py \\
        --database-url "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev"
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from ruhu.classifier.registry import resolve_lora
from ruhu.db import build_session_factory
from ruhu.db_models import AgentRecord


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve legacy classifier.mode → classifier.strategy per agent.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("RUHU_DATABASE_URL") or os.environ.get("DATABASE_URL", ""),
        help="DB URL. Defaults to RUHU_DATABASE_URL or DATABASE_URL env.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes without writing.",
    )
    parser.add_argument(
        "--agent-id",
        default=None,
        help="Only migrate the named agent (debugging / single-agent runs).",
    )
    return parser.parse_args()


def _resolve_strategy_for_agent(
    session,
    *,
    agent_id: str,
    organization_id: str | None,
    legacy_mode: str | None,
    existing_strategy: str | None,
) -> tuple[str, str]:
    """Return ``(new_strategy, reason)`` for a single agent."""
    if existing_strategy in {"off", "main_llm", "prefill"}:
        return existing_strategy, "already_set"

    if legacy_mode == "off":
        return "off", "legacy_mode=off"

    # legacy_mode is None or "always" — both treat as "wants classification"
    lora_name = resolve_lora(
        session,
        agent_id=agent_id,
        step_id=None,
        organization_id=organization_id,
    )
    if lora_name is not None:
        return "prefill", f"production_lora={lora_name}"
    if legacy_mode == "always":
        return "main_llm", "legacy_mode=always_no_lora"
    return "main_llm", "default_no_lora"


def main() -> int:
    args = _parse_args()
    if not args.database_url:
        print("error: DB URL not set (pass --database-url or set RUHU_DATABASE_URL).", file=sys.stderr)
        return 2

    session_factory = build_session_factory(args.database_url)

    changes: list[dict[str, object]] = []
    skipped = 0

    with session_factory.begin() as session:
        query = select(AgentRecord)
        if args.agent_id is not None:
            query = query.where(AgentRecord.agent_id == args.agent_id)
        agents = session.execute(query).scalars().all()

        for agent in agents:
            # Deep-copy so the in-place mutations below don't share refs
            # with the loaded ORM dict (SQLAlchemy can't detect nested
            # JSON mutations through the same object — even after
            # reassignment of the outer dict — without ``flag_modified``).
            settings = copy.deepcopy(agent.settings_json or {})
            agent_settings = settings.get("agent_settings")
            if not isinstance(agent_settings, dict):
                skipped += 1
                continue
            llm_config = agent_settings.setdefault("llm_config", {})
            classifier_config = llm_config.setdefault("classifier", {})
            existing_strategy = classifier_config.get("strategy")
            legacy_mode = classifier_config.get("mode")

            new_strategy, reason = _resolve_strategy_for_agent(
                session,
                agent_id=agent.agent_id,
                organization_id=agent.organization_id,
                legacy_mode=legacy_mode,
                existing_strategy=existing_strategy,
            )

            if new_strategy == existing_strategy:
                skipped += 1
                continue

            change = {
                "agent_id": agent.agent_id,
                "organization_id": agent.organization_id,
                "from_strategy": existing_strategy,
                "from_mode": legacy_mode,
                "to_strategy": new_strategy,
                "reason": reason,
            }
            changes.append(change)

            if not args.dry_run:
                classifier_config["strategy"] = new_strategy
                # Drop the legacy ``mode`` field — strategy is the only
                # supported shape post-migration.
                classifier_config.pop("mode", None)
                agent.settings_json = settings
                # Belt-and-suspenders: tell SQLAlchemy the JSON column has
                # changed even if the deep-copy + reassignment above
                # didn't trigger automatic dirty detection.
                flag_modified(agent, "settings_json")

        if args.dry_run:
            session.rollback()

    print(f"\n{'planned' if args.dry_run else 'applied'} changes: {len(changes)}")
    print(f"skipped (already set / no agent_settings): {skipped}\n")
    for change in changes:
        print(json.dumps(change))

    return 0


if __name__ == "__main__":
    sys.exit(main())
