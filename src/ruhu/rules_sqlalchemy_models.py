from __future__ import annotations

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .db_models import Base, OptionalTenantScopeMixin


class RuleDefinitionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "rule_definitions"

    rule_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class RuleDefinitionRevisionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "rule_definition_revisions"
    __table_args__ = (
        UniqueConstraint("rule_id", "revision", name="uq_rule_definition_revisions_rule_revision"),
        ForeignKeyConstraint(
            ["rule_id"],
            ["rule_definitions.rule_id"],
            ondelete="CASCADE",
            name="fk_rule_definition_revisions_rule_id",
        ),
        Index(
            "uq_rule_definition_revisions_single_draft",
            "rule_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )

    revision_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    predicate_json: Mapped[dict] = mapped_column(JSON, default=dict)
    expression_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    effect_json: Mapped[dict] = mapped_column(JSON, default=dict)
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    published_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class RuleLibraryRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "rule_libraries"
    __table_args__ = (
        UniqueConstraint("library_id", "version", name="uq_rule_libraries_library_version"),
    )

    library_version_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    library_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    published_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class RuleLibraryEntryRecord(Base):
    __tablename__ = "rule_library_entries"
    __table_args__ = (
        UniqueConstraint(
            "library_id",
            "library_version",
            "rule_id",
            "rule_revision",
            name="uq_rule_library_entries_library_rule_revision",
        ),
        ForeignKeyConstraint(
            ["library_id", "library_version"],
            ["rule_libraries.library_id", "rule_libraries.version"],
            ondelete="CASCADE",
            name="fk_rule_library_entries_library_version",
        ),
        ForeignKeyConstraint(
            ["rule_id", "rule_revision"],
            ["rule_definition_revisions.rule_id", "rule_definition_revisions.revision"],
            ondelete="RESTRICT",
            name="fk_rule_library_entries_rule_revision",
        ),
    )

    library_entry_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    library_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    library_version: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    rule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class RuleBindingRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "rule_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_id", "rule_revision"],
            ["rule_definition_revisions.rule_id", "rule_definition_revisions.revision"],
            ondelete="RESTRICT",
            name="fk_rule_bindings_rule_revision",
        ),
        Index(
            "uq_rule_bindings_scope_fingerprint",
            func.coalesce(literal_column("organization_id"), ""),
            "rule_id",
            "rule_revision",
            "scope_fingerprint",
            unique=True,
        ),
    )

    binding_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    rule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False, index=True, default=100)
    channels: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    agent_ids: Mapped[list[str]] = mapped_column(ARRAY(String(255)), default=list)
    step_ids: Mapped[list[str]] = mapped_column(ARRAY(String(255)), default=list)
    tool_refs: Mapped[list[str]] = mapped_column(ARRAY(String(255)), default=list)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    scope_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
