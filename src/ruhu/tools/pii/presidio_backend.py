"""Thin lazy-import wrapper around presidio-analyzer + presidio-anonymizer.

Presidio is an optional dependency. is_available() probes for its presence
without side effects. The analyzer is initialized on first use and falls back
gracefully if the spaCy NLP model is missing.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PresidioBackend:
    """Lazy-initialized Presidio analyzer + anonymizer for entity detection and redaction."""

    def __init__(self, *, entities: list[str], language: str = "en", spacy_model: str = "en_core_web_lg") -> None:
        """Initialize Presidio backend (lazy).

        Args:
            entities: List of entity types to recognize (e.g., ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER"])
            language: Language for NLP (default "en")
            spacy_model: spaCy model name to load (default "en_core_web_lg")
        """
        self._entities = entities
        self._language = language
        self._spacy_model = spacy_model
        self._analyzer: Any = None
        self._anonymizer: Any = None
        self._initialized = False
        self._init_error: Exception | None = None

    @classmethod
    def is_available(cls) -> bool:
        """Check if presidio-analyzer and presidio-anonymizer are installed.

        No side effects — only probes imports.
        """
        try:
            import presidio_analyzer  # noqa: F401
            import presidio_anonymizer  # noqa: F401

            return True
        except ImportError:
            return False

    def redact_text(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        """Scan and redact PII entities in text.

        Chunks text at ≤10,000 chars to respect spaCy NLP limits and recombines results.

        Args:
            text: Input text to redact

        Returns:
            Tuple of (redacted_text, findings_list).
            findings_list contains dicts with keys: entity_type, score (confidence)

        Raises:
            RuntimeError if the analyzer fails to initialize.
        """
        self._init()
        if self._init_error is not None:
            raise RuntimeError(f"Presidio analyzer initialization failed: {self._init_error}") from self._init_error

        chunk_size = 10000
        chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
        redacted_chunks = []
        all_findings: list[dict[str, Any]] = []

        for chunk in chunks:
            try:
                results = self._analyzer.analyze(text=chunk, language=self._language, entities=self._entities)
                redacted = self._anonymizer.anonymize(text=chunk, analyzer_results=results)
                redacted_chunks.append(redacted.text)
                for result in results:
                    all_findings.append({
                        "entity_type": result.entity_type,
                        "score": result.score,
                        "start": result.start,
                        "end": result.end,
                    })
            except Exception as e:
                logger.error(f"Presidio redaction failed for chunk: {e}", exc_info=True)
                raise

        return "".join(redacted_chunks), all_findings

    def _init(self) -> None:
        """Lazy initialization of Presidio analyzer and anonymizer.

        Falls back: en_core_web_lg → en_core_web_sm → raises RuntimeError.
        """
        if self._initialized or self._init_error is not None:
            return

        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine

            try:
                import spacy

                try:
                    spacy.load(self._spacy_model)
                except OSError:
                    logger.warning(f"spaCy model {self._spacy_model} not found, trying en_core_web_sm")
                    spacy.load("en_core_web_sm")
            except Exception as e:
                logger.error(f"Failed to load spaCy model: {e}")
                raise RuntimeError(
                    f"No spaCy model available. Install with: python -m spacy download en_core_web_sm"
                ) from e

            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()
            self._initialized = True
        except Exception as e:
            self._init_error = e
            logger.error(f"Presidio initialization failed: {e}", exc_info=True)
