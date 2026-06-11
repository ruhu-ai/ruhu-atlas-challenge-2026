"""Add rate_limit_multiplier to billing_plans table."""
from alembic import op
import sqlalchemy as sa

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "billing_plans",
        sa.Column("rate_limit_multiplier", sa.Numeric(5, 2), nullable=True),
    )


def downgrade():
    op.drop_column("billing_plans", "rate_limit_multiplier")
