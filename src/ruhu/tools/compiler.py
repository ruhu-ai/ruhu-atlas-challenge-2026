"""ToolSpecCompiler — translates DB records into executable ToolSpec objects.

The compiler bridges the storage model (APIConnectionRecord + ToolDefinitionRecord)
and the runtime model (ToolSpec + HttpExecutor).  It is the only place that
resolves base_url + endpoint_path, decrypts credentials, and builds auth headers.

Phase 2 of the credential-encryption rollout: OAuth2 connections are now
decrypted through ``APIConnectionStore.decrypt_oauth_token_from_record`` when
a ``caller`` is supplied, so every compile-time decrypt emits a
``credential.decrypted`` audit event keyed to the actual actor.  Without a
caller (i.e. list / preview paths) the compiler skips OAuth decryption and
produces a spec with no Authorization header — the resulting spec is safe
to show to clients but not safe to execute, which is the intended contract
for the catalog-list flow.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from .specs import ToolAnnotations, ToolSpec

if TYPE_CHECKING:
    from ruhu.db_models import APIConnectionRecord, ToolDefinitionRecord
    from .management import APIConnectionStore, CredentialCipher
    from .types import ToolCaller

log = logging.getLogger(__name__)


def _actor_from_caller(caller: "ToolCaller | None") -> tuple[str | None, str]:
    """Map a ``ToolCaller`` to the ``(actor_id, actor_type)`` pair expected by
    ``APIConnectionStore.decrypt_oauth_token_from_record``.

    Prefers ``user_id`` when present (the conversation's end-user is the
    audit subject); falls back to ``agent_id`` under ``actor_type="tool_runtime"``
    for agent-initiated calls where no user is in the loop.  ``None`` caller
    is only legal on list / preview paths where the compiler skips decryption
    entirely.
    """
    if caller is None:
        return None, "tool_runtime"
    if caller.user_id:
        return caller.user_id, "user"
    return caller.agent_id, "tool_runtime"

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
_PERMISSIVE_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": True,
}


class ToolSpecCompiler:
    """Compiles a (connection, definition) pair into a ToolSpec for HttpExecutor.

    Pass a ``CredentialCipher`` for any connections that use ``api_key`` or
    ``bearer_token`` auth.  OAuth2 connections read tokens directly from
    ``oauth_token_json`` and do not require the cipher.

    If the cipher is None and a definition requires credential decryption, the
    compiled spec will carry no auth headers — the HTTP call will reach the
    endpoint unauthenticated and is expected to fail.  This allows catalog
    resolution to succeed even in environments where the cipher key is not
    configured, so spec listings work without crashing.
    """

    def __init__(
        self,
        cipher: CredentialCipher | None = None,
        *,
        connection_store: "APIConnectionStore | None" = None,
    ) -> None:
        """``cipher`` is the legacy Fernet-dict cipher used for ``credentials_enc``
        (api_key / bearer_token auth).  ``connection_store`` is the phase-1
        store wrapping the AEAD cipher + audit router; when supplied, OAuth2
        decrypts go through it so each compile-time decrypt emits a
        ``credential.decrypted`` audit event.  Leaving ``connection_store``
        as None preserves legacy behaviour — ``oauth_token_json`` is read
        directly without an audit event (pre-phase-2 compatibility)."""
        self._cipher = cipher
        self._connection_store = connection_store

    def compile(
        self,
        connection: "APIConnectionRecord | None",
        definition: ToolDefinitionRecord,
        *,
        caller: "ToolCaller | None" = None,
    ) -> ToolSpec:
        # Connection-less kinds: code + composite bodies live entirely in
        # metadata_json, so there's no URL / auth to resolve. Dispatch to
        # dedicated spec builders that bypass the HTTP-assembly path.
        if definition.kind == "code":
            return self._compile_code(definition)
        if definition.kind == "composite":
            return self._compile_composite(definition)

        if connection is None:
            raise ValueError(
                f"definition {definition.tool_definition_id} has kind={definition.kind} "
                "but no connection — HTTP / integration tools require a connection"
            )
        url = self._resolve_url(connection.base_url or "", definition.endpoint_path)
        headers = self._build_auth_headers(connection, caller=caller)
        input_schema = self._coerce_schema(
            definition.input_schema_json,
            additional_properties=False,
            definition=definition,
        )
        output_schema = self._coerce_schema(
            definition.output_schema_json,
            additional_properties=True,
            definition=definition,
        )
        executor_config = {
            "url": url,
            "method": definition.http_method.upper(),
            "headers": headers,
            "connection_id": connection.connection_id,
            # ``organization_id`` is included so the executor's
            # ``on_unauthorized`` callback can scope a force-refresh to
            # the correct tenant without having to traverse caller
            # context. Tenant binding lives on the ToolCall itself for
            # the runtime path; this duplicate exists for the auth-only
            # refresh seam.
            "organization_id": connection.organization_id,
            "provider": connection.provider,
        }
        extra_executor_config = self._definition_executor_config(definition)
        extra_headers = extra_executor_config.pop("headers", None)
        if isinstance(extra_headers, dict):
            executor_config["headers"] = {
                **headers,
                **{str(key): str(value) for key, value in extra_headers.items()},
            }
        executor_config.update(extra_executor_config)
        metadata = dict(definition.metadata_json or {})
        annotations = self._definition_annotations(metadata)
        return ToolSpec(
            ref=definition.tool_ref,
            kind="http",
            display_name=definition.display_name,
            description=definition.description,
            input_schema=input_schema,
            output_schema=output_schema,
            annotations=annotations,
            timeout_ms=definition.timeout_ms,
            confirmation=self._definition_confirmation(metadata),
            confirmation_prompt=self._definition_confirmation_prompt(metadata),
            allowed_channels=self._definition_allowed_channels(metadata),
            tags=self._definition_tags(metadata),
            purpose=self._definition_purpose(metadata),
            when_to_use=self._definition_guidance_list(metadata, "when_to_use"),
            when_not_to_use=self._definition_guidance_list(metadata, "when_not_to_use"),
            input_examples=self._definition_input_examples(metadata),
            failure_modes=self._definition_failure_modes(metadata),
            output_validation_mode=self._definition_output_validation_mode(metadata),
            output_mapping=self._definition_output_mapping(metadata),
            executor_config=executor_config,
        )

    # ── Kind-specific builders (connection-less) ───────────────────────────────

    def _compile_code(self, definition: ToolDefinitionRecord) -> ToolSpec:
        """Compile a ``kind='code'`` definition into a ToolSpec routed to
        the CodeExecutor. Body lives in ``metadata_json.code_body``."""
        metadata = dict(definition.metadata_json or {})
        code_body = str(metadata.get("code_body") or "")
        callable_refs, callable_aliases = self._definition_callable_bindings(metadata)
        input_schema = self._coerce_schema(
            definition.input_schema_json,
            additional_properties=False,
            definition=definition,
        )
        output_schema = self._coerce_schema(
            definition.output_schema_json,
            additional_properties=True,
            definition=definition,
        )
        return ToolSpec(
            ref=definition.tool_ref,
            kind="code",
            display_name=definition.display_name,
            description=definition.description,
            input_schema=input_schema,
            output_schema=output_schema,
            annotations=self._definition_annotations(metadata),
            timeout_ms=definition.timeout_ms,
            confirmation=self._definition_confirmation(metadata),
            confirmation_prompt=self._definition_confirmation_prompt(metadata),
            allowed_channels=self._definition_allowed_channels(metadata),
            tags=self._definition_tags(metadata),
            purpose=self._definition_purpose(metadata),
            when_to_use=self._definition_guidance_list(metadata, "when_to_use"),
            when_not_to_use=self._definition_guidance_list(metadata, "when_not_to_use"),
            input_examples=self._definition_input_examples(metadata),
            failure_modes=self._definition_failure_modes(metadata),
            output_validation_mode=self._definition_output_validation_mode(metadata),
            output_mapping=self._definition_output_mapping(metadata),
            callable_refs=callable_refs,
            callable_aliases=callable_aliases,
            executor_config={"code_body": code_body},
        )

    def _compile_composite(self, definition: ToolDefinitionRecord) -> ToolSpec:
        """Compile a ``kind='composite'`` definition. Steps live in
        ``metadata_json.composite_steps`` as a list of ``{ref, args}``."""
        metadata = dict(definition.metadata_json or {})
        raw_steps = metadata.get("composite_steps") or []
        steps: list[dict[str, Any]] = []
        if isinstance(raw_steps, list):
            for step in raw_steps:
                if isinstance(step, dict) and step.get("ref"):
                    steps.append(copy.deepcopy(step))
        input_schema = self._coerce_schema(
            definition.input_schema_json,
            additional_properties=False,
            definition=definition,
        )
        output_schema = self._coerce_schema(
            definition.output_schema_json,
            additional_properties=True,
            definition=definition,
        )
        return ToolSpec(
            ref=definition.tool_ref,
            kind="composite",
            display_name=definition.display_name,
            description=definition.description,
            input_schema=input_schema,
            output_schema=output_schema,
            annotations=self._definition_annotations(metadata),
            timeout_ms=definition.timeout_ms,
            confirmation=self._definition_confirmation(metadata),
            confirmation_prompt=self._definition_confirmation_prompt(metadata),
            allowed_channels=self._definition_allowed_channels(metadata),
            tags=self._definition_tags(metadata),
            purpose=self._definition_purpose(metadata),
            when_to_use=self._definition_guidance_list(metadata, "when_to_use"),
            when_not_to_use=self._definition_guidance_list(metadata, "when_not_to_use"),
            input_examples=self._definition_input_examples(metadata),
            failure_modes=self._definition_failure_modes(metadata),
            output_validation_mode=self._definition_output_validation_mode(metadata),
            output_mapping=self._definition_output_mapping(metadata),
            executor_config={"composite_steps": steps},
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_url(base_url: str, endpoint_path: str) -> str:
        base = base_url.rstrip("/")
        path = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
        return f"{base}{path}"

    def _build_auth_headers(
        self,
        connection: APIConnectionRecord,
        *,
        caller: "ToolCaller | None" = None,
    ) -> dict[str, str]:
        auth_type = connection.auth_type
        if auth_type == "none":
            return {}
        if auth_type == "oauth2":
            token_payload = self._resolve_oauth_token(connection, caller=caller)
            access_token = (token_payload or {}).get("access_token")
            if not access_token:
                log.warning(
                    "connection %s has oauth2 auth but no access_token available",
                    connection.connection_id,
                )
                return {}
            return {"Authorization": f"Bearer {access_token}"}
        creds = self._resolve_credentials(connection, caller=caller)
        if not creds:
            return {}
        if auth_type == "bearer_token":
            token = str(creds.get("token") or "")
            return {"Authorization": f"Bearer {token}"} if token else {}
        if auth_type == "api_key":
            api_key = str(creds.get("api_key") or "")
            header_name = str(creds.get("header_name") or "X-Api-Key")
            return {header_name: api_key} if api_key else {}
        log.warning(
            "connection %s has unknown auth_type=%s", connection.connection_id, auth_type
        )
        return {}

    def _resolve_oauth_token(
        self,
        connection: APIConnectionRecord,
        *,
        caller: "ToolCaller | None",
    ) -> dict[str, Any] | None:
        """Return the decrypted OAuth token payload, or None if unavailable.

        - ``caller`` is None  (list / preview path) → skip decrypt, return None
          and let the caller emit a spec with no Authorization header.  We
          deliberately do not fall back to ``oauth_token_json`` in this case
          because audit trails are the phase-2 contract — reading plaintext
          silently would leave a gap.

        - ``caller`` is provided and a ``connection_store`` is wired → route
          through ``decrypt_oauth_token_from_record`` so the read is audited.
          Phase-1 rows without ``oauth_token_ct`` are handled by the store's
          fallback branch.

        - ``caller`` is provided but no ``connection_store`` (legacy wiring
          pre-phase-2) → fall back to ``oauth_token_json`` so the runtime
          still works during the rollout.  A warning is logged so the gap
          is visible.
        """
        if caller is None:
            return None

        if self._connection_store is not None:
            actor_id, actor_type = _actor_from_caller(caller)
            try:
                return self._connection_store.decrypt_oauth_token_from_record(
                    connection,
                    actor_id=actor_id,
                    actor_type=actor_type,  # type: ignore[arg-type]
                    purpose="http_tool_call",
                )
            except Exception:
                log.exception(
                    "oauth decrypt via connection_store failed for %s",
                    connection.connection_id,
                )
                return None

        log.warning(
            "ToolSpecCompiler has no connection_store; refusing plaintext "
            "oauth_token_json fallback for connection %s",
            connection.connection_id,
        )
        return None

    def _resolve_credentials(
        self,
        connection: APIConnectionRecord,
        *,
        caller: "ToolCaller | None",
    ) -> dict[str, Any]:
        if caller is None:
            return {}

        if self._connection_store is not None:
            actor_id, actor_type = _actor_from_caller(caller)
            try:
                return self._connection_store.decrypt_credentials_from_record(
                    connection,
                    actor_id=actor_id,
                    actor_type=actor_type,  # type: ignore[arg-type]
                    purpose="http_tool_call",
                )
            except Exception:
                log.exception(
                    "credential decrypt via connection_store failed for %s",
                    connection.connection_id,
                )
                return {}

        if self._cipher is None or not connection.credentials_enc:
            log.warning(
                "ToolSpecCompiler has no audited credential path for connection %s",
                connection.connection_id,
            )
            return {}
        try:
            return self._cipher.decrypt(connection.credentials_enc)
        except Exception:
            log.exception(
                "failed to decrypt credentials for connection %s",
                connection.connection_id,
            )
            return {}

    @staticmethod
    def _definition_executor_config(definition: ToolDefinitionRecord) -> dict[str, Any]:
        metadata = dict(definition.metadata_json or {})
        executor_config = metadata.get("executor_config")
        if not isinstance(executor_config, dict):
            return {}
        return copy.deepcopy(executor_config)

    @staticmethod
    def _definition_annotations(metadata: dict[str, Any]) -> ToolAnnotations:
        raw = metadata.get("annotations")
        if not isinstance(raw, dict):
            return ToolAnnotations(read_only=bool(metadata.get("read_only", False)))
        normalized = copy.deepcopy(raw)
        normalized.setdefault("read_only", bool(metadata.get("read_only", False)))
        return ToolAnnotations.model_validate(normalized)

    @staticmethod
    def _definition_confirmation(metadata: dict[str, Any]) -> str:
        raw = metadata.get("confirmation")
        if isinstance(raw, str) and raw in {"never", "always", "destructive_only"}:
            return raw
        return "never"

    @staticmethod
    def _definition_confirmation_prompt(metadata: dict[str, Any]) -> str | None:
        raw = metadata.get("confirmation_prompt")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    @staticmethod
    def _definition_allowed_channels(metadata: dict[str, Any]) -> list[str]:
        raw = metadata.get("allowed_channels")
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if isinstance(item, str) and item]

    @staticmethod
    def _definition_tags(metadata: dict[str, Any]) -> list[str]:
        raw = metadata.get("tags")
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if isinstance(item, str) and item]

    @staticmethod
    def _definition_purpose(metadata: dict[str, Any]) -> str | None:
        raw = metadata.get("purpose")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    @staticmethod
    def _definition_guidance_list(metadata: dict[str, Any], key: str) -> list[str]:
        raw = metadata.get(key)
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]

    @staticmethod
    def _definition_input_examples(metadata: dict[str, Any]) -> list[dict[str, Any]]:
        raw = metadata.get("input_examples")
        if not isinstance(raw, list):
            return []
        examples: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                examples.append(copy.deepcopy(item))
        return examples

    @staticmethod
    def _definition_failure_modes(metadata: dict[str, Any]) -> list[dict[str, Any]]:
        raw = metadata.get("failure_modes")
        if not isinstance(raw, list):
            return []
        modes: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                modes.append(copy.deepcopy(item))
        return modes

    @staticmethod
    def _definition_output_validation_mode(metadata: dict[str, Any]) -> str:
        raw = metadata.get("output_validation_mode")
        if raw == "strict":
            return "strict"
        return "warn"

    @staticmethod
    def _definition_output_mapping(metadata: dict[str, Any]) -> dict[str, str]:
        """Coerce ``metadata.output_mapping`` to ``{fact_name: expr}``.

        Each entry maps a fact name to an extraction expression. Expressions
        starting with ``$.`` are dotted paths walked through the result
        output; anything else is read as a top-level key. Non-string keys
        and non-string values are dropped silently — invalid entries should
        not block tool resolution."""
        raw = metadata.get("output_mapping")
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not key.strip():
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            out[key.strip()] = value.strip()
        return out

    @staticmethod
    def _definition_callable_bindings(
        metadata: dict[str, Any],
    ) -> tuple[list[str], dict[str, str]]:
        """Read ``metadata.callable_refs`` and ``metadata.callable_aliases``.

        ``callable_refs`` is a list of refs the code body is permitted to
        invoke. ``callable_aliases`` maps the sandbox-visible function name
        to the ref. Missing/invalid entries are dropped silently — the
        ToolSpec validator catches structurally bad combinations
        (alias->ref pointing at undeclared ref, duplicate refs, etc)."""
        raw_refs = metadata.get("callable_refs")
        refs: list[str] = []
        if isinstance(raw_refs, list):
            seen: set[str] = set()
            for entry in raw_refs:
                if not isinstance(entry, str):
                    continue
                ref = entry.strip()
                if not ref or ref in seen:
                    continue
                seen.add(ref)
                refs.append(ref)
        raw_aliases = metadata.get("callable_aliases")
        aliases: dict[str, str] = {}
        if isinstance(raw_aliases, dict):
            for alias, target in raw_aliases.items():
                if not isinstance(alias, str) or not isinstance(target, str):
                    continue
                alias_clean = alias.strip()
                target_clean = target.strip()
                if not alias_clean or not target_clean:
                    continue
                aliases[alias_clean] = target_clean
        return refs, aliases

    @staticmethod
    def _coerce_schema(
        schema: dict[str, Any] | None,
        *,
        additional_properties: bool,
        definition: "ToolDefinitionRecord | None" = None,
    ) -> dict[str, Any]:
        """Ensure the stored schema is a valid JSON Schema object block."""
        if ToolSpecCompiler._should_use_permissive_template_schema(
            definition,
            schema=schema,
        ):
            return copy.deepcopy(_PERMISSIVE_OBJECT_SCHEMA)
        if not schema or not isinstance(schema, dict):
            base = dict(
                _DEFAULT_INPUT_SCHEMA if not additional_properties else _DEFAULT_OUTPUT_SCHEMA
            )
            return base
        if schema.get("type") != "object":
            return {
                "type": "object",
                "properties": {},
                "additionalProperties": additional_properties,
            }
        return schema

    @staticmethod
    def _should_use_permissive_template_schema(
        definition: "ToolDefinitionRecord | None",
        *,
        schema: dict[str, Any] | None,
    ) -> bool:
        """Compat path for legacy provider-template integration tools.

        Older one-click integrations were seeded with empty ``{}`` input/output
        schemas. Treating those as the strict default object schema blocks every
        real invocation with ``additionalProperties`` validation errors before
        the request ever reaches the provider. Provider-template tools should be
        permissive until an explicit schema is authored.
        """
        if definition is None or definition.kind != "integration":
            return False
        metadata = definition.metadata_json if isinstance(definition.metadata_json, dict) else {}
        if not metadata.get("template_slug"):
            return False
        return not schema or not isinstance(schema, dict) or not schema.keys()
