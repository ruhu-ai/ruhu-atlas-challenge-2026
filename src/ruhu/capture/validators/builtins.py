from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from ruhu.capture.types import ValidationResult
from ruhu.capture.validators.base import failed, passed
from ruhu.schemas import FactDef

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\+?\d[\d\s\-\(\)]{7,}\d")
ID_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{3,63}$")


class EmailValidator:
    fact_type = "email"
    is_exact = True

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = str(raw or "").strip()
        if EMAIL_RE.fullmatch(value):
            return passed(value.lower())
        return failed("invalid_email")


class PhoneValidator:
    fact_type = "phone"
    is_exact = True

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = str(raw or "").strip()
        if not PHONE_RE.search(value):
            return failed("invalid_phone")
        digits = re.sub(r"\D", "", value)
        if not 10 <= len(digits) <= 15:
            return failed("invalid_phone")
        if value.strip().startswith("+"):
            return passed("+" + digits)
        if digits.startswith("0") and len(digits) in {10, 11}:
            return passed("+234" + digits[1:])
        if digits.startswith("234"):
            return passed("+" + digits)
        return passed(digits)


class MoneyValidator:
    fact_type = "money"
    is_exact = False

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        currency_default = str(fact_def.validator_config.get("currency_default") or "NGN")
        if isinstance(raw, int | float | Decimal):
            amount = Decimal(str(raw))
            return passed({"amount": int(amount) if amount == amount.to_integral() else float(amount), "currency": currency_default})
        value = str(raw or "").strip()
        lowered = value.lower()
        currency = currency_default
        if "$" in value or "usd" in lowered or "dollar" in lowered:
            currency = "USD"
        elif "₦" in value or "ngn" in lowered or "naira" in lowered:
            currency = "NGN"
        match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|million|thousand)?", lowered)
        if not match:
            return failed("invalid_money")
        try:
            amount = Decimal(match.group(1).replace(",", ""))
        except InvalidOperation:
            return failed("invalid_money")
        suffix = match.group(2)
        if suffix == "k" or suffix == "thousand":
            amount *= Decimal(1000)
        elif suffix == "m" or suffix == "million":
            amount *= Decimal(1_000_000)
        min_amount = fact_def.validator_config.get("min_amount")
        if min_amount is not None and amount < Decimal(str(min_amount)):
            return failed("below_min_amount")
        normalized = int(amount) if amount == amount.to_integral() else float(amount)
        return passed({"amount": normalized, "currency": currency})


class DurationValidator:
    fact_type = "duration"
    is_exact = False

    _WORDS = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "twelve": 12,
        "twenty four": 24,
        "twenty-four": 24,
    }

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = str(raw or "").strip().lower()
        match = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|twenty four|twenty-four)\s*(day|days|week|weeks|month|months|year|years|yrs?)\b", value)
        if not match:
            return failed("invalid_duration")
        count = self._WORDS.get(match.group(1), int(match.group(1)) if match.group(1).isdigit() else 0)
        unit = match.group(2)
        if unit.startswith("day"):
            months = max(1, round(count / 30))
        elif unit.startswith("week"):
            months = max(1, round(count / 4))
        elif unit.startswith("year") or unit.startswith("yr"):
            months = count * 12
        else:
            months = count
        return passed({"months": months})


class CycleValidator:
    fact_type = "cycle"
    is_exact = True

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = str(raw or "").strip().lower()
        for option in ("daily", "weekly", "monthly", "quarterly", "seasonal", "harvest", "planting", "production"):
            if option in value:
                return passed(option)
        return failed("invalid_cycle")


class BooleanValidator:
    fact_type = "boolean"
    is_exact = True

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        if isinstance(raw, bool):
            return passed(raw)
        value = str(raw or "").strip().lower()
        if re.search(r"\b(yes|true|ready|available|have|agree|approve|ok|okay|consent)\b", value):
            return passed(True)
        if re.search(r"\b(no|false|not ready|don'?t|do not|none)\b", value):
            return passed(False)
        return failed("invalid_boolean")


class ConsentValidator(BooleanValidator):
    fact_type = "consent"

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = str(raw or "").strip().lower()
        if re.search(r"\b(i consent|i agree|yes.*consent|approve|authorized?|you may call|call me)\b", value):
            return passed(True)
        return failed("consent_not_explicit")


class DateTimeValidator:
    fact_type = "datetime"
    is_exact = False

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = str(raw or "").strip()
        if not value:
            return failed("invalid_datetime")
        normalized = self._parse_iso(value)
        if normalized is not None:
            return passed(normalized)
        lowered = value.lower()
        if lowered in {"today", "now"}:
            return passed(lowered)
        if lowered in {"tomorrow", "yesterday"}:
            return passed(lowered)
        if re.fullmatch(
            r"(?:(next|this)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+(morning|afternoon|evening))?",
            lowered,
        ):
            return passed(" ".join(value.split()))
        if re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", lowered):
            return passed(lowered)
        return failed("invalid_datetime")

    @staticmethod
    def _parse_iso(value: str) -> str | None:
        try:
            parsed_date = date.fromisoformat(value)
            return parsed_date.isoformat()
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat()


class NameValidator:
    fact_type = "name"
    is_exact = False

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = " ".join(str(raw or "").strip().split())
        if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{0,79}", value) and len(value.split()) <= 6:
            return passed(value)
        return failed("invalid_name")


class AddressValidator:
    fact_type = "address"
    is_exact = False

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = " ".join(str(raw or "").strip().split())
        if 3 <= len(value) <= 240:
            return passed(value)
        return failed("invalid_address")


class EnumValidator:
    fact_type = "enum"
    is_exact = True

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = str(raw or "").strip()
        allowed = fact_def.validator_config.get("allowed_values") or []
        if not allowed:
            return passed(value)
        by_lower = {str(item).lower(): item for item in allowed}
        selected = by_lower.get(value.lower())
        if selected is None:
            return failed("invalid_enum")
        return passed(selected)


class IdValidator:
    fact_type = "id"
    is_exact = True

    def validate(self, raw: Any, fact_def: FactDef) -> ValidationResult:
        value = str(raw or "").strip()
        if ID_VALUE_RE.fullmatch(value):
            return passed(value)
        return failed("invalid_id")
