"""phone number registry foundation

Revision ID: 0026_phone_number_registry
Revises: 0025_realtime_outbox_fix
Create Date: 2026-04-11 19:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026_phone_number_registry"
down_revision = "0025_realtime_outbox_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phone_numbers",
        sa.Column("phone_number_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("e164_number", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ownership_mode", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("phone_number_id"),
        sa.UniqueConstraint("e164_number", name="uq_phone_numbers_e164"),
    )
    op.create_index("ix_phone_numbers_organization_id", "phone_numbers", ["organization_id"], unique=False)
    op.create_index("ix_phone_numbers_country_code", "phone_numbers", ["country_code"], unique=False)
    op.create_index("ix_phone_numbers_status", "phone_numbers", ["status"], unique=False)

    op.create_table(
        "phone_number_bindings",
        sa.Column("binding_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("phone_number_id", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("provider_resource_id", sa.String(length=255), nullable=True),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("health_status", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("transport_metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["phone_number_id"], ["phone_numbers.phone_number_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("binding_id"),
        sa.UniqueConstraint("provider", "provider_resource_id", name="uq_phone_number_bindings_provider_resource"),
    )
    op.create_index(
        "ix_phone_number_bindings_organization_id",
        "phone_number_bindings",
        ["organization_id"],
        unique=False,
    )
    op.create_index("ix_phone_number_bindings_phone_number_id", "phone_number_bindings", ["phone_number_id"], unique=False)
    op.create_index("ix_phone_number_bindings_channel", "phone_number_bindings", ["channel"], unique=False)
    op.create_index("ix_phone_number_bindings_provider", "phone_number_bindings", ["provider"], unique=False)
    op.create_index(
        "ix_phone_number_bindings_provider_resource_id",
        "phone_number_bindings",
        ["provider_resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_phone_number_bindings_verification_status",
        "phone_number_bindings",
        ["verification_status"],
        unique=False,
    )
    op.create_index(
        "ix_phone_number_bindings_health_status",
        "phone_number_bindings",
        ["health_status"],
        unique=False,
    )
    op.create_index("ix_phone_number_bindings_is_active", "phone_number_bindings", ["is_active"], unique=False)

    op.create_table(
        "phone_number_routes",
        sa.Column("route_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("phone_number_id", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("graph_id", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["graphs.graph_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["phone_number_id"], ["phone_numbers.phone_number_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("route_id"),
    )
    op.create_index("ix_phone_number_routes_organization_id", "phone_number_routes", ["organization_id"], unique=False)
    op.create_index("ix_phone_number_routes_phone_number_id", "phone_number_routes", ["phone_number_id"], unique=False)
    op.create_index("ix_phone_number_routes_channel", "phone_number_routes", ["channel"], unique=False)
    op.create_index("ix_phone_number_routes_graph_id", "phone_number_routes", ["graph_id"], unique=False)
    op.create_index("ix_phone_number_routes_priority", "phone_number_routes", ["priority"], unique=False)
    op.create_index("ix_phone_number_routes_enabled", "phone_number_routes", ["enabled"], unique=False)
    op.create_index(
        "uq_phone_number_routes_enabled_channel",
        "phone_number_routes",
        ["phone_number_id", "channel"],
        unique=True,
        postgresql_where=sa.text("enabled = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_phone_number_routes_enabled_channel", table_name="phone_number_routes")
    op.drop_index("ix_phone_number_routes_enabled", table_name="phone_number_routes")
    op.drop_index("ix_phone_number_routes_priority", table_name="phone_number_routes")
    op.drop_index("ix_phone_number_routes_graph_id", table_name="phone_number_routes")
    op.drop_index("ix_phone_number_routes_channel", table_name="phone_number_routes")
    op.drop_index("ix_phone_number_routes_phone_number_id", table_name="phone_number_routes")
    op.drop_index("ix_phone_number_routes_organization_id", table_name="phone_number_routes")
    op.drop_table("phone_number_routes")

    op.drop_index("ix_phone_number_bindings_is_active", table_name="phone_number_bindings")
    op.drop_index("ix_phone_number_bindings_health_status", table_name="phone_number_bindings")
    op.drop_index("ix_phone_number_bindings_verification_status", table_name="phone_number_bindings")
    op.drop_index("ix_phone_number_bindings_provider_resource_id", table_name="phone_number_bindings")
    op.drop_index("ix_phone_number_bindings_provider", table_name="phone_number_bindings")
    op.drop_index("ix_phone_number_bindings_channel", table_name="phone_number_bindings")
    op.drop_index("ix_phone_number_bindings_phone_number_id", table_name="phone_number_bindings")
    op.drop_index("ix_phone_number_bindings_organization_id", table_name="phone_number_bindings")
    op.drop_table("phone_number_bindings")

    op.drop_index("ix_phone_numbers_status", table_name="phone_numbers")
    op.drop_index("ix_phone_numbers_country_code", table_name="phone_numbers")
    op.drop_index("ix_phone_numbers_organization_id", table_name="phone_numbers")
    op.drop_table("phone_numbers")
