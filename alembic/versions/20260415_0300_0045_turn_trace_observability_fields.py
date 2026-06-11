"""turn_traces: observability Phase 1 schema

Adds the fields specified in
docs/observability-system/Observability-System-First-Principles-And-Rebuild-Spec.md
and the indexes specified in its §11 "Immediate Spec Consequences".

Schema additions:
- schema_version (int, default 1)
- otel_trace_id (nullable, partial index)
- channel / modality / event_type (denormalized for indexing/filtering)
- normalized_observation_json
- guard_results_json
- model_outputs_json
- truncated_fields_json
- error_kind (string, default 'none')

Indexes:
- (conversation_id, recorded_at)
- (organization_id, graph_id, recorded_at)
- (organization_id, graph_version_id, recorded_at)
- (organization_id, error_kind, recorded_at)
- (otel_trace_id) partial WHERE otel_trace_id IS NOT NULL

GRANT changes:
- revokes UPDATE/DELETE on turn_traces from the application writer role
  (spec §7; acceptance criterion #13).
- uses current_user fallback when the role name env var is not set so the
  migration remains applicable in local/dev environments.

Revision ID: 0045_turn_trace_observability_fields
Revises: 0044_attachment_view_deliveries
Create Date: 2026-04-15 03:00:00+00:00
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision = "0045_turn_trace_observability_fields"
down_revision = "0044_attachment_view_deliveries"
branch_labels = None
depends_on = None


_NEW_COLUMNS = [
    sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("otel_trace_id", sa.String(length=64), nullable=True),
    sa.Column("channel", sa.String(length=64), nullable=False, server_default=""),
    sa.Column("modality", sa.String(length=32), nullable=False, server_default=""),
    sa.Column("event_type", sa.String(length=64), nullable=False, server_default=""),
    sa.Column("normalized_observation_json", sa.JSON(), nullable=True),
    sa.Column("guard_results_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    sa.Column("model_outputs_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    sa.Column("truncated_fields_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    sa.Column("error_kind", sa.String(length=32), nullable=False, server_default="none"),
]

_NEW_INDEXES: list[tuple[str, list[str]]] = [
    ("ix_turn_traces_conv_recorded", ["conversation_id", "recorded_at"]),
    ("ix_turn_traces_org_graph_recorded", ["organization_id", "graph_id", "recorded_at"]),
    (
        "ix_turn_traces_org_graphver_recorded",
        ["organization_id", "graph_version_id", "recorded_at"],
    ),
    ("ix_turn_traces_org_errorkind_recorded", ["organization_id", "error_kind", "recorded_at"]),
]


def _resolve_app_role() -> str:
    """Return the DB role name that the application writes under.

    Prefer the explicit env var; fall back to current_user so the
    migration does not fail in dev environments that run under a
    superuser. The GRANT revoke is a best-effort hardening step —
    application correctness does not depend on the role differing
    from the owner in local dev.
    """
    return os.getenv("RUHU_APP_DB_ROLE") or "current_user"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 1. Add columns
    for column in _NEW_COLUMNS:
        op.add_column("turn_traces", column)

    # 2. Compound indexes
    for name, cols in _NEW_INDEXES:
        op.create_index(name, "turn_traces", cols)

    # 3. Partial index on otel_trace_id (Postgres) or plain index elsewhere
    if dialect == "postgresql":
        op.execute(
            "CREATE INDEX ix_turn_traces_otel_trace_id "
            "ON turn_traces (otel_trace_id) "
            "WHERE otel_trace_id IS NOT NULL"
        )
    else:
        op.create_index("ix_turn_traces_otel_trace_id", "turn_traces", ["otel_trace_id"])

    # 4. Revoke UPDATE/DELETE from application writer role — Postgres only.
    # Spec §7: append-only enforced at DB permission layer.
    # Other dialects (SQLite in tests) do not support role-based grants;
    # append-only is enforced at the application layer there.
    if dialect == "postgresql":
        role = _resolve_app_role()
        if role != "current_user":
            # Quote the role name safely. Postgres identifiers are quoted with
            # double quotes; we also reject anything that isn't a safe subset.
            if not role.replace("_", "").isalnum():
                raise ValueError(
                    f"RUHU_APP_DB_ROLE must be alphanumeric/underscore, got: {role!r}"
                )
            op.execute(f'REVOKE UPDATE, DELETE ON TABLE turn_traces FROM "{role}"')


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Re-grant for downgrade in Postgres (best-effort)
    if dialect == "postgresql":
        role = _resolve_app_role()
        if role != "current_user" and role.replace("_", "").isalnum():
            op.execute(f'GRANT UPDATE, DELETE ON TABLE turn_traces TO "{role}"')

    op.drop_index("ix_turn_traces_otel_trace_id", table_name="turn_traces")
    for name, _ in reversed(_NEW_INDEXES):
        op.drop_index(name, table_name="turn_traces")

    for column in reversed(_NEW_COLUMNS):
        op.drop_column("turn_traces", column.name)
