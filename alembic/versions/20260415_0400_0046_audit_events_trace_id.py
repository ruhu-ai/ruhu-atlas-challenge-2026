"""audit_events: add trace_id correlation column (Observability Phase 2)

Adds ``trace_id`` (nullable) to ``audit_events`` so turn-derived audit
records can be joined back to the append-only ``turn_traces`` row
without scanning the detail blob. See
``docs/observability-system/Observability-System-First-Principles-And-Rebuild-Spec.md``
§ "Cross-layer correlation".

Schema additions:
- ``trace_id`` (String(64), nullable) — matches ``turn_traces.trace_id`` width.
- Index ``ix_audit_trace_id`` for direct trace → audit lookup.

The hash chain in ``AuditEvent.compute_hash()`` now covers ``trace_id``
(treating None as ``""``). Existing rows carry ``trace_id = NULL``; their
historical hashes remain valid because they were computed against the
old schema — verification consumers pin hashes to the schema version in
effect when the row was written.

Revision ID: 0046_audit_events_trace_id
Revises: 0045_turn_trace_observability_fields
Create Date: 2026-04-15 04:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046_audit_events_trace_id"
down_revision = "0045_turn_trace_observability_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("trace_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_audit_trace_id", "audit_events", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_trace_id", table_name="audit_events")
    op.drop_column("audit_events", "trace_id")
