"""add rules foundation tables

Revision ID: 0018_rules_foundation
Revises: 0017_intent_tags_review
Create Date: 2026-04-11 08:30:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0018_rules_foundation"
down_revision = "0017_intent_tags_review"
branch_labels = None
depends_on = None


_RLS_TABLES = (
    "rule_definitions",
    "rule_definition_revisions",
    "rule_libraries",
    "rule_bindings",
)


def _policy_sql(table_name: str) -> sa.TextClause:
    policy_name = f"tenant_scope_{table_name}"
    return sa.text(
        f'''
        CREATE POLICY "{policy_name}" ON "{table_name}"
        USING (
            current_setting('app.current_is_superuser', true) = 'true'
            OR organization_id IS NULL
            OR organization_id = nullif(current_setting('app.current_organization_id', true), '')
        )
        WITH CHECK (
            current_setting('app.current_is_superuser', true) = 'true'
            OR organization_id IS NULL
            OR organization_id = nullif(current_setting('app.current_organization_id', true), '')
        )
        '''
    )


def upgrade() -> None:
    op.create_table(
        "rule_definitions",
        sa.Column("rule_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rule_definitions_organization_id", "rule_definitions", ["organization_id"])
    op.create_index("ix_rule_definitions_created_by_user_id", "rule_definitions", ["created_by_user_id"])
    op.create_index("ix_rule_definitions_archived_at", "rule_definitions", ["archived_at"])

    op.create_table(
        "rule_definition_revisions",
        sa.Column("revision_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("rule_id", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("predicate_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("effect_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("tags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["rule_definitions.rule_id"],
            name="fk_rule_definition_revisions_rule_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("rule_id", "revision", name="uq_rule_definition_revisions_rule_revision"),
        sa.CheckConstraint(
            "(status <> 'published') OR (published_at IS NOT NULL)",
            name="ck_rule_definition_revisions_published_requires_timestamp",
        ),
        sa.CheckConstraint(
            "(status <> 'draft') OR (published_at IS NULL)",
            name="ck_rule_definition_revisions_draft_has_no_published_timestamp",
        ),
    )
    op.create_index("ix_rule_definition_revisions_organization_id", "rule_definition_revisions", ["organization_id"])
    op.create_index("ix_rule_definition_revisions_rule_id", "rule_definition_revisions", ["rule_id"])
    op.create_index("ix_rule_definition_revisions_status", "rule_definition_revisions", ["status"])
    op.create_index("ix_rule_definition_revisions_stage", "rule_definition_revisions", ["stage"])
    op.create_index("ix_rule_definition_revisions_checksum", "rule_definition_revisions", ["checksum"])
    op.create_index("ix_rule_definition_revisions_created_by_user_id", "rule_definition_revisions", ["created_by_user_id"])
    op.create_index("ix_rule_definition_revisions_published_at", "rule_definition_revisions", ["published_at"])
    op.create_index(
        "uq_rule_definition_revisions_single_draft",
        "rule_definition_revisions",
        ["rule_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )

    op.create_table(
        "rule_libraries",
        sa.Column("library_version_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("library_id", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("library_id", "version", name="uq_rule_libraries_library_version"),
        sa.CheckConstraint(
            "(visibility = 'system' AND organization_id IS NULL) "
            "OR (visibility = 'organization' AND organization_id IS NOT NULL)",
            name="ck_rule_libraries_visibility_scope",
        ),
    )
    op.create_index("ix_rule_libraries_organization_id", "rule_libraries", ["organization_id"])
    op.create_index("ix_rule_libraries_library_id", "rule_libraries", ["library_id"])
    op.create_index("ix_rule_libraries_version", "rule_libraries", ["version"])
    op.create_index("ix_rule_libraries_visibility", "rule_libraries", ["visibility"])
    op.create_index("ix_rule_libraries_created_by_user_id", "rule_libraries", ["created_by_user_id"])
    op.create_index("ix_rule_libraries_published_at", "rule_libraries", ["published_at"])

    op.create_table(
        "rule_library_entries",
        sa.Column("library_entry_id", sa.String(length=255), primary_key=True),
        sa.Column("library_id", sa.String(length=255), nullable=False),
        sa.Column("library_version", sa.String(length=128), nullable=False),
        sa.Column("rule_id", sa.String(length=255), nullable=False),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["library_id", "library_version"],
            ["rule_libraries.library_id", "rule_libraries.version"],
            name="fk_rule_library_entries_library_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id", "rule_revision"],
            ["rule_definition_revisions.rule_id", "rule_definition_revisions.revision"],
            name="fk_rule_library_entries_rule_revision",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "library_id",
            "library_version",
            "rule_id",
            "rule_revision",
            name="uq_rule_library_entries_library_rule_revision",
        ),
    )
    op.create_index("ix_rule_library_entries_library_id", "rule_library_entries", ["library_id"])
    op.create_index("ix_rule_library_entries_library_version", "rule_library_entries", ["library_version"])
    op.create_index("ix_rule_library_entries_rule_id", "rule_library_entries", ["rule_id"])

    op.create_table(
        "rule_bindings",
        sa.Column("binding_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("rule_id", sa.String(length=255), nullable=False),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("channels", postgresql.ARRAY(sa.String(length=64)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("graph_ids", postgresql.ARRAY(sa.String(length=255)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("state_ids", postgresql.ARRAY(sa.String(length=255)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("tool_refs", postgresql.ARRAY(sa.String(length=255)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("event_types", postgresql.ARRAY(sa.String(length=64)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("scope_fingerprint", sa.String(length=255), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["rule_id", "rule_revision"],
            ["rule_definition_revisions.rule_id", "rule_definition_revisions.revision"],
            name="fk_rule_bindings_rule_revision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint('"order" >= 1', name="ck_rule_bindings_order_positive"),
    )
    op.create_index("ix_rule_bindings_organization_id", "rule_bindings", ["organization_id"])
    op.create_index("ix_rule_bindings_rule_id", "rule_bindings", ["rule_id"])
    op.create_index("ix_rule_bindings_mode", "rule_bindings", ["mode"])
    op.create_index("ix_rule_bindings_order", "rule_bindings", ["order"])
    op.create_index("ix_rule_bindings_scope_fingerprint", "rule_bindings", ["scope_fingerprint"])
    op.create_index("ix_rule_bindings_created_by_user_id", "rule_bindings", ["created_by_user_id"])
    op.create_index("ix_rule_bindings_updated_by_user_id", "rule_bindings", ["updated_by_user_id"])
    op.create_index("ix_rule_bindings_org_mode_order", "rule_bindings", ["organization_id", "mode", "order"])
    op.create_index("ix_rule_bindings_channels_gin", "rule_bindings", ["channels"], postgresql_using="gin")
    op.create_index("ix_rule_bindings_graph_ids_gin", "rule_bindings", ["graph_ids"], postgresql_using="gin")
    op.create_index("ix_rule_bindings_state_ids_gin", "rule_bindings", ["state_ids"], postgresql_using="gin")
    op.create_index("ix_rule_bindings_tool_refs_gin", "rule_bindings", ["tool_refs"], postgresql_using="gin")
    op.create_index("ix_rule_bindings_event_types_gin", "rule_bindings", ["event_types"], postgresql_using="gin")
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_rule_bindings_scope_fingerprint
            ON rule_bindings (coalesce(organization_id, ''), rule_id, rule_revision, scope_fingerprint)
            """
        )
    )

    for table_name in _RLS_TABLES:
        policy_name = f"tenant_scope_{table_name}"
        op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(_policy_sql(table_name))


