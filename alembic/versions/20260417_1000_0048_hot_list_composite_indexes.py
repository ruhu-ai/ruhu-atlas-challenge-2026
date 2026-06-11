"""Add composite (organization_id, created_at DESC) indexes to hot list tables.

The existing schema has separate single-column indexes on ``organization_id``
and ``created_at`` for ``conversations``, ``turn_traces``, and
``realtime_events``, which forces Postgres into an index-merge + sort for
the extremely common "list the most recent N rows for this org" pattern used
by the dashboard, inbox, and analytics list endpoints.

A composite ``(organization_id, created_at DESC)`` index lets those queries
stream rows in the correct order with no sort. The existing single-column
indexes are kept because they still serve per-column predicates (e.g.
``WHERE organization_id = ?`` without a sort, and ``WHERE created_at > ?``
for TTL sweeps).

``CREATE INDEX CONCURRENTLY`` is used so the migration can run against a live
table without blocking writes. The Alembic default is to wrap each migration
in a transaction, which is incompatible with CONCURRENTLY — we opt out via
``autocommit_block``.

``turn_traces`` uses ``recorded_at`` (not ``created_at``).
"""

from __future__ import annotations

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


_NEW_INDEXES: list[tuple[str, str, str]] = [
    # (index_name, table, timestamp column used for DESC sort)
    ("ix_conversations_org_created", "conversations", "created_at"),
    ("ix_turn_traces_org_recorded", "turn_traces", "recorded_at"),
    ("ix_realtime_events_org_created", "realtime_events", "created_at"),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite / other test backends: use a plain CREATE INDEX so the
        # schema still matches at the ORM level, but skip the CONCURRENTLY
        # keyword (unsupported outside Postgres).
        for name, table, ts_col in _NEW_INDEXES:
            op.create_index(name, table, ["organization_id", ts_col], unique=False)
        return

    # Postgres: CONCURRENTLY requires running outside a transaction.
    with op.get_context().autocommit_block():
        for name, table, ts_col in _NEW_INDEXES:
            op.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" '
                f'ON "{table}" ("organization_id", "{ts_col}" DESC)'
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        for name, _table, _ts in _NEW_INDEXES:
            op.drop_index(name)
        return

    with op.get_context().autocommit_block():
        for name, _table, _ts in _NEW_INDEXES:
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
