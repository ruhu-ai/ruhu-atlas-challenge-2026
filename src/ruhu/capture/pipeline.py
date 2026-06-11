from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ruhu.agent_document import CompiledAgentDocument, Step
from ruhu.capture.audit import AuditWriter, InMemoryAuditWriter
from ruhu.capture.deterministic import DeterministicFactExtractor
from ruhu.capture.llm_extractor import FieldExtractorLLM, LLMFactExtractor
from ruhu.capture.safety import SafetyGuard
from ruhu.capture.types import CaptureAuditRow, FactCandidate, PipelineDecision
from ruhu.capture.validators import build_default_validator_registry
from ruhu.capture.validators.base import ValidatorRegistry
from ruhu.schemas import ArbitrationRule, FactDef, FactRequirement, FactUpdate, PendingFactUpdate

logger = logging.getLogger(__name__)

_FACT_METADATA_KEY = "__ruhu_fact_metadata__"


@dataclass(slots=True)
class ClassifierEntitySlot:
    raw_value: str
    confidence: float
    evidence: str | None = None
    source_ref: str | None = None


@dataclass(slots=True)
class FactExtractionResult:
    updates: list[FactUpdate] = field(default_factory=list)
    needs_confirmation: list[PendingFactUpdate] = field(default_factory=list)
    rejected: list[tuple[FactCandidate, PipelineDecision]] = field(default_factory=list)
    new_fact_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    storage_writes: dict[str, dict[str, FactUpdate]] = field(default_factory=dict)


def build_default_fact_pipeline(
    field_extractor: FieldExtractorLLM | None = None,
    *,
    audit_writer: AuditWriter | None = None,
) -> "FactPipeline":
    return FactPipeline(
        deterministic=DeterministicFactExtractor(),
        llm=LLMFactExtractor(field_extractor),
        validators=build_default_validator_registry(),
        guard=SafetyGuard(),
        audit_writer=audit_writer or InMemoryAuditWriter(),
    )