def downgrade() -> None:
    for table_name in reversed(_RLS_TABLES):
        policy_name = f"tenant_scope_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))

    op.execute(sa.text("DROP INDEX IF EXISTS uq_rule_bindings_scope_fingerprint"))
    op.drop_index("ix_rule_bindings_event_types_gin", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_tool_refs_gin", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_state_ids_gin", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_graph_ids_gin", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_channels_gin", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_org_mode_order", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_updated_by_user_id", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_created_by_user_id", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_scope_fingerprint", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_order", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_mode", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_rule_id", table_name="rule_bindings")
    op.drop_index("ix_rule_bindings_organization_id", table_name="rule_bindings")
    op.drop_table("rule_bindings")

    op.drop_index("ix_rule_library_entries_rule_id", table_name="rule_library_entries")
    op.drop_index("ix_rule_library_entries_library_version", table_name="rule_library_entries")
    op.drop_index("ix_rule_library_entries_library_id", table_name="rule_library_entries")
    op.drop_table("rule_library_entries")

    op.drop_index("ix_rule_libraries_published_at", table_name="rule_libraries")
    op.drop_index("ix_rule_libraries_created_by_user_id", table_name="rule_libraries")
    op.drop_index("ix_rule_libraries_visibility", table_name="rule_libraries")
    op.drop_index("ix_rule_libraries_version", table_name="rule_libraries")
    op.drop_index("ix_rule_libraries_library_id", table_name="rule_libraries")
    op.drop_index("ix_rule_libraries_organization_id", table_name="rule_libraries")
    op.drop_table("rule_libraries")

    op.drop_index("uq_rule_definition_revisions_single_draft", table_name="rule_definition_revisions")
    op.drop_index("ix_rule_definition_revisions_published_at", table_name="rule_definition_revisions")
    op.drop_index("ix_rule_definition_revisions_created_by_user_id", table_name="rule_definition_revisions")
    op.drop_index("ix_rule_definition_revisions_checksum", table_name="rule_definition_revisions")
    op.drop_index("ix_rule_definition_revisions_stage", table_name="rule_definition_revisions")
    op.drop_index("ix_rule_definition_revisions_status", table_name="rule_definition_revisions")
    op.drop_index("ix_rule_definition_revisions_rule_id", table_name="rule_definition_revisions")
    op.drop_index("ix_rule_definition_revisions_organization_id", table_name="rule_definition_revisions")
    op.drop_table("rule_definition_revisions")

    op.drop_index("ix_rule_definitions_archived_at", table_name="rule_definitions")
    op.drop_index("ix_rule_definitions_created_by_user_id", table_name="rule_definitions")
    op.drop_index("ix_rule_definitions_organization_id", table_name="rule_definitions")
    op.drop_table("rule_definitions")
