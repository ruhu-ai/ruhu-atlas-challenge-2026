"""LLM-based extractor for HTML API documentation pages.

Used by Atlas's API discovery flow when a user submits a ``website_url``
or HTML body. Mirrors the pattern of [`AtlasProposalGenerator`](atlas_generator.py):

* Optional dataclass with ``from_env`` factory.
* Calls Anthropic via ``httpx`` with retry on transient status.
* Returns ``None`` on any failure (no api key, network error, parse error,
  malformed JSON) so callers can fall back to the existing regex
  heuristic.

The caller (``atlas_provisioning.discovery_result_for_request``) tries
the LLM first; if ``parse(...)`` returns None, falls back to the
heuristic and labels the result ``spec_type="heuristic"`` instead of
``"llm_parsed"`` — so the label always reflects what actually ran.

This module deliberately does **not** import ``atlas_provisioning`` to
avoid a circular dependency.
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .observability.metrics import (
    atlas_docs_parser_fallback_total,
    atlas_docs_parser_request_duration_seconds,
    atlas_docs_parser_requests_total,
    safe_observe,
)

logger = logging.getLogger(__name__)


# ── Output shape ────────────────────────────────────────────────────


class AtlasDocsPageEndpoint(BaseModel):
    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    requires_auth: bool = False


class AtlasDocsPageExtraction(BaseModel):
    """Structured extraction returned by the LLM parser.

    All fields are best-effort. Empty ``endpoints`` is treated by the
    caller as ``status="failed"`` with a graceful note, not an exception.
    """

    provider_name: str | None = None
    base_url: str | None = None
    endpoints: list[AtlasDocsPageEndpoint] = Field(default_factory=list)
    missing_auth_fields: list[str] = Field(default_factory=list)


# Maps a failed ``_call_anthropic`` outcome (requests-counter label) to the
# reason label recorded on ``atlas_docs_parser_fallback_total``.
_FALLBACK_REASON_BY_CALL_OUTCOME = {
    "empty": "empty_result",
    "http_4xx": "http_error",
    "http_5xx": "http_error",
    "network": "network_error",
    "error": "unexpected_error",
}

_AUTH_FIELD_VALUES = {"api_key", "bearer_token", "basic_auth", "oauth_authorization"}
_HTTP_METHOD_VALUES = {"GET", "POST", "PUT", "PATCH", "DELETE"}

_MAX_HTML_INPUT_CHARS = 60_000  # ~15k tokens worst case
_HTML_BOILERPLATE_PATTERN = re.compile(
    r"<(?:script|style|nav|footer|svg|noscript)\b[^>]*>.*?</(?:script|style|nav|footer|svg|noscript)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def _strip_html_for_llm(html_body: str) -> str:
    """Reduce an HTML page to plain text suitable for an LLM prompt.

    Drops boilerplate (script/style/nav/footer), unescapes entities,
    collapses whitespace, and truncates to ``_MAX_HTML_INPUT_CHARS``
    characters so we don't blow the model's context window.
    """
    cleaned = _HTML_BOILERPLATE_PATTERN.sub(" ", html_body)
    cleaned = _HTML_TAG_PATTERN.sub(" ", cleaned)
    cleaned = _html.unescape(cleaned)
    cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned).strip()
    if len(cleaned) > _MAX_HTML_INPUT_CHARS:
        cleaned = cleaned[:_MAX_HTML_INPUT_CHARS]
    return cleaned


def _extract_json_object(text: str) -> str | None:
    """Find the first balanced JSON object in a text blob.

    The LLM may wrap its JSON in commentary even when instructed not to;
    this finds ``{ ... }`` while accounting for nested braces. The scan
    is string-aware: braces inside JSON string values (including escaped
    quotes via backslash) do not affect depth, so e.g.
    ``{"summary": "ends with :}"}`` is returned in full. Returns None if
    no balanced object is present.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            if depth > 0:
                in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : index + 1]
    return None


