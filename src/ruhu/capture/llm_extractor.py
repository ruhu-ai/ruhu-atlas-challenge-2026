from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ruhu.agent_document import Step
from ruhu.capture.deterministic import _locate
from ruhu.capture.types import FactCandidate
from ruhu.schemas import FactDef

logger = logging.getLogger(__name__)

_CONVERSATION_EXTRACTION_PROMPT = """\
Extract only the requested fields that the user explicitly stated or strongly implied.
Return a JSON object with every requested field as a key. Use null when absent or uncertain.
Do not invent values. Do not extract OTPs, PINs, passwords, card numbers, or secrets.
Only return JSON.

Fields:
{fields_spec}

Conversation text:
\"\"\"
{text}
\"\"\"
"""


class FieldExtractorLLM(Protocol):
    def extract(self, *, text: str, fields: list[str], hints: dict[str, str]) -> dict[str, str | None]: ...


@dataclass(slots=True)
class ConversationGeminiExtractor:
    api_key: str | None = None
    model: str = "gemini-3-flash-preview"
    timeout_seconds: float = 8.0
    endpoint_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    def extract(self, *, text: str, fields: list[str], hints: dict[str, str]) -> dict[str, str | None]:
        if not self.api_key or not fields:
            return {field: None for field in fields}
        fields_spec = "\n".join(
            f"- {field}: {hints[field]}" if hints.get(field) else f"- {field}"
            for field in fields
        )
        prompt = _CONVERSATION_EXTRACTION_PROMPT.format(
            fields_spec=fields_spec,
            text=text[:8000],
        )
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 512,
                "responseMimeType": "application/json",
            },
        }
        url = f"{self.endpoint_base_url}/models/{self.model}:generateContent?key={self.api_key}"
        try:
            import httpx

            started = time.monotonic()
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
            logger.debug(
                "conversation field extractor completed in %.2fs for fields=%s",
                time.monotonic() - started,
                fields,
            )
        except Exception as exc:
            logger.warning("conversation field extractor failed for fields=%s: %s", fields, exc)
            return {field: None for field in fields}
        raw_text = _extract_gemini_text(response.json())
        if not raw_text:
            return {field: None for field in fields}
        try:
            extracted: dict[str, Any] = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning("conversation field extractor returned invalid JSON: %s", raw_text[:200])
            return {field: None for field in fields}
        return {
            field: str(extracted.get(field)).strip()
            if extracted.get(field) is not None and str(extracted.get(field)).strip()
            else None
            for field in fields
        }


def _extract_gemini_text(data: dict[str, Any]) -> str | None:
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None


class LLMFactExtractor:
    def __init__(self, backend: FieldExtractorLLM | None) -> None:
        self._backend = backend

    def extract(
        self,
        *,
        text: str,
        fact_defs: list[FactDef],
        gap_facts: list[str],
        existing_facts: dict[str, object],
        step: Step,
        transcript_context: str | None = None,
    ) -> list[FactCandidate]:
        if os.getenv("RUHU_LLM_FACT_EXTRACTION", "").strip().lower() in {"0", "false", "no", "off"}:
            self._observe_llm_call("disabled")
            return []
        if self._backend is None or not gap_facts:
            return []
        fact_def_by_name = {fact_def.name: fact_def for fact_def in fact_defs}
        fields = [
            fact_name
            for fact_name in gap_facts
            if "llm_proposed" in fact_def_by_name.get(fact_name, FactDef(name=fact_name, type="string")).allowed_sources
        ]
        if not fields:
            return []
        hints: dict[str, str] = {}
        for fact_name in fields:
            fact_def = fact_def_by_name.get(fact_name)
            hint_parts = [fact_name.replace("_", " ")]
            if fact_def is not None:
                hint_parts.extend(fact_def.entity_hints)
                if fact_def.capture_aliases:
                    hint_parts.append("aliases: " + ", ".join(fact_def.capture_aliases))
            hints[fact_name] = "; ".join(part for part in hint_parts if part)
        prompt_text = text
        if transcript_context:
            prompt_text = f"Recent conversation:\n{transcript_context[-4000:]}\n\nCurrent user message:\n{text}"
        try:
            extracted = self._backend.extract(text=prompt_text, fields=fields, hints=hints)
        except Exception as exc:
            logger.warning("conversation field extraction failed: %s", exc)
            self._observe_llm_call("error")
            return []
        self._observe_llm_call("success")
        candidates: list[FactCandidate] = []
        for fact_name, raw_value in extracted.items():
            if raw_value is None or fact_name in existing_facts:
                continue
            fact_def = fact_def_by_name.get(fact_name)
            confidence = fact_def.llm_confidence_default if fact_def and fact_def.llm_confidence_default is not None else 0.65
            span = _locate(text, raw_value)
            candidates.append(
                FactCandidate(
                    fact_name, raw_value, "llm_proposed", raw_value, confidence,
                    transcript_span=span,
                )
            )
        return candidates

    @staticmethod
    def _observe_llm_call(outcome: str) -> None:
        try:
            from ruhu.observability.metrics import capture_llm_calls_total

            capture_llm_calls_total.labels(outcome=outcome).inc()
        except Exception:
            pass
