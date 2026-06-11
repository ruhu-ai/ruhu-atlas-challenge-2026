from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .management import (
    APIConnectionStore,
    ToolAgentAssignmentStore,
    ToolDefinitionStore,
)


_NAME_SANITIZER = re.compile(r"[^a-z0-9_]+")


# Auth types the HTTP executor's compiler knows how to inject. ``"auto"``
# resolves to the first detected scheme that lands inside this set, or
# ``"none"`` if nothing matches. Keeping this whitelist explicit prevents
# the combined route from creating a connection with an auth_type the
# runtime can't satisfy (e.g., ``openid_connect`` — recognised by the
# detector but not yet plumbed into the compiler).
_SUPPORTED_RUNTIME_AUTH_TYPES: tuple[str, ...] = (
    "oauth2",
    "bearer_token",
    "api_key",
    "basic",
    "none",
)
_AUTO_AUTH_TYPE_PREFERENCE: tuple[str, ...] = (
    "oauth2",
    "bearer_token",
    "api_key",
    "basic",
)


def auto_select_auth_type(detected: list[DetectedAuthScheme]) -> str:
    """Pick the most-usable runtime auth type from *detected*, falling
    back to ``"none"`` when nothing matches.

    Preference is OAuth2 → bearer → api_key → basic. OAuth2 is preferred
    when present because it's the only scheme that supports automatic
    token rotation; the others all require the user to paste a static
    secret on connection creation.
    """
    for preference in _AUTO_AUTH_TYPE_PREFERENCE:
        for scheme in detected:
            if scheme.auth_type == preference:
                return preference
    return "none"


@dataclass
class DetectedAuthScheme:
    """An auth scheme parsed from an OpenAPI document.

    The ``auth_type`` field maps directly to ``APIConnectionRecord.auth_type``
    so callers (e.g., the combined create+ingest route) can pre-populate
    a connection without re-mapping. Spec-only metadata (header name for
    api_key, OAuth URLs, scopes) is carried on the same record so the UI
    can pre-fill the connection-creation form.

    OpenAPI 3.x ``components.securitySchemes`` is the source of record;
    OpenAPI 2.0 ``securityDefinitions`` is also recognised for legacy
    Swagger specs (still common in customer-supplied uploads).
    """

    name: str
    auth_type: str  # "bearer_token" | "api_key" | "oauth2" | "basic" | "openid_connect"
    description: str | None = None
    # api_key only
    api_key_location: str | None = None  # "header" | "query" | "cookie"
    api_key_name: str | None = None
    # oauth2 only
    oauth_flow: str | None = None  # "authorization_code" | "client_credentials" | "implicit" | "password"
    authorization_url: str | None = None
    token_url: str | None = None
    refresh_url: str | None = None
    scopes: list[str] = field(default_factory=list)
    # openid_connect only
    openid_connect_url: str | None = None


@dataclass
class OpenAPIIngestionResult:
    connection_id: str
    created_tool_ids: list[str] = field(default_factory=list)
    updated_tool_ids: list[str] = field(default_factory=list)
    assigned_tool_ids: list[str] = field(default_factory=list)
    detected_auth_schemes: list[DetectedAuthScheme] = field(default_factory=list)


# ── Auth-scheme detection ─────────────────────────────────────────────


def detect_auth_schemes(spec: dict[str, Any]) -> list[DetectedAuthScheme]:
    """Parse the OpenAPI document's auth schemes into ``DetectedAuthScheme``s.

    Accepts both shapes:

    * OpenAPI 3.x: ``components.securitySchemes``
    * OpenAPI 2.0: ``securityDefinitions`` at the root

    Returns an empty list when neither is present (a public API). The
    list ordering matches the spec's iteration order so a UI showing
    "preferred auth scheme" can rely on the document author's intent.

    Unknown ``type`` values are skipped silently — better than raising,
    because some specs include vendor extensions and the caller only
    wants the schemes we can pre-populate. The skipped schemes can be
    surfaced via a warning channel by future callers if needed.
    """
    if not isinstance(spec, dict):
        return []

    raw: dict[str, Any] = {}
    components = spec.get("components")
    if isinstance(components, dict):
        ss = components.get("securitySchemes")
        if isinstance(ss, dict):
            raw = ss
    if not raw:
        sd = spec.get("securityDefinitions")
        if isinstance(sd, dict):
            raw = sd
    if not raw:
        return []

    detected: list[DetectedAuthScheme] = []
    for name, scheme in raw.items():
        parsed = _parse_security_scheme(name, scheme)
        if parsed is not None:
            detected.append(parsed)
    return detected


