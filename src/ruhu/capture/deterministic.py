from __future__ import annotations

import re
from typing import Any

from ruhu.capture.types import FactCandidate
from ruhu.capture.validators.builtins import EMAIL_RE, ID_VALUE_RE, PHONE_RE
from ruhu.schemas import FactDef, FactRequirement


class DeterministicFactExtractor:
    def extract(
        self,
        *,
        text: str,
        fact_requirements: list[FactRequirement],
        fact_defs: list[FactDef],
        existing_facts: dict[str, Any],
    ) -> list[FactCandidate]:
        fact_names = [requirement.name for requirement in fact_requirements]
        candidates: list[FactCandidate] = []
        missing = [f for f in fact_names if f not in existing_facts]
        if not fact_names or not text.strip():
            return candidates
        fact_def_by_name = {item.name: item for item in fact_defs}

        if "email" in missing:
            match = EMAIL_RE.search(text)
            if match:
                candidates.append(
                    FactCandidate(
                        "email", match.group(0), "deterministic", match.group(0), 1.0,
                        transcript_span=(match.start(), match.end()),
                    )
                )

        phone_fact = next((fact for fact in fact_names if fact == "phone" or "phone" in fact), None)
        if phone_fact is not None:
            match = PHONE_RE.search(text)
            if match:
                normalized = re.sub(r"[^\d+]", "", match.group(0))
                candidates.append(
                    FactCandidate(
                        phone_fact, normalized, "deterministic", match.group(0), 0.95,
                        transcript_span=(match.start(), match.end()),
                    )
                )

        for name_fact in ("name", "full_name", "customer_name"):
            if name_fact in missing:
                cleaned = text.strip()
                words = cleaned.split()
                if 1 <= len(words) <= 4 and all(w.isalpha() or w == "-" for w in words):
                    span = _locate(text, cleaned)
                    candidates.append(
                        FactCandidate(
                            name_fact, cleaned, "deterministic", cleaned, 0.85,
                            transcript_span=span,
                        )
                    )
                    break

        for fact_name in missing:
            if fact_name in {candidate.fact_name for candidate in candidates}:
                continue
            if any(kw in fact_name for kw in ("_id", "_number", "_code", "_ref")):
                stripped = text.strip()
                match = ID_VALUE_RE.search(stripped)
                if match:
                    span = _locate(text, match.group(0))
                    candidates.append(
                        FactCandidate(
                            fact_name, match.group(0), "deterministic", match.group(0), 0.9,
                            transcript_span=span,
                        )
                    )

        labelled_fact_names = [f for f in fact_names if f not in {candidate.fact_name for candidate in candidates}]
        labelled_values = self._extract_labelled_fact_values(
            text=text,
            fact_names=labelled_fact_names,
            fact_requirements=fact_requirements,
            fact_defs=fact_defs,
        )
        for fact_name in labelled_fact_names:
            raw_value = labelled_values.get(fact_name)
            if raw_value is None:
                continue
            normalized = self._normalize_delimited_fact_value(fact_name, raw_value)
            if self._is_plausible_capture_value(
                fact_name=fact_name,
                value=normalized,
                fact_def=fact_def_by_name.get(fact_name),
            ):
                span = _locate(text, raw_value)
                candidates.append(
                    FactCandidate(
                        fact_name, normalized, "deterministic", raw_value, 0.88,
                        transcript_span=span,
                    )
                )

        still_missing = [f for f in missing if f not in {candidate.fact_name for candidate in candidates}]
        if len(still_missing) > 1:
            delimited_parts = self._split_delimited_fact_values(text)
            if 1 < len(delimited_parts) <= len(missing):
                captured_fact_names = {candidate.fact_name for candidate in candidates}
                next_candidates: list[FactCandidate] = []
                for fact_name, raw_value in zip(missing, delimited_parts, strict=False):
                    if fact_name in captured_fact_names or fact_name not in still_missing:
                        continue
                    normalized = self._normalize_delimited_fact_value(fact_name, raw_value)
                    if not self._is_plausible_capture_value(
                        fact_name=fact_name,
                        value=normalized,
                        fact_def=fact_def_by_name.get(fact_name),
                    ):
                        next_candidates = []
                        break
                    span = _locate(text, raw_value)
                    next_candidates.append(
                        FactCandidate(
                            fact_name, normalized, "deterministic", raw_value, 0.88,
                            transcript_span=span,
                        )
                    )
                    captured_fact_names.add(fact_name)
                candidates.extend(next_candidates)

        still_unseen = [f for f in fact_names if f not in {candidate.fact_name for candidate in candidates}]
        fact_specific_values = self._extract_fact_specific_voice_values(text=text, fact_names=still_unseen)
        for fact_name in still_unseen:
            raw_value = fact_specific_values.get(fact_name)
            if raw_value is None:
                continue
            normalized = self._normalize_delimited_fact_value(fact_name, raw_value)
            if self._is_plausible_capture_value(
                fact_name=fact_name,
                value=normalized,
                fact_def=fact_def_by_name.get(fact_name),
            ):
                span = _locate(text, raw_value)
                candidates.append(
                    FactCandidate(
                        fact_name, normalized, "deterministic", raw_value, 0.88,
                        transcript_span=span,
                    )
                )

        still_missing = [f for f in missing if f not in {candidate.fact_name for candidate in candidates}]
        if len(still_missing) == 1 and not candidates and text.strip():
            value = text.strip()
            if len(value) < 100:
                fact_name = still_missing[0]
                skip_patterns = {"email", "phone", "url", "address"}
                if not any(p in fact_name for p in skip_patterns) and self._is_plausible_capture_value(
                    fact_name=fact_name,
                    value=value,
                    fact_def=fact_def_by_name.get(fact_name),
                    allow_generic=True,
                ):
                    span = _locate(text, value)
                    candidates.append(
                        FactCandidate(
                            fact_name, value, "deterministic", value, 0.9,
                            transcript_span=span,
                        )
                    )

        return candidates

    @staticmethod
    def _split_delimited_fact_values(text: str) -> list[str]:
        if "," not in text and ";" not in text and "\n" not in text:
            return []
        parts = [
            part.strip(" \t\r\n.-")
            for part in re.split(r"[,;\n]+", text)
            if part.strip(" \t\r\n.-")
        ]
        if any(len(part) > 120 for part in parts):
            return []
        return parts

    @classmethod
    def _extract_labelled_fact_values(
        cls,
        *,
        text: str,
        fact_names: list[str],
        fact_requirements: list[FactRequirement],
        fact_defs: list[FactDef],
    ) -> dict[str, str]:
        if not fact_names:
            return {}
        target_fact_names = set(fact_names)
        requirement_by_name = {requirement.name: requirement for requirement in fact_requirements}
        fact_def_by_name = {fact_def.name: fact_def for fact_def in fact_defs}
        labels: list[tuple[str, str]] = []
        boundary_fact_names = list(dict.fromkeys([*fact_names, *(fact_def.name for fact_def in fact_defs)]))
        for fact_name in boundary_fact_names:
            for label in cls._fact_capture_labels(
                fact_name,
                requirement_by_name.get(fact_name),
                fact_def_by_name.get(fact_name),
            ):
                labels.append((fact_name, label))
        if not labels:
            return {}

        label_pattern = "|".join(
            re.escape(label).replace(r"\ ", r"\s+")
            for _fact_name, label in sorted(labels, key=lambda item: len(item[1]), reverse=True)
        )
        connector_pattern = r"(?:\s*(?:is|are|=|:|-|from|via|through)\s+|\s+)"
        matches = list(
            re.finditer(
                rf"(?<!\w)(?P<label>{label_pattern})(?!\w){connector_pattern}",
                text,
                flags=re.IGNORECASE,
            )
        )
        if not matches:
            return {}

        label_to_fact: dict[str, str] = {}
        for fact_name, label in labels:
            label_to_fact.setdefault(label.lower(), fact_name)
        values: dict[str, str] = {}
        for index, match in enumerate(matches):
            raw_label = " ".join(match.group("label").lower().split())
            fact_name = label_to_fact.get(raw_label)
            if not fact_name or fact_name not in target_fact_names:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            raw_value = text[match.end() : end].strip(" \t\r\n,;.")
            raw_value = cls._trim_natural_language_boundary(raw_value)
            if fact_name == "documents_ready" and raw_value.lower() == "ready":
                continue
            if raw_value:
                values[fact_name] = raw_value
        return values

    @staticmethod
    def _fact_capture_labels(
        fact_name: str,
        requirement: FactRequirement | None,
        fact_def: FactDef | None = None,
    ) -> list[str]:
        base = fact_name.replace("_", " ").strip()
        labels = [base]
        if fact_def is not None:
            labels.extend(fact_def.capture_aliases)
        for prefix in ("loan ", "preferred ", "customer ", "deposit ", "agri "):
            if base.startswith(prefix):
                labels.append(base[len(prefix) :])
        if fact_name == "preferred_tenor":
            labels.extend(["tenor", "term", "duration", "repayment period"])
        elif fact_name == "repayment_source":
            labels.extend(["repayment source", "source", "repay from", "income source"])
        elif fact_name == "documents_ready":
            labels.extend(["document", "documents", "document ready", "documents ready", "docs", "docs ready"])
        elif fact_name == "loan_amount" or fact_name.endswith("_amount"):
            labels.extend(["amount", "requested amount"])
        elif fact_name.endswith("_goal"):
            labels.extend(["goal", "purpose"])
        elif fact_name == "phone_number":
            labels.extend(["phone", "phone number", "mobile number"])
        elif fact_name == "consent_to_callback":
            labels.extend(["consent", "callback consent", "consent to callback"])
        if requirement and requirement.purpose:
            purpose = requirement.purpose.lower()
            if "amount" in purpose:
                labels.append("amount")
            if "repayment" in purpose:
                labels.append("repayment")
        deduped: list[str] = []
        for label in labels:
            normalized = " ".join(str(label).lower().split())
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped

    @staticmethod
    def _normalize_delimited_fact_value(fact_name: str, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        lowered = cleaned.lower()
        if any(token in fact_name for token in ("purpose", "goal", "need")):
            for prefix in ("i want to ", "i need to ", "i would like to ", "i want ", "i need ", "for "):
                if lowered.startswith(prefix):
                    cleaned = cleaned[len(prefix) :].strip() or cleaned
                    lowered = cleaned.lower()
                    break
            cleaned = re.sub(r"\s+(?:and\s+the|and)\s*$", "", cleaned, flags=re.IGNORECASE).strip() or cleaned
        if "repayment_source" == fact_name:
            for prefix in ("from ", "through ", "via "):
                if lowered.startswith(prefix):
                    cleaned = cleaned[len(prefix) :].strip() or cleaned
                    lowered = cleaned.lower()
                    break
            cleaned = re.split(
                r"\s+(?:and\s+)?(?:yes|no)\b|(?:,|;|\.)\s*(?:yes|no)\b|\b(?:document|documents|docs)\b",
                cleaned,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" ,;.")
        return cleaned

    @classmethod
    def _extract_fact_specific_voice_values(cls, *, text: str, fact_names: list[str]) -> dict[str, str]:
        values: dict[str, str] = {}
        if "repayment_source" in fact_names:
            repayment_source = cls._extract_repayment_source(text)
            if repayment_source:
                values["repayment_source"] = repayment_source
        if "documents_ready" in fact_names:
            documents_ready = cls._extract_documents_ready(text)
            if documents_ready is not None:
                values["documents_ready"] = documents_ready
        if "consent_to_callback" in fact_names:
            consent = cls._extract_callback_consent(text)
            if consent is not None:
                values["consent_to_callback"] = consent
        return values

    @classmethod
    def _extract_repayment_source(cls, text: str) -> str | None:
        patterns = (
            r"\brepayment\s+source\s*(?:is|=|:|-|from|via|through)?\s+(?P<value>[^.;,]+)",
            r"\b(?:repay|pay\s+back|repayment)\s*(?:from|through|via)\s+(?P<value>[^.;,]+)",
            r"\bfrom\s+(?P<value>salar(?:y|ies)|salary|pension|income|business income|business|sales|farm income|harvest proceeds|proceeds)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = cls._normalize_delimited_fact_value("repayment_source", match.group("value"))
            if value:
                return value
        return None

    @staticmethod
    def _extract_documents_ready(text: str) -> str | None:
        lowered = text.lower()
        document_pattern = r"\b(?:document|documents|docs|paperwork)\b"
        negative_near_document = (
            rf"\b(?:no|not|missing|without)\b(?:\W+\w+){{0,6}}\W+{document_pattern}"
            rf"|{document_pattern}(?:\W+\w+){{0,6}}\W+\b(?:not|missing|unavailable)\b"
        )
        if re.search(negative_near_document, lowered, flags=re.IGNORECASE):
            return "no"
        positive_near_document = (
            rf"\b(?:yes|ready|available|prepared|have|has|got)\b(?:\W+\w+){{0,8}}\W+{document_pattern}"
            rf"|{document_pattern}(?:\W+\w+){{0,8}}\W+\b(?:ready|available|prepared|yes)\b"
        )
        if re.search(positive_near_document, lowered, flags=re.IGNORECASE):
            return "yes"
        return None

    @staticmethod
    def _extract_callback_consent(text: str) -> str | None:
        lowered = text.lower()
        if re.search(r"\b(?:i\s+)?(?:do\s+not|don't|cannot|can't|no)\s+consent\b", lowered):
            return None
        if re.search(
            r"\b(?:yes[,\s]+)?(?:i\s+)?(?:consent|agree|approve)\s+(?:to\s+)?(?:a\s+)?callback\b"
            r"|\byou\s+may\s+call\s+me\b|\bcall\s+me\b",
            lowered,
        ):
            return "I consent to a callback"
        return None

    @staticmethod
    def _trim_natural_language_boundary(value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            return cleaned
        cleaned = re.split(
            r"\s+\band\s+(?=(?:my\s+|the\s+|then\s+)?(?:requested\s+)?(?:amount|loan amount|preferred tenor|tenor|term|duration|repayment source|source|documents?|docs?|phone|phone number|mobile number|branch|base|consent)\b)",
            cleaned,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" ,;.")
        return re.sub(
            r"(?:[.;,]\s*)?(?:(?:\band\s+)(?:my|the|then|then\s+my)?|(?:\bmy|\bthe|\bthen))\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip(" ,;.")

    @classmethod
    def _is_plausible_capture_value(
        cls,
        *,
        fact_name: str,
        value: str,
        fact_def: FactDef | None = None,
        allow_generic: bool = False,
    ) -> bool:
        cleaned = " ".join(str(value or "").strip().split())
        if not cleaned or len(cleaned) > 120:
            return False
        fact_type = str(getattr(fact_def, "type", "") or "").lower()
        normalized_name = fact_name.lower()

        if "email" in normalized_name or fact_type == "email":
            return EMAIL_RE.fullmatch(cleaned) is not None
        if "phone" in normalized_name or fact_type == "phone":
            return PHONE_RE.search(cleaned) is not None
        if any(token in normalized_name for token in ("amount", "price", "cost", "budget")) or fact_type == "money":
            return cls._looks_like_amount(cleaned)
        if any(token in normalized_name for token in ("tenor", "timeframe", "duration", "period", "cycle")) or fact_type == "duration":
            return cls._looks_like_duration(cleaned) or cls._looks_like_cycle(cleaned)
        if any(token in normalized_name for token in ("ready", "consent", "liquidity")) or fact_type in {"boolean", "consent"}:
            return cls._looks_like_boolean_or_preference(cleaned)
        if normalized_name in {"name", "full_name", "customer_name"} or fact_type == "name":
            return bool(re.fullmatch(r"[A-Za-z][A-Za-z .'-]{0,79}", cleaned)) and len(cleaned.split()) <= 6
        if normalized_name.endswith(("_id", "_code", "_ref")) or fact_type == "id":
            return ID_VALUE_RE.fullmatch(cleaned) is not None
        if not allow_generic and cls._looks_like_structured_value_for_other_field(cleaned):
            return False
        return len(cleaned) <= 80

    @staticmethod
    def _looks_like_amount(value: str) -> bool:
        lowered = value.lower()
        if re.search(r"\b(?:day|days|week|weeks|month|months|year|years)\b", lowered):
            return False
        return bool(re.search(r"(?:[$₦]|(?:\bngn\b)|(?:\bnaira\b)|(?:\bn\b\s*\d)|\d[\d,]*(?:\.\d+)?\s*(?:k|m|million|thousand)?\b)", lowered))

    @staticmethod
    def _looks_like_duration(value: str) -> bool:
        return bool(re.search(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|twenty four|twenty-four)\s*(?:day|days|week|weeks|month|months|year|years|yrs?)\b", value.lower()))

    @staticmethod
    def _looks_like_cycle(value: str) -> bool:
        return bool(re.search(r"\b(?:daily|weekly|monthly|quarterly|season|seasonal|harvest|planting|production|cycle)\b", value.lower()))

    @staticmethod
    def _looks_like_boolean_or_preference(value: str) -> bool:
        return bool(re.search(r"\b(?:yes|no|ready|available|have|don'?t|do not|need|access|withdraw|consent|agree|approve|ok|okay)\b", value.lower()))

    @staticmethod
    def _looks_like_structured_value_for_other_field(value: str) -> bool:
        lowered = value.lower()
        return bool(
            EMAIL_RE.search(value)
            or PHONE_RE.search(value)
            or re.search(r"\b(?:day|days|week|weeks|month|months|year|years)\b", lowered)
            or re.search(r"(?:[$₦]|\bngn\b|\bnaira\b|\bn\s*\d)", lowered)
        )


def _locate(text: str, value: str) -> tuple[int, int] | None:
    if not text or not value:
        return None
    needle = value.strip()
    if not needle:
        return None
    start = text.find(needle)
    if start == -1:
        lowered_start = text.lower().find(needle.lower())
        if lowered_start == -1:
            return None
        start = lowered_start
    return (start, start + len(needle))
