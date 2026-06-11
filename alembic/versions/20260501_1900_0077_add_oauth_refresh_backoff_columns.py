"""add OAuth refresh backoff columns

Adds two columns to ``api_connections`` that drive exponential-backoff
behaviour in the token refresher:

* ``refresh_failure_count`` — consecutive failed refresh attempts; reset
  to 0 on every successful refresh. Drives the backoff curve.
* ``last_refresh_attempt_at`` — UTC timestamp of the last refresh
  attempt (success or failure). The refresher skips connections whose
  last attempt + computed backoff has not yet elapsed.

Both columns are NULLABLE-or-defaulted-zero so the migration is
backwards compatible: existing rows automatically get
``refresh_failure_count = 0`` and ``last_refresh_attempt_at = NULL``,
which is the same state the refresher would synthesize for a fresh
connection.

Revision ID: 0077
Revises: 0076
Create Date: 2026-05-01 19:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_connections",
        sa.Column(
            "refresh_failure_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "api_connections",
        sa.Column(
            "last_refresh_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("api_connections", "last_refresh_attempt_at")
    op.drop_column("api_connections", "refresh_failure_count")
