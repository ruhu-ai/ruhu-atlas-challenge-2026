"""SQLAlchemyCatalogResolver — ToolCatalogResolver backed by the database.

Resolves custom tool specs per-request from the tool_definitions and
api_connections tables.  Implements the ToolCatalogResolver protocol so it
can be passed directly to ToolRuntime without any additional wiring.

The ``caller`` argument propagates to ``ToolSpecCompiler.compile()`` so
credential decryption on the compile path can emit a
``credential.decrypted`` audit event keyed to the real actor.  When
``caller`` is None we're in the list / preview path and the compiler
skips decryption (headers are still produced for non-credentialed auth
types, but OAuth tokens are not materialised).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ruhu.db_models import APIConnectionRecord, ToolDefinitionRecord

from .catalog import ToolCatalogResolver
from .compiler import ToolSpecCompiler
from .specs import ToolSpec

if TYPE_CHECKING:
    from .types import ToolCaller


class SQLAlchemyCatalogResolver:
    """Implements ToolCatalogResolver using SQLAlchemy sessions.

    Each call opens a short-lived session, resolves the spec(s), and closes.
    The compiler is responsible for credential decryption and URL assembly.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        compiler: ToolSpecCompiler,
    ) -> None:
        self._sf = session_factory
        self._compiler = compiler

    def resolve(
        self,
        tool_ref: str,
        *,
        organization_id: str | None,
        caller: "ToolCaller | None" = None,
    ) -> ToolSpec | None:
        if not organization_id:
            return None
        with self._sf() as session:
            definition = session.scalar(
                select(ToolDefinitionRecord).where(
                    ToolDefinitionRecord.organization_id == organization_id,
                    ToolDefinitionRecord.tool_ref == tool_ref,
                    ToolDefinitionRecord.enabled.is_(True),
                )
            )
            if definition is None:
                return None
            # Connection-less kinds: code + composite bodies live in
            # metadata_json, so skip the connection lookup entirely.
            if definition.kind in ("code", "composite"):
                session.expunge(definition)
                try:
                    return self._compiler.compile(None, definition, caller=caller)
                except Exception:
                    return None
            connection = session.get(APIConnectionRecord, definition.connection_id) if definition.connection_id else None
            if connection is None or connection.status != "active":
                return None
            # Detach from the session so the compiler can read attributes
            # after close without triggering a fresh SELECT.
            session.expunge(connection)
            session.expunge(definition)
        try:
            return self._compiler.compile(connection, definition, caller=caller)
        except Exception:
            return None

    def list_for_organization(
        self,
        *,
        organization_id: str | None,
        caller: "ToolCaller | None" = None,
    ) -> list[ToolSpec]:
        if not organization_id:
            return []
        with self._sf() as session:
            definitions = session.scalars(
                select(ToolDefinitionRecord).where(
                    ToolDefinitionRecord.organization_id == organization_id,
                    ToolDefinitionRecord.enabled.is_(True),
                )
            ).all()
            if not definitions:
                return []
            connection_ids = {d.connection_id for d in definitions if d.connection_id}
            connections = {
                c.connection_id: c
                for c in session.scalars(
                    select(APIConnectionRecord).where(
                        APIConnectionRecord.connection_id.in_(connection_ids),
                        APIConnectionRecord.status == "active",
                    )
                ).all()
            } if connection_ids else {}
            for defn in definitions:
                session.expunge(defn)
            for conn in connections.values():
                session.expunge(conn)

        specs: list[ToolSpec] = []
        for definition in definitions:
            # Connection-less kinds compile without a connection row.
            if definition.kind in ("code", "composite"):
                try:
                    specs.append(self._compiler.compile(None, definition, caller=caller))
                except Exception:
                    continue
                continue
            connection = connections.get(definition.connection_id) if definition.connection_id else None
            if connection is None:
                continue
            try:
                specs.append(self._compiler.compile(connection, definition, caller=caller))
            except Exception:
                continue
        return specs
