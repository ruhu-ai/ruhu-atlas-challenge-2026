from __future__ import annotations

import math
import re

from ruhu.agent_document import Step
from ruhu.capture.types import FactCandidate, SafetyVerdict
from ruhu.schemas import FactDef, FactRequirement


def _luhn_passes(value: str) -> bool:
    total = 0
    reverse_digits = [int(ch) for ch in value[::-1]]
    for index, digit in enumerate(reverse_digits):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total > 0 and total % 10 == 0


def _looks_high_entropy(value: str) -> bool:
    if re.search(r"\s", value):
        return False
    if len(value) < 20:
        return False
    if not re.fullmatch(r"[A-Za-z0-9._~+/=\-]+", value):
        return False
    alphabet = len(set(value))
    return alphabet >= 12 and any(ch.isdigit() for ch in value) and any(ch.isalpha() for ch in value)


class SafetyGuard:
    def scan(
        self,
        candidate: FactCandidate,
        fact_def: FactDef | None,
        fact_requirement: FactRequirement | None,
        step: Step,
        deny_patterns: list[str] | None = None,
    ) -> SafetyVerdict:
        raw = "" if candidate.raw_value is None else str(candidate.raw_value).strip()
        digits = re.sub(r"\D", "", raw)
        context = " ".join(
            str(part or "")
            for part in (
                getattr(fact_def, "name", None),
                candidate.fact_name,
                getattr(fact_requirement, "purpose", None),
                getattr(step, "description", None),
                getattr(step, "name", None),
            )
        ).lower()
        storage_policy = getattr(fact_def, "storage_policy", None)

        credential_context = re.search(r"verify|code|otp|pin|token|password|credential|secret|api[_-]?key|session|auth", context)
        if credential_context and re.fullmatch(r"\d{4,6}", digits or raw):
            return SafetyVerdict("reject", "otp_shape_in_credential_context", True)
        if len(digits) == 11 and "bvn" not in context and not re.search(r"phone|mobile|telephone|msisdn", context):
            return SafetyVerdict("reject", "bvn_shape_in_non_bvn_slot", True)
        if 13 <= len(digits) <= 19 and _luhn_passes(digits):
            return SafetyVerdict("reject", "luhn_card_shape", True)
        if _looks_high_entropy(raw) and not (storage_policy and storage_policy.sensitivity == "secret"):
            return SafetyVerdict("reject", "token_shape_in_non_secret_slot", True)
        for pattern in deny_patterns or []:
            try:
                if re.search(pattern, raw, re.IGNORECASE):
                    return SafetyVerdict("reject", "tenant_deny_pattern", True)
            except re.error:
                continue
        if "password" in context or "credential" in context:
            if storage_policy and storage_policy.scope == "audit_only" and storage_policy.retention == "do_not_store":
                return SafetyVerdict("redact_audit_only", "credential_redacted_audit_only", True)
            if raw and not raw.isspace():
                return SafetyVerdict("reject", "credential_capture_forbidden", True)
        return SafetyVerdict("allow")
