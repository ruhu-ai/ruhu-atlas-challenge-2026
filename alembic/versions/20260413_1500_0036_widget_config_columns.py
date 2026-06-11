"""graphs: add per-agent widget configuration columns

Adds ``is_widget_enabled``, ``widget_mode``, and ``widget_config`` to the
``graphs`` table so each agent carries its own widget branding and behaviour
settings.

Design notes:
  - ``is_widget_enabled`` defaults to false — widget embedding is opt-in.
  - ``widget_mode`` defaults to 'multimodal' — the richest experience.
  - ``widget_config`` stores visual/UX settings as JSONB (validated by
    Pydantic on write).  Empty dict ``{}`` means "use platform defaults".
  - All three columns have server defaults, so no backfill is needed.

Revision ID: 0036_widget_config_columns
Revises: 0035_api_connection_token_expiry
Create Date: 2026-04-13 15:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0036_widget_config_columns"
down_revision = "0035_api_connection_token_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graphs",
        sa.Column(
            "is_widget_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "graphs",
        sa.Column(
            "widget_mode",
            sa.String(20),
            nullable=False,
            server_default="multimodal",
        ),
    )
    op.add_column(
        "graphs",
        sa.Column(
            "widget_config",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("graphs", "widget_config")
    op.drop_column("graphs", "widget_mode")
    op.drop_column("graphs", "is_widget_enabled")