def _parse_security_scheme(name: str, scheme: Any) -> DetectedAuthScheme | None:
    if not isinstance(scheme, dict):
        return None
    scheme_type = str(scheme.get("type") or "").lower()
    description = scheme.get("description")
    description_str = str(description) if isinstance(description, str) else None

    if scheme_type == "http":
        # OpenAPI 3.x http auth: scheme="bearer" or "basic"
        http_scheme = str(scheme.get("scheme") or "").lower()
        if http_scheme == "bearer":
            return DetectedAuthScheme(
                name=name, auth_type="bearer_token", description=description_str
            )
        if http_scheme == "basic":
            return DetectedAuthScheme(
                name=name, auth_type="basic", description=description_str
            )
        return None  # digest, hoba, etc. — not a connection auth_type we support
    if scheme_type == "basic":
        # OpenAPI 2.0 basic auth.
        return DetectedAuthScheme(
            name=name, auth_type="basic", description=description_str
        )
    if scheme_type == "apikey":
        return DetectedAuthScheme(
            name=name,
            auth_type="api_key",
            description=description_str,
            api_key_location=str(scheme.get("in") or "").lower() or None,
            api_key_name=str(scheme.get("name") or "") or None,
        )
    if scheme_type == "oauth2":
        flow_name, flow = _select_oauth_flow(scheme)
        scopes_dict = (flow or {}).get("scopes") or {}
        scopes_list: list[str] = list(scopes_dict.keys()) if isinstance(scopes_dict, dict) else []
        return DetectedAuthScheme(
            name=name,
            auth_type="oauth2",
            description=description_str,
            oauth_flow=flow_name,
            authorization_url=(flow or {}).get("authorizationUrl"),
            token_url=(flow or {}).get("tokenUrl"),
            refresh_url=(flow or {}).get("refreshUrl"),
            scopes=scopes_list,
        )
    if scheme_type == "openidconnect":
        return DetectedAuthScheme(
            name=name,
            auth_type="openid_connect",
            description=description_str,
            openid_connect_url=str(scheme.get("openIdConnectUrl") or "") or None,
        )
    return None