def _normalize_extraction(raw: dict[str, Any]) -> AtlasDocsPageExtraction:
    """Validate + clean an LLM-returned dict into the typed shape.

    Drops endpoints with malformed methods or paths, normalizes auth
    field literals, caps list sizes to defend against pathological LLM
    output.
    """
    raw_endpoints = raw.get("endpoints") or []
    cleaned_endpoints: list[AtlasDocsPageEndpoint] = []
    for entry in raw_endpoints[:50]:
        if not isinstance(entry, dict):
            continue
        method = str(entry.get("method") or "").strip().upper()
        path = str(entry.get("path") or "").strip()
        if method not in _HTTP_METHOD_VALUES or not path.startswith("/"):
            continue
        operation_id = entry.get("operation_id")
        operation_id = (
            str(operation_id).strip() or None
            if isinstance(operation_id, (str, int, float)) and not isinstance(operation_id, bool)
            else None
        )
        summary = entry.get("summary")
        summary = str(summary).strip() if isinstance(summary, str) else None
        requires_auth = bool(entry.get("requires_auth"))
        cleaned_endpoints.append(
            AtlasDocsPageEndpoint(
                method=method,
                path=path,
                operation_id=operation_id,
                summary=summary,
                requires_auth=requires_auth,
            )
        )

    raw_auth = raw.get("missing_auth_fields") or []
    cleaned_auth: list[str] = []
    seen: set[str] = set()
    for entry in raw_auth:
        if not isinstance(entry, str):
            continue
        slug = entry.strip().lower().replace("-", "_")
        if slug in _AUTH_FIELD_VALUES and slug not in seen:
            seen.add(slug)
            cleaned_auth.append(slug)

    provider_name = raw.get("provider_name")
    provider_name = (
        str(provider_name).strip() or None if isinstance(provider_name, str) else None
    )
    base_url = raw.get("base_url")
    base_url = str(base_url).strip() or None if isinstance(base_url, str) else None

    return AtlasDocsPageExtraction(
        provider_name=provider_name,
        base_url=base_url,
        endpoints=cleaned_endpoints,
        missing_auth_fields=cleaned_auth,
    )


# ── Parser ───────────────────────────────────────────────────────────


