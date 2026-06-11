"""Structured field extraction from attachment text views.

Extracts declared facts from ``inline_text`` carried by a view-ready
``system_event`` turn.  Used by the kernel when a state declares
``attachment_capture`` fields.

Extraction strategy (two-pass, deterministic-first):

1. **Deterministic pass** — regex patterns for high-confidence fields
   (email, phone, URL).  These fields are extracted with ``confidence=1.0``
   and are NOT sent to the LLM even if an LLM is configured.

2. **LLM pass** — remaining fields (full_name, company, job_title, etc.)
   are bundled into a single JSON extraction call.  If no LLM is
   configured, or the call fails, the field is left absent.

The extractor is injected into the kernel as an optional dependency
(``ConversationKernel.field_extractor``).  If not configured, only the
deterministic pass runs.

Protocol
--------
Callers that need a custom LLM backend implement ``FieldExtractorLLM``:

    class MyExtractor:
        def extract(
            self,
            *,
            text: str,
            fields: list[str],
            hints: dict[str, str],
        ) -> dict[str, str | None]:
            # Return a dict mapping fact_name → extracted_value_or_None.
            ...

The ``GeminiFieldExtractor`` class is the default implementation used in
production when ``GOOGLE_API_KEY`` / Gemini credentials are available.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# ── Deterministic patterns ─────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-\(\)]{6,}\d")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")

# Fields that the deterministic pass handles; LLM is skipped for these even
# when an LLM extractor is configured.
_DETERMINISTIC_FIELDS: frozenset[str] = frozenset({"email", "phone", "url", "website"})


def _deterministic_extract(text: str, fact: str) -> str | None:
    """Return a deterministically extracted value for known field types."""
    lf = fact.lower()
    if lf in {"email", "email_address"}:
        m = _EMAIL_RE.search(text)
        return m.group(0).lower() if m else None
    if lf in {"phone", "phone_number", "mobile", "telephone"}:
        m = _PHONE_RE.search(text)
        if m:
            return re.sub(r"[\s\-\(\)]", "", m.group(0))
        return None
    if lf in {"url", "website", "link"}:
        m = _URL_RE.search(text)
        return m.group(0) if m else None
    return None  # Not a deterministic field


# ── LLM extractor protocol ────────────────────────────────────────────────────


class FieldExtractorLLM(Protocol):
    """Sync LLM interface for structured field extraction.

    Implementations should accept ``fields`` (list of fact names) and
    ``hints`` (mapping of fact_name → extraction hint) and return a dict
    mapping each fact_name to its extracted string value or ``None`` if
    not found.
    """

    def extract(
        self,
        *,
        text: str,
        fields: list[str],
        hints: dict[str, str],
    ) -> dict[str, str | None]: ...


# ── Gemini implementation ─────────────────────────────────────────────────────

_EXTRACTION_PROMPT_TEMPLATE = """\
Extract the following fields from the document text below.
Return a JSON object with each field name as the key and the extracted value as the string value.
If a field cannot be found, use null.
Only return the JSON object — no explanation, no markdown.

Fields to extract:
{fields_spec}

