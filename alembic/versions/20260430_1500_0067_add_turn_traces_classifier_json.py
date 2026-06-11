"""Add ``turn_traces.classifier_json`` for prefill-first classifier observability.

Revision ID: 0067
Revises: 0066
Create Date: 2026-04-30 15:00:00.000000

Adds a nullable JSON column to ``turn_traces`` capturing per-turn classifier
output: backend, model, lora_name, intent_name, confidence, decode_logprobs,
cache_hit, prefill/decode token counts, elapsed_ms, error. Schema documented
in ``docs/pre-fill-intent-classifier-design/02-architecture-spec.md``
§Trace records.

Backwards-compatible. Existing rows have NULL; new turns populate via
``stores._project_classifier_trace`` from ``SemanticEventRecord.payload``
on events with ``source="classifier"``. No backfill needed.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "turn_traces",
        sa.Column("classifier_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("turn_traces", "classifier_json")
