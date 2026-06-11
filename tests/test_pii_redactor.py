from __future__ import annotations

from ruhu.tools.dlp_service import DLPScanResult
from ruhu.tools.pii_redactor import PiiRedactionResult, PiiRedactor


class FakeDLPService:
    def scan_text(self, text: str, *, info_types=None, min_likelihood="POSSIBLE") -> DLPScanResult:
        if "alice@example.com" in text:
            return DLPScanResult(
                has_findings=True,
                finding_count=1,
                findings_by_type={"EMAIL_ADDRESS": 1},
                has_high_likelihood=True,
                top_types=["EMAIL_ADDRESS"],
            )
        return DLPScanResult.empty()

    def redact_text(self, text: str, *, info_types=None, min_likelihood="POSSIBLE") -> str:
        return text.replace("alice@example.com", "[EMAIL_ADDRESS]")


def test_regex_redactor_redacts_known_patterns() -> None:
    result = PiiRedactor().process_dict(
        {"email": "alice@example.com", "card": "4111 1111 1111 1111"},
        policy={"provider": "regex", "mode": "redact"},
    )

    assert result.output["email"] == "[REDACTED]"
    assert result.output["card"] == "[REDACTED]"
    assert result.blocked is False


def test_google_dlp_redactor_blocks_high_likelihood_payloads() -> None:
    redactor = PiiRedactor(dlp=FakeDLPService())

    result = redactor.process_dict(
        {"email": "alice@example.com"},
        policy={"provider": "google_dlp", "mode": "block"},
    )

    assert result.blocked is True
    assert result.error == "pii_redaction_blocked"
    assert result.metadata["pii_redaction"]["has_high_likelihood"] is True


def test_google_dlp_redactor_redacts_and_records_metadata() -> None:
    redactor = PiiRedactor(dlp=FakeDLPService())

    result = redactor.process_dict(
        {"email": "alice@example.com"},
        policy={"provider": "google_dlp", "mode": "redact"},
    )

    assert result.output["email"] == "[EMAIL_ADDRESS]"
    assert result.blocked is False
    assert result.metadata["pii_redaction"]["provider"] == "google_dlp"