Document text:
\"\"\"
{text}
\"\"\"
"""


@dataclass(slots=True)
class GeminiFieldExtractor:
    """LLM-based structured extraction using the Gemini generateContent API.

    Parameters
    ----------
    api_key:
        Gemini API key.  If None, extraction is skipped (returns all None).
    model:
        Gemini model to use.  Defaults to ``gemini-3-flash-preview`` which
        has good instruction following for JSON extraction tasks.
    timeout_seconds:
        HTTP request timeout.
    endpoint_base_url:
        Gemini API base URL.
    """

    api_key: str | None = None
    model: str = "gemini-3-flash-preview"
    timeout_seconds: float = 8.0
    endpoint_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    def extract(
        self,
        *,
        text: str,
        fields: list[str],
        hints: dict[str, str],
    ) -> dict[str, str | None]:
        if not self.api_key or not fields:
            return {f: None for f in fields}

        fields_spec = "\n".join(
            f"- {f}: {hints[f]}" if f in hints else f"- {f}"
            for f in fields
        )
        prompt = _EXTRACTION_PROMPT_TEMPLATE.format(
            fields_spec=fields_spec,
            text=text[:8000],  # cap context to avoid token overflow
        )
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 512,
                "responseMimeType": "application/json",
            },
        }
        url = (
            f"{self.endpoint_base_url}/models/{self.model}"
            f":generateContent?key={self.api_key}"
        )
        try:
            import httpx
            _start = time.monotonic()
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
            elapsed = time.monotonic() - _start
            logger.debug(
                "field extractor: gemini call completed in %.2fs for fields=%s",
                elapsed,
                fields,
            )
        except Exception as exc:
            logger.warning(
                "field extractor: gemini call failed for fields=%s: %s",
                fields,
                exc,
            )
            return {f: None for f in fields}

        raw_text = _extract_gemini_text(response.json())
        if not raw_text:
            return {f: None for f in fields}

        try:
            extracted: dict[str, Any] = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning(
                "field extractor: could not parse gemini JSON response: %s",
                raw_text[:200],
            )
            return {f: None for f in fields}

        result: dict[str, str | None] = {}
        for f in fields:
            val = extracted.get(f)
            result[f] = str(val).strip() if val is not None and str(val).strip() else None
        return result


def _extract_gemini_text(data: dict[str, Any]) -> str | None:
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None


# ── Public extraction entry point ─────────────────────────────────────────────


def extract_attachment_fields(
    text: str,
    capture_configs: list[Any],  # list[FieldCaptureConfig] — avoid circular import
    *,
    llm: "FieldExtractorLLM | None" = None,
) -> list[Any]:  # list[FactUpdate] — avoid circular import
    """Extract declared facts from attachment text.

    Returns a list of ``FactUpdate`` objects ready to be merged into
    ``working_facts`` by the kernel.

    Parameters
    ----------
    text:
        The ``inline_text`` from the attachment's text view.
    capture_configs:
        Exported step attachment-capture metadata — list of ``FieldCaptureConfig``.
    llm:
        Optional LLM extractor for non-deterministic fields.  When None,
        only regex-based extraction runs (email, phone, URL).
    """
    # Avoid circular import: import here (kernel calls us, schemas imports
    # from attachments, but this module is loaded lazily via the kernel).
    from ..schemas import FactUpdate

    if not text or not capture_configs:
        return []

    results: list[FactUpdate] = []
    llm_fields: list[str] = []
    llm_hints: dict[str, str] = {}

    # ── Pass 1: deterministic ─────────────────────────────────────────────────
    remaining_configs: list[Any] = []
    for cfg in capture_configs:
        det_value = _deterministic_extract(text, cfg.fact)
        if det_value is not None:
            results.append(
                FactUpdate(
                    name=cfg.fact,
                    value=det_value,
                    source="deterministic",
                    confidence=1.0,
                )
            )
        else:
            remaining_configs.append(cfg)

    # ── Pass 2: LLM for remaining fields ──────────────────────────────────────
    if remaining_configs and llm is not None:
        for cfg in remaining_configs:
            llm_fields.append(cfg.fact)
            if cfg.hint:
                llm_hints[cfg.fact] = cfg.hint

        try:
            llm_result = llm.extract(text=text, fields=llm_fields, hints=llm_hints)
        except Exception as exc:
            logger.warning("field extractor: LLM extraction failed: %s", exc)
            llm_result = {}

        for cfg in remaining_configs:
            value = llm_result.get(cfg.fact)
            if value:
                results.append(
                    FactUpdate(
                        name=cfg.fact,
                        value=value,
                        source="extractor",
                        confidence=0.85,
                    )
                )
            elif cfg.required:
                logger.warning(
                    "field extractor: required field '%s' not found in text (len=%d)",
                    cfg.fact,
                    len(text),
                )
    elif remaining_configs and llm is None:
        # Deterministic-only mode: skip non-deterministic fields silently
        for cfg in remaining_configs:
            if cfg.required:
                logger.debug(
                    "field extractor: required field '%s' skipped (no LLM configured)",
                    cfg.fact,
                )

    return results
