"""WI-6.7 — training-scheduler trigger logic + manual enqueue API.

Decides when to retrain an agent's LoRA per
``docs/pre-fill-intent-classifier-design/05-training-pipeline.md``
§Scheduling. Three auto-trigger predicates plus a manual override:

1. **Quality drift** — agent's eval macro-F1 has dropped ≥3 points
   vs. its production LoRA's score.
2. **Volume** — agent has accumulated 1000+ new traces since the
   last training run.
3. **Catalog growth** — author published a new agent version with an
   expanded intent catalog (≥1 new intent).

Cool-down: an agent is trained at most once per 24 hours regardless
of how many predicates fire. Prevents thrashing when an agent both
drifts and accumulates volume in the same window.

This module is the *decision* layer — it doesn't dispatch jobs. The
training run itself happens in ``ruhu-ai-training/qwen``; the runtime's
job is to evaluate predicates, respect the cool-down, and produce a
deterministic trigger record the training pipeline reads. Wiring into
the ``journeys/`` periodic runner is a small follow-up
(``training_worker.py`` per spec) — kept out of this module so the
trigger logic stays unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

TriggerKind = Literal[
    "quality_drift",
    "volume",
    "catalog_growth",
    "manual",
]


@dataclass(slots=True, frozen=True)
class TrainingScheduleThresholds:
    """Tunable predicates per spec §Auto-scheduled training runs."""

    macro_f1_drop_threshold: float = 0.03
    traces_since_last_train_threshold: int = 1000
    cooldown_hours: int = 24


class TrainingTriggerInputs(BaseModel):
    """Plain-data inputs for ``evaluate_triggers``.

    Callers (the worker, the API endpoint, tests) build this from the
    agent's runtime state and pass it in. Keeps the predicate logic
    free of DB dependencies.
    """

    agent_id: str
    organization_id: str | None = None
    current_macro_f1: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Agent's most-recent eval macro-F1 (None when no eval has run).",
    )
    prod_lora_macro_f1: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Macro-F1 of the agent's current production LoRA at promotion time.",
    )
    traces_since_last_train: int = Field(default=0, ge=0)
    has_new_intents: bool = False
    last_trained_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class TriggerCheck:
    """One predicate's outcome with a human-readable rationale."""

    kind: TriggerKind
    fired: bool
    detail: str


@dataclass(slots=True, frozen=True)
class TriggerDecision:
    """Aggregate result. ``should_train`` reflects predicates AND cool-down."""

    agent_id: str
    should_train: bool
    cooldown_active: bool
    cooldown_until: datetime | None
    triggers: list[TriggerCheck] = field(default_factory=list)

    @property
    def fired_kinds(self) -> list[TriggerKind]:
        return [trigger.kind for trigger in self.triggers if trigger.fired]


# ── public entry point ─────────────────────────────────────────────────────


def evaluate_triggers(
    inputs: TrainingTriggerInputs,
    *,
    thresholds: TrainingScheduleThresholds | None = None,
    now: datetime | None = None,
) -> TriggerDecision:
    """Evaluate the three auto predicates + cool-down. Returns a decision."""
    settings = thresholds or TrainingScheduleThresholds()
    timestamp = now or datetime.now(timezone.utc)

    triggers: list[TriggerCheck] = [
        _check_quality_drift(inputs, settings),
        _check_volume(inputs, settings),
        _check_catalog_growth(inputs),
    ]
    cooldown_until = _cooldown_expiry(inputs.last_trained_at, settings)
    cooldown_active = cooldown_until is not None and timestamp < cooldown_until
    any_trigger_fired = any(trigger.fired for trigger in triggers)

    return TriggerDecision(
        agent_id=inputs.agent_id,
        should_train=any_trigger_fired and not cooldown_active,
        cooldown_active=cooldown_active,
        cooldown_until=cooldown_until,
        triggers=triggers,
    )


def evaluate_manual_request(
    inputs: TrainingTriggerInputs,
    *,
    thresholds: TrainingScheduleThresholds | None = None,
    now: datetime | None = None,
    override_cooldown: bool = False,
) -> TriggerDecision:
    """Manual training request via the API endpoint.

    Records a synthetic ``manual`` trigger so the audit trail captures
    that the run was operator-initiated rather than predicate-driven.
    Honours cool-down by default; ops engineers can set
    ``override_cooldown=True`` to force-enqueue (still recorded in the
    decision, so the audit can see the override happened).
    """
    settings = thresholds or TrainingScheduleThresholds()
    timestamp = now or datetime.now(timezone.utc)

    cooldown_until = _cooldown_expiry(inputs.last_trained_at, settings)
    cooldown_active = cooldown_until is not None and timestamp < cooldown_until

    manual_trigger = TriggerCheck(
        kind="manual",
        fired=True,
        detail="manual operator request",
    )
    return TriggerDecision(
        agent_id=inputs.agent_id,
        should_train=override_cooldown or not cooldown_active,
        cooldown_active=cooldown_active,
        cooldown_until=cooldown_until,
        triggers=[manual_trigger],
    )


# ── individual predicate checks ────────────────────────────────────────────


def _check_quality_drift(
    inputs: TrainingTriggerInputs,
    settings: TrainingScheduleThresholds,
) -> TriggerCheck:
    if inputs.current_macro_f1 is None or inputs.prod_lora_macro_f1 is None:
        return TriggerCheck(
            kind="quality_drift",
            fired=False,
            detail="no current_macro_f1 / prod_lora_macro_f1 baseline",
        )
    drop = inputs.prod_lora_macro_f1 - inputs.current_macro_f1
    fired = drop >= settings.macro_f1_drop_threshold
    return TriggerCheck(
        kind="quality_drift",
        fired=fired,
        detail=(
            f"current={inputs.current_macro_f1:.4f} prod={inputs.prod_lora_macro_f1:.4f} "
            f"drop={drop:+.4f} (≥ {settings.macro_f1_drop_threshold:.4f})"
        ),
    )


def _check_volume(
    inputs: TrainingTriggerInputs,
    settings: TrainingScheduleThresholds,
) -> TriggerCheck:
    fired = inputs.traces_since_last_train >= settings.traces_since_last_train_threshold
    return TriggerCheck(
        kind="volume",
        fired=fired,
        detail=(
            f"traces_since_last_train={inputs.traces_since_last_train} "
            f"(≥ {settings.traces_since_last_train_threshold})"
        ),
    )


def _check_catalog_growth(inputs: TrainingTriggerInputs) -> TriggerCheck:
    return TriggerCheck(
        kind="catalog_growth",
        fired=inputs.has_new_intents,
        detail="has_new_intents=" + ("true" if inputs.has_new_intents else "false"),
    )


def _cooldown_expiry(
    last_trained_at: datetime | None,
    settings: TrainingScheduleThresholds,
) -> datetime | None:
    if last_trained_at is None:
        return None
    return last_trained_at + timedelta(hours=settings.cooldown_hours)


__all__ = [
    "TrainingScheduleThresholds",
    "TrainingTriggerInputs",
    "TriggerCheck",
    "TriggerDecision",
    "TriggerKind",
    "evaluate_manual_request",
    "evaluate_triggers",
]