@dataclass
class AtlasDocsPageParser:
    """LLM-driven extractor for HTML API documentation pages.

    Construction is intentionally lightweight; the heavy work happens in
    ``parse()``. Field defaults match ``AtlasProposalGenerator`` so
    operators can configure both with the same env-var conventions.
    """

    api_key: str | None = None
    model: str = "claude-sonnet-4-6"
    timeout_seconds: float = 12.0
    endpoint_base_url: str = "https://api.anthropic.com/v1/messages"
    anthropic_version: str = "2023-06-01"
    max_retries: int = 2
    retry_backoff_seconds: float = 0.25
    max_output_tokens: int = 2048
    # Test seam: when set, the httpx client routes through this transport
    # (e.g. ``httpx.MockTransport``) instead of the network.
    transport: httpx.BaseTransport | None = None

    @classmethod
    def from_env(cls) -> "AtlasDocsPageParser":
        def _env_float(name: str, default: float) -> float:
            raw = (os.getenv(name) or "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning("invalid %s=%r; using default %s", name, raw, default)
                return default

        def _env_int(name: str, default: int) -> int:
            raw = (os.getenv(name) or "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning("invalid %s=%r; using default %s", name, raw, default)
                return default

        api_key = (
            (os.getenv("RUHU_ATLAS_DOCS_PARSER_API_KEY") or "").strip()
            or (os.getenv("RUHU_ATLAS_GENERATOR_API_KEY") or "").strip()
            or (os.getenv("ANTHROPIC_API_KEY") or "").strip()
            or None
        )
        model = (
            os.getenv("RUHU_ATLAS_DOCS_PARSER_MODEL")
            or os.getenv("RUHU_ATLAS_GENERATOR_MODEL")
            or "claude-sonnet-4-6"
        ).strip()
        timeout_seconds = _env_float("RUHU_ATLAS_DOCS_PARSER_TIMEOUT_SECONDS", 12.0)
        anthropic_version = (
            os.getenv("RUHU_ATLAS_DOCS_PARSER_ANTHROPIC_VERSION")
            or os.getenv("RUHU_ATLAS_GENERATOR_ANTHROPIC_VERSION")
            or "2023-06-01"
        ).strip()
        max_retries = _env_int("RUHU_ATLAS_DOCS_PARSER_MAX_RETRIES", 2)
        retry_backoff_seconds = _env_float("RUHU_ATLAS_DOCS_PARSER_RETRY_BACKOFF_SECONDS", 0.25)
        return cls(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            anthropic_version=anthropic_version,
            max_retries=max(0, max_retries),
            retry_backoff_seconds=max(0.0, retry_backoff_seconds),
        )

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def parse(self, *, html_body: str, source_url: str | None) -> AtlasDocsPageExtraction | None:
        """Parse an HTML docs page into a structured extraction.

        Returns None on any failure: caller should fall back to the
        heuristic regex parser. Every None return increments
        ``atlas_docs_parser_fallback_total`` with a specific reason label
        (``missing_api_key``, ``empty_body``, ``empty_after_cleaning``,
        ``http_error``, ``network_error``, ``empty_result``,
        ``unexpected_error``, ``parse_error``), and each HTTP request
        increments ``atlas_docs_parser_requests_total`` exactly once with
        its final outcome — ``ok`` only when the response also parsed
        into a JSON object.
        """
        if not self.api_key:
            self._observe_fallback("missing_api_key")
            return None
        if not html_body or not html_body.strip():
            self._observe_fallback("empty_body")
            return None

        cleaned = _strip_html_for_llm(html_body)
        if not cleaned:
            self._observe_fallback("empty_after_cleaning")
            return None

        prompt = self._build_prompt(cleaned=cleaned, source_url=source_url)
        text, call_outcome, started_at = self._call_anthropic(prompt)
        if text is None:
            self._observe_request(outcome=call_outcome, started_at=started_at)
            self._observe_fallback(
                _FALLBACK_REASON_BY_CALL_OUTCOME.get(call_outcome, call_outcome)
            )
            return None

        json_block = _extract_json_object(text)
        if json_block is None:
            logger.warning(
                "atlas docs parser response had no extractable JSON object",
                extra={"model": self.model, "response_preview": text[:500]},
            )
            self._observe_parse_error(started_at)
            return None

        try:
            raw = json.loads(json_block)
        except json.JSONDecodeError:
            logger.warning(
                "atlas docs parser JSON decode failed",
                extra={"model": self.model, "json_block_preview": json_block[:500]},
            )
            self._observe_parse_error(started_at)
            return None

        if not isinstance(raw, dict):
            self._observe_parse_error(started_at)
            return None

        self._observe_request(outcome="ok", started_at=started_at)
        return _normalize_extraction(raw)

    # ── Internals ────────────────────────────────────────────────────

    def _build_prompt(self, *, cleaned: str, source_url: str | None) -> str:
        url_note = f"Source URL: {source_url}\n\n" if source_url else ""
        return f"""You will be given the visible text content of an HTTP API documentation page. \
Extract the API operations and authentication requirements into JSON.

Return ONLY a single JSON object with this exact shape (no commentary):

{{
  "provider_name": "<short provider/product name, or null>",
  "base_url": "<full base URL like https://api.example.com/v1, or null>",
  "endpoints": [
    {{
      "method": "GET|POST|PUT|PATCH|DELETE",
      "path": "/path/with/{{params}}",
      "operation_id": "<camelCase id from docs, or null>",
      "summary": "<one-sentence description, or null>",
      "requires_auth": true|false
    }}
  ],
  "missing_auth_fields": ["api_key" | "bearer_token" | "basic_auth" | "oauth_authorization"]
}}

Rules:
- Only include endpoints clearly documented as available HTTP operations.
- Do NOT invent endpoints. If unsure, omit.
- Use null for unknown fields.
- ``missing_auth_fields`` should list auth requirements visible in the docs (e.g. mention of "API key" → "api_key").
- Methods MUST be uppercase. Paths MUST start with "/".
- Cap output at 50 endpoints.

{url_note}Documentation page content follows:
---
{cleaned}
---
"""

    def _call_anthropic(self, prompt: str) -> tuple[str | None, str, float]:
        """Call the Anthropic API, returning ``(text, outcome, started_at)``.

        Records NO metrics itself: ``parse()`` is the single recording
        point, because only it knows whether a 2xx response also yielded
        a parseable JSON object (the request's final outcome). On
        failure, ``text`` is None and ``outcome`` is the failure label
        (``http_4xx``, ``http_5xx``, ``network``, ``empty``, ``error``).
        """
        started_at = time.monotonic()
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "temperature": 0.1,
            "system": (
                "You are an extraction assistant. Read API documentation HTML and "
                "return a strict JSON object describing the operations and "
                "authentication. Return only the JSON object, no commentary."
            ),
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": str(self.api_key),
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(self.timeout_seconds), transport=self.transport
                ) as client:
                    response = client.post(self.endpoint_base_url, json=payload, headers=headers)
                if response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"transient atlas docs parser status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                text = self._extract_text(response.json())
                if not text:
                    return None, "empty", started_at
                return text, "ok", started_at
            except httpx.HTTPStatusError as exc:
                last_status = exc.response.status_code if exc.response is not None else None
                if last_status is not None and last_status not in {408, 409, 425, 429, 500, 502, 503, 504}:
                    logger.warning(
                        "atlas docs parser non-retryable HTTP status",
                        extra={"status_code": last_status, "model": self.model},
                    )
                    return None, "http_4xx", started_at
                if attempt >= self.max_retries:
                    logger.warning(
                        "atlas docs parser exhausted retries on transient HTTP",
                        extra={"status_code": last_status, "model": self.model},
                    )
                    return None, "http_5xx", started_at
                time.sleep(self.retry_backoff_seconds * (attempt + 1))
            except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
                if attempt >= self.max_retries:
                    logger.warning(
                        "atlas docs parser network failure",
                        extra={"model": self.model, "error": str(exc)},
                    )
                    return None, "network", started_at
                time.sleep(self.retry_backoff_seconds * (attempt + 1))
            except Exception:
                logger.exception("atlas docs parser unexpected error", extra={"model": self.model})
                return None, "error", started_at
        return None, "error", started_at

    @staticmethod
    def _extract_text(payload: object) -> str | None:
        """Pull the first text block out of an Anthropic /v1/messages response."""
        if not isinstance(payload, dict):
            return None
        content = payload.get("content")
        if not isinstance(content, list):
            return None
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return text
        return None

    def _observe_parse_error(self, started_at: float) -> None:
        """Record the final outcome of a request whose HTTP call succeeded
        but whose response did not yield a usable JSON object."""
        self._observe_request(outcome="parse_error", started_at=started_at)
        self._observe_fallback("parse_error")

    def _observe_request(self, *, outcome: str, started_at: float | None) -> None:
        duration = max(0.0, time.monotonic() - started_at) if started_at is not None else 0.0
        safe_observe(
            "atlas_docs_parser_requests_total",
            atlas_docs_parser_requests_total.labels(
                provider="anthropic", model=self.model, outcome=outcome
            ).inc,
        )
        if started_at is not None:
            safe_observe(
                "atlas_docs_parser_request_duration_seconds",
                atlas_docs_parser_request_duration_seconds.labels(
                    provider="anthropic", model=self.model, outcome=outcome
                ).observe,
                duration,
            )

    def _observe_fallback(self, reason: str) -> None:
        safe_observe(
            "atlas_docs_parser_fallback_total",
            atlas_docs_parser_fallback_total.labels(reason=reason).inc,
        )
