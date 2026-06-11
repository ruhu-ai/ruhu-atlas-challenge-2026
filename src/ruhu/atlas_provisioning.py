from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

from .atlas_docs_parser import AtlasDocsPageExtraction, AtlasDocsPageParser
from .atlas_protocol import (
    AtlasAPIDiscoveryRequest,
    AtlasAPIDiscoveryResult,
    AtlasDependency,
    AtlasProvisioningCandidate,
    AtlasProvisioningManifestItem,
)
from .tools.provider_templates import PROVIDER_TEMPLATES, list_templates


_ATLAS_REMOTE_FETCH_MAX_CHARS = 1_000_000
_ATLAS_REMOTE_FETCH_MAX_BYTES = 1_000_000
_ATLAS_REMOTE_FETCH_MAX_REDIRECTS = 3
_ATLAS_BLOCKED_REMOTE_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
)
_ATLAS_REMOTE_UNSUPPORTED_CONTENT_TYPE_PREFIXES = (
    "image/",
    "audio/",
    "video/",
    "font/",
)
_ATLAS_REMOTE_UNSUPPORTED_CONTENT_TYPES = {
    "application/pdf",
    "application/zip",
    "application/octet-stream",
}
_PROVIDER_TEMPLATE_METADATA = {item["slug"]: item for item in list_templates()}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _load_structured_payload(source_value: str) -> dict[str, Any] | None:
    raw = (source_value or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _is_blocked_remote_ip(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return not parsed.is_global


def _validate_remote_fetch_url(url: str) -> str:
    normalized = (url or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("remote fetch URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("remote fetch URL must not include credentials")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("remote fetch URL must include a hostname")
    if hostname == "localhost" or hostname.endswith(_ATLAS_BLOCKED_REMOTE_HOST_SUFFIXES):
        raise ValueError("remote fetch URL host is not allowed")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname:
            raise ValueError("remote fetch URL must not target internal hostnames")
        try:
            resolved = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError(f"remote fetch URL host could not be resolved: {hostname}") from exc
        for item in resolved:
            ip = item[4][0]
            if _is_blocked_remote_ip(ip):
                raise ValueError("remote fetch URL resolves to a non-public address")
    else:
        if _is_blocked_remote_ip(str(literal_ip)):
            raise ValueError("remote fetch URL targets a non-public address")
    return normalized


def is_safe_provisioning_base_url(base_url: str | None) -> bool:
    """Reject a provisioning base URL that targets an internal/non-public host.

    Used to gate a base_url that originates from fetched spec content or LLM
    extraction (``servers[0].url`` / docs-page extraction) before it is
    embedded in a provisioning delta and, later, executed against. Unlike
    ``_validate_remote_fetch_url`` this performs NO DNS resolution: base URLs
    are frequently templated (``https://{region}.api.example.com``) and tool
    execution re-validates at connect time; the goal here is to block the
    obvious literal-internal targets (metadata IP, loopback, RFC1918,
    ``localhost``) a prompt-injected spec might plant.

    An empty value is "safe" (not every provider template needs a base URL);
    the caller decides whether absence is acceptable.
    """
    if not base_url:
        return True
    normalized = base_url.strip()
    if not normalized:
        return True
    # Strip template placeholders so a host like "{region}.example.com" is
    # judged on its static suffix rather than rejected for being unparseable.
    host_probe = re.sub(r"\{[^}]*\}", "x", normalized)
    parsed = urlparse(host_probe)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname == "localhost" or hostname.endswith(_ATLAS_BLOCKED_REMOTE_HOST_SUFFIXES):
        return False
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        return True  # a (possibly templated) DNS name; execution re-validates
    return not _is_blocked_remote_ip(str(literal_ip))


def _pinned_request_for_url(url: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Resolve the URL's host once and pin the connection to a validated IP.

    Defends against DNS rebinding: ``_validate_remote_fetch_url`` resolves
    the hostname to check it is public, but if the connection then
    re-resolves the name independently, a rebinding DNS server can pass
    validation and still steer the connect to an internal address. This
    helper performs a single resolution, validates EVERY returned address,
    and returns ``(request_url, headers, extensions)`` where:

    * ``request_url`` targets the validated IP literal directly (so httpx
      never re-resolves the name),
    * ``headers`` carries the original ``Host`` header, and
    * ``extensions`` sets ``sni_hostname`` to the original hostname for
      HTTPS, so TLS SNI and certificate verification still run against
      the ORIGINAL hostname, not the IP.

    Literal-IP URLs pass through unchanged (already validated, nothing to
    rebind). Resolution and pinning fail CLOSED: ``_validate_remote_fetch_url``
    resolved this exact name a moment earlier, so a failure or empty result
    here means the name's resolution just changed under us — the rebinding
    signal itself — and we must not fall back to letting httpx re-resolve.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().lower()
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return url, {}, {}

    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        resolved = socket.getaddrinfo(hostname, port or default_port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"remote fetch URL host could not be pinned: {hostname}") from exc

    pinned_ip: str | None = None
    for item in resolved:
        ip = item[4][0]
        if _is_blocked_remote_ip(ip):
            raise ValueError("remote fetch URL resolves to a non-public address")
        if pinned_ip is None:
            pinned_ip = ip
    if pinned_ip is None:
        raise ValueError(f"remote fetch URL host did not resolve to a pinnable address: {hostname}")

    ip_literal = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    pinned_netloc = f"{ip_literal}:{port}" if port is not None else ip_literal
    pinned_url = parsed._replace(netloc=pinned_netloc).geturl()
    host_header = f"{hostname}:{port}" if port is not None else hostname
    headers = {"Host": host_header}
    extensions = {"sni_hostname": hostname} if parsed.scheme == "https" else {}
    return pinned_url, headers, extensions


def _fetch_remote_text(url: str) -> tuple[str | None, str | None]:
    current_url = _validate_remote_fetch_url(url)
    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        for redirect_count in range(_ATLAS_REMOTE_FETCH_MAX_REDIRECTS + 1):
            request_url, pinned_headers, pinned_extensions = _pinned_request_for_url(current_url)
            with client.stream(
                "GET", request_url, headers=pinned_headers, extensions=pinned_extensions
            ) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("remote fetch redirect did not include a location")
                    if redirect_count >= _ATLAS_REMOTE_FETCH_MAX_REDIRECTS:
                        raise ValueError("remote fetch exceeded redirect limit")
                    current_url = _validate_remote_fetch_url(urljoin(current_url, location))
                    continue

                response.raise_for_status()
                content_type = response.headers.get("content-type")
                normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
                if normalized_content_type in _ATLAS_REMOTE_UNSUPPORTED_CONTENT_TYPES or any(
                    normalized_content_type.startswith(prefix)
                    for prefix in _ATLAS_REMOTE_UNSUPPORTED_CONTENT_TYPE_PREFIXES
                ):
                    raise ValueError(f"unsupported remote content type: {normalized_content_type or 'unknown'}")

                chunks: list[bytes] = []
                total_bytes = 0
                for chunk in response.iter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > _ATLAS_REMOTE_FETCH_MAX_BYTES:
                        raise ValueError(
                            f"remote content exceeds Atlas fetch limit ({total_bytes} bytes > {_ATLAS_REMOTE_FETCH_MAX_BYTES})"
                        )
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                body = b"".join(chunks).decode(encoding, errors="replace")
                if len(body) > _ATLAS_REMOTE_FETCH_MAX_CHARS:
                    raise ValueError(
                        f"remote content exceeds Atlas fetch limit ({len(body)} chars > {_ATLAS_REMOTE_FETCH_MAX_CHARS})"
                    )
                return body, content_type
    raise ValueError("remote fetch failed")



def _looks_like_html(content_type: str | None, body: str) -> bool:
    normalized = (content_type or "").lower()
    if "text/html" in normalized or "application/xhtml" in normalized:
        return True
    snippet = body.lstrip()[:200].lower()
    return "<html" in snippet or "<body" in snippet or "<title" in snippet


def _add_auth_field(missing_auth_fields: set[str], field_name: str | None) -> None:
    if field_name:
        missing_auth_fields.add(field_name)


def _auth_field_from_scheme_name(scheme_name: str) -> str:
    normalized = _slug(scheme_name)
    return normalized or "credentials"


def _canonical_auth_field(field_name: str | None) -> str | None:
    normalized = _slug(field_name or "")
    if not normalized:
        return None
    if "api_key" in normalized:
        return "api_key"
    if "bearer" in normalized or normalized in {"authorization", "auth_token", "access_token"}:
        return "bearer_token"
    if "basic" in normalized:
        return "basic_auth"
    if "oauth" in normalized or "openid" in normalized:
        return "oauth_authorization"
    return normalized


def _normalized_host(url: str | None) -> str:
    if not url:
        return ""
    host = urlparse(url).netloc.lower()
    host = re.sub(r"^\{[^}]+\}\.", "", host)
    return host.strip(".")


def _infer_provider_slug(
    *,
    provider_name: str | None,
    base_url: str | None,
    candidate_tool_refs: list[str],
) -> str | None:
    provider_slug = _slug(provider_name or "")
    provider_host = _normalized_host(base_url)
    for slug, template in PROVIDER_TEMPLATES.items():
        template_name = _slug(template.display_name)
        template_host = _normalized_host(template.base_url)
        starter_refs = {starter.ref for starter in template.starter_tools}
        if provider_slug and provider_slug in {slug, template_name}:
            return slug
        if provider_slug and (slug in provider_slug or template_name in provider_slug):
            return slug
        if provider_host and template_host and (provider_host == template_host or provider_host.endswith(template_host)):
            return slug
        if starter_refs and any(tool_ref in starter_refs for tool_ref in candidate_tool_refs):
            return slug
    return None


def _provider_setup_guidance(
    *,
    provider_slug: str | None,
    provider_name: str | None,
    missing_auth_fields: list[str],
    source_value: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    if provider_slug and provider_slug in _PROVIDER_TEMPLATE_METADATA:
        metadata = _PROVIDER_TEMPLATE_METADATA[provider_slug]
        display_name = metadata["display_name"]
        required_config = metadata.get("required_config") or []
        setup_url = f"/settings/integrations?provider={quote(provider_slug)}"
        documentation_url = source_value if source_value and source_value.startswith(("http://", "https://")) else None
        if "oauth_authorization" in missing_auth_fields and metadata.get("has_oauth"):
            action = f"Set up the {display_name} integration and complete OAuth authorization."
        elif "api_key" in missing_auth_fields:
            action = f"Set up the {display_name} integration and enter the required API key."
        elif "bearer_token" in missing_auth_fields:
            action = f"Set up the {display_name} integration and enter the required bearer token."
        elif "basic_auth" in missing_auth_fields:
            action = f"Set up the {display_name} integration and enter the required basic-auth credentials."
        else:
            action = f"Set up the {display_name} integration and review the discovered operations before provisioning."
        if required_config:
            action = f"{action} Required setup fields: {', '.join(required_config)}."
        return provider_slug, setup_url, documentation_url, action
    if not missing_auth_fields:
        return (
            None,
            None,
            source_value if source_value and source_value.startswith(("http://", "https://")) else None,
            "Review the discovered operations and choose which tools or integrations to provision.",
        )
    if "oauth_authorization" in missing_auth_fields:
        return (
            "custom_oauth",
            "/settings/integrations?provider=custom_oauth",
            source_value if source_value and source_value.startswith(("http://", "https://")) else None,
            f"Set up a custom OAuth provider connection for {provider_name or 'this API'} and complete authorization.",
        )
    custom_auth = (
        "api_key"
        if "api_key" in missing_auth_fields
        else "bearer"
        if "bearer_token" in missing_auth_fields
        else "basic"
        if "basic_auth" in missing_auth_fields
        else "none"
    )
    auth_phrase = {
        "api_key": "API key",
        "bearer": "bearer token",
        "basic": "basic-auth credentials",
        "none": "connection settings",
    }[custom_auth]
    return (
        "custom_api",
        f"/settings/integrations?provider=custom_api&auth_type={quote(custom_auth)}",
        source_value if source_value and source_value.startswith(("http://", "https://")) else None,
        f"Create a custom API connection for {provider_name or 'this API'} and provide the required {auth_phrase}.",
    )


def _auth_fields_from_openapi_security_schemes(payload: dict[str, Any]) -> list[str]:
    missing_auth_fields: set[str] = set()
    security_schemes = (
        payload.get("components", {}).get("securitySchemes", {})
        if isinstance(payload.get("components"), dict)
        else {}
    )
    for scheme_name, scheme in security_schemes.items():
        if not isinstance(scheme, dict):
            continue
        scheme_type = str(scheme.get("type") or "").strip()
        if scheme_type == "apiKey":
            _add_auth_field(
                missing_auth_fields,
                _canonical_auth_field(str(scheme.get("name") or "")) or "api_key",
            )
        elif scheme_type == "http":
            http_scheme = str(scheme.get("scheme") or "").strip().lower()
            if http_scheme == "bearer":
                _add_auth_field(missing_auth_fields, "bearer_token")
            elif http_scheme == "basic":
                _add_auth_field(missing_auth_fields, "basic_auth")
            else:
                _add_auth_field(missing_auth_fields, _auth_field_from_scheme_name(str(scheme_name)))
        elif scheme_type in {"oauth2", "openIdConnect"}:
            _add_auth_field(missing_auth_fields, "oauth_authorization")
        elif scheme_type:
            _add_auth_field(missing_auth_fields, _auth_field_from_scheme_name(str(scheme_name)))
    return sorted(missing_auth_fields)


def _auth_fields_from_postman_auth(auth_payload: Any) -> list[str]:
    if not isinstance(auth_payload, dict):
        return []
    auth_type = str(auth_payload.get("type") or "").strip().lower()
    if auth_type == "apikey":
        return ["api_key"]
    if auth_type == "bearer":
        return ["bearer_token"]
    if auth_type == "basic":
        return ["basic_auth"]
    if auth_type in {"oauth2", "oauth1", "awsv4"}:
        return ["oauth_authorization"]
    return []


def _heuristic_docs_page_result(
    request: AtlasAPIDiscoveryRequest,
    body: str,
) -> AtlasAPIDiscoveryResult:
    """Regex-based extraction of HTTP operations from a docs HTML page.

    Cheap, no external calls. Used as the primary path when no LLM
    parser is configured, and as the fallback path when the LLM parser
    fails. The returned ``spec_type`` is ``"heuristic"`` (not
    ``"llm_parsed"``) so the label honestly reflects what ran.
    """
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
    provider_name = (
        html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip())
        if title_match
        else "API documentation"
    )
    normalized = html.unescape(re.sub(r"<[^>]+>", " ", body))
    endpoint_matches = re.findall(
        r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[-A-Za-z0-9_./{}:]+)",
        normalized,
        flags=re.IGNORECASE,
    )
    candidate_endpoints: list[dict[str, Any]] = []
    candidate_tool_refs: list[str] = []
    seen: set[tuple[str, str]] = set()
    for method, path in endpoint_matches:
        key = (method.upper(), path)
        if key in seen:
            continue
        seen.add(key)
        tool_ref = _slug(f"{method}_{path}")
        candidate_tool_refs.append(tool_ref)
        candidate_endpoints.append(
            {
                "method": method.upper(),
                "path": path,
                "operation_id": None,
                "summary": None,
                "requires_auth": bool(re.search(r"\boauth\b|\bapi[- ]?key\b|\bauthorization\b", normalized, flags=re.IGNORECASE)),
            }
        )
    missing_auth_fields: list[str] = []
    if re.search(r"\bapi[- ]?key\b", normalized, flags=re.IGNORECASE):
        missing_auth_fields.append("api_key")
    if re.search(r"\bbearer(?:\s+token)?\b", normalized, flags=re.IGNORECASE):
        missing_auth_fields.append("bearer_token")
    if re.search(r"\bbasic\s+auth\b", normalized, flags=re.IGNORECASE):
        missing_auth_fields.append("basic_auth")
    if not missing_auth_fields and re.search(r"\boauth\b|\bauthorization\b", normalized, flags=re.IGNORECASE):
        missing_auth_fields.append("oauth_authorization")
    provider_slug = _infer_provider_slug(
        provider_name=provider_name,
        base_url=request.source_value,
        candidate_tool_refs=candidate_tool_refs,
    )
    resolved_provider_slug, setup_url, documentation_url, suggested_setup_action = _provider_setup_guidance(
        provider_slug=provider_slug,
        provider_name=provider_name,
        missing_auth_fields=missing_auth_fields,
        source_value=request.source_value,
    )
    candidate_provider_slug = resolved_provider_slug or _slug(provider_name or "docs")
    return AtlasAPIDiscoveryResult(
        request_id=request.request_id,
        status="discovered" if candidate_endpoints else "failed",
        provider_name=provider_name or None,
        candidate_tool_refs=candidate_tool_refs[:50],
        missing_auth_fields=missing_auth_fields,
        notes=(
            f"Discovered {len(candidate_endpoints)} candidate endpoint(s) from the provided documentation page."
            if candidate_endpoints
            else "Atlas could not detect structured API operations in the provided documentation page."
        ),
        spec_type="heuristic",
        base_url=request.source_value,
        candidate_endpoints=candidate_endpoints[:50],
        provisioning_candidates=[
            AtlasProvisioningCandidate(
                binding_key=f"{candidate_provider_slug}:{tool_ref}",
                display_name=tool_ref.replace("_", " ").strip() or tool_ref,
                tool_ref=tool_ref,
                requires_credentials=bool(missing_auth_fields),
                missing_fields=missing_auth_fields,
                suggested_setup_action=suggested_setup_action,
                provider_slug=resolved_provider_slug,
                setup_url=setup_url,
                documentation_url=documentation_url,
            )
            for tool_ref in candidate_tool_refs[:25]
        ],
        requires_review_before_provisioning=True,
    )


def _llm_docs_page_result(
    request: AtlasAPIDiscoveryRequest,
    extraction: AtlasDocsPageExtraction,
) -> AtlasAPIDiscoveryResult:
    """Build an ``AtlasAPIDiscoveryResult`` from an LLM extraction.

    Returned ``spec_type`` is ``"llm_parsed"`` because the LLM actually
    ran. The caller is responsible for handling the LLM-failure case
    by falling back to ``_heuristic_docs_page_result``.
    """
    candidate_endpoints: list[dict[str, Any]] = []
    candidate_tool_refs: list[str] = []
    seen_refs: set[str] = set()
    for endpoint in extraction.endpoints[:50]:
        ref = endpoint.operation_id or _slug(f"{endpoint.method}_{endpoint.path}")
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        candidate_tool_refs.append(ref)
        candidate_endpoints.append(
            {
                "method": endpoint.method,
                "path": endpoint.path,
                "operation_id": endpoint.operation_id,
                "summary": endpoint.summary,
                "requires_auth": endpoint.requires_auth,
            }
        )

    missing_auth_fields = list(extraction.missing_auth_fields)
    provider_name = extraction.provider_name
    base_url = extraction.base_url or request.source_value

    provider_slug = _infer_provider_slug(
        provider_name=provider_name,
        base_url=base_url,
        candidate_tool_refs=candidate_tool_refs,
    )
    resolved_provider_slug, setup_url, documentation_url, suggested_setup_action = _provider_setup_guidance(
        provider_slug=provider_slug,
        provider_name=provider_name,
        missing_auth_fields=missing_auth_fields,
        source_value=request.source_value,
    )
    candidate_provider_slug = resolved_provider_slug or _slug(provider_name or "docs")

    return AtlasAPIDiscoveryResult(
        request_id=request.request_id,
        status="discovered" if candidate_endpoints else "failed",
        provider_name=provider_name or None,
        candidate_tool_refs=candidate_tool_refs,
        missing_auth_fields=missing_auth_fields,
        notes=(
            f"LLM extracted {len(candidate_endpoints)} endpoint(s) from the documentation page."
            if candidate_endpoints
            else "LLM read the documentation page but did not identify any structured API operations."
        ),
        spec_type="llm_parsed",
        base_url=base_url,
        candidate_endpoints=candidate_endpoints,
        provisioning_candidates=[
            AtlasProvisioningCandidate(
                binding_key=f"{candidate_provider_slug}:{tool_ref}",
                display_name=tool_ref.replace("_", " ").strip() or tool_ref,
                tool_ref=tool_ref,
                requires_credentials=bool(missing_auth_fields),
                missing_fields=missing_auth_fields,
                suggested_setup_action=suggested_setup_action,
                provider_slug=resolved_provider_slug,
                setup_url=setup_url,
                documentation_url=documentation_url,
            )
            for tool_ref in candidate_tool_refs[:25]
        ],
        requires_review_before_provisioning=True,
    )


def _docs_page_result(
    request: AtlasAPIDiscoveryRequest,
    body: str,
    *,
    docs_parser: AtlasDocsPageParser | None = None,
) -> AtlasAPIDiscoveryResult:
    """Dispatcher: try the LLM parser first, fall back to the heuristic.

    The fallback path is the source of truth when no LLM is configured
    OR when the LLM call fails (network / parse / 4xx). The label on
    the returned result always reflects which path actually produced it
    (``"llm_parsed"`` vs ``"heuristic"``).
    """
    if docs_parser is not None and docs_parser.is_configured():
        extraction = docs_parser.parse(html_body=body, source_url=request.source_value)
        if extraction is not None and extraction.endpoints:
            return _llm_docs_page_result(request, extraction)
    return _heuristic_docs_page_result(request, body)


def _openapi_result(request: AtlasAPIDiscoveryRequest, payload: dict[str, Any]) -> AtlasAPIDiscoveryResult:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    title = str(info.get("title") or "OpenAPI integration").strip()
    provider_name = title or None
    servers = payload.get("servers") if isinstance(payload.get("servers"), list) else []
    server = next((item for item in servers if isinstance(item, dict) and item.get("url")), None)
    base_url = str(server.get("url")).strip() if isinstance(server, dict) else None
    paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
    candidate_endpoints: list[dict[str, Any]] = []
    candidate_tool_refs: list[str] = []
    missing_auth_fields = _auth_fields_from_openapi_security_schemes(payload)
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "").strip()
            tool_ref = operation_id or _slug(f"{method}_{path}")
            candidate_tool_refs.append(tool_ref)
            candidate_endpoints.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operation_id": operation_id or None,
                    "summary": str(operation.get("summary") or operation.get("description") or "").strip() or None,
                    "requires_auth": bool(operation.get("security") or payload.get("security") or missing_auth_fields),
                }
            )
    provider_slug = _infer_provider_slug(
        provider_name=provider_name,
        base_url=base_url,
        candidate_tool_refs=candidate_tool_refs,
    )
    resolved_provider_slug, setup_url, documentation_url, suggested_setup_action = _provider_setup_guidance(
        provider_slug=provider_slug,
        provider_name=provider_name,
        missing_auth_fields=missing_auth_fields,
        source_value=request.source_value,
    )
    candidate_provider_slug = resolved_provider_slug or _slug(provider_name or "openapi")
    provisioning_candidates = [
        AtlasProvisioningCandidate(
            binding_key=f"{candidate_provider_slug}:{tool_ref}",
            display_name=tool_ref.replace("_", " ").strip() or tool_ref,
            tool_ref=tool_ref,
            requires_credentials=bool(missing_auth_fields),
            missing_fields=missing_auth_fields,
            suggested_setup_action=suggested_setup_action,
            provider_slug=resolved_provider_slug,
            setup_url=setup_url,
            documentation_url=documentation_url,
        )
        for tool_ref in candidate_tool_refs[:25]
    ]
    return AtlasAPIDiscoveryResult(
        request_id=request.request_id,
        status="discovered" if candidate_endpoints else "failed",
        provider_name=provider_name,
        candidate_tool_refs=candidate_tool_refs[:50],
        missing_auth_fields=missing_auth_fields,
        notes=(
            f"Discovered {len(candidate_endpoints)} candidate endpoint(s) from the provided OpenAPI schema."
            if candidate_endpoints
            else "No operations were discovered in the provided OpenAPI schema."
        ),
        spec_type="openapi",
        base_url=base_url,
        candidate_endpoints=candidate_endpoints[:50],
        provisioning_candidates=provisioning_candidates,
        requires_review_before_provisioning=True,
    )


def _postman_result(request: AtlasAPIDiscoveryRequest, payload: dict[str, Any]) -> AtlasAPIDiscoveryResult:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    provider_name = str(info.get("name") or "Postman collection").strip() or None
    items = payload.get("item") if isinstance(payload.get("item"), list) else []
    candidate_endpoints: list[dict[str, Any]] = []
    candidate_tool_refs: list[str] = []
    collection_auth_fields = _auth_fields_from_postman_auth(payload.get("auth"))
    missing_auth_fields: set[str] = set(collection_auth_fields)

    def _walk(collection_items: list[Any]) -> None:
        for item in collection_items:
            if not isinstance(item, dict):
                continue
            nested = item.get("item")
            if isinstance(nested, list):
                _walk(nested)
                continue
            request_payload = item.get("request")
            if not isinstance(request_payload, dict):
                continue
            method = str(request_payload.get("method") or "GET").upper()
            url_payload = request_payload.get("url")
            raw_url = (
                url_payload.get("raw")
                if isinstance(url_payload, dict)
                else str(url_payload or "")
            )
            name = str(item.get("name") or "").strip()
            tool_ref = _slug(name or f"{method}_{raw_url}")
            request_auth_fields = _auth_fields_from_postman_auth(request_payload.get("auth"))
            if request_auth_fields:
                missing_auth_fields.update(request_auth_fields)
            candidate_tool_refs.append(tool_ref)
            candidate_endpoints.append(
                {
                    "method": method,
                    "path": raw_url or None,
                    "operation_id": name or None,
                    "summary": name or None,
                    "requires_auth": bool(request_auth_fields or collection_auth_fields),
                }
            )

    _walk(items)
    sorted_missing_auth_fields = sorted(missing_auth_fields)
    provider_slug = _infer_provider_slug(
        provider_name=provider_name,
        base_url=None,
        candidate_tool_refs=candidate_tool_refs,
    )
    resolved_provider_slug, setup_url, documentation_url, suggested_setup_action = _provider_setup_guidance(
        provider_slug=provider_slug,
        provider_name=provider_name,
        missing_auth_fields=sorted_missing_auth_fields,
        source_value=request.source_value,
    )
    candidate_provider_slug = resolved_provider_slug or _slug(provider_name or "postman")
    return AtlasAPIDiscoveryResult(
        request_id=request.request_id,
        status="discovered" if candidate_endpoints else "failed",
        provider_name=provider_name,
        candidate_tool_refs=candidate_tool_refs[:50],
        missing_auth_fields=sorted_missing_auth_fields,
        notes=(
            f"Discovered {len(candidate_endpoints)} candidate request(s) from the provided Postman collection."
            if candidate_endpoints
            else "No requests were discovered in the provided Postman collection."
        ),
        spec_type="postman",
        base_url=None,
        candidate_endpoints=candidate_endpoints[:50],
        provisioning_candidates=[
            AtlasProvisioningCandidate(
                binding_key=f"{candidate_provider_slug}:{tool_ref}",
                display_name=tool_ref.replace("_", " ").strip() or tool_ref,
                tool_ref=tool_ref,
                requires_credentials=bool(sorted_missing_auth_fields),
                missing_fields=sorted_missing_auth_fields,
                suggested_setup_action=suggested_setup_action,
                provider_slug=resolved_provider_slug,
                setup_url=setup_url,
                documentation_url=documentation_url,
            )
            for tool_ref in candidate_tool_refs[:25]
        ],
        requires_review_before_provisioning=True,
    )


def discovery_result_with_payload(
    request: AtlasAPIDiscoveryRequest,
    *,
    docs_parser: AtlasDocsPageParser | None = None,
) -> tuple[AtlasAPIDiscoveryResult, dict[str, Any] | None]:
    """Resolve a discovery request and return both the result and the
    structured spec payload it was parsed from (``None`` for HTML/docs
    pages or unparseable sources).

    The payload is returned so callers that later ingest the spec can reuse
    the EXACT bytes the result was derived from — fetching a second time
    opens a TOCTOU window where an attacker-controlled server serves a benign
    spec to discovery (which the human reviews) and a different spec to
    ingestion. There is exactly one network fetch per request.

    ``docs_parser`` is optional. When provided AND configured (has an
    api_key), HTML documentation pages are extracted via the LLM and
    labeled ``spec_type="llm_parsed"``. Otherwise the regex heuristic
    runs and the result is labeled ``spec_type="heuristic"``.

    OpenAPI / Swagger / Postman parsing is purely structural and never
    invokes the LLM regardless of whether ``docs_parser`` is supplied.
    """
    def _failed(notes: str) -> tuple[AtlasAPIDiscoveryResult, None]:
        return (
            AtlasAPIDiscoveryResult(
                request_id=request.request_id,
                status="failed",
                notes=notes,
                spec_type="unknown",
                requires_review_before_provisioning=True,
            ),
            None,
        )

    if request.source_type in {"openapi_url", "swagger_url", "postman_url", "website_url"}:
        try:
            body, content_type = _fetch_remote_text(request.source_value)
        except Exception as exc:
            return _failed(f"Atlas could not fetch the remote API discovery source: {exc}")
        if body is None:
            return _failed("Atlas fetched the remote discovery source but received an empty body.")
        if request.source_type == "website_url" or _looks_like_html(content_type, body):
            return _docs_page_result(request, body, docs_parser=docs_parser), None
        payload = _load_structured_payload(body)
        if payload is None:
            return _failed("Atlas fetched the remote discovery source but could not parse it as JSON or YAML.")
        if request.source_type == "postman_url":
            return _postman_result(request, payload), payload
        if payload.get("openapi") or payload.get("swagger"):
            return _openapi_result(request, payload), payload
        if isinstance(payload.get("item"), list):
            return _postman_result(request, payload), payload
        return _failed("Atlas fetched the remote source but could not recognize it as OpenAPI, Swagger, or Postman.")
    payload = _load_structured_payload(request.source_value)
    if payload is None:
        return _failed("Atlas could not parse the provided API discovery payload as JSON or YAML.")
    if request.source_type in {"uploaded_postman", "pasted_postman"}:
        return _postman_result(request, payload), payload
    if payload.get("openapi") or payload.get("swagger"):
        return _openapi_result(request, payload), payload
    if request.source_type in {"uploaded_spec", "pasted_schema"}:
        # Fall back to Postman if the shape looks like a collection.
        if isinstance(payload.get("item"), list):
            return _postman_result(request, payload), payload
        return _failed("Atlas could not recognize the pasted or uploaded spec as OpenAPI, Swagger, or Postman.")
    return (
        AtlasAPIDiscoveryResult(
            request_id=request.request_id,
            status="unsupported",
            notes="This API discovery source type is not implemented in the current Atlas phase.",
            spec_type="unknown",
            requires_review_before_provisioning=True,
        ),
        None,
    )


def discovery_result_for_request(
    request: AtlasAPIDiscoveryRequest,
    *,
    docs_parser: AtlasDocsPageParser | None = None,
) -> AtlasAPIDiscoveryResult:
    """Resolve an API discovery request into a structured result.

    Thin wrapper over :func:`discovery_result_with_payload` for callers that
    do not need the parsed spec payload.
    """
    return discovery_result_with_payload(request, docs_parser=docs_parser)[0]


def build_provisioning_manifest(
    dependencies: list[AtlasDependency],
    *,
    definition_by_ref: dict[str, Any] | None = None,
    bindings_by_tool_definition_id: dict[str, Any] | None = None,
    connections_by_id: dict[str, Any] | None = None,
) -> list[AtlasProvisioningManifestItem]:
    definition_by_ref = definition_by_ref or {}
    bindings_by_tool_definition_id = bindings_by_tool_definition_id or {}
    connections_by_id = connections_by_id or {}
    manifest: list[AtlasProvisioningManifestItem] = []
    for item in dependencies:
        if item.kind != "tool":
            continue
        if item.status in {"connected", "available", "configured"} and not item.blocking:
            continue
        tool_ref = item.reference_ids[0] if item.reference_ids else None
        definition = definition_by_ref.get(tool_ref or "")
        binding = (
            bindings_by_tool_definition_id.get(definition.tool_definition_id)
            if definition is not None
            else None
        )
        connection_id = (
            binding.connection_id
            if binding is not None
            else getattr(definition, "connection_id", None)
        )
        connection = connections_by_id.get(connection_id) if connection_id else None
        provider_slug = (
            getattr(connection, "provider", None)
            or (
                str(getattr(definition, "metadata_json", {}).get("template_slug"))
                if isinstance(getattr(definition, "metadata_json", None), dict)
                and getattr(definition, "metadata_json", {}).get("template_slug")
                else None
            )
            or getattr(definition, "kind", None)
            or "tooling"
        )
        provider_template = PROVIDER_TEMPLATES.get(provider_slug)
        provider_label = provider_template.display_name if provider_template is not None else str(provider_slug)
        connection_status = item.status
        requires_credentials = item.status in {"requires_auth", "missing"}
        missing_fields: list[str]
        if definition is None:
            missing_fields = ["tool_definition"]
        elif connection is None:
            missing_fields = ["connection_binding"]
        elif item.status == "requires_auth":
            missing_fields = ["oauth_authorization"] if getattr(connection, "auth_type", None) == "oauth2" else ["credentials"]
        elif item.status == "invalid":
            missing_fields = ["connection_health"]
        else:
            missing_fields = []
        if item.status == "requires_auth" and connection_id:
            setup_action = f"Reconnect or authorize the {provider_label} connection for '{tool_ref or item.display_name}'."
        elif item.status == "missing" and definition is None:
            setup_action = f"Create or import a tool definition for '{tool_ref or item.display_name}'."
        elif item.status == "missing" and connection_id is None:
            setup_action = f"Create or bind an API connection for '{tool_ref or item.display_name}'."
        elif item.status == "invalid":
            setup_action = f"Repair or replace the {provider_label} connection for '{tool_ref or item.display_name}'."
        else:
            setup_action = item.suggested_action
        documentation_url = (
            f"/settings/integrations?connection_id={connection_id}"
            if connection_id
            else f"/settings/integrations?tool_ref={tool_ref}"
            if tool_ref
            else None
        )
        note_parts = [item.reason] if item.reason else []
        if connection is not None and getattr(connection, "display_name", None):
            note_parts.append(f"Connection: {connection.display_name}")
        if connection is not None and getattr(connection, "base_url", None):
            note_parts.append(f"Base URL: {connection.base_url}")
        manifest.append(
            AtlasProvisioningManifestItem(
                agent_id="",
                provider=str(provider_slug),
                tool_ref=tool_ref,
                binding_target=item.key,
                connection_id=connection_id,
                requires_credentials=requires_credentials,
                connection_status=connection_status,
                missing_fields=missing_fields,
                setup_action=setup_action,
                documentation_url=documentation_url,
                blocking=item.blocking,
                notes=" ".join(part for part in note_parts if part),
            )
        )
    return manifest
