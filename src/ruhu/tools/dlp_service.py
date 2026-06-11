from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

_DEFAULT_INFO_TYPES = [
    "PERSON_NAME",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "STREET_ADDRESS",
    "CREDIT_CARD_NUMBER",
    "US_SOCIAL_SECURITY_NUMBER",
    "MEDICAL_RECORD_NUMBER",
]
_DLP_CONTENT_LIMIT_BYTES = 480_000
_HIGH_LIKELIHOODS = {"LIKELY", "VERY_LIKELY"}


@dataclass
class DLPScanResult:
    has_findings: bool = False
    finding_count: int = 0
    findings_by_type: dict[str, int] = field(default_factory=dict)
    has_high_likelihood: bool = False
    top_types: list[str] = field(default_factory=list)
    provider: str = "google_dlp"

    @classmethod
    def empty(cls) -> "DLPScanResult":
        return cls()

    def as_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "has_findings": self.has_findings,
            "finding_count": self.finding_count,
            "findings_by_type": dict(self.findings_by_type),
            "has_high_likelihood": self.has_high_likelihood,
            "top_types": list(self.top_types),
        }


class DLPService:
    def __init__(self) -> None:
        self._initialised = False
        self._enabled = False
        self._client: Any = None
        self._project_id: str | None = None
        self._default_info_types: list[dict[str, str]] = []

    def _init(self) -> bool:
        if self._initialised:
            return self._enabled
        self._initialised = True

        enabled = os.getenv("RUHU_DLP_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            return False

        project_id = (
            os.getenv("RUHU_DLP_PROJECT_ID")
            or os.getenv("GCP_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
        )
        if not project_id:
            return False

        try:
            from google.cloud import dlp_v2  # type: ignore

            self._client = dlp_v2.DlpServiceClient()
        except Exception:
            return False

        raw_types = os.getenv("RUHU_DLP_INFO_TYPES", "").strip()
        names = [name.strip() for name in raw_types.split(",") if name.strip()] if raw_types else _DEFAULT_INFO_TYPES
        self._default_info_types = [{"name": name} for name in names]
        self._project_id = project_id
        self._enabled = True
        return True

    def scan_text(
        self,
        text: str,
        *,
        info_types: list[str] | None = None,
        min_likelihood: str = "POSSIBLE",
    ) -> DLPScanResult:
        if not self._init() or not text.strip():
            return DLPScanResult.empty()
        try:
            findings: list[dict[str, Any]] = []
            for chunk in self._split_text(text):
                findings.extend(
                    self._inspect_chunk(
                        chunk,
                        info_types=info_types,
                        min_likelihood=min_likelihood,
                    )
                )
        except Exception:
            return DLPScanResult.empty()
        return self._build_result(findings)

    def redact_text(
        self,
        text: str,
        *,
        info_types: list[str] | None = None,
        min_likelihood: str = "POSSIBLE",
    ) -> str:
        if not self._init() or not text.strip():
            return text
        try:
            return "".join(
                self._deidentify_chunk(
                    chunk,
                    info_types=info_types,
                    min_likelihood=min_likelihood,
                )
                for chunk in self._split_text(text)
            )
        except Exception:
            return text

    def _split_text(self, text: str) -> list[str]:
        encoded = text.encode("utf-8")
        if len(encoded) <= _DLP_CONTENT_LIMIT_BYTES:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(encoded):
            end = start + _DLP_CONTENT_LIMIT_BYTES
            chunks.append(encoded[start:end].decode("utf-8", errors="ignore"))
            start = end
        return chunks

    def _inspect_chunk(
        self,
        text: str,
        *,
        info_types: list[str] | None,
        min_likelihood: str,
    ) -> list[dict[str, Any]]:
        from google.cloud import dlp_v2  # type: ignore

        response = self._client.inspect_content(
            request={
                "parent": f"projects/{self._project_id}/locations/global",
                "inspect_config": {
                    "info_types": self._info_types(info_types),
                    "min_likelihood": getattr(dlp_v2.Likelihood, min_likelihood, dlp_v2.Likelihood.POSSIBLE),
                    "include_quote": False,
                    "limits": {"max_findings_per_request": 1000},
                },
                "item": {"value": text},
            }
        )
        findings: list[dict[str, Any]] = []
        for item in response.result.findings:
            findings.append(
                {
                    "info_type": item.info_type.name,
                    "likelihood": item.likelihood.name,
                }
            )
        return findings

    def _deidentify_chunk(
        self,
        text: str,
        *,
        info_types: list[str] | None,
        min_likelihood: str,
    ) -> str:
        from google.cloud import dlp_v2  # type: ignore

        response = self._client.deidentify_content(
            request={
                "parent": f"projects/{self._project_id}/locations/global",
                "deidentify_config": {
                    "info_type_transformations": {
                        "transformations": [
                            {
                                "info_types": self._info_types(info_types),
                                "primitive_transformation": {
                                    "replace_with_info_type_config": {}
                                },
                            }
                        ]
                    }
                },
                "inspect_config": {
                    "info_types": self._info_types(info_types),
                    "min_likelihood": getattr(dlp_v2.Likelihood, min_likelihood, dlp_v2.Likelihood.POSSIBLE),
                },
                "item": {"value": text},
            }
        )
        return response.item.value

    def _info_types(self, info_types: list[str] | None) -> list[dict[str, str]]:
        if not info_types:
            return list(self._default_info_types)
        return [{"name": str(name).strip()} for name in info_types if str(name).strip()]

    @staticmethod
    def _build_result(findings: list[dict[str, Any]]) -> DLPScanResult:
        if not findings:
            return DLPScanResult.empty()
        findings_by_type: dict[str, int] = {}
        has_high = False
        for finding in findings:
            info_type = str(finding.get("info_type") or "UNKNOWN")
            findings_by_type[info_type] = findings_by_type.get(info_type, 0) + 1
            if str(finding.get("likelihood") or "").upper() in _HIGH_LIKELIHOODS:
                has_high = True
        top_types = sorted(findings_by_type, key=findings_by_type.get, reverse=True)[:5]
        return DLPScanResult(
            has_findings=True,
            finding_count=len(findings),
            findings_by_type=findings_by_type,
            has_high_likelihood=has_high,
            top_types=top_types,
        )


dlp_service = DLPService()