def _select_oauth_flow(scheme: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Pick the most user-relevant OAuth flow definition from the scheme.

    Preference order: authorization_code > client_credentials > password >
    implicit > legacy 2.0 single-flow shape. The first form covers most
    SaaS providers (HubSpot, Google, etc.); the others are uncommon but
    still legal so we don't silently lose them.

    OpenAPI 2.0 stored the flow type in ``flow`` and the URLs at the
    root of the scheme, not under a ``flows`` map. We normalise that to
    OpenAPI 3 shape on the way out.
    """
    flows = scheme.get("flows")
    if isinstance(flows, dict):
        for flow_name in ("authorizationCode", "clientCredentials", "password", "implicit"):
            flow = flows.get(flow_name)
            if isinstance(flow, dict):
                # Map to snake_case for the result so downstream code
                # doesn't need to learn the OpenAPI camelCase variant.
                return _flow_name_to_snake(flow_name), flow
        return None, None
    # OpenAPI 2.0: single flow at root.
    legacy_flow = scheme.get("flow")
    if isinstance(legacy_flow, str):
        return legacy_flow.lower().replace("accesscode", "authorization_code"), {
            "authorizationUrl": scheme.get("authorizationUrl"),
            "tokenUrl": scheme.get("tokenUrl"),
            "scopes": scheme.get("scopes") or {},
        }
    return None, None


def _flow_name_to_snake(camel: str) -> str:
    return {
        "authorizationCode": "authorization_code",
        "clientCredentials": "client_credentials",
        "password": "password",
        "implicit": "implicit",
    }.get(camel, camel)


class OpenAPIToolIngestionService:
    def __init__(
        self,
        *,
        connection_store: APIConnectionStore,
        definition_store: ToolDefinitionStore,
        assignment_store: ToolAgentAssignmentStore | None = None,
    ) -> None:
        self._connection_store = connection_store
        self._definition_store = definition_store
        self._assignment_store = assignment_store

    def ingest(
        self,
        *,
        organization_id: str,
        spec: dict[str, Any],
        connection_id: str | None = None,
        display_name: str | None = None,
        provider: str = "openapi",
        auth_type: str = "none",
        base_url: str | None = None,
        tool_ref_prefix: str | None = None,
        agent_id: str | None = None,
    ) -> OpenAPIIngestionResult:
        """Ingest *spec* into a connection and emit tool definitions.

        ``auth_type="auto"`` resolves to the first detected auth scheme
        (preference: oauth2 > bearer_token > api_key > basic), or
        ``"none"`` when nothing is detected. This makes the combined
        create-and-ingest flow a single round-trip for the typical case
        of "give me a connection that matches the spec's auth".

        Transactional guarantee for the create-then-ingest path: when
        this call CREATES a connection (``connection_id`` was None) and
        any tool-definition write fails, the connection is deleted so
        the caller is not left with a half-set-up integration.
        Ingest-into-existing (``connection_id`` provided) is NOT rolled
        back — the existing connection wasn't created by us.
        """
        detected = detect_auth_schemes(spec)
        # Resolve auth_type before connection creation so the stored
        # value reflects what the runtime will actually use.
        if auth_type == "auto":
            auth_type = auto_select_auth_type(detected)

        is_new_connection = connection_id is None
        connection = self._resolve_connection(
            organization_id=organization_id,
            spec=spec,
            connection_id=connection_id,
            display_name=display_name,
            provider=provider,
            auth_type=auth_type,
            base_url=base_url,
        )
        result = OpenAPIIngestionResult(
            connection_id=connection.connection_id,
            detected_auth_schemes=detected,
        )

        try:
            self._write_definitions(
                organization_id=organization_id,
                connection=connection,
                spec=spec,
                tool_ref_prefix=tool_ref_prefix,
                agent_id=agent_id,
                result=result,
            )
        except Exception:
            # Roll back the connection IF we created it in this call —
            # half-set-up integrations are worse than no integration:
            # they show up in the UI's connection list but have no tools.
            if is_new_connection:
                try:
                    self._connection_store.delete(connection.connection_id)
                except Exception:
                    # Cleanup best-effort. Log via the surrounding caller's
                    # exception path; we never swallow the original error.
                    pass
            raise
        return result

    def _write_definitions(
        self,
        *,
        organization_id: str,
        connection: Any,
        spec: dict[str, Any],
        tool_ref_prefix: str | None,
        agent_id: str | None,
        result: OpenAPIIngestionResult,
    ) -> None:
        for operation in self._iter_operations(spec):
            tool_ref = self._tool_ref_for_operation(
                operation=operation,
                prefix=tool_ref_prefix,
            )
            existing = self._definition_store.get_by_ref(organization_id, tool_ref)
            payload = self._definition_payload(operation, tool_ref=tool_ref)
            if existing is None:
                created = self._definition_store.create(
                    organization_id=organization_id,
                    connection_id=connection.connection_id,
                    **payload,
                )
                result.created_tool_ids.append(created.tool_definition_id)
                tool_definition_id = created.tool_definition_id
            else:
                updated = self._definition_store.update(
                    existing.tool_definition_id,
                    display_name=payload["display_name"],
                    description=payload["description"],
                    function_name=payload["function_name"],
                    endpoint_path=payload["endpoint_path"],
                    http_method=payload["http_method"],
                    input_schema=payload["input_schema"],
                    output_schema=payload["output_schema"],
                    read_only=payload["read_only"],
                    metadata=payload["metadata"],
                )
                result.updated_tool_ids.append(updated.tool_definition_id)
                tool_definition_id = updated.tool_definition_id
            if agent_id and self._assignment_store is not None:
                assignment = self._assignment_store.assign(
                    organization_id=organization_id,
                    agent_id=agent_id,
                    tool_definition_id=tool_definition_id,
                )
                result.assigned_tool_ids.append(assignment.tool_definition_id)

    def _resolve_connection(
        self,
        *,
        organization_id: str,
        spec: dict[str, Any],
        connection_id: str | None,
        display_name: str | None,
        provider: str,
        auth_type: str,
        base_url: str | None,
    ) -> Any:
        if connection_id:
            record = self._connection_store.get(connection_id)
            if record is None or record.organization_id != organization_id:
                raise KeyError("connection not found")
            return record

        info = dict(spec.get("info") or {})
        server_urls = self._server_urls(spec)
        created = self._connection_store.create(
            organization_id=organization_id,
            display_name=display_name or str(info.get("title") or "Imported OpenAPI"),
            provider=provider,
            auth_type=auth_type,
            base_url=base_url or (server_urls[0] if server_urls else None),
            metadata={
                "ingestion_source": "openapi",
                "openapi_version": str(spec.get("openapi") or ""),
            },
        )
        return created

    @staticmethod
    def _server_urls(spec: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        for server in spec.get("servers", []):
            if not isinstance(server, dict):
                continue
            url = str(server.get("url") or "").strip()
            if url:
                urls.append(url.rstrip("/"))
        return urls

    def _iter_operations(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        operations: list[dict[str, Any]] = []
        paths = spec.get("paths", {})
        if not isinstance(paths, dict):
            return operations
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            shared_parameters = path_item.get("parameters") if isinstance(path_item.get("parameters"), list) else []
            for method, operation in path_item.items():
                normalized_method = str(method).lower()
                if normalized_method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if not isinstance(operation, dict):
                    continue
                operations.append(
                    {
                        "path": str(path),
                        "method": normalized_method.upper(),
                        "operation": operation,
                        "shared_parameters": list(shared_parameters),
                    }
                )
        return operations

    def _definition_payload(self, operation: dict[str, Any], *, tool_ref: str) -> dict[str, Any]:
        op = dict(operation["operation"] or {})
        method = str(operation["method"])
        path = str(operation["path"])
        display_name = str(op.get("summary") or op.get("operationId") or f"{method} {path}").strip()
        description = str(op.get("description") or display_name).strip()
        if len(description) < 20:
            description = f"{description}. Imported from an OpenAPI specification."
        input_schema = self._build_input_schema(operation)
        output_schema = self._build_output_schema(op)
        metadata = {
            "ingestion_source": "openapi",
            "operation_id": op.get("operationId"),
            "tags": list(op.get("tags") or []),
        }
        return {
            "kind": "api",
            "tool_ref": tool_ref,
            "function_name": self._function_name(tool_ref),
            "display_name": display_name,
            "description": description,
            "endpoint_path": path,
            "http_method": method,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "read_only": method in {"GET", "HEAD", "OPTIONS"},
            "metadata": metadata,
        }

    def _build_input_schema(self, operation: dict[str, Any]) -> dict[str, Any]:
        op = dict(operation["operation"] or {})
        parameters = list(operation.get("shared_parameters") or [])
        parameters.extend(op.get("parameters") or [])
        properties: dict[str, Any] = {}
        required: list[str] = []

        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("name") or "").strip()
            if not name:
                continue
            schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
            param_type = str(schema.get("type") or parameter.get("type") or "string")
            properties[name] = {
                "type": param_type,
                "description": str(parameter.get("description") or f"{name} parameter").strip() or f"{name} parameter",
            }
            enum = schema.get("enum")
            if isinstance(enum, list):
                properties[name]["enum"] = list(enum)
            if bool(parameter.get("required")) and name not in required:
                required.append(name)

        request_body = op.get("requestBody")
        if isinstance(request_body, dict):
            schema = self._json_schema_from_request_body(request_body)
            if isinstance(schema, dict):
                body_properties = schema.get("properties")
                if isinstance(body_properties, dict):
                    properties.update(body_properties)
                body_required = schema.get("required")
                if isinstance(body_required, list):
                    for name in body_required:
                        if isinstance(name, str) and name not in required:
                            required.append(name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    @staticmethod
    def _build_output_schema(operation: dict[str, Any]) -> dict[str, Any]:
        responses = operation.get("responses")
        if not isinstance(responses, dict):
            return {"type": "object", "properties": {}, "additionalProperties": True}
        preferred = next(
            (
                response
                for status, response in responses.items()
                if str(status).startswith("2") and isinstance(response, dict)
            ),
            None,
        )
        if preferred is None:
            return {"type": "object", "properties": {}, "additionalProperties": True}
        content = preferred.get("content")
        if not isinstance(content, dict):
            return {"type": "object", "properties": {}, "additionalProperties": True}
        json_content = content.get("application/json")
        if not isinstance(json_content, dict):
            return {"type": "object", "properties": {}, "additionalProperties": True}
        schema = json_content.get("schema")
        if isinstance(schema, dict) and schema.get("type") == "object":
            output = dict(schema)
            output.setdefault("additionalProperties", True)
            return output
        return {"type": "object", "properties": {"data": dict(schema or {})}, "additionalProperties": False}

    @staticmethod
    def _json_schema_from_request_body(request_body: dict[str, Any]) -> dict[str, Any] | None:
        content = request_body.get("content")
        if not isinstance(content, dict):
            return None
        json_content = content.get("application/json")
        if not isinstance(json_content, dict):
            return None
        schema = json_content.get("schema")
        return dict(schema) if isinstance(schema, dict) else None

    @staticmethod
    def _tool_ref_for_operation(operation: dict[str, Any], *, prefix: str | None) -> str:
        op = dict(operation["operation"] or {})
        operation_id = str(op.get("operationId") or "").strip()
        if operation_id:
            suffix = _NAME_SANITIZER.sub("_", operation_id.lower()).strip("_")
        else:
            method = str(operation["method"]).lower()
            path = str(operation["path"]).replace("/", "_")
            suffix = _NAME_SANITIZER.sub("_", f"{method}_{path}".lower()).strip("_")
        if prefix:
            return f"{prefix.rstrip('.')}.{suffix}"
        return suffix

    @staticmethod
    def _function_name(tool_ref: str) -> str:
        return _NAME_SANITIZER.sub("_", tool_ref.split(".")[-1].lower()).strip("_") or "imported_tool"
