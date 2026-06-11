"""Tests for src/ruhu/classifier/training_scheduler.py — WI-6.7 logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ruhu.classifier.training_scheduler import (
    TrainingScheduleThresholds,
    TrainingTriggerInputs,
    evaluate_manual_request,
    evaluate_triggers,
)


def _now() -> datetime:
    return datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _inputs(**overrides) -> TrainingTriggerInputs:
    base = dict(agent_id="agent_a")
    base.update(overrides)
    return TrainingTriggerInputs(**base)


# ── quality drift ──────────────────────────────────────────────────────────


def test_quality_drift_fires_when_macro_f1_drops_three_pp_or_more() -> None:
    decision = evaluate_triggers(
        _inputs(current_macro_f1=0.82, prod_lora_macro_f1=0.85),
        now=_now(),
    )
    assert "quality_drift" in decision.fired_kinds
    assert decision.should_train


def test_quality_drift_does_not_fire_at_two_pp_drop() -> None:
    decision = evaluate_triggers(
        _inputs(current_macro_f1=0.83, prod_lora_macro_f1=0.85),
        now=_now(),
    )
    assert "quality_drift" not in decision.fired_kinds


def test_quality_drift_skipped_without_baselines() -> None:
    """Missing current or prod scores → predicate doesn't fire (and isn't a false positive)."""
    decision = evaluate_triggers(
        _inputs(current_macro_f1=None, prod_lora_macro_f1=0.85),
        now=_now(),
    )
    assert "quality_drift" not in decision.fired_kinds
    decision = evaluate_triggers(
        _inputs(current_macro_f1=0.85, prod_lora_macro_f1=None),
        now=_now(),
    )
    assert "quality_drift" not in decision.fired_kinds


# ── volume ─────────────────────────────────────────────────────────────────


def test_volume_fires_at_one_thousand_traces() -> None:
    decision = evaluate_triggers(
        _inputs(traces_since_last_train=1000),
        now=_now(),
    )
    assert "volume" in decision.fired_kinds


def test_volume_does_not_fire_at_999_traces() -> None:
    decision = evaluate_triggers(
        _inputs(traces_since_last_train=999),
        now=_now(),
    )
    assert "volume" not in decision.fired_kinds


# ── catalog growth ─────────────────────────────────────────────────────────


def test_catalog_growth_fires_when_has_new_intents() -> None:
    decision = evaluate_triggers(
        _inputs(has_new_intents=True),
        now=_now(),
    )
    assert "catalog_growth" in decision.fired_kinds


def test_catalog_growth_does_not_fire_when_intents_unchanged() -> None:
    decision = evaluate_triggers(
        _inputs(has_new_intents=False),
        now=_now(),
    )
    assert "catalog_growth" not in decision.fired_kinds


# ── cool-down ─────────────────────────────────────────────────────────────


def test_cooldown_blocks_training_within_24_hours() -> None:
    last = _now() - timedelta(hours=12)
    decision = evaluate_triggers(
        _inputs(traces_since_last_train=2000, last_trained_at=last),
        now=_now(),
    )
    assert decision.cooldown_active is True
    assert decision.should_train is False
    # Trigger still records as fired — audit shows what would have run
    assert "volume" in decision.fired_kinds


def test_cooldown_clears_after_24_hours() -> None:
    last = _now() - timedelta(hours=24, minutes=1)
    decision = evaluate_triggers(
        _inputs(traces_since_last_train=2000, last_trained_at=last),
        now=_now(),
    )
    assert decision.cooldown_active is False
    assert decision.should_train is True


def test_no_last_trained_at_means_no_cooldown() -> None:
    decision = evaluate_triggers(
        _inputs(traces_since_last_train=2000, last_trained_at=None),
        now=_now(),
    )
    assert decision.cooldown_active is False
    assert decision.should_train is True


def test_cooldown_active_still_blocks_when_no_predicate_fires() -> None:
    """Belt-and-braces: if cool-down is active and nothing fires,
    should_train stays False (predicate AND cooldown contract)."""
    last = _now() - timedelta(hours=12)
    decision = evaluate_triggers(
        _inputs(last_trained_at=last),
        now=_now(),
    )
    assert decision.should_train is False


def test_no_predicates_fire_means_no_train_even_without_cooldown() -> None:
    decision = evaluate_triggers(
        _inputs(last_trained_at=None),
        now=_now(),
    )
    assert decision.cooldown_active is False
    assert decision.should_train is False


# ── manual ────────────────────────────────────────────────────────────────


def test_manual_request_always_records_manual_trigger() -> None:
    decision = evaluate_manual_request(_inputs(), now=_now())
    assert "manual" in decision.fired_kinds
    assert decision.should_train is True


def test_manual_request_honours_cooldown_by_default() -> None:
    last = _now() - timedelta(hours=12)
    decision = evaluate_manual_request(
        _inputs(last_trained_at=last),
        now=_now(),
    )
    assert decision.cooldown_active is True
    assert decision.should_train is False


def test_manual_request_override_cooldown_forces_train() -> None:
    last = _now() - timedelta(hours=12)
    decision = evaluate_manual_request(
        _inputs(last_trained_at=last),
        now=_now(),
        override_cooldown=True,
    )
    assert decision.cooldown_active is True
    assert decision.should_train is True


# ── thresholds override ───────────────────────────────────────────────────


def test_threshold_overrides_lower_volume_bar() -> None:
    relaxed = TrainingScheduleThresholds(traces_since_last_train_threshold=100)
    decision = evaluate_triggers(
        _inputs(traces_since_last_train=150),
        thresholds=relaxed,
        now=_now(),
    )
    assert "volume" in decision.fired_kinds


def test_threshold_overrides_lower_quality_drop_bar() -> None:
    strict = TrainingScheduleThresholds(macro_f1_drop_threshold=0.01)
    decision = evaluate_triggers(
        _inputs(current_macro_f1=0.84, prod_lora_macro_f1=0.85),
        thresholds=strict,
        now=_now(),
    )
    assert "quality_drift" in decision.fired_kinds


def test_threshold_overrides_lengthen_cooldown() -> None:
    long_cooldown = TrainingScheduleThresholds(cooldown_hours=48)
    last = _now() - timedelta(hours=30)
    decision = evaluate_triggers(
        _inputs(traces_since_last_train=2000, last_trained_at=last),
        thresholds=long_cooldown,
        now=_now(),
    )
    assert decision.cooldown_active is True


# ── multiple triggers / cooldown report ──────────────────────────────────


def test_multiple_triggers_fire_independently() -> None:
    decision = evaluate_triggers(
        _inputs(
            current_macro_f1=0.80,
            prod_lora_macro_f1=0.85,
            traces_since_last_train=2000,
            has_new_intents=True,
        ),
        now=_now(),
    )
    assert {"quality_drift", "volume", "catalog_growth"}.issubset(set(decision.fired_kinds))
    assert decision.should_train is True


def test_cooldown_until_reflects_24_hour_default() -> None:
    last = _now() - timedelta(hours=4)
    decision = evaluate_triggers(
        _inputs(last_trained_at=last),
        now=_now(),
    )
    assert decision.cooldown_until == last + timedelta(hours=24)