class FactPipeline:
    def __init__(
        self,
        *,
        deterministic: DeterministicFactExtractor,
        llm: LLMFactExtractor,
        validators: ValidatorRegistry,
        guard: SafetyGuard,
        audit_writer: AuditWriter,
        strict_audit: bool = False,
    ) -> None:
        self._deterministic = deterministic
        self._llm = llm
        self._validators = validators
        self._guard = guard
        self._audit_writer = audit_writer
        self._strict_audit = strict_audit

    def extract(
        self,
        *,
        text: str,
        turn_id: str,
        step: Step,
        agent_document: CompiledAgentDocument,
        existing_facts: dict[str, Any],
        existing_fact_metadata: dict[str, dict[str, Any]],
        classifier_entity_slots: dict[str, Any] | None = None,
        conversation_id: str,
        organization_id: str | None,
        transcript_context: str | None = None,
    ) -> FactExtractionResult:
        started = time.monotonic()
        fact_requirements = list(step.fact_requirements)
        fact_defs = self._fact_defs_for_step(agent_document, step)
        candidates: list[FactCandidate] = []
        for fact_name, slot in (classifier_entity_slots or {}).items():
            if isinstance(slot, dict):
                raw_value = slot.get("raw_value", slot.get("value"))
                confidence = float(slot.get("confidence", 0.75) or 0.75)
                evidence = slot.get("evidence")
                source_ref = slot.get("source_ref") or slot.get("classifier_event_id")
                slot_span = slot.get("transcript_span")
            else:
                raw_value = getattr(slot, "raw_value", getattr(slot, "value", None))
                confidence = float(getattr(slot, "confidence", 0.75) or 0.75)
                evidence = getattr(slot, "evidence", None)
                source_ref = getattr(slot, "source_ref", None)
                slot_span = getattr(slot, "transcript_span", None)
            if raw_value is not None:
                span = _coerce_span(slot_span)
                if span is None and evidence:
                    span = _locate_in_text(text, str(evidence))
                if span is None and isinstance(raw_value, str):
                    span = _locate_in_text(text, raw_value)
                candidates.append(
                    FactCandidate(
                        fact_name, raw_value, "classifier", evidence, confidence, source_ref,
                        transcript_span=span,
                    )
                )

        deterministic_candidates = self._deterministic.extract(
            text=text,
            fact_requirements=fact_requirements,
            fact_defs=fact_defs,
            existing_facts=existing_facts,
        )
        candidates.extend(deterministic_candidates)

        supplied_names = {candidate.fact_name for candidate in candidates}
        gap_facts = [
            requirement.name
            for requirement in fact_requirements
            if requirement.name not in existing_facts and requirement.name not in supplied_names
        ]
        candidates.extend(
            self._llm.extract(
                text=text,
                fact_defs=fact_defs,
                gap_facts=gap_facts,
                existing_facts=existing_facts,
                step=step,
                transcript_context=transcript_context,
            )
        )
        try:
            return self.process_candidates(
                candidates=candidates,
                turn_id=turn_id,
                step=step,
                agent_document=agent_document,
                existing_facts=existing_facts,
                existing_fact_metadata=existing_fact_metadata,
                conversation_id=conversation_id,
                organization_id=organization_id,
            )
        finally:
            self._observe_duration("extract", started)

    def process_candidates(
        self,
        *,
        candidates: list[FactCandidate],
        turn_id: str,
        step: Step,
        agent_document: CompiledAgentDocument,
        existing_facts: dict[str, Any],
        existing_fact_metadata: dict[str, dict[str, Any]],
        conversation_id: str,
        organization_id: str | None,
    ) -> FactExtractionResult:
        started = time.monotonic()
        result = FactExtractionResult()
        audit_rows: list[CaptureAuditRow] = []
        requirements = {requirement.name: requirement for requirement in step.fact_requirements}
        fact_defs = {fact_def.name: fact_def for fact_def in self._fact_defs_for_step(agent_document, step)}
        safety_deny_patterns = self._safety_deny_patterns(agent_document)
        validated: dict[str, list[tuple[FactCandidate, Any, bool]]] = {}

        for candidate in candidates:
            fact_def = fact_defs.get(candidate.fact_name)
            fact_requirement = requirements.get(candidate.fact_name)
            verdict = self._guard.scan(candidate, fact_def, fact_requirement, step, safety_deny_patterns)
            if verdict.action == "reject":
                decision = PipelineDecision("rejected_safety", verdict.reason)
                result.rejected.append((candidate, decision))
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def))
                continue
            if verdict.action == "redact_audit_only":
                decision = PipelineDecision("stored_audit_only_redacted", verdict.reason)
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def))
                continue
            if fact_def is None:
                decision = PipelineDecision("rejected_validation", "unknown_fact_name")
                result.rejected.append((candidate, decision))
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def))
                continue
            if candidate.source not in fact_def.allowed_sources:
                decision = PipelineDecision("rejected_policy", "source_not_allowed")
                result.rejected.append((candidate, decision))
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def))
                continue
            if fact_def.storage_policy.retention == "do_not_store" and fact_def.storage_policy.scope == "audit_only":
                decision = PipelineDecision("stored_audit_only_redacted", "retention_do_not_store")
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def))
                continue
            if fact_def.storage_policy.retention == "do_not_store":
                decision = PipelineDecision("rejected_policy", "retention_do_not_store")
                result.rejected.append((candidate, decision))
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def))
                continue
            if fact_def.storage_policy.sensitivity == "secret" and fact_def.storage_policy.scope != "audit_only":
                decision = PipelineDecision("rejected_policy", "secret_storage_forbidden")
                result.rejected.append((candidate, decision))
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def))
                continue
            if fact_def.storage_policy.sensitivity == "secret":
                decision = PipelineDecision("stored_audit_only_redacted", "secret_redacted")
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def))
                continue

            effective_type = self._effective_fact_type(fact_def)
            validator = self._validators.get(effective_type)
            validation = validator.validate(candidate.raw_value, fact_def)
            if validation.status == "failed":
                decision = PipelineDecision("rejected_validation", validation.reason)
                result.rejected.append((candidate, decision))
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def))
                continue
            validated.setdefault(candidate.fact_name, []).append((candidate, validation.normalized_value, validator.is_exact))

        for fact_name, entries in validated.items():
            fact_def = fact_defs[fact_name]
            winner, arbitration_confirmation_reason = self._choose_winner(entries, fact_def.arbitration_rules)
            for candidate, normalized_value, _is_exact in entries:
                if candidate is winner[0]:
                    continue
                decision = PipelineDecision("rejected_lower_priority_candidate", "lower_priority_conflicting_value")
                result.rejected.append((candidate, decision))
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def, normalized_value))

            candidate, normalized_value, _is_exact = winner
            if arbitration_confirmation_reason is not None:
                pending = PendingFactUpdate(
                    pending_id=str(uuid4()),
                    name=fact_name,
                    proposed_value=normalized_value,
                    raw_value=candidate.raw_value,
                    source=candidate.source,
                    confidence=candidate.confidence,
                    evidence=candidate.evidence,
                    source_ref=candidate.source_ref,
                    reason="conflict_requires_confirmation",
                    previous_value=existing_facts.get(fact_name),
                    previous_metadata=existing_fact_metadata.get(fact_name),
                    turn_id=turn_id,
                )
                result.needs_confirmation.append(pending)
                decision = PipelineDecision("needs_confirmation_conflict", arbitration_confirmation_reason)
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def, normalized_value))
                continue
            if candidate.confidence < fact_def.confidence_threshold:
                pending = PendingFactUpdate(
                    pending_id=str(uuid4()),
                    name=fact_name,
                    proposed_value=normalized_value,
                    raw_value=candidate.raw_value,
                    source=candidate.source,
                    confidence=candidate.confidence,
                    evidence=candidate.evidence,
                    source_ref=candidate.source_ref,
                    reason="below_threshold",
                    previous_value=existing_facts.get(fact_name),
                    previous_metadata=existing_fact_metadata.get(fact_name),
                    turn_id=turn_id,
                )
                result.needs_confirmation.append(pending)
                decision = PipelineDecision("needs_confirmation_threshold", "below_threshold")
                audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def, normalized_value))
                continue

            if fact_name in existing_facts and candidate.source != "user_confirmed":
                existing_metadata = existing_fact_metadata.get(fact_name)
                if not existing_metadata:
                    decision = PipelineDecision("rejected_conflict", "existing_metadata_missing")
                    result.rejected.append((candidate, decision))
                    audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def, normalized_value))
                    continue
                existing_source = str(existing_metadata.get("source") or "")
                existing_confidence = self._metadata_confidence(existing_metadata)
                if (
                    fact_def.conflict_policy == "prefer_deterministic"
                    and existing_source == "deterministic"
                    and candidate.source != "deterministic"
                ):
                    decision = PipelineDecision("rejected_conflict", "existing_deterministic_preferred")
                    result.rejected.append((candidate, decision))
                    audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def, normalized_value))
                    continue
                if (
                    fact_def.conflict_policy == "prefer_latest_high_confidence"
                    and existing_confidence is not None
                    and float(candidate.confidence or 0) < existing_confidence
                ):
                    decision = PipelineDecision("rejected_conflict", "existing_confidence_higher")
                    result.rejected.append((candidate, decision))
                    audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def, normalized_value))
                    continue
                if fact_def.conflict_policy == "require_confirmation":
                    pending = PendingFactUpdate(
                        pending_id=str(uuid4()),
                        name=fact_name,
                        proposed_value=normalized_value,
                        raw_value=candidate.raw_value,
                        source=candidate.source,
                        confidence=candidate.confidence,
                        evidence=candidate.evidence,
                        source_ref=candidate.source_ref,
                        reason="conflict_requires_confirmation",
                        previous_value=existing_facts.get(fact_name),
                        previous_metadata=existing_metadata,
                        turn_id=turn_id,
                    )
                    result.needs_confirmation.append(pending)
                    decision = PipelineDecision("needs_confirmation_conflict", "conflict_requires_confirmation")
                    audit_rows.append(self._audit_row(candidate, decision, conversation_id, turn_id, step, organization_id, fact_def, normalized_value))
                    continue

            update = FactUpdate(
                name=fact_name,
                value=normalized_value,
                source=candidate.source,
                confidence=candidate.confidence,
                raw_value=candidate.raw_value,
                evidence=candidate.evidence,
                source_ref=candidate.source_ref,
                outcome="accepted" if fact_def.storage_policy.scope != "audit_only" else "stored_audit_only",
                turn_id=turn_id,
                replaced_previous=fact_name in existing_facts,
            )
            decision = PipelineDecision("accepted" if fact_def.storage_policy.scope != "audit_only" else "stored_audit_only")
            audit_rows.append(
                self._audit_row(
                    candidate,
                    decision,
                    conversation_id,
                    turn_id,
                    step,
                    organization_id,
                    fact_def,
                    normalized_value,
                    replaced_previous=fact_name in existing_facts,
                )
            )
            if fact_def.storage_policy.scope == "conversation":
                result.updates.append(update)
                result.new_fact_metadata[fact_name] = {
                    "source": candidate.source,
                    "confidence": candidate.confidence,
                    "turn_id": turn_id,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "storage_policy": fact_def.storage_policy.model_dump(),
                    "source_ref": candidate.source_ref,
                }
            else:
                result.storage_writes.setdefault(fact_def.storage_policy.scope, {})[fact_name] = update

        try:
            self._write_audit(audit_rows)
            return result
        finally:
            self._observe_duration("process_candidates", started)

    def _choose_winner(
        self,
        entries: list[tuple[FactCandidate, Any, bool]],
        rules: list[ArbitrationRule],
    ) -> tuple[tuple[FactCandidate, Any, bool], str | None]:
        remaining = list(entries)
        confirmation_reason: str | None = None
        for rule in rules:
            if len(remaining) <= 1:
                break
            narrowed, should_confirm = self._apply_arbitration_rule(remaining, rule)
            if should_confirm:
                confirmation_reason = rule.kind
                break
            if narrowed:
                remaining = narrowed
        winner = self._fallback_winner(remaining)
        return winner, confirmation_reason

    def _apply_arbitration_rule(
        self,
        entries: list[tuple[FactCandidate, Any, bool]],
        rule: ArbitrationRule,
    ) -> tuple[list[tuple[FactCandidate, Any, bool]], bool]:
        if rule.kind == "prefer_user_confirmed":
            confirmed = [entry for entry in entries if entry[0].source == "user_confirmed"]
            return confirmed or entries, False
        if rule.kind == "prefer_authoritative_tool":
            refs = set(rule.config.get("authoritative_tools") or rule.config.get("tool_refs") or [])
            authoritative = [
                entry
                for entry in entries
                if entry[0].source == "tool" and (not refs or entry[0].source_ref in refs)
            ]
            return authoritative or entries, False
        if rule.kind == "prefer_exact_validator":
            exact = [entry for entry in entries if entry[2]]
            return exact or entries, False
        if rule.kind == "prefer_classifier_over_llm":
            classifier = [entry for entry in entries if entry[0].source == "classifier"]
            if classifier and any(entry[0].source == "llm_proposed" for entry in entries):
                return classifier, False
            return entries, False
        if rule.kind == "prefer_highest_confidence":
            max_confidence = max(float(entry[0].confidence or 0) for entry in entries)
            return [entry for entry in entries if float(entry[0].confidence or 0) == max_confidence], False
        if rule.kind == "prefer_latest":
            return [entries[-1]], False
        if rule.kind == "require_confirmation_on_disagreement":
            if len({self._normalized_key(entry[1]) for entry in entries}) > 1:
                return entries, True
            return entries, False
        return entries, False

    @staticmethod
    def _fallback_winner(entries: list[tuple[FactCandidate, Any, bool]]) -> tuple[FactCandidate, Any, bool]:
        source_rank = {
            "user_confirmed": 0,
            "deterministic": 1,
            "classifier": 2,
            "tool": 3,
            "llm_proposed": 4,
            "extractor": 5,
            "system": 6,
        }
        return sorted(
            entries,
            key=lambda item: (
                source_rank.get(item[0].source, 99),
                0 if item[2] else 1,
                -float(item[0].confidence or 0),
            ),
        )[0]

    @staticmethod
    def _normalized_key(value: Any) -> str:
        import json

        try:
            return json.dumps(value, sort_keys=True, default=str)
        except TypeError:
            return str(value)

    @staticmethod
    def _fact_defs_for_step(agent_document: CompiledAgentDocument, step: Step) -> list[FactDef]:
        """Return explicit fact schema plus legacy implicit step requirements.

        Older fixtures and agent documents sometimes declare
        ``step.fact_requirements`` without adding matching ``FactDef`` entries.
        The capture pipeline should still collect those values with the same
        permissive string behavior the kernel helper used before the lift.
        """
        fact_defs = list(agent_document.fact_schema)
        known = {fact_def.name for fact_def in fact_defs}
        for requirement in step.fact_requirements:
            if requirement.name not in known:
                fact_defs.append(FactDef(name=requirement.name, type="string"))
                known.add(requirement.name)
        return fact_defs

    @staticmethod
    def _effective_fact_type(fact_def: FactDef) -> str:
        fact_type = (fact_def.type or "").lower()
        name = fact_def.name.lower()
        if fact_type not in {"string", "str", ""}:
            return fact_type
        if "email" in name:
            return "email"
        if "phone" in name:
            return "phone"
        if any(token in name for token in ("amount", "price", "cost", "budget")):
            return "money"
        if any(token in name for token in ("tenor", "duration", "timeframe", "period")):
            return "duration"
        if any(token in name for token in ("ready", "consent", "liquidity")):
            return "boolean"
        if name in {"name", "full_name", "customer_name"}:
            return "name"
        if name.endswith(("_id", "_code", "_ref")):
            return "id"
        return fact_type

    @staticmethod
    def _metadata_confidence(metadata: dict[str, Any]) -> float | None:
        try:
            value = metadata.get("confidence")
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safety_deny_patterns(agent_document: CompiledAgentDocument) -> list[str]:
        metadata = getattr(getattr(agent_document, "document", None), "metadata", {}) or {}
        raw_patterns = metadata.get("safety_deny_patterns")
        if not isinstance(raw_patterns, list):
            safety_config = metadata.get("safety_config")
            if isinstance(safety_config, dict):
                raw_patterns = safety_config.get("deny_patterns")
        return [str(pattern) for pattern in raw_patterns or [] if str(pattern).strip()]

    @staticmethod
    def _audit_row(
        candidate: FactCandidate,
        decision: PipelineDecision,
        conversation_id: str,
        turn_id: str,
        step: Step,
        organization_id: str | None,
        fact_def: FactDef | None,
        normalized_value: Any | None = None,
        *,
        replaced_previous: bool = False,
    ) -> CaptureAuditRow:
        storage_policy = fact_def.storage_policy if fact_def is not None else None
        secret = storage_policy is not None and storage_policy.sensitivity == "secret"
        redacted = decision.decision in {"rejected_safety", "stored_audit_only_redacted"} or secret
        FactPipeline._observe_candidate(candidate, decision)
        return CaptureAuditRow(
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=getattr(step, "id", None),
            fact_name=candidate.fact_name,
            source=candidate.source,
            outcome=decision.decision,
            reason=decision.reason,
            raw_value=None if redacted else candidate.raw_value,
            normalized_value=None if redacted else normalized_value,
            confidence=candidate.confidence,
            evidence=candidate.evidence,
            source_ref=candidate.source_ref,
            storage_scope=storage_policy.scope if storage_policy is not None else "conversation",
            retention_policy=storage_policy.retention if storage_policy is not None else "conversation",
            sensitivity=storage_policy.sensitivity if storage_policy is not None else "personal",
            audit_raw_policy=storage_policy.audit_raw_policy if storage_policy is not None else "hash",
            replaced_previous=replaced_previous,
            organization_id=organization_id,
            transcript_span=None if redacted else candidate.transcript_span,
        )

    def _write_audit(self, rows: list[CaptureAuditRow]) -> None:
        if not rows:
            return
        try:
            self._audit_writer.write(rows)
        except Exception:
            logger.exception("capture audit write failed")
            self._observe_audit_failure()
            if self._strict_audit:
                raise

    @staticmethod
    def _observe_duration(entrypoint: str, started: float) -> None:
        try:
            from ruhu.observability.metrics import capture_pipeline_duration_seconds

            capture_pipeline_duration_seconds.labels(entrypoint=entrypoint).observe(time.monotonic() - started)
        except Exception:
            pass

    @staticmethod
    def _observe_candidate(candidate: FactCandidate, decision: PipelineDecision) -> None:
        try:
            from ruhu.observability.metrics import (
                capture_candidates_total,
                capture_safety_hits_total,
                capture_validator_rejections_total,
            )

            source = candidate.source if candidate.source in {
                "deterministic",
                "classifier",
                "extractor",
                "tool",
                "llm_proposed",
                "user_confirmed",
                "system",
            } else "unknown"
            capture_candidates_total.labels(source=source, outcome=decision.decision).inc()
            if decision.decision == "rejected_validation":
                capture_validator_rejections_total.labels(reason=_bounded_reason(decision.reason)).inc()
            if decision.decision == "rejected_safety":
                capture_safety_hits_total.labels(reason=_bounded_reason(decision.reason)).inc()
        except Exception:
            pass

    def _observe_audit_failure(self) -> None:
        try:
            from ruhu.observability.metrics import capture_audit_write_failures_total

            capture_audit_write_failures_total.labels(mode="strict" if self._strict_audit else "best_effort").inc()
        except Exception:
            pass


