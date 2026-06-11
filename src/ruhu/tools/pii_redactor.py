"""PII redaction for tool outputs.

Supports two paths:
- local regex redaction for lightweight deployments
- optional Google Cloud DLP-backed scanning/redaction for regulated workloads
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .dlp_service import DLPService, DLPScanResult, dlp_service

_REDACTED = "[REDACTED]"
_MAX_DEPTH = 10

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"\+\d{1,3}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{2,4}[\s\-.]?\d{2,4}(?:[\s\-.]?\d{1,4})?"
)
_NG_LOCAL_PHONE_RE = re.compile(r"\b0[789]\d{9}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE = re.compile(r"\b(?:\d[\s\-]?){12,18}\d\b")

_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_EMAIL_RE, _REDACTED),
    (_PHONE_RE, _REDACTED),
    (_NG_LOCAL_PHONE_RE, _REDACTED),
    (_SSN_RE, _REDACTED),
    (_CC_RE, _REDACTED),
]


@dataclass
class PiiRedactionResult:
    output: dict[str, Any]
    blocked: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class PiiRedactor:
    """Stateless PII redactor for dict payloads."""

    def __init__(
        self,
        *,
        patterns: list[tuple[re.Pattern[str], str]] | None = None,
        dlp: DLPService | None = None,
    ) -> None:
        self._patterns = patterns or _PII_PATTERNS
        self._dlp = dlp or dlp_service

    def process_dict(
        self,
        data: dict[str, Any],
        *,
        policy: dict[str, Any] | None = None,
    ) -> PiiRedactionResult:
        effective_policy = dict(policy or {})
        provider = str(effective_policy.get("provider") or "regex").strip().lower()
        mode = str(effective_policy.get("mode") or "redact").strip().lower()
        info_types = effective_policy.get("info_types")
        if not isinstance(info_types, list):
            info_types = None
        min_likelihood = str(effective_policy.get("min_likelihood") or "POSSIBLE").strip().upper()

        if provider == "google_dlp":
            return self._process_with_dlp(
                data,
                mode=mode,
                info_types=info_types,
                min_likelihood=min_likelihood,
            )

        redacted = self.redact_dict(data)
        return PiiRedactionResult(
            output=redacted,
            metadata={"pii_redaction": {"provider": "regex", "mode": mode}},
        )

    def redact_string(self, value: str) -> str:
        result = value
        for pattern, replacement in self._patterns:
            result = pattern.sub(replacement, result)
        return result

    def redact_dict(self, data: dict[str, Any], *, _depth: int = 0) -> dict[str, Any]:
        if _depth >= _MAX_DEPTH:
            return data
        result: dict[str, Any] = {}
        for key, value in data.items():
            result[key] = self._redact_value(value, _depth=_depth)
        return result

    def _redact_value(self, value: Any, *, _depth: int) -> Any:
        if isinstance(value, str):
            return self.redact_string(value)
        if isinstance(value, dict):
            return self.redact_dict(value, _depth=_depth + 1)
        if isinstance(value, list):
            return [self._redact_value(item, _depth=_depth + 1) for item in value]
        return value

    def _process_with_dlp(
        self,
        data: dict[str, Any],
        *,
        mode: str,
        info_types: list[str] | None,
        min_likelihood: str,
    ) -> PiiRedactionResult:
        aggregate_scan = DLPScanResult.empty()
        blocked = False

        def process(value: Any, *, depth: int) -> Any:
            nonlocal aggregate_scan, blocked
            if depth >= _MAX_DEPTH:
                return value
            if isinstance(value, str):
                scan = self._dlp.scan_text(
                    value,
                    info_types=info_types,
                    min_likelihood=min_likelihood,
                )
                aggregate_scan = self._merge_scans(aggregate_scan, scan)
                if mode == "block" and scan.has_high_likelihood:
                    blocked = True
                    return value
                if mode == "flag":
                    return value
                return self._dlp.redact_text(
                    value,
                    info_types=info_types,
                    min_likelihood=min_likelihood,
                )
            if isinstance(value, dict):
                return {
                    str(key): process(child, depth=depth + 1)
                    for key, child in value.items()
                }
            if isinstance(value, list):
                return [process(child, depth=depth + 1) for child in value]
            return value

        redacted = {
            str(key): process(value, depth=0)
            for key, value in data.items()
        }
        metadata = {
            "pii_redaction": {
                "provider": "google_dlp",
                "mode": mode,
                **aggregate_scan.as_metadata(),
            }
        }
        error = "pii_redaction_blocked" if blocked else None
        return PiiRedactionResult(
            output=redacted,
            blocked=blocked,
            metadata=metadata,
            error=error,
        )

    @staticmethod
    def _merge_scans(current: DLPScanResult, incoming: DLPScanResult) -> DLPScanResult:
        if not incoming.has_findings:
            return current
        merged = DLPScanResult(
            has_findings=current.has_findings or incoming.has_findings,
            finding_count=current.finding_count + incoming.finding_count,
            findings_by_type=dict(current.findings_by_type),
            has_high_likelihood=current.has_high_likelihood or incoming.has_high_likelihood,
            top_types=list(current.top_types),
            provider=incoming.provider,
        )
        for key, value in incoming.findings_by_type.items():
            merged.findings_by_type[key] = merged.findings_by_type.get(key, 0) + value
        merged.top_types = sorted(
            merged.findings_by_type,
            key=merged.findings_by_type.get,
            reverse=True,
        )[:5]
        return merged
