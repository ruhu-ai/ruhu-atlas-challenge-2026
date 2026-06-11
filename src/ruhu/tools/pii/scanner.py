"""Tiered PII scanning and redaction pipeline.

Tier execution order:
1. Presidio (local NLP, sub-millisecond, no cloud egress)
2. Google DLP (cloud, compliance audit trail — scans Presidio-redacted text)
3. Regex (existing patterns, always runs as backstop)

All tiers fail-open: if all fail, original text returned with metrics alert.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from ruhu.observability import metrics
from ruhu.tools.dlp_service import DLPService
from ruhu.tools.pii_redactor import PiiRedactor

from .presidio_backend import PresidioBackend

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PiiScannerConfig:
    """Configuration for tiered PII scanner."""

    presidio_enabled: bool
    presidio_entities: list[str]
    presidio_language: str
    presidio_spacy_model: str
    dlp_enabled: bool
    dlp_project_id: str | None
    dlp_info_types: list[str]
    dlp_min_likelihood: str
    dlp_always_run: bool  # If False, run DLP only when Presidio finds something
    regex_fallback_enabled: bool
    audit_findings: bool  # Emit to audit_events when findings found
    scan_timeout_seconds: float


@dataclass
class PiiScanResult:
    """Result of a PII scan."""

    redacted_text: str | None
    redacted_dict: dict[str, Any] | None
    has_findings: bool
    finding_count: int
    findings_by_type: dict[str, int]
    tiers_executed: list[str]
    tiers_failed: list[str]
    scan_latency_ms: int


class TieredPiiScanner:
    """Unified PII scanner with Presidio + DLP + regex tiers."""

    def __init__(
        self,
        config: PiiScannerConfig,
        *,
        dlp_service: DLPService | None = None,
        presidio_backend: PresidioBackend | None = None,
        audit_router: Any | None = None,
    ) -> None:
        """Initialize the tiered PII scanner.

        Args:
            config: Scanner configuration
            dlp_service: Optional DLPService instance (lazy-initialized if needed)
            presidio_backend: Optional PresidioBackend (created if needed and enabled)
            audit_router: Optional AuditEventRouter for emitting findings
        """
        self._config = config
        self._dlp_service = dlp_service
        self._presidio_backend = presidio_backend
        self._audit_router = audit_router
        self._regex_redactor = PiiRedactor()

    def scan_and_redact_text(
        self,
        text: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> PiiScanResult:
        """Scan and redact PII in plain text.

        Args:
            text: Input text to scan
            context: Optional context dict (e.g., {"field_context": "tool_args", "organization_id": "org_123"})

        Returns:
            PiiScanResult with redacted text and findings
        """
        context = context or {}
        start_time = time.perf_counter()
        tiers_executed: list[str] = []
        tiers_failed: list[str] = []
        current_text = text
        all_findings_by_type: dict[str, int] = {}

        # Tier 1: Presidio
        if self._config.presidio_enabled:
            try:
                if PresidioBackend.is_available():
                    if self._presidio_backend is None:
                        self._presidio_backend = PresidioBackend(
                            entities=self._config.presidio_entities,
                            language=self._config.presidio_language,
                            spacy_model=self._config.presidio_spacy_model,
                        )
                    redacted_text, findings = self._presidio_backend.redact_text(text)
                    current_text = redacted_text
                    tiers_executed.append("presidio")
                    for finding in findings:
                        entity_type = finding.get("entity_type", "UNKNOWN")
                        all_findings_by_type[entity_type] = all_findings_by_type.get(entity_type, 0) + 1
                    metrics.pii_findings_total.labels(entity_type="presidio_multi", tier="presidio").inc(
                        len(findings)
                    )
            except Exception as e:
                logger.error(f"Presidio tier failed: {e}", exc_info=True)
                tiers_failed.append("presidio")
                metrics.pii_tier_failures_total.labels(tier="presidio").inc()

        # Tier 2: Google DLP
        if self._config.dlp_enabled:
            should_run_dlp = self._config.dlp_always_run or all_findings_by_type
            if should_run_dlp:
                try:
                    if self._dlp_service is None:
                        self._dlp_service = DLPService(
                            enabled=True,
                            project_id=self._config.dlp_project_id,
                            info_types=self._config.dlp_info_types,
                        )
                    redacted_text = self._dlp_service.redact_text(
                        current_text,
                        min_likelihood=self._config.dlp_min_likelihood,
                    )
                    current_text = redacted_text
                    tiers_executed.append("dlp")
                    # TODO: DLP findings parsed from response and added to all_findings_by_type
                    # For now, we just note that DLP ran; detailed findings extraction can follow
                except Exception as e:
                    logger.error(f"DLP tier failed: {e}", exc_info=True)
                    tiers_failed.append("dlp")
                    metrics.pii_tier_failures_total.labels(tier="dlp").inc()

        # Tier 3: Regex fallback (always available)
        if self._config.regex_fallback_enabled:
            try:
                # Apply PiiRedactor's patterns as final backstop
                redacted_text = current_text
                for pattern, replacement in self._regex_redactor._patterns:
                    redacted_text = pattern.sub(replacement, redacted_text)
                if redacted_text != current_text:
                    # Track that regex found something
                    all_findings_by_type["regex"] = all_findings_by_type.get("regex", 0) + 1
                    metrics.pii_findings_total.labels(entity_type="regex", tier="regex").inc()
                current_text = redacted_text
                tiers_executed.append("regex")
            except Exception as e:
                logger.error(f"Regex tier failed: {e}", exc_info=True)
                tiers_failed.append("regex")
                metrics.pii_tier_failures_total.labels(tier="regex").inc()

        # Fail-open: if all tiers failed, return original text with alert
        if len(tiers_failed) == 3 or not tiers_executed:
            logger.critical(
                f"All PII scanner tiers failed for field {context.get('field_context', 'unknown')}. "
                f"Data will pass through unredacted."
            )
            metrics.pii_all_tiers_failed_total.inc()
            current_text = text  # Return original
            has_findings = False
        else:
            has_findings = bool(all_findings_by_type)

        latency_ms = int((time.perf_counter() - start_time) * 1000)
        metrics.pii_scans_total.labels(
            field_context=context.get("field_context", "unknown"),
            has_findings=str(has_findings),
        ).inc()
        metrics.pii_scan_duration_seconds.labels(field_context=context.get("field_context", "unknown")).observe(
            latency_ms / 1000.0
        )

        return PiiScanResult(
            redacted_text=current_text,
            redacted_dict=None,
            has_findings=has_findings,
            finding_count=sum(all_findings_by_type.values()),
            findings_by_type=all_findings_by_type,
            tiers_executed=tiers_executed,
            tiers_failed=tiers_failed,
            scan_latency_ms=latency_ms,
        )

    def scan_and_redact_dict(
        self,
        data: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> PiiScanResult:
        """Scan and redact PII in a dictionary recursively.

        Walks the dict up to depth 10, calling scan_and_redact_text on string leaves.

        Args:
            data: Input dict to scan
            context: Optional context dict

        Returns:
            PiiScanResult with redacted dict and findings
        """
        context = context or {}
        start_time = time.perf_counter()

        redacted = self._redact_dict_recursive(data, depth=0, max_depth=10)

        # For now, do a single scan on the entire dict's string representation to get findings
        # (A more sophisticated implementation would track findings per field)
        text_repr = str(redacted)
        text_scan = self.scan_and_redact_text(text_repr, context=context)

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        return PiiScanResult(
            redacted_text=None,
            redacted_dict=redacted,
            has_findings=text_scan.has_findings,
            finding_count=text_scan.finding_count,
            findings_by_type=text_scan.findings_by_type,
            tiers_executed=text_scan.tiers_executed,
            tiers_failed=text_scan.tiers_failed,
            scan_latency_ms=latency_ms,
        )

    def _redact_dict_recursive(self, obj: Any, *, depth: int, max_depth: int) -> Any:
        """Recursively redact a dict/list structure.

        Args:
            obj: Object to process (dict, list, str, or scalar)
            depth: Current recursion depth
            max_depth: Maximum depth before stopping

        Returns:
            Redacted version of obj
        """
        if depth > max_depth:
            return obj

        if isinstance(obj, dict):
            return {k: self._redact_dict_recursive(v, depth=depth + 1, max_depth=max_depth) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._redact_dict_recursive(item, depth=depth + 1, max_depth=max_depth) for item in obj]
        elif isinstance(obj, str):
            # Scan the string field
            scan = self.scan_and_redact_text(obj, context={"field_context": "dict_value"})
            return scan.redacted_text or obj
        else:
            # Scalars pass through unchanged
            return obj

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        audit_router: Any | None = None,
    ) -> TieredPiiScanner:
        """Create a TieredPiiScanner from RuntimeSettings.

        Args:
            settings: RuntimeSettings instance
            audit_router: Optional AuditEventRouter

        Returns:
            Configured TieredPiiScanner
        """
        config = PiiScannerConfig(
            presidio_enabled=settings.pii_presidio_enabled,
            presidio_entities=list(settings.pii_presidio_entities),
            presidio_language=settings.pii_presidio_language,
            presidio_spacy_model=settings.pii_presidio_spacy_model,
            dlp_enabled=settings.pii_dlp_enabled,
            dlp_project_id=settings.pii_dlp_project_id,
            dlp_info_types=list(settings.pii_dlp_info_types),
            dlp_min_likelihood=settings.pii_dlp_min_likelihood,
            dlp_always_run=settings.pii_dlp_always_run,
            regex_fallback_enabled=settings.pii_regex_fallback_enabled,
            audit_findings=settings.pii_audit_findings,
            scan_timeout_seconds=settings.pii_scan_timeout_seconds,
        )
        return cls(config, audit_router=audit_router)
