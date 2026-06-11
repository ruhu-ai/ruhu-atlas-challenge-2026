"""scope billing usage idempotency by organization

Revision ID: 0027_billing_usage_scope
Revises: 0026_journey_worker_leases, 0026_phone_number_registry
Create Date: 2026-04-11 21:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0027_billing_usage_scope"
down_revision = ("0026_journey_worker_leases", "0026_phone_number_registry")
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        constraint.get("name") == constraint_name
        for constraint in inspector.get_unique_constraints(table_name)
    )


def upgrade() -> None:
    if not _has_table("billing_usage_records"):
        return
    if _has_unique_constraint("billing_usage_records", "uq_billing_usage_records_usage_key"):
        op.drop_constraint(
            "uq_billing_usage_records_usage_key",
            "billing_usage_records",
            type_="unique",
        )
    if not _has_unique_constraint("billing_usage_records", "uq_billing_usage_records_org_usage_key"):
        op.create_unique_constraint(
            "uq_billing_usage_records_org_usage_key",
            "billing_usage_records",
            ["organization_id", "usage_key"],
        )


def downgrade() -> None:
    if not _has_table("billing_usage_records"):
        return
    if _has_unique_constraint("billing_usage_records", "uq_billing_usage_records_org_usage_key"):
        op.drop_constraint(
            "uq_billing_usage_records_org_usage_key",
            "billing_usage_records",
            type_="unique",
        )
    if not _has_unique_constraint("billing_usage_records", "uq_billing_usage_records_usage_key"):
        op.create_unique_constraint(
            "uq_billing_usage_records_usage_key",
            "billing_usage_records",
            ["usage_key"],
        )
