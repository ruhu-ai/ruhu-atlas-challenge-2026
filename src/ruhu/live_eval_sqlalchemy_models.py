"""SQLAlchemy model for ``LiveTurnScore`` persistence.

Single-table design — one row per (trace, scorer, version) triple. Re-running
the same scorer at the same version on the same trace is idempotent under the
primary key; re-running with a bumped version creates a new row, preserving
the history needed for A/B comparisons of scoring logic over time.

The table is registered automatically into the runtime tenant RLS policy set
because it carries an ``organization_id`` column — see
``ruhu.db._compute_runtime_tenant_rls_tables``. No manual list edit needed.
"""
from __future__ import annotations

from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db_models import Base


class LiveTurnScoreRecord(Base):
    """A single quality dimension score for a sampled live turn."""

    __tablename__ = "live_turn_scores"
    __table_args__ = (
        # Composite primary key (trace_id, scorer_name, scorer_version):
        # bumping the version produces a new row (A/B history); rerunning
        # the same scorer at the same version is idempotent via UPSERT.
        # Indexes below cover the two common read patterns: per-conversation
        # rollups and per-org time-window queries.
        Index("ix_live_turn_scores_conversation", "conversation_id"),
        Index(
            "ix_live_turn_scores_org_scored_at",
            "organization_id",
            "scored_at",
        ),
        Index(
            "ix_live_turn_scores_dimension_scored_at",
            "dimension",
            "scored_at",
        ),
    )

    trace_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    scorer_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    scorer_version: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Carried for tenant scoping (RLS) — must be NOT NULL so the policy can
    # match against it without ambiguity. Live-eval scores written by
    # internal jobs that aren't org-scoped are out of scope today.
    organization_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # The taxonomy dimension this row scores. Kept as a string (not enum)
    # because Postgres ENUM migrations are painful and the set is fixed by
    # ``QUALITY_DIMENSIONS`` at the application layer anyway.
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)

    # Scores are continuous in [0.0, 1.0]. Stored as float (not Numeric)
    # because we never do arithmetic-with-rounding on them — they're
    # observed values, not currency.
    score: Mapped[float] = mapped_column(Float, nullable=False)

    # Optional human-readable rationale; capped to avoid runaway storage.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    scored_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
