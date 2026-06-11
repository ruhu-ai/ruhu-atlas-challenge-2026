#!/usr/bin/env python3
"""One-shot migration to the edge-owned outcomes workflow shape.

Walks every authored agent document stored in the runtime DB and rewrites
each step's transitions in-place:

- ``event_hints`` + ``"kind": "event"`` transitions
  → ``OutcomeCondition`` transitions with stable ``event`` tokens and
    LLM-evaluated ``description``. ``event_hints`` is dropped.
- Sibling renames on existing condition shapes:
  ``fact_present.value`` → ``fact_name``, ``fact_missing.value`` →
  ``fact_name``, ``guard_failure.value`` → ``guard_id``, and
  ``tool_outcome.value`` → ``outcome``.

Tables touched:

- ``agent_versions.agent_document_json`` — every authored version (draft +
  published + archived).
- ``agent_templates.agent_document_json`` — system + tenant templates.
- ``turn_traces.classifier_json`` — renames the legacy ``intent_name`` key
  to ``chosen_label`` so the new ``ClassifierTraceRecord`` schema parses
  historical traces without a shim. ``semantic_events_json`` is left
  untouched: the analytics ``intent_detected:*`` family is a separate
  subsystem and continues to be valid event shape.

The script is idempotent — rows already in the new shape pass through
untouched, and re-running on a partially-migrated DB completes the
remainder.

Examples
--------
Dry-run against the dev DB (no writes; per-row diff printed):

    python scripts/migrate_to_edge_outcomes.py \\
        --database-url "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev" \\
        --dry-run

Apply for real:

    python scripts/migrate_to_edge_outcomes.py \\
        --database-url "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev"

Restrict to a single agent (debugging / single-tenant runs):

    python scripts/migrate_to_edge_outcomes.py \\
        --database-url ... \\
        --agent-id agent_melonpay_support_demo

The post-apply check fails the run with exit code 3 if any
``event_hints`` or legacy ``intent_detected:`` token survives in either
``agent_document_json`` table — that's the contract that lets the
runtime drop legacy parsing entirely.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from ruhu.db import build_session_factory
from ruhu.db_models import (
    AgentTemplateStorageRecord,
    AgentVersionRecord,
    TurnTraceRecord,
)

EVENT_PREFIX_RE = re.compile(r"^intent_detected:(?P<name>[a-z][a-z0-9_]+)$")


# ── transform helpers ────────────────────────────────────────────────────────


def _description_for(
    event: str,
    hints: dict[str, str],
    existing_label: str | None,
) -> str:
    """Pick the best human-language description for the new OutcomeCondition.

    Priority:
    1. authored ``event_hints[event]`` (the LLM-evaluated description today)
    2. authored ``transition.label`` (canvas badge text)
    3. synthesised fallback so the validator's ``min_length=8`` passes
    """
    text = (hints.get(event) or "").strip()
    if not text:
        text = (existing_label or "").strip()
    if not text or len(text) < 8:
        text = f"User triggers the {event.replace('_', ' ')} workflow outcome."
    return text


def _migrate_step(step: dict) -> bool:
    """Rewrite one step in-place. Returns ``True`` if anything changed."""
    changed = False
    hints = step.pop("event_hints", None)
    hints_dict: dict[str, str] = dict(hints) if isinstance(hints, dict) else {}
    if hints is not None:
        changed = True

    surviving: list[dict] = []
    for transition in step.get("transitions") or []:
        when = transition.get("when") or {}
        kind = when.get("kind")

        if kind == "event":
            value = (when.get("value") or "").strip()
            # Legacy ``uncertain_understanding:fallback_text`` transitions
            # are subsumed by the kernel's auto-emitted fallback event when
            # no ``routing.outcome_resolved`` fires, so they're dropped
            # rather than translated. Authors who relied on this should
            # add an explicit ``OtherwiseCondition`` transition.
            if value.startswith("uncertain_understanding:"):
                changed = True
                continue
            match = EVENT_PREFIX_RE.match(value)
            if not match:
                raise ValueError(
                    f"transition {transition.get('id')!r} has unrecognised "
                    f"event value {value!r}; expected ``intent_detected:<name>``"
                )
            event_name = match.group("name")
            transition["when"] = {
                "kind": "outcome",
                "event": event_name,
                "description": _description_for(
                    event_name, hints_dict, transition.get("label")
                ),
            }
            changed = True

        elif kind == "fact_present" and "value" in when:
            when["fact_name"] = when.pop("value")
            changed = True
        elif kind == "fact_missing" and "value" in when:
            when["fact_name"] = when.pop("value")
            changed = True
        elif kind == "guard_failure" and "value" in when:
            when["guard_id"] = when.pop("value")
            changed = True
        elif kind == "tool_outcome" and "value" in when:
            when["outcome"] = when.pop("value")
            changed = True

        surviving.append(transition)

    if changed:
        step["transitions"] = surviving

    return changed


def _migrate_document(doc: dict) -> bool:
    """Walk an AgentDocument JSON dict (or a wrapping payload that nests
    ``agent_document``). Returns ``True`` if anything changed."""
    changed = False
    payload = doc.get("agent_document") if isinstance(doc.get("agent_document"), dict) else doc
    for scenario in payload.get("scenarios") or []:
        for step in scenario.get("steps") or []:
            if _migrate_step(step):
                changed = True
    return changed


def _migrate_classifier_json(blob: dict) -> bool:
    """Rename the legacy ``intent_name`` key to ``chosen_label``.

    Idempotent — rows already on the new shape pass through. When both
    keys are present (shouldn't happen but defensive), prefers the
    existing ``chosen_label`` value and drops ``intent_name``.
    """
    if "intent_name" not in blob:
        return False
    legacy = blob.pop("intent_name")
    if "chosen_label" not in blob:
        blob["chosen_label"] = legacy
    return True


# ── post-check ──────────────────────────────────────────────────────────────


_FORBIDDEN_PATTERNS = (
    re.compile(r'"event_hints"'),
    re.compile(r'"intent_detected:'),
    re.compile(r'"kind":\s*"event"'),
)


def _scan_for_legacy_refs(value: Any) -> list[str]:
    """Return a list of forbidden patterns found inside ``value`` (a JSON-able
    dict). The migration's contract is that no row may carry these tokens
    after apply — readers can drop legacy parsing entirely.
    """
    text = json.dumps(value, ensure_ascii=False)
    hits: list[str] = []
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            hits.append(pattern.pattern)
    return hits


# ── orchestration ───────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate stored agent documents to edge-owned outcomes shape.",
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
        help="Restrict to one agent's versions (debugging / single-agent runs).",
    )
    parser.add_argument(
        "--skip-traces",
        action="store_true",
        help="Skip turn_traces.classifier_json migration (rare — for replay testing).",
    )
    parser.add_argument(
        "--skip-post-check",
        action="store_true",
        help=(
            "Skip the legacy-ref post-check. Use only for partial migrations "
            "where you intentionally left rows untouched."
        ),
    )
    return parser.parse_args()


def _migrate_versions(
    session,
    *,
    agent_id: str | None,
    dry_run: bool,
) -> list[dict[str, Any]]:
    query = select(AgentVersionRecord)
    if agent_id is not None:
        query = query.where(AgentVersionRecord.agent_id == agent_id)
    rows = session.execute(query).scalars().all()
    changes: list[dict[str, Any]] = []
    for row in rows:
        document = copy.deepcopy(row.agent_document_json or {})
        try:
            changed = _migrate_document(document)
        except ValueError as exc:
            print(
                f"! agent_versions {row.version_id} (agent={row.agent_id}): {exc}",
                file=sys.stderr,
            )
            raise
        if not changed:
            continue
        changes.append(
            {
                "table": "agent_versions",
                "version_id": row.version_id,
                "agent_id": row.agent_id,
                "status": row.status,
            }
        )
        if not dry_run:
            row.agent_document_json = document
            flag_modified(row, "agent_document_json")
    return changes


def _migrate_templates(session, *, dry_run: bool) -> list[dict[str, Any]]:
    rows = session.execute(select(AgentTemplateStorageRecord)).scalars().all()
    changes: list[dict[str, Any]] = []
    for row in rows:
        document = copy.deepcopy(row.agent_document_json or {})
        try:
            changed = _migrate_document(document)
        except ValueError as exc:
            print(
                f"! agent_templates {row.template_id} (slug={row.slug}): {exc}",
                file=sys.stderr,
            )
            raise
        if not changed:
            continue
        changes.append(
            {
                "table": "agent_templates",
                "template_id": row.template_id,
                "slug": row.slug,
            }
        )
        if not dry_run:
            row.agent_document_json = document
            flag_modified(row, "agent_document_json")
    return changes


def _migrate_traces(
    session,
    *,
    agent_id: str | None,
    dry_run: bool,
) -> int:
    query = select(TurnTraceRecord).where(TurnTraceRecord.classifier_json.is_not(None))
    if agent_id is not None:
        query = query.where(TurnTraceRecord.agent_id == agent_id)
    rows = session.execute(query).scalars().all()
    touched = 0
    for row in rows:
        blob = copy.deepcopy(row.classifier_json or {})
        if not _migrate_classifier_json(blob):
            continue
        touched += 1
        if not dry_run:
            row.classifier_json = blob
            flag_modified(row, "classifier_json")
    return touched


def _post_check(session, *, agent_id: str | None) -> list[str]:
    failures: list[str] = []

    versions = session.execute(
        select(AgentVersionRecord).where(
            AgentVersionRecord.agent_id == agent_id
            if agent_id is not None
            else AgentVersionRecord.version_id.is_not(None)
        )
    ).scalars().all()
    for row in versions:
        hits = _scan_for_legacy_refs(row.agent_document_json or {})
        if hits:
            failures.append(
                f"agent_versions {row.version_id} (agent={row.agent_id}) "
                f"still contains: {', '.join(hits)}"
            )

    templates = session.execute(select(AgentTemplateStorageRecord)).scalars().all()
    for row in templates:
        hits = _scan_for_legacy_refs(row.agent_document_json or {})
        if hits:
            failures.append(
                f"agent_templates {row.template_id} (slug={row.slug}) "
                f"still contains: {', '.join(hits)}"
            )

    return failures


def main() -> int:
    args = _parse_args()
    if not args.database_url:
        print(
            "error: DB URL not set (pass --database-url or set RUHU_DATABASE_URL).",
            file=sys.stderr,
        )
        return 2

    session_factory = build_session_factory(args.database_url)

    with session_factory.begin() as session:
        version_changes = _migrate_versions(
            session, agent_id=args.agent_id, dry_run=args.dry_run
        )
        template_changes = (
            [] if args.agent_id is not None
            else _migrate_templates(session, dry_run=args.dry_run)
        )
        traces_touched = (
            0 if args.skip_traces
            else _migrate_traces(session, agent_id=args.agent_id, dry_run=args.dry_run)
        )

        if args.dry_run:
            session.rollback()

        verb = "planned" if args.dry_run else "applied"
        print(f"\n{verb} agent_versions changes: {len(version_changes)}")
        print(f"{verb} agent_templates changes: {len(template_changes)}")
        print(f"{verb} turn_traces classifier_json renames: {traces_touched}\n")
        for change in version_changes + template_changes:
            print(json.dumps(change))

        if args.dry_run or args.skip_post_check or args.agent_id is not None:
            return 0

        failures = _post_check(session, agent_id=args.agent_id)
        if failures:
            print(
                f"\n! post-check failed — {len(failures)} row(s) still carry legacy refs:",
                file=sys.stderr,
            )
            for line in failures:
                print(f"  {line}", file=sys.stderr)
            return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
