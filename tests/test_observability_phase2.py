"""Audit ↔ Trace cross-layer correlation tests.

Originally authored under Observability Implementation Plan Phase 2. The
customer-facing redaction surface from that phase has been removed per the
V1 scope revision (Ruhu-staff observability is V1; customer observability
is V2+). What remains — and is still load-bearing — is the ``trace_id``
field on ``AuditEvent`` + ``audit_events.trace_id`` column: the correlation
anchor defined in spec §1.1 that lets staff pivot from any audit event
back to the source ``TurnTrace``.

Scope:

- ``AuditEvent`` carries ``trace_id`` and it participates in the hash chain
- ``audit_events.trace_id`` round-trips through the SQLAlchemy store

Removed with the customer surface:

- ``SQLAlchemyTraceStore.get_by_trace_id``
- ``SQLAlchemyTraceStore.redact_fields``
- ``POST /admin/traces/{id}/redact`` endpoint
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ruhu.audit.events import AuditEvent
from ruhu.audit.store import AuditEventRecord, SQLAlchemyAuditStore


# ── AuditEvent.trace_id + hash chain ─────────────────────────────────


def test_audit_event_has_trace_id_field_default_none() -> None:
    event = AuditEvent(event_type="resource.created", organization_id="org1")
    assert event.trace_id is None


def test_audit_event_trace_id_participates_in_hash_chain() -> None:
    """Two otherwise-identical events with different trace_ids hash differently."""
    kwargs = dict(
        event_id="evt_1",
        event_type="admin.settings_changed",
        organization_id="org1",
        actor_id="user_1",
        resource_type="turn_trace",
        resource_id="trace_abc",
        detail={"action": "trace_redaction"},
        created_at="2026-04-15T04:00:00Z",
    )
    e_a = AuditEvent(**kwargs, trace_id="trace_abc")
    e_b = AuditEvent(**kwargs, trace_id="trace_xyz")
    e_none = AuditEvent(**kwargs)  # trace_id = None

    h_a = e_a.compute_hash()
    h_b = e_b.compute_hash()
    h_none = e_none.compute_hash()

    assert h_a != h_b, "trace_id must affect content_hash"
    assert h_a != h_none
    # Hash remains stable when recomputed
    assert h_a == e_a.compute_hash()


def test_audit_event_to_dict_includes_trace_id() -> None:
    event = AuditEvent(
        event_type="resource.updated",
        organization_id="org1",
        trace_id="trace_abc",
    )
    assert event.to_dict()["trace_id"] == "trace_abc"


def test_audit_store_persists_trace_id_round_trip() -> None:
    """trace_id written and read back through SQLAlchemy round-trip."""
    engine = create_engine("sqlite:///:memory:")
    AuditEventRecord.__table__.create(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    store = SQLAlchemyAuditStore(sf)

    event = AuditEvent(
        event_type="admin.settings_changed",
        organization_id="org1",
        actor_id="user_1",
        resource_type="turn_trace",
        resource_id="trace_abc",
        trace_id="trace_abc",
        detail={"action": "trace_redaction", "fields": ["emitted_messages"]},
    )
    event.finalize()
    store.save(event)

    retrieved = store.get(event.event_id, organization_id="org1")
    assert retrieved is not None
    assert retrieved.trace_id == "trace_abc"
