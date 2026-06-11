"""CRUD stores for API connections, tool definitions, and agent assignments.

This module carries two generations of credential encryption:

  - Legacy ``CredentialCipher`` (this file) — Fernet-string round-trip for
    ``credentials_enc`` and OAuth state-token cookies.  Predates phase 1 of
    the credential-encryption rollout and is still used by OAuth state
    handling in ``tools/oauth.py``.
  - Phase-1 ``CredentialCipher`` protocol (``ruhu.tools.cipher``) — AEAD blob
    format with AAD binding and audit emission.  Imported here as
    ``BlobCipher`` to avoid the name collision; used by ``APIConnectionStore``
    to dual-write the new ``oauth_token_ct`` / ``credentials_ct`` columns
    alongside the legacy ones.

Phase 2 will drop the legacy columns and the legacy cipher can be retired
from credential-at-rest use (OAuth state-token usage can stay or migrate
separately).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ruhu.db_models import (
    AgentToolBindingRecord,
    APIConnectionRecord,
    ToolAgentAssignmentRecord,
    ToolDefinitionRecord,
)

from .cipher import (
    AEADCipherError,
    CredentialCipher as BlobCipher,
    DecryptionFailed,
    build_aad,
)
from .specs import ToolSpec
from .validators import ToolSpecValidator

logger = logging.getLogger(__name__)

# Closed vocabulary for the ``purpose`` label on audit events + metrics.
# Extending this list is fine; label cardinality stays small.
DecryptPurpose = Literal["http_tool_call", "oauth_refresh", "admin_inspect", "browser_task_session"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


# ── Credential cipher ─────────────────────────────────────────────────────────


class CredentialCipher:
    """Fernet-based symmetric encryption for connection credentials.

    The key must be a URL-safe base64-encoded 32-byte value, as produced by
    ``cryptography.fernet.Fernet.generate_key()``.
    """

    def __init__(self, key: str | bytes) -> None:
        from cryptography.fernet import Fernet

        self._fernet = Fernet(key if isinstance(key, bytes) else key.encode())

    def encrypt(self, data: dict[str, Any]) -> str:
        plaintext = json.dumps(data, separators=(",", ":")).encode()
        return self._fernet.encrypt(plaintext).decode()

    def decrypt(self, ciphertext: str, *, ttl: int | None = None) -> dict[str, Any]:
        """Decrypt a Fernet-encrypted JSON payload.

        ``ttl`` (seconds), when supplied, asks Fernet to enforce its embedded
        timestamp: tokens older than ``ttl`` raise ``InvalidToken``. Without
        ``ttl`` the encrypted payload never expires from Fernet's perspective
        (the historical behavior — credential blobs need to outlive any
        sensible TTL since OAuth refresh tokens can live for months).

        OAuth ``state`` cookies pass ``ttl=_STATE_TTL_SECONDS`` from
        ``OAuthFlowManager.decode_state`` so a stale or replayed state
        parameter is rejected after the consent window closes.
        """
        if ttl is None:
            plaintext = self._fernet.decrypt(ciphertext.encode())
        else:
            plaintext = self._fernet.decrypt(ciphertext.encode(), ttl=ttl)
        return json.loads(plaintext)


# ── API connection store ──────────────────────────────────────────────────────


class APIConnectionStore:
    """Stores OAuth tokens and connection credentials.

    Phase 1 of the credential-encryption rollout: every write dual-writes
    the legacy plaintext column *and* the new AEAD-encrypted column
    (``oauth_token_ct`` / ``credentials_ct``).  Reads through
    :py:meth:`get_oauth_token` prefer the encrypted column, verify AAD,
    and emit a ``credential.decrypted`` audit event.  Direct ORM reads of
    ``record.oauth_token_json`` still work for phase 1 — phase 2 drops the
    plaintext columns and forces all callers through the store.

    The ``blob_cipher`` argument is required and carries the AEAD scheme
    (see ``ruhu.tools.cipher``).  ``audit_router`` is optional only because
    some internal tooling constructs a store before the audit system is
    wired; production wiring always passes it, and decrypt calls without
    it fall back to a metric-only record with a warning log.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        blob_cipher: BlobCipher,
        legacy_cipher: CredentialCipher | None = None,
        audit_router: Any | None = None,  # ``AuditEventRouter`` — avoid circular import
    ) -> None:
        self._sf = session_factory
        self._cipher = blob_cipher
        self._legacy_cipher = legacy_cipher
        self._audit = audit_router

    @property
    def blob_cipher(self) -> BlobCipher:
        """The AEAD cipher backing this store.

        Exposed so callers that need the cipher directly (OAuth refresh
        worker, OAuth flow manager) can share the same key ring without
        re-reading env vars — otherwise a dev-fallback ``Fernet.generate_key``
        would produce different keys on each read.
        """
        return self._cipher

    def set_audit_router(self, audit_router: Any) -> None:
        """Install the audit router after construction.

        ``APIConnectionStore`` is sometimes built before the app's
        ``AuditEventRouter`` exists (tool runtime is constructed before the
        FastAPI app in ``api.py``).  Call this once the router is available
        so subsequent decrypts start emitting audit events; decrypts that
        happen before the router is wired log ``credential.decrypted_no_audit_router``
        to make the gap visible in the logs.
        """
        self._audit = audit_router

    # ── Internal helpers ────────────────────────────────────────────────────

    def _encrypt_oauth_token(
        self,
        organization_id: str,
        connection_id: str,
        oauth_token: dict[str, Any] | None,
    ) -> bytes | None:
        """Serialise + encrypt an OAuth token dict.  Returns None if empty."""
        if not oauth_token:
            return None
        plaintext = json.dumps(oauth_token, separators=(",", ":"), sort_keys=True).encode()
        return self._cipher.encrypt(
            plaintext,
            aad=build_aad(organization_id=organization_id, connection_id=connection_id),
        )

    def _encrypt_credentials(
        self,
        organization_id: str,
        connection_id: str,
        credentials_enc: str | None,
        *,
        credentials_plain: dict[str, Any] | None = None,
    ) -> bytes | None:
        """Encrypt connection credentials for the primary AEAD column.

        New writes prefer a JSON-serialised plaintext dict inside the AEAD
        envelope. Older rows may still wrap the legacy Fernet ciphertext; the
        decrypt path understands both formats.
        """
        if credentials_plain is not None:
            plaintext = json.dumps(
                credentials_plain,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        elif credentials_enc is not None:
            plaintext = credentials_enc.encode("utf-8")
        else:
            return None
        return self._cipher.encrypt(
            plaintext,
            aad=build_aad(organization_id=organization_id, connection_id=connection_id),
        )

    def _emit_decrypt_event(
        self,
        *,
        organization_id: str,
        connection_id: str,
        actor_id: str | None,
        actor_type: str,
        purpose: DecryptPurpose,
        outcome: Literal["success", "failure"],
        error: str | None = None,
    ) -> None:
        """Record a ``credential.decrypted`` audit event + metric.

        The audit router may be absent (e.g., test harnesses); we still
        increment the metric so operators see the decrypt volume even if
        the audit trail is missing.  A missing router is logged at WARNING
        once per call so the gap is visible.
        """
        try:
            from ..observability.metrics import (
                credential_decrypt_failures_total,
                credential_decrypts_total,
            )

            if outcome == "success":
                credential_decrypts_total.labels(purpose=purpose).inc()
            else:
                credential_decrypt_failures_total.labels(
                    purpose=purpose, error=error or "unknown"
                ).inc()
        except Exception:  # noqa: BLE001 — metrics never fail a decrypt
            pass

        if self._audit is None:
            logger.warning(
                "credential.decrypted_no_audit_router",
                extra={"connection_id": connection_id, "purpose": purpose, "outcome": outcome},
            )
            return

        try:
            from ..audit.events import AuditEvent

            self._audit.route(
                AuditEvent(
                    event_type="credential.decrypted",
                    organization_id=organization_id,
                    outcome=outcome,
                    actor_id=actor_id,
                    resource_type="api_connection",
                    resource_id=connection_id,
                    detail={
                        "actor_type": actor_type,
                        "purpose": purpose,
                        "key_id": getattr(self._cipher, "primary_key_id_hex", None),
                        **({"error": error} if error else {}),
                    },
                )
            )
        except Exception:  # noqa: BLE001 — audit routing never fails a decrypt
            logger.exception("credential.decrypted_audit_failed")

    # ── Public API ──────────────────────────────────────────────────────────

    def create(
        self,
        *,
        organization_id: str,
        display_name: str,
        provider: str,
        auth_type: str,
        base_url: str | None = None,
        credentials_enc: str | None = None,
        credentials_plain: dict[str, Any] | None = None,
        oauth_token: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> APIConnectionRecord:
        now = _utcnow()
        connection_id = _new_id("conn")
        if credentials_plain is not None and self._legacy_cipher is not None:
            credentials_enc = self._legacy_cipher.encrypt(credentials_plain)
        # Encrypt BEFORE opening the transaction so crypto errors fail the
        # request cleanly rather than polluting an open session.
        oauth_token_ct = self._encrypt_oauth_token(
            organization_id, connection_id, oauth_token
        )
        credentials_ct = self._encrypt_credentials(
            organization_id,
            connection_id,
            credentials_enc,
            credentials_plain=credentials_plain,
        )
        record = APIConnectionRecord(
            connection_id=connection_id,
            organization_id=organization_id,
            display_name=display_name,
            provider=provider,
            auth_type=auth_type,
            base_url=base_url,
            credentials_enc=credentials_enc,
            credentials_ct=credentials_ct,
            oauth_token_json=dict(oauth_token or {}),
            oauth_token_ct=oauth_token_ct,
            status="active",
            metadata_json=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        with self._sf.begin() as session:
            session.add(record)
            session.flush()
            session.expunge(record)
        return record

    def get(self, connection_id: str) -> APIConnectionRecord | None:
        with self._sf() as session:
            return session.get(APIConnectionRecord, connection_id)

    def list_for_org(self, organization_id: str) -> list[APIConnectionRecord]:
        with self._sf() as session:
            rows = session.scalars(
                select(APIConnectionRecord)
                .where(APIConnectionRecord.organization_id == organization_id)
                .order_by(APIConnectionRecord.display_name)
            ).all()
            return list(rows)

    def update(
        self,
        connection_id: str,
        *,
        display_name: str | None = None,
        base_url: str | None = None,
        credentials_enc: str | None = None,
        credentials_plain: dict[str, Any] | None = None,
        oauth_token: dict[str, Any] | None = None,
        status: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> APIConnectionRecord:
        now = _utcnow()
        with self._sf.begin() as session:
            record = session.get(APIConnectionRecord, connection_id)
            if record is None:
                raise KeyError(connection_id)
            if display_name is not None:
                record.display_name = display_name
            if base_url is not None:
                record.base_url = base_url
            if credentials_plain is not None and self._legacy_cipher is not None:
                credentials_enc = self._legacy_cipher.encrypt(credentials_plain)
            if credentials_enc is not None or credentials_plain is not None:
                record.credentials_enc = credentials_enc
                record.credentials_ct = self._encrypt_credentials(
                    record.organization_id,
                    connection_id,
                    credentials_enc,
                    credentials_plain=credentials_plain,
                )
            if oauth_token is not None:
                record.oauth_token_json = dict(oauth_token)
                record.oauth_token_ct = self._encrypt_oauth_token(
                    record.organization_id, connection_id, oauth_token
                )
            if status is not None:
                record.status = status
            if error_message is not None:
                record.error_message = error_message
            if metadata is not None:
                record.metadata_json = {**dict(record.metadata_json or {}), **metadata}
            record.updated_at = now
            session.flush()
            session.expunge(record)
        return record

    def delete(self, connection_id: str) -> bool:
        with self._sf.begin() as session:
            record = session.get(APIConnectionRecord, connection_id)
            if record is None:
                return False
            session.delete(record)
        return True

    def get_oauth_token(
        self,
        connection_id: str,
        *,
        actor_id: str | None,
        actor_type: Literal["user", "system", "tool_runtime"],
        purpose: DecryptPurpose,
    ) -> dict[str, Any]:
        """Authorised, audited read of a connection's OAuth token.

        Every call emits one ``credential.decrypted`` audit event (success or
        failure) keyed to the caller's ``actor_id`` + ``purpose`` — so the
        audit trail answers "who decrypted which credential, when, for what
        reason?" with no ambiguity.

        Performs a DB lookup by ``connection_id``.  Callers that already hold
        a hydrated ``APIConnectionRecord`` should use
        :py:meth:`decrypt_oauth_token_from_record` instead to avoid the
        redundant SELECT.

        During phase 1 the encrypted column may be empty on rows written
        before the rollout; we transparently fall back to
        ``oauth_token_json``.  Migration 0050 drops that column after the
        backfill script (phase 2); at that point the fallback branch is
        dead code and gets removed.

        Raises ``KeyError`` if the connection is missing, ``DecryptionFailed``
        if the ciphertext is tampered or the AAD mismatches (emits a failure
        audit event either way).
        """
        with self._sf() as session:
            record = session.get(APIConnectionRecord, connection_id)
            if record is None:
                raise KeyError(connection_id)
            session.expunge(record)
        return self.decrypt_oauth_token_from_record(
            record,
            actor_id=actor_id,
            actor_type=actor_type,
            purpose=purpose,
        )

    def decrypt_oauth_token_from_record(
        self,
        record: APIConnectionRecord,
        *,
        actor_id: str | None,
        actor_type: Literal["user", "system", "tool_runtime"],
        purpose: DecryptPurpose,
    ) -> dict[str, Any]:
        """Audited decrypt using an already-loaded ``APIConnectionRecord``.

        This is the seam the tool-spec compiler uses: the catalog resolver
        has the row in hand from its own SELECT, and re-querying via
        :py:meth:`get_oauth_token` would double the DB round-trips on every
        tool call.  Same audit + AAD semantics as ``get_oauth_token``.
        """
        connection_id = record.connection_id
        organization_id = record.organization_id
        encrypted = record.oauth_token_ct

        if encrypted is not None:
            aad = build_aad(organization_id=organization_id, connection_id=connection_id)
            try:
                plaintext = self._cipher.decrypt(encrypted, aad=aad)
                token = json.loads(plaintext)
            except AEADCipherError as exc:
                self._emit_decrypt_event(
                    organization_id=organization_id,
                    connection_id=connection_id,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    purpose=purpose,
                    outcome="failure",
                    error=type(exc).__name__,
                )
                raise
            self._emit_decrypt_event(
                organization_id=organization_id,
                connection_id=connection_id,
                actor_id=actor_id,
                actor_type=actor_type,
                purpose=purpose,
                outcome="success",
            )
            return token

        # Phase 1 fallback: row predates the rollout.  Still audit so we have
        # the trail; the ``key_id`` field in the detail will be the current
        # cipher's key_id even though the data isn't encrypted — that's OK,
        # it marks "which key would have been used going forward".
        self._emit_decrypt_event(
            organization_id=organization_id,
            connection_id=connection_id,
            actor_id=actor_id,
            actor_type=actor_type,
            purpose=purpose,
            outcome="success",
        )
        return dict(record.oauth_token_json or {})

    def decrypt_credentials_from_record(
        self,
        record: APIConnectionRecord,
        *,
        actor_id: str | None,
        actor_type: Literal["user", "system", "tool_runtime"],
        purpose: DecryptPurpose,
    ) -> dict[str, Any]:
        connection_id = record.connection_id
        organization_id = record.organization_id
        encrypted = record.credentials_ct

        if encrypted is not None:
            aad = build_aad(organization_id=organization_id, connection_id=connection_id)
            try:
                plaintext = self._cipher.decrypt(encrypted, aad=aad)
                try:
                    decoded = json.loads(plaintext.decode("utf-8"))
                    if isinstance(decoded, dict):
                        token = decoded
                    else:
                        raise ValueError("credential payload is not an object")
                except Exception:
                    if self._legacy_cipher is None:
                        raise DecryptionFailed(
                            "legacy credential payload requires legacy cipher support"
                        )
                    token = self._legacy_cipher.decrypt(plaintext.decode("utf-8"))
            except (AEADCipherError, DecryptionFailed) as exc:
                self._emit_decrypt_event(
                    organization_id=organization_id,
                    connection_id=connection_id,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    purpose=purpose,
                    outcome="failure",
                    error=type(exc).__name__,
                )
                raise
            self._emit_decrypt_event(
                organization_id=organization_id,
                connection_id=connection_id,
                actor_id=actor_id,
                actor_type=actor_type,
                purpose=purpose,
                outcome="success",
            )
            return token

        if record.credentials_enc:
            if self._legacy_cipher is None:
                self._emit_decrypt_event(
                    organization_id=organization_id,
                    connection_id=connection_id,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    purpose=purpose,
                    outcome="failure",
                    error="missing_legacy_cipher",
                )
                raise DecryptionFailed("legacy credential cipher is not configured")
            token = self._legacy_cipher.decrypt(record.credentials_enc)
            self._emit_decrypt_event(
                organization_id=organization_id,
                connection_id=connection_id,
                actor_id=actor_id,
                actor_type=actor_type,
                purpose=purpose,
                outcome="success",
            )
            return token

        self._emit_decrypt_event(
            organization_id=organization_id,
            connection_id=connection_id,
            actor_id=actor_id,
            actor_type=actor_type,
            purpose=purpose,
            outcome="success",
        )
        return {}


# ── Tool definition store ─────────────────────────────────────────────────────


_DEFAULT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
_DEFAULT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}
_tool_spec_validator = ToolSpecValidator()


def _raise_if_invalid_schema(*, schema: dict[str, Any], field_root: str, require_descriptions: bool) -> None:
    report = _tool_spec_validator.validate_schema(
        schema,
        field_root=field_root,
        require_parameter_descriptions=require_descriptions,
    )
    if report.is_valid:
        return
    raise ValueError(
        "; ".join(f"{issue.field}: {issue.message}" for issue in report.issues)
    )


def _validate_tool_schemas(
    *,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
) -> None:
    _raise_if_invalid_schema(
        schema=input_schema,
        field_root="input_schema",
        require_descriptions=True,
    )
    _raise_if_invalid_schema(
        schema=output_schema,
        field_root="output_schema",
        require_descriptions=False,
    )


def _validate_tool_spec_payload(
    *,
    tool_ref: str,
    kind: str,
    display_name: str,
    description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    timeout_ms: int,
    metadata: dict[str, Any] | None,
    read_only: bool,
) -> None:
    metadata_payload = dict(metadata or {})
    ToolSpec.model_validate(
        {
            "ref": tool_ref,
            "kind": kind if kind in {"builtin", "http", "mcp"} else "http",
            "display_name": display_name,
            "description": description,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "timeout_ms": timeout_ms,
            "annotations": metadata_payload.get("annotations")
            or {"read_only": read_only},
            "confirmation": metadata_payload.get("confirmation", "never"),
            "confirmation_prompt": metadata_payload.get("confirmation_prompt"),
            "allowed_channels": metadata_payload.get("allowed_channels", []),
            "tags": metadata_payload.get("tags", []),
            "purpose": metadata_payload.get("purpose"),
            "when_to_use": metadata_payload.get("when_to_use", []),
            "when_not_to_use": metadata_payload.get("when_not_to_use", []),
            "input_examples": metadata_payload.get("input_examples", []),
            "failure_modes": metadata_payload.get("failure_modes", []),
            "output_validation_mode": metadata_payload.get("output_validation_mode", "warn"),
        }
    )


class ToolDefinitionStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """Public accessor for the session factory.

        Lets collaborators (e.g. the Atlas coordinator's provisioning apply)
        build sibling stores without reaching into the private ``_sf``.
        """
        return self._sf

    def create(
        self,
        *,
        organization_id: str,
        connection_id: str | None = None,
        kind: str = "api",
        tool_ref: str,
        function_name: str | None = None,
        display_name: str,
        description: str,
        endpoint_path: str | None = None,
        http_method: str = "POST",
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        timeout_ms: int = 5000,
        read_only: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ToolDefinitionRecord:
        if len(description.strip()) < 20:
            raise ValueError("tool description must be at least 20 characters")
        input_schema_payload = dict(input_schema or _DEFAULT_INPUT_SCHEMA)
        output_schema_payload = dict(output_schema or _DEFAULT_OUTPUT_SCHEMA)
        _validate_tool_schemas(
            input_schema=input_schema_payload,
            output_schema=output_schema_payload,
        )
        _validate_tool_spec_payload(
            tool_ref=tool_ref,
            kind=kind,
            display_name=display_name,
            description=description,
            input_schema=input_schema_payload,
            output_schema=output_schema_payload,
            timeout_ms=timeout_ms,
            metadata=metadata,
            read_only=read_only,
        )
        now = _utcnow()
        record = ToolDefinitionRecord(
            tool_definition_id=_new_id("tool"),
            organization_id=organization_id,
            connection_id=connection_id,
            kind=kind,
            tool_ref=tool_ref,
            function_name=function_name,
            display_name=display_name,
            description=description,
            endpoint_path=endpoint_path,
            http_method=http_method.upper(),
            input_schema_json=input_schema_payload,
            output_schema_json=output_schema_payload,
            timeout_ms=timeout_ms,
            read_only=read_only,
            enabled=True,
            metadata_json=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        with self._sf.begin() as session:
            session.add(record)
            session.flush()
            session.expunge(record)
        return record

    def get(self, tool_definition_id: str) -> ToolDefinitionRecord | None:
        with self._sf() as session:
            return session.get(ToolDefinitionRecord, tool_definition_id)

    def get_by_ref(self, organization_id: str, tool_ref: str) -> ToolDefinitionRecord | None:
        with self._sf() as session:
            return session.scalar(
                select(ToolDefinitionRecord).where(
                    ToolDefinitionRecord.organization_id == organization_id,
                    ToolDefinitionRecord.tool_ref == tool_ref,
                    ToolDefinitionRecord.enabled.is_(True),
                )
            )

    def list_for_org(
        self,
        organization_id: str,
        *,
        enabled_only: bool = True,
        kind: str | None = None,
        connection_id: str | None = None,
    ) -> list[ToolDefinitionRecord]:
        with self._sf() as session:
            stmt = select(ToolDefinitionRecord).where(
                ToolDefinitionRecord.organization_id == organization_id
            )
            if enabled_only:
                stmt = stmt.where(ToolDefinitionRecord.enabled.is_(True))
            if kind is not None:
                stmt = stmt.where(ToolDefinitionRecord.kind == kind)
            if connection_id is not None:
                stmt = stmt.where(ToolDefinitionRecord.connection_id == connection_id)
            rows = session.scalars(stmt.order_by(ToolDefinitionRecord.tool_ref)).all()
            return list(rows)

    def update(
        self,
        tool_definition_id: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        function_name: str | None = None,
        endpoint_path: str | None = None,
        http_method: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        read_only: bool | None = None,
        enabled: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolDefinitionRecord:
        if description is not None and len(description.strip()) < 20:
            raise ValueError("tool description must be at least 20 characters")
        now = _utcnow()
        with self._sf.begin() as session:
            record = session.get(ToolDefinitionRecord, tool_definition_id)
            if record is None:
                raise KeyError(tool_definition_id)
            next_display_name = display_name if display_name is not None else record.display_name
            next_description = description if description is not None else record.description
            next_input_schema = dict(input_schema) if input_schema is not None else dict(
                record.input_schema_json or _DEFAULT_INPUT_SCHEMA
            )
            next_output_schema = dict(output_schema) if output_schema is not None else dict(
                record.output_schema_json or _DEFAULT_OUTPUT_SCHEMA
            )
            next_timeout_ms = timeout_ms if timeout_ms is not None else record.timeout_ms
            next_read_only = read_only if read_only is not None else record.read_only
            next_metadata = (
                {**dict(record.metadata_json or {}), **metadata}
                if metadata is not None
                else dict(record.metadata_json or {})
            )
            if display_name is not None:
                record.display_name = display_name
            if description is not None:
                record.description = description
            if function_name is not None:
                record.function_name = function_name
            if endpoint_path is not None:
                record.endpoint_path = endpoint_path
            if http_method is not None:
                record.http_method = http_method.upper()
            if input_schema is not None:
                _validate_tool_schemas(
                    input_schema=dict(input_schema),
                    output_schema=dict(record.output_schema_json or _DEFAULT_OUTPUT_SCHEMA),
                )
                record.input_schema_json = input_schema
            if output_schema is not None:
                _validate_tool_schemas(
                    input_schema=dict(record.input_schema_json or _DEFAULT_INPUT_SCHEMA),
                    output_schema=dict(output_schema),
                )
                record.output_schema_json = output_schema
            if timeout_ms is not None:
                record.timeout_ms = timeout_ms
            if read_only is not None:
                record.read_only = read_only
            if enabled is not None:
                record.enabled = enabled
            if metadata is not None:
                record.metadata_json = {**dict(record.metadata_json or {}), **metadata}
            _validate_tool_spec_payload(
                tool_ref=record.tool_ref,
                kind=record.kind,
                display_name=next_display_name,
                description=next_description,
                input_schema=next_input_schema,
                output_schema=next_output_schema,
                timeout_ms=next_timeout_ms,
                metadata=next_metadata,
                read_only=next_read_only,
            )
            record.updated_at = now
            session.flush()
            session.expunge(record)
        return record

    def delete(self, tool_definition_id: str) -> bool:
        with self._sf.begin() as session:
            record = session.get(ToolDefinitionRecord, tool_definition_id)
            if record is None:
                return False
            session.delete(record)
        return True


# ── Tool agent assignment store ───────────────────────────────────────────────


class ToolAgentAssignmentStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def assign(
        self,
        *,
        organization_id: str,
        agent_id: str,
        tool_definition_id: str,
    ) -> ToolAgentAssignmentRecord:
        now = _utcnow()
        record = ToolAgentAssignmentRecord(
            assignment_id=_new_id("asgn"),
            organization_id=organization_id,
            agent_id=agent_id,
            tool_definition_id=tool_definition_id,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        with self._sf.begin() as session:
            session.add(record)
            session.flush()
            session.expunge(record)
        return record

    def list_for_agent(
        self,
        organization_id: str,
        agent_id: str,
        *,
        enabled_only: bool = True,
    ) -> list[ToolAgentAssignmentRecord]:
        with self._sf() as session:
            stmt = select(ToolAgentAssignmentRecord).where(
                ToolAgentAssignmentRecord.organization_id == organization_id,
                ToolAgentAssignmentRecord.agent_id == agent_id,
            )
            if enabled_only:
                stmt = stmt.where(ToolAgentAssignmentRecord.enabled.is_(True))
            rows = session.scalars(stmt).all()
            return list(rows)

    def list_for_definition(self, tool_definition_id: str) -> list[ToolAgentAssignmentRecord]:
        with self._sf() as session:
            rows = session.scalars(
                select(ToolAgentAssignmentRecord).where(
                    ToolAgentAssignmentRecord.tool_definition_id == tool_definition_id
                )
            ).all()
            return list(rows)

    def unassign(self, assignment_id: str) -> bool:
        with self._sf.begin() as session:
            record = session.get(ToolAgentAssignmentRecord, assignment_id)
            if record is None:
                return False
            session.delete(record)
        return True


# ── Agent tool binding store (per-agent connection overrides) ─────────────────


class AgentToolBindingStore:
    """CRUD for per-agent connection overrides.

    When an org has multiple connections for the same provider, this store
    manages which connection a specific agent uses for a given tool.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def create_or_update(
        self,
        *,
        organization_id: str,
        agent_id: str,
        tool_definition_id: str,
        connection_id: str,
        enabled: bool = True,
    ) -> AgentToolBindingRecord:
        now = _utcnow()
        with self._sf.begin() as session:
            existing = session.scalar(
                select(AgentToolBindingRecord).where(
                    AgentToolBindingRecord.organization_id == organization_id,
                    AgentToolBindingRecord.agent_id == agent_id,
                    AgentToolBindingRecord.tool_definition_id == tool_definition_id,
                )
            )
            if existing is not None:
                existing.connection_id = connection_id
                existing.enabled = enabled
                existing.updated_at = now
                session.flush()
                session.expunge(existing)
                return existing
            record = AgentToolBindingRecord(
                binding_id=_new_id("bind"),
                organization_id=organization_id,
                agent_id=agent_id,
                tool_definition_id=tool_definition_id,
                connection_id=connection_id,
                enabled=enabled,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            session.expunge(record)
        return record

    def list_for_agent(
        self,
        organization_id: str,
        agent_id: str,
    ) -> list[AgentToolBindingRecord]:
        with self._sf() as session:
            rows = session.scalars(
                select(AgentToolBindingRecord).where(
                    AgentToolBindingRecord.organization_id == organization_id,
                    AgentToolBindingRecord.agent_id == agent_id,
                    AgentToolBindingRecord.enabled.is_(True),
                )
            ).all()
            return list(rows)

    def get_override(
        self,
        *,
        agent_id: str,
        tool_definition_id: str,
    ) -> AgentToolBindingRecord | None:
        """Return the connection override for a specific agent+tool, or None."""
        with self._sf() as session:
            return session.scalar(
                select(AgentToolBindingRecord).where(
                    AgentToolBindingRecord.agent_id == agent_id,
                    AgentToolBindingRecord.tool_definition_id == tool_definition_id,
                    AgentToolBindingRecord.enabled.is_(True),
                )
            )

    def delete(self, binding_id: str) -> bool:
        with self._sf.begin() as session:
            record = session.get(AgentToolBindingRecord, binding_id)
            if record is None:
                return False
            session.delete(record)
        return True
