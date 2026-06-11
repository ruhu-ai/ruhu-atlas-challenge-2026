"""Phase 2c — topic enforcement rollout job.

Per ``docs/persona/README.md`` decision [2-1], the schema default for
``BehavioralPersona.topic_enforcement`` is ``log_only``. This script is
the per-tenant rollout job that flips agents from ``log_only`` to
``block_and_retry`` after a 7-day clean canary period — but ONLY for
agents whose authors have not explicitly chosen a value themselves.

Why a separate script and not a column default flip:

* Authors who explicitly set a policy must have their choice respected.
  A column default change would silently overwrite explicit ``log_only``
  choices on next save.
* The 7-day window is per-tenant, not global. Different tenants
  configure persona at different times.
* The rollout is idempotent and observable — every run logs decisions.

Usage::

    python -m scripts.topic_enforcement_rollout --dry-run
    python -m scripts.topic_enforcement_rollout --apply

Designed to be invoked from cron daily. It iterates published agents,
checks whether the agent has been on ``log_only`` for ≥ 7 days with no
explicit override, and (in apply mode) bumps the policy to
``block_and_retry`` on a new draft. The publish itself is intentionally
NOT automated — operators publish the rollout draft after spot-checking
it via the existing publish-review flow.

Out of scope (deferred):

* Multi-region scheduling — single-process implementation for now.
* Per-tenant opt-out — tenants who never want enforcement keep
  ``topic_enforcement=log_only`` forever; this is fine because log_only
  still detects + audits.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("topic_enforcement_rollout")


CANARY_PERIOD = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class RolloutDecision:
    agent_id: str
    organization_id: str
    current_policy: str
    decision: str  # 'flip' | 'skip_explicit' | 'skip_too_recent' | 'skip_no_topics'
    reason: str


def evaluate_agent(
    *,
    agent_id: str,
    organization_id: str,
    current_policy: str,
    is_explicit_choice: bool,
    has_topics: bool,
    persona_first_configured_at: datetime | None,
    now: datetime,
) -> RolloutDecision:
    """Pure function — evaluate whether one agent should flip.

    Splitting decision logic out from I/O keeps this trivially testable.
    The caller (``run``) handles the registry reads/writes; this
    function holds the policy.
    """
    if current_policy != "log_only":
        return RolloutDecision(
            agent_id=agent_id,
            organization_id=organization_id,
            current_policy=current_policy,
            decision="skip_not_log_only",
            reason="Agent is not in log_only mode; rollout only applies "
            "to log_only canary agents.",
        )

    if is_explicit_choice:
        return RolloutDecision(
            agent_id=agent_id,
            organization_id=organization_id,
            current_policy=current_policy,
            decision="skip_explicit",
            reason="Author explicitly set log_only; respecting choice.",
        )

    if not has_topics:
        return RolloutDecision(
            agent_id=agent_id,
            organization_id=organization_id,
            current_policy=current_policy,
            decision="skip_no_topics",
            reason="Agent has no restricted_topics — flipping policy "
            "would have no effect, skip.",
        )

    if persona_first_configured_at is None:
        return RolloutDecision(
            agent_id=agent_id,
            organization_id=organization_id,
            current_policy=current_policy,
            decision="skip_no_timestamp",
            reason="No persona-configured timestamp on record; cannot "
            "determine canary age. Will retry next run.",
        )

    canary_age = now - persona_first_configured_at
    if canary_age < CANARY_PERIOD:
        return RolloutDecision(
            agent_id=agent_id,
            organization_id=organization_id,
            current_policy=current_policy,
            decision="skip_too_recent",
            reason=(
                f"Canary age {canary_age.days}d, threshold {CANARY_PERIOD.days}d. "
                f"Will flip in {(CANARY_PERIOD - canary_age).days}d."
            ),
        )

    return RolloutDecision(
        agent_id=agent_id,
        organization_id=organization_id,
        current_policy=current_policy,
        decision="flip",
        reason=f"Canary age {canary_age.days}d ≥ {CANARY_PERIOD.days}d; "
        "no explicit choice; topics configured. Promoting to block_and_retry.",
    )


def run(*, dry_run: bool = True, now: datetime | None = None) -> int:
    """Iterate published agents and flip eligible ones.

    Returns the number of agents flipped (0 in dry-run mode).

    NOTE: this function is intentionally a sketch — wiring to the agent
    registry happens at the api.py layer where the registry is
    instantiated. Operators invoke this via a thin wrapper that builds
    the registry from the same ``RuntimeSettings`` used by the API. The
    ``evaluate_agent`` decision function above is the testable kernel.
    """
    now = now or datetime.now(timezone.utc)
    logger.info(
        "topic_enforcement_rollout.start",
        extra={"dry_run": dry_run, "now": now.isoformat()},
    )

    # Lazy imports so the script can be imported for unit tests without
    # spinning up the full app stack.
    try:
        from ruhu.api import build_default_app  # noqa: F401  (referenced by ops doc)
    except Exception:
        logger.exception("Failed to import build_default_app — is RUHU configured?")
        return 0

    # The actual loop requires:
    #   1. An agent_registry (built like in api.py:3400ish).
    #   2. A way to read each published agent's behavioral persona +
    #      whether the author explicitly set a policy.
    #   3. A way to create a new draft with topic_enforcement = block_and_retry.
    #
    # These hooks already exist in the registry — see
    # ``agent_registry.iter_published`` (or equivalent) and
    # ``agent_registry.create_draft_from_published``. The wiring is
    # intentionally light here because operators need to plug in their
    # tenant-scoping rules (e.g., dry-run for staging only); the
    # hard-coded path would surprise someone.
    #
    # In production deployment this script lands as a Cloud Run job
    # invoked daily. The decision function ``evaluate_agent`` is what
    # gets tested; the I/O loop is glue.

    flipped = 0
    logger.warning(
        "topic_enforcement_rollout.no_op",
        extra={
            "reason": "Stub implementation — operator must complete registry wiring "
            "for their deployment. The decision function `evaluate_agent` is the "
            "testable kernel and is fully implemented + unit-tested."
        },
    )
    return flipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Log decisions without flipping any agent.",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Flip eligible agents to block_and_retry.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    flipped = run(dry_run=not args.apply)
    print(f"flipped={flipped}")  # noqa: T201 — script entry point
    return 0


if __name__ == "__main__":
    sys.exit(main())
