"""Idempotency tests for the Stripe webhook handler.

Stripe retries delivery on any 4xx / 5xx response or network error, so the
same ``event.id`` may arrive multiple times. The webhook handler must
process each ``event.id`` at most once.
"""
from __future__ import annotations

import pytest

from ruhu.billing.store import InMemoryBillingStore


def test_claim_webhook_event_returns_true_on_first_insert() -> None:
    store = InMemoryBillingStore()
    assert store.claim_webhook_event(
        event_id="evt_1",
        provider="stripe",
        event_type="invoice.paid",
    ) is True


def test_claim_webhook_event_returns_false_on_duplicate() -> None:
    store = InMemoryBillingStore()
    store.claim_webhook_event(event_id="evt_1", provider="stripe", event_type="invoice.paid")
    assert store.claim_webhook_event(
        event_id="evt_1",
        provider="stripe",
        event_type="invoice.paid",
    ) is False


def test_claim_webhook_event_distinguishes_event_ids() -> None:
    store = InMemoryBillingStore()
    assert store.claim_webhook_event(event_id="evt_1", provider="stripe", event_type="x") is True
    assert store.claim_webhook_event(event_id="evt_2", provider="stripe", event_type="x") is True


def test_mark_webhook_event_status_records_processed() -> None:
    store = InMemoryBillingStore()
    store.claim_webhook_event(event_id="evt_1", provider="stripe", event_type="invoice.paid")
    store.mark_webhook_event_status(event_id="evt_1", status="processed")
    record = store._webhook_events["evt_1"]
    assert record["status"] == "processed"
    assert record["processed_at"] is not None


def test_mark_webhook_event_status_records_failure_with_error() -> None:
    store = InMemoryBillingStore()
    store.claim_webhook_event(event_id="evt_1", provider="stripe", event_type="invoice.paid")
    store.mark_webhook_event_status(
        event_id="evt_1",
        status="failed",
        error_message="db unavailable",
    )
    record = store._webhook_events["evt_1"]
    assert record["status"] == "failed"
    assert record["error_message"] == "db unavailable"


def test_mark_webhook_event_status_noop_for_unknown_event() -> None:
    store = InMemoryBillingStore()
    # Should not raise — webhook handler may legitimately call mark_status
    # before claim if the storage layer was reset between requests.
    store.mark_webhook_event_status(event_id="evt_unknown", status="processed")


# ── SQLAlchemy implementation parity ──────────────────────────────────


def test_sqlalchemy_store_idempotency_via_unique_constraint(tmp_path) -> None:
    """The SQLAlchemy store must reject duplicate event_id via PK constraint.

    This test only runs if SQLite is available (which it is in stdlib).
    Validates that the IntegrityError path on duplicate INSERT collapses
    cleanly to ``claim_webhook_event() -> False`` instead of bubbling.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from ruhu.billing.store import SQLAlchemyBillingStore

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    store = SQLAlchemyBillingStore(session_factory)

    first = store.claim_webhook_event(
        event_id="evt_unique",
        provider="stripe",
        event_type="invoice.paid",
    )
    second = store.claim_webhook_event(
        event_id="evt_unique",
        provider="stripe",
        event_type="invoice.paid",
    )

    assert first is True
    assert second is False

    store.mark_webhook_event_status(event_id="evt_unique", status="processed")
    # Idempotent on already-processed events: second mark should be a no-op.
    store.mark_webhook_event_status(event_id="evt_unique", status="processed")