def _coerce_span(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 2:
        try:
            start, end = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
        if 0 <= start <= end:
            return (start, end)
        return None
    if isinstance(value, list | tuple) and len(value) == 2:
        try:
            start, end = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
        if 0 <= start <= end:
            return (start, end)
    if isinstance(value, dict):
        start_raw = value.get("start")
        end_raw = value.get("end")
        if start_raw is None or end_raw is None:
            return None
        try:
            start, end = int(start_raw), int(end_raw)
        except (TypeError, ValueError):
            return None
        if 0 <= start <= end:
            return (start, end)
    return None


def _locate_in_text(text: str, needle: str) -> tuple[int, int] | None:
    if not text or not needle:
        return None
    stripped = needle.strip()
    if not stripped:
        return None
    start = text.find(stripped)
    if start == -1:
        lowered_start = text.lower().find(stripped.lower())
        if lowered_start == -1:
            return None
        start = lowered_start
    return (start, start + len(stripped))


def _bounded_reason(reason: str | None) -> str:
    allowed = {
        "unknown_fact_name",
        "source_not_allowed",
        "retention_do_not_store",
        "secret_storage_forbidden",
        "invalid_email",
        "invalid_phone",
        "invalid_money",
        "invalid_duration",
        "invalid_boolean",
        "invalid_enum",
        "invalid_id",
        "otp_shape_in_credential_context",
        "bvn_shape_in_non_bvn_slot",
        "luhn_card_shape",
        "token_shape_in_non_secret_slot",
        "tenant_deny_pattern",
        "credential_capture_forbidden",
    }
    return reason if reason in allowed else "other"
