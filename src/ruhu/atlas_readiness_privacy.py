from __future__ import annotations

from typing import Any

from .tools.pii_redactor import PiiRedactor

# Matched as whole segments / suffixes, NOT substrings: a substring rule on
# "auth"/"session" over-redacts identifiers like "author" and
# "atlas_session_id", destroying the audit linkage those keys carry.
_SECRET_SEGMENTS = {
    "auth",
    "authorization",
    "bearer",
    "credential",
    "credentials",
    "password",
    "passwd",
    "secret",
}
_SECRET_SUFFIXES = (
    "_token",
    "_secret",
    "_password",
    "_passwd",
    "_credential",
    "_credentials",
    "_api_key",
    "_apikey",
    "_authorization",
)
_SECRET_KEY_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "bearer_token",
    "client_secret",
    "credential",
    "credentials",
    "csrf_token",
    "id_token",
    "password",
    "refresh_token",
    "secret",
    "session_token",
    "token",
}
_REDACTED = "[REDACTED]"


class AtlasReadinessPrivacyScrubber:
    """Redact PII and secrets before readiness payloads enter durable storage."""

    def __init__(self, *, pii_redactor: PiiRedactor | None = None) -> None:
        self._pii_redactor = pii_redactor or PiiRedactor()

    def scrub(self, value: Any) -> Any:
        if isinstance(value, dict):
            return self.scrub_dict(value)
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, str):
            return self._pii_redactor.redact_string(value)
        return value

    def scrub_dict(self, payload: dict[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            if self._is_secret_key(key_text):
                redacted[key_text] = _REDACTED
            else:
                redacted[key_text] = self.scrub(value)
        return redacted

    @staticmethod
    def _is_secret_key(key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        # Identifiers (…_id) are not secrets — preserve audit linkage such as
        # atlas_session_id, agent_id, connection_id.
        if normalized.endswith("_id"):
            return False
        if normalized in _SECRET_KEY_NAMES:
            return True
        if normalized.endswith(_SECRET_SUFFIXES):
            return True
        segments = set(normalized.split("_"))
        if segments & _SECRET_SEGMENTS:
            return True
        # Split forms of "api key" (x_api_key, api_key_value, …).
        if "api" in segments and "key" in segments:
            return True
        return False
