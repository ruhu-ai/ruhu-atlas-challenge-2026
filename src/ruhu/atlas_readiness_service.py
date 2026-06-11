from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from .agent_document import AgentDocument, validate_agent_document
from .atlas_model_gateway import AtlasModelGateway
from .atlas_models import AtlasSession
from .atlas_protocol import AtlasBlocker, AtlasProposedChanges, StepDelta
from .atlas_readiness import microfinance_repayment_readiness_cases
from .atlas_readiness_models import (
    AtlasCancellationToken,
    AtlasProviderInvocationMetadata,
    AtlasReadinessBudgetExceeded,
    AtlasReadinessCancelled,
    AtlasReadinessCase,
    AtlasReadinessCaseSet,
    AtlasReadinessEvent,
    AtlasReadinessProviderPolicy,
    AtlasReadinessReport,
    AtlasReadinessProviderHealth,
    AtlasReadinessRun,
    AtlasReadinessRunRequest,
    AtlasReadinessRunSummary,
    AtlasReadinessRunTerminal,
    AtlasReadinessScore,
    AtlasReadinessTimeoutExceeded,
    AtlasReadinessTrace,
    AtlasSyntheticTestProfile,
    SimpleAtlasCancellationToken,
    new_atlas_readiness_case_id,
    new_atlas_readiness_case_set_id,
    new_atlas_readiness_event_id,
    new_atlas_readiness_run_id,
)
from .atlas_readiness_privacy import AtlasReadinessPrivacyScrubber
from .atlas_readiness_store import ATLAS_SYSTEM_SCOPE, AtlasReadinessStore, AtlasSystemScope
from .atlas_store import AtlasStore, new_atlas_session_id
from .atlas_voice_harness import AtlasVoiceHarness, DeterministicAtlasVoiceHarness, GoogleAtlasVoiceHarness
from .blob_store import BlobStore
from .interpreter import SemanticInterpreter
from .schemas import SimulationRun
from .simulator import simulate_transcript

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _AtlasOrchestrationPlanPayload(BaseModel):
    objective: str = "Run bounded Atlas readiness evaluation."
    tool_calls: list[str] = Field(default_factory=list)
    next_safe_action: str = "execute_readiness_run"


class _AtlasWorkflowUnderstandingPayload(BaseModel):
    workflow_name: str = "Draft workflow"
    summary: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)


class _AtlasDraftDocumentPayload(BaseModel):
    agent_document: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class _AtlasCaseGenerationPayload(BaseModel):
    cases: list[AtlasReadinessCase] = Field(default_factory=list)
    generation_summary: str = ""


class _AtlasTraceDiagnosisPayload(BaseModel):
    diagnosis_summary: str = ""
    patch_rationales: dict[str, str] = Field(default_factory=dict)
    next_safe_action: str = "review_deltas"


class _AtlasReportWriterPayload(BaseModel):
    executive_summary: str = ""
    before_after_summary: str = ""
    residual_risks: list[str] = Field(default_factory=list)
    evidence_summary: str = ""


def _document_hash(document: AgentDocument) -> str:
    payload = document.model_dump_json(by_alias=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AtlasReadinessService:
    def __init__(
        self,
        *,
        agent_registry: Any,
        atlas_store: AtlasStore,
        readiness_store: AtlasReadinessStore,
        model_gateway: AtlasModelGateway | None = None,
        interpreter: SemanticInterpreter | None = None,
        voice_harness: AtlasVoiceHarness | None = None,
        artifact_store: BlobStore | None = None,
        default_provider_policy: AtlasReadinessProviderPolicy = "deterministic",
        demo_case_set: bool = False,
    ) -> None:
        self._agent_registry = agent_registry
        self._atlas_store = atlas_store
        self._readiness_store = readiness_store
        self._model_gateway = model_gateway
        self._interpreter = interpreter
        self._voice_harness = voice_harness
        self._artifact_store = artifact_store
        # AR-4.2: the hardcoded microfinance demo case set only makes sense for
        # the microfinance demo agent — applied to an arbitrary agent it fails
        # containment on every case and makes the verdict meaningless. Off by
        # default; cases are derived from the agent's own document instead.
        self._demo_case_set = demo_case_set
        self._default_provider_policy = default_provider_policy
        # Same scrubber the relational store uses, applied at the other egress
        # points (blob artifact, MCP tool outputs) so PII/secrets do not leak
        # around the DB-write boundary (AR-2.5).
        self._privacy = AtlasReadinessPrivacyScrubber()
        # Registry of cancellation tokens for in-flight runs, keyed by run_id,
        # so cancel_run (a separate request thread) can signal the worker
        # thread executing start_run. Guarded by a lock for cross-thread access.
        self._cancellation_tokens: dict[str, AtlasCancellationToken] = {}
        self._cancellation_lock = threading.Lock()

    def scrub_for_export(self, value: Any) -> Any:
        """Redact PII/secrets from a payload bound for a non-DB egress point
        (MCP tool output, external artifact). Same policy as the store."""
        return self._privacy.scrub(value)

    def _provider_prompt_text(self, value: Any) -> str:
        """Return text that is safe to include in an external provider prompt."""
        scrubbed = self.scrub_for_export(value)
        return "" if scrubbed is None else str(scrubbed)

    def _provider_prompt_json(self, value: Any) -> str:
        """Return JSON that is safe to include in an external provider prompt."""
        return json.dumps(self.scrub_for_export(value), sort_keys=True, default=str)

    def _register_cancellation_token(self, run_id: str, token: AtlasCancellationToken) -> None:
        with self._cancellation_lock:
            self._cancellation_tokens[run_id] = token

    def _unregister_cancellation_token(self, run_id: str) -> None:
        with self._cancellation_lock:
            self._cancellation_tokens.pop(run_id, None)

    def _get_cancellation_token(self, run_id: str) -> AtlasCancellationToken | None:
        with self._cancellation_lock:
            return self._cancellation_tokens.get(run_id)

    def start_run(
        self,
        request: AtlasReadinessRunRequest,
        *,
        organization_id: str,
        user_id: str | None,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> AtlasReadinessRunSummary:
        provider_policy = request.provider_policy or self._default_provider_policy
        gateway = self._model_gateway or AtlasModelGateway(provider_policy=provider_policy)
        provider_invocations: list[AtlasProviderInvocationMetadata] = []
        orchestration_plan: dict[str, object] = {}
        now = _utcnow()
        run = AtlasReadinessRun(
            run_id=new_atlas_readiness_run_id(),
            organization_id=organization_id,
            agent_id=request.agent_id,
            agent_version_id=request.agent_version_id,
            scope=request.scope,
            state="created",
            provider_policy=provider_policy,
            request=request,
            created_by_user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        self._readiness_store.create_run(run)
        self._event(run.run_id, "run_created", {"scope": request.scope, "provider_policy": provider_policy}, organization_id)

        # Register a cancellation token so cancel_run (another request thread)
        # can signal this worker; the in-flight checkpoints below check it.
        if cancellation_token is None:
            cancellation_token = SimpleAtlasCancellationToken()
        self._register_cancellation_token(run.run_id, cancellation_token)

        # Wall-clock budget: a 100-case run with voice + LLM calls can run for
        # minutes; enforce the declared ceiling at every node boundary so a run
        # cannot exceed it indefinitely (AR-4.1).
        deadline = time.monotonic() + float(request.max_wall_clock_seconds)

        apply_lock_acquired = False
        try:
            self._check_budget(request, provider_policy)
            cancellation_token.throw_if_cancelled() if cancellation_token is not None else None
            orchestration_plan = self._plan_with_adk_envelope(
                request=request,
                gateway=gateway,
                provider_policy=provider_policy,
                provider_invocations=provider_invocations,
            )
            if orchestration_plan:
                self._event(run.run_id, "adk_plan_created", orchestration_plan, organization_id)
            run = self._transition(run.run_id, "resolving_document", organization_id=organization_id)
            document, resolved_agent_id, resolved_version_id = self._resolve_document(
                request,
                organization_id=organization_id,
                gateway=gateway,
                provider_policy=provider_policy,
                provider_invocations=provider_invocations,
            )
            # AR-4.4: a fix run will try to acquire an apply lock on the draft at
            # proposing_deltas — probe for an existing unexpired lock now and
            # fail fast, instead of burning a full simulation suite first.
            if (
                request.scope == "fix"
                and resolved_agent_id is not None
                and resolved_version_id is not None
            ):
                if self._readiness_store.has_active_apply_lock(
                    resolved_agent_id, resolved_version_id, organization_id=organization_id
                ):
                    raise ValueError("readiness_apply_lock_conflict")
            doc_hash = _document_hash(document)
            run = self._readiness_store.update_run(
                run.run_id,
                organization_id=organization_id,
                agent_id=resolved_agent_id,
                agent_version_id=resolved_version_id,
                document_hash=doc_hash,
                policy_hash=self._policy_hash(request),
                provider_config_hash=self._provider_config_hash(provider_policy),
            )

            validation = validate_agent_document(document)
            validation_blockers = [
                AtlasBlocker(code=issue.code, message=issue.message, reference_ids=[item for item in [issue.scenario_id, issue.step_id] if item])
                for issue in validation.issues
                if issue.severity == "error"
            ]
            if validation_blockers:
                report = AtlasReadinessReport(
                    run_id=run.run_id,
                    agent_id=resolved_agent_id,
                    before_scores=[],
                    proposed_changes=AtlasProposedChanges(),
                    publish_recommendation="do_not_publish",
                    blockers=validation_blockers,
                    next_steps=["Fix AgentDocument validation errors before running readiness simulations."],
                    provider_invocations=[],
                    estimated_cost_usd=Decimal("0"),
                    observed_cost_usd=Decimal("0"),
                    score_breakdown={"run_score": 0.0, "blocking_case_count": len(validation_blockers)},
                )
                self._readiness_store.save_report(report, organization_id=organization_id)
                run = self._readiness_store.update_run(
                    run.run_id,
                    organization_id=organization_id,
                    state="failed",
                    blocker_codes=[blocker.code for blocker in validation_blockers],
                    error="candidate AgentDocument failed validation",
                    completed_at=_utcnow(),
                )
                self._event(run.run_id, "run_failed", {"blockers": [item.model_dump(mode="json") for item in validation_blockers]}, organization_id)
                return AtlasReadinessRunSummary(run=run, report=report)

            run = self._transition(run.run_id, "generating_cases", organization_id=organization_id)
            case_set = self._resolve_case_set(
                request,
                document=document,
                provider_policy=provider_policy,
                organization_id=organization_id,
                gateway=gateway,
                provider_invocations=provider_invocations,
            )
            self._readiness_store.save_case_set(case_set, run_id=run.run_id, organization_id=organization_id)
            run = self._readiness_store.update_run(
                run.run_id,
                organization_id=organization_id,
                case_set_id=case_set.case_set_id,
                document_hash=doc_hash,
                policy_hash=self._policy_hash(request),
                provider_config_hash=self._provider_config_hash(provider_policy),
            )
            self._event(
                run.run_id,
                "case_generated",
                {"case_set_id": case_set.case_set_id, "case_count": len(case_set.cases)},
                organization_id,
            )

            run = self._transition(run.run_id, "running_simulations", organization_id=organization_id)
            traces: list[AtlasReadinessTrace] = []
            for case in case_set.cases:
                cancellation_token.throw_if_cancelled() if cancellation_token is not None else None
                self._check_deadline(deadline)
                trace = self._run_case(
                    document,
                    case,
                    agent_id=resolved_agent_id or "atlas_readiness_agent",
                    agent_name=self._agent_name(resolved_agent_id, organization_id=organization_id),
                )
                traces.append(trace)
                self._readiness_store.save_trace(run.run_id, trace, organization_id=organization_id)
                self._event(
                    run.run_id,
                    "simulation_finished",
                    {"case_id": case.case_id, "final_step_id": trace.final_step_id},
                    organization_id,
                )

            if request.voice_case_count > 0:
                run = self._transition(run.run_id, "running_voice_cases", organization_id=organization_id)
                voice_harness = self._resolve_voice_harness(provider_policy)
                for case, trace in zip(case_set.cases[: request.voice_case_count], traces[: request.voice_case_count]):
                    cancellation_token.throw_if_cancelled() if cancellation_token is not None else None
                    self._check_deadline(deadline)
                    voice_result = voice_harness.run_voice_case(
                        run_id=run.run_id,
                        case=case,
                        trace=trace,
                    )
                    trace.voice_metrics.update(voice_result.metrics)
                    artifacts = self._export_voice_artifacts(
                        run_id=run.run_id,
                        artifacts=voice_result.artifacts,
                        blob_payloads=voice_result.blob_payloads,
                        organization_id=organization_id,
                    )
                    self._enforce_real_voice_io(request=request, case=case, trace=trace, artifacts=artifacts)
                    # AR-4.3: the trace was persisted before the voice phase, so
                    # re-save it now that voice_metrics are populated — otherwise
                    # the stored snapshot can't explain the voice_reliability
                    # score, breaking run reproducibility.
                    self._readiness_store.save_trace(run.run_id, trace, organization_id=organization_id)
                    for artifact in artifacts:
                        self._readiness_store.save_voice_artifact(artifact, organization_id=organization_id)
                    self._event(
                        run.run_id,
                        "voice_case_finished",
                        {"case_id": trace.case_id, "provider": str(trace.voice_metrics.get("provider") or "unknown")},
                        organization_id,
                    )

            run = self._transition(run.run_id, "extracting_traces", organization_id=organization_id)
            for trace in traces:
                self._event(run.run_id, "trace_extracted", {"case_id": trace.case_id, "trace_id": trace.trace_id}, organization_id)

            run = self._transition(run.run_id, "scoring", organization_id=organization_id)
            scores = [self._score_case(case, trace) for case, trace in zip(case_set.cases, traces)]
            for score in scores:
                self._readiness_store.save_score(run.run_id, score, organization_id=organization_id)
                self._event(run.run_id, "score_recorded", {"case_id": score.case_id, "case_score": score.case_score, "passed": score.passed}, organization_id)

            proposed_changes = AtlasProposedChanges()
            if request.scope == "fix":
                run = self._transition(run.run_id, "proposing_deltas", organization_id=organization_id)
                proposed_changes = self._propose_deltas(
                    run_id=run.run_id,
                    agent_id=resolved_agent_id or request.agent_id or "atlas_readiness_agent",
                    document=document,
                    document_hash=doc_hash,
                    cases=case_set.cases,
                    scores=scores,
                    organization_id=organization_id,
                    provider_policy=provider_policy,
                    gateway=gateway,
                    provider_invocations=provider_invocations,
                )
                if any(getattr(proposed_changes, field) for field in AtlasProposedChanges.model_fields):
                    if resolved_agent_id is not None and resolved_version_id is not None:
                        self._readiness_store.acquire_apply_lock(
                            run.run_id,
                            agent_id=resolved_agent_id,
                            draft_version_id=resolved_version_id,
                            expires_at=_utcnow() + timedelta(seconds=request.paused_run_ttl_seconds),
                            organization_id=organization_id,
                        )
                        apply_lock_acquired = True
                        self._event(
                            run.run_id,
                            "apply_lock_acquired",
                            {"agent_id": resolved_agent_id, "draft_version_id": resolved_version_id},
                            organization_id,
                        )
                    atlas_session = self._ensure_atlas_session(
                        run=run,
                        agent_id=resolved_agent_id or request.agent_id,
                        agent_version_id=resolved_version_id,
                        organization_id=organization_id,
                        user_id=user_id,
                    )
                    self._atlas_store.replace_proposed_changes(
                        atlas_session.session_id,
                        proposed_changes,
                        organization_id=organization_id,
                    )
                    run = self._readiness_store.update_run(
                        run.run_id,
                        organization_id=organization_id,
                        atlas_session_id=atlas_session.session_id,
                    )
                    self._event(
                        run.run_id,
                        "delta_proposed",
                        {
                            "atlas_session_id": atlas_session.session_id,
                            "delta_count": len(proposed_changes.step_deltas),
                        },
                        organization_id,
                    )

            run = self._transition(run.run_id, "writing_report", organization_id=organization_id)
            report = self._build_report(
                run_id=run.run_id,
                agent_id=resolved_agent_id,
                scores=scores,
                proposed_changes=proposed_changes,
                provider_policy=provider_policy,
                gateway=gateway,
                request=request,
                orchestration_plan=orchestration_plan,
                provider_invocations=provider_invocations,
            )
            for provider_metadata in report.provider_invocations:
                self._readiness_store.save_provider_invocation(
                    run.run_id,
                    provider_metadata,
                    organization_id=organization_id,
                )
            report = self._export_report_artifact(report, organization_id=organization_id)
            self._readiness_store.save_report(report, organization_id=organization_id)
            self._event(run.run_id, "report_written", {"publish_recommendation": report.publish_recommendation}, organization_id)
            final_state = "awaiting_review" if proposed_changes.step_deltas else "completed"
            run = self._readiness_store.update_run(
                run.run_id,
                organization_id=organization_id,
                state=final_state,
                completed_at=_utcnow() if final_state == "completed" else None,
            )
            self._event(
                run.run_id,
                "run_completed" if final_state == "completed" else "review_required",
                {"state": final_state},
                organization_id,
            )
            return AtlasReadinessRunSummary(run=run, case_set=case_set, report=report)
        except AtlasReadinessRunTerminal:
            # A concurrent cancel (or other terminal write) already finalized
            # this run; leave the terminal record untouched.
            if apply_lock_acquired:
                self._readiness_store.release_apply_lock(run.run_id, organization_id=organization_id)
            raise
        except Exception as exc:
            if apply_lock_acquired:
                self._readiness_store.release_apply_lock(run.run_id, organization_id=organization_id)
            is_cancel = isinstance(exc, AtlasReadinessCancelled)
            # Budget/timeout exhaustion is a failure with a specific blocker
            # code so operators can tell a stopped run apart from a crash.
            blocker_code = None
            if isinstance(exc, AtlasReadinessBudgetExceeded):
                blocker_code = "budget_exceeded"
            elif isinstance(exc, AtlasReadinessTimeoutExceeded):
                blocker_code = "timeout_exceeded"
            try:
                run = self._readiness_store.update_run(
                    run.run_id,
                    organization_id=organization_id,
                    state="cancelled" if is_cancel else "failed",
                    blocker_codes=(
                        sorted(set(run.blocker_codes + [blocker_code])) if blocker_code else None
                    ),
                    error=str(exc),
                    completed_at=_utcnow(),
                )
            except AtlasReadinessRunTerminal:
                # cancel_run finalized the record between the failure and here.
                run = self._readiness_store.get_run(run.run_id, organization_id=organization_id) or run
            self._event(
                run.run_id,
                "run_cancelled" if run.state == "cancelled" else "run_failed",
                {"error": str(exc), **({"blocker": blocker_code} if blocker_code else {})},
                organization_id,
            )
            raise
        finally:
            self._unregister_cancellation_token(run.run_id)

    def get_run_summary(self, run_id: str, *, organization_id: str) -> AtlasReadinessRunSummary | None:
        run = self._readiness_store.get_run(run_id, organization_id=organization_id)
        if run is None:
            return None
        case_set = (
            self._readiness_store.get_case_set(run.case_set_id, organization_id=organization_id)
            if run.case_set_id
            else None
        )
        report = self._readiness_store.get_report(run_id, organization_id=organization_id)
        return AtlasReadinessRunSummary(run=run, case_set=case_set, report=report)

    def provider_health(
        self,
        *,
        provider_policy: AtlasReadinessProviderPolicy | None = None,
    ) -> AtlasReadinessProviderHealth:
        policy = provider_policy or self._default_provider_policy
        gateway = AtlasModelGateway(provider_policy=policy)
        health = gateway.health()
        warnings = list(health.get("warnings") or [])
        if self._artifact_store is None:
            warnings.append("artifact_store_not_configured")
        voice_harness_name = self._voice_harness.__class__.__name__ if self._voice_harness is not None else (
            "DeterministicAtlasVoiceHarness" if policy == "deterministic" else "GoogleAtlasVoiceHarness"
        )
        return AtlasReadinessProviderHealth(
            provider_policy=policy,
            gemini_configured=bool(health["gemini_configured"]),
            anthropic_configured=bool(health["anthropic_configured"]),
            artifact_store_configured=self._artifact_store is not None,
            voice_harness=voice_harness_name,
            warnings=warnings,
        )

    def _should_use_provider_roles(self, provider_policy: AtlasReadinessProviderPolicy) -> bool:
        return provider_policy != "deterministic"

    def _plan_with_adk_envelope(
        self,
        *,
        request: AtlasReadinessRunRequest,
        gateway: AtlasModelGateway,
        provider_policy: AtlasReadinessProviderPolicy,
        provider_invocations: list[AtlasProviderInvocationMetadata],
    ) -> dict[str, object]:
        default_tools = [
            "get_agent_document" if request.agent_id else "generate_agent_document_draft",
            "generate_evaluation_cases",
            "run_simulation",
            "score_trace",
            "create_publish_report",
        ]
        if request.scope == "fix":
            default_tools.insert(4, "propose_agent_document_deltas")
        if request.voice_case_count > 0:
            default_tools.insert(3, "run_voice_simulation")
        fallback = _AtlasOrchestrationPlanPayload(
            objective=f"{request.scope} Atlas readiness for {request.agent_id or 'workflow brief'}",
            tool_calls=default_tools,
            next_safe_action="run_fix_review" if request.scope == "fix" else "run_validation_report",
        )
        if not self._should_use_provider_roles(provider_policy):
            return fallback.model_dump(mode="json")
        payload, metadata = gateway.generate_structured(
            role="orchestrator",
            schema_name="AtlasADKBoundedPlan",
            prompt=(
                "Plan a bounded Atlas readiness run. Choose only these tool names: "
                f"{default_tools}. Do not include side effects outside the typed review flow."
            ),
            response_model=_AtlasOrchestrationPlanPayload,
            trace_context={"deterministic_response": fallback.model_dump(mode="json")},
            temperature_policy="deterministic",
        )
        provider_invocations.append(metadata)
        return payload.model_dump(mode="json")

    def rerun(self, run_id: str, *, organization_id: str, user_id: str | None) -> AtlasReadinessRunSummary:
        run = self._readiness_store.get_run(run_id, organization_id=organization_id)
        if run is None:
            raise KeyError(f"unknown atlas readiness run: {run_id}")
        request = run.request.model_copy(update={"reuse_case_set_id": run.case_set_id})
        return self.start_run(request, organization_id=organization_id, user_id=user_id)

    def cancel_run(
        self,
        run_id: str,
        *,
        organization_id: str,
        reason: str = "operator_cancelled",
    ) -> AtlasReadinessRunSummary:
        run = self._readiness_store.get_run(run_id, organization_id=organization_id)
        if run is None:
            raise KeyError(f"unknown atlas readiness run: {run_id}")
        if run.state in {"completed", "failed", "cancelled"}:
            raise ValueError(f"cannot cancel terminal atlas readiness run in state {run.state}")
        # Signal the in-flight worker (if any) first, so it stops at its next
        # checkpoint instead of continuing to spend providers/voice/MCP calls.
        token = self._get_cancellation_token(run_id)
        if token is not None and hasattr(token, "cancel"):
            token.cancel()  # type: ignore[attr-defined]
        run = self._readiness_store.update_run(
            run_id,
            organization_id=organization_id,
            state="cancelled",
            blocker_codes=sorted(set(run.blocker_codes + [reason])),
            error=reason,
            completed_at=_utcnow(),
        )
        self._readiness_store.release_apply_lock(run_id, organization_id=organization_id)
        self._event(run_id, "run_cancelled", {"reason": reason}, organization_id)
        case_set = (
            self._readiness_store.get_case_set(run.case_set_id, organization_id=organization_id)
            if run.case_set_id
            else None
        )
        report = self._readiness_store.get_report(run_id, organization_id=organization_id)
        return AtlasReadinessRunSummary(run=run, case_set=case_set, report=report)

    # In-flight (non-terminal, non-paused) states a crashed worker can leave
    # stranded forever; a sweep marks them failed so they don't poll-hang.
    _SWEEPABLE_ACTIVE_STATES = [
        "created",
        "resolving_document",
        "generating_cases",
        "running_simulations",
        "running_voice_cases",
        "extracting_traces",
        "scoring",
        "proposing_deltas",
        "writing_report",
    ]

    @staticmethod
    def _run_scope(run: AtlasReadinessRun) -> str | AtlasSystemScope:
        """Store scope for a run swept under system authority (F17).

        A legacy run row without an organization can only be touched with the
        explicit system sentinel — never an implicit unscoped ``None``.
        """
        return run.organization_id if run.organization_id is not None else ATLAS_SYSTEM_SCOPE

    def sweep_stale_runs(
        self,
        *,
        stuck_after_seconds: int = 1800,
        organization_id: str | AtlasSystemScope = ATLAS_SYSTEM_SCOPE,
    ) -> dict[str, int]:
        """Recover runs a crash or TTL expiry left non-terminal (AR-4.4).

        Intended to be driven by a worker tick / startup hook, hence the
        cross-tenant default scope (F17: deliberate, sentinel-marked — not an
        implicit ``None``):

        * a run stuck in an active state past ``stuck_after_seconds`` (its
          worker died mid-run) is marked ``failed`` with ``run_stuck``;
        * an ``awaiting_review`` run whose apply lock has expired (its pause TTL
          elapsed) is ``cancelled`` with ``pause_ttl_expired`` and its lock
          released.

        Returns a small counter dict for telemetry.
        """
        now = _utcnow()
        cutoff = now - timedelta(seconds=stuck_after_seconds)
        result = {"stuck_failed": 0, "paused_expired": 0}

        for run in self._readiness_store.list_runs_in_states(
            self._SWEEPABLE_ACTIVE_STATES, updated_before=cutoff, organization_id=organization_id
        ):
            try:
                self._readiness_store.update_run(
                    run.run_id,
                    organization_id=self._run_scope(run),
                    state="failed",
                    blocker_codes=sorted(set(run.blocker_codes + ["run_stuck"])),
                    error="run was stuck in a non-terminal state and swept",
                    completed_at=now,
                )
                self._event(run.run_id, "run_failed", {"blocker": "run_stuck"}, self._run_scope(run))
                result["stuck_failed"] += 1
            except AtlasReadinessRunTerminal:
                continue  # finalized concurrently

        for run in self._readiness_store.list_runs_in_states(
            ["awaiting_review"], organization_id=organization_id
        ):
            # Paused run whose apply lock has expired → its pause TTL elapsed.
            if run.agent_id and run.agent_version_id and self._readiness_store.has_active_apply_lock(
                run.agent_id, run.agent_version_id, organization_id=self._run_scope(run)
            ):
                continue  # still within the pause TTL
            try:
                self._readiness_store.update_run(
                    run.run_id,
                    organization_id=self._run_scope(run),
                    state="cancelled",
                    blocker_codes=sorted(set(run.blocker_codes + ["pause_ttl_expired"])),
                    error="paused readiness run exceeded its TTL",
                    completed_at=now,
                )
                self._readiness_store.release_apply_lock(run.run_id, organization_id=self._run_scope(run))
                self._event(run.run_id, "run_cancelled", {"reason": "pause_ttl_expired"}, self._run_scope(run))
                result["paused_expired"] += 1
            except AtlasReadinessRunTerminal:
                continue

        return result

    def _check_budget(self, request: AtlasReadinessRunRequest, provider_policy: AtlasReadinessProviderPolicy) -> None:
        estimate = Decimal("0") if provider_policy == "deterministic" else Decimal("0.05") * Decimal(request.case_limit)
        if request.voice_case_count and provider_policy != "deterministic":
            estimate += Decimal("0.02") * Decimal(request.voice_case_count)
        if provider_policy != "deterministic" and request.max_estimated_cost_usd is None:
            raise AtlasReadinessBudgetExceeded(
                "budget_exceeded: non-deterministic readiness runs require max_estimated_cost_usd"
            )
        if request.max_estimated_cost_usd is not None and estimate > request.max_estimated_cost_usd:
            raise AtlasReadinessBudgetExceeded(
                "budget_exceeded: estimated readiness run cost exceeds request ceiling"
            )

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        """Raise if the run has exceeded its wall-clock budget (AR-4.1).

        Called at every node boundary so a long run stops promptly rather than
        running for minutes past its declared ``max_wall_clock_seconds``.
        """
        if time.monotonic() > deadline:
            raise AtlasReadinessTimeoutExceeded(
                "timeout_exceeded: readiness run exceeded max_wall_clock_seconds"
            )

    def _resolve_voice_harness(self, provider_policy: AtlasReadinessProviderPolicy) -> AtlasVoiceHarness:
        if self._voice_harness is not None:
            return self._voice_harness
        if provider_policy == "deterministic":
            return DeterministicAtlasVoiceHarness()
        return GoogleAtlasVoiceHarness.from_platform_voice_provider()

    def _transition(self, run_id: str, state: str, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessRun:
        run = self._readiness_store.update_run(run_id, organization_id=organization_id, state=state)  # type: ignore[arg-type]
        self._event(run_id, "node_started", {"state": state}, organization_id)
        return run

    def _event(self, run_id: str, event_type: str, payload: dict[str, object], organization_id: str | AtlasSystemScope) -> None:
        self._readiness_store.append_event(
            AtlasReadinessEvent(
                event_id=new_atlas_readiness_event_id(),
                run_id=run_id,
                sequence_number=0,
                type=event_type,
                payload=payload,
                created_at=_utcnow(),
            ),
            organization_id=organization_id,
        )

    def _resolve_document(
        self,
        request: AtlasReadinessRunRequest,
        *,
        organization_id: str | None,
        gateway: AtlasModelGateway | None = None,
        provider_policy: AtlasReadinessProviderPolicy = "deterministic",
        provider_invocations: list[AtlasProviderInvocationMetadata] | None = None,
    ) -> tuple[AgentDocument, str | None, str | None]:
        if request.agent_id is None:
            if not request.workflow_brief:
                raise ValueError("agent_id or workflow_brief is required")
            document = self._document_from_brief(
                request.workflow_brief,
                gateway=gateway,
                provider_policy=provider_policy,
                provider_invocations=provider_invocations,
            )
            return document, None, None
        if request.agent_version_id:
            snapshot = self._agent_registry.get_version_snapshot(request.agent_version_id, organization_id=organization_id)
            if snapshot.agent_id != request.agent_id:
                raise ValueError("agent_version_id does not belong to agent_id")
            if snapshot.agent_document is None:
                # AR-4.5 (F7): the caller asked to evaluate a specific version;
                # silently falling back to draft/published would store a verdict
                # for a document they didn't request.
                raise ValueError("agent_version_id has no stored document")
            return snapshot.agent_document, snapshot.agent_id, snapshot.version_id
        target = "draft"
        try:
            version_id = self._agent_registry.resolve_version_id(request.agent_id, target=target, organization_id=organization_id)
            return (
                self._agent_registry.get_agent_document(request.agent_id, target=target, organization_id=organization_id),
                request.agent_id,
                version_id,
            )
        except KeyError:
            version_id = self._agent_registry.resolve_version_id(request.agent_id, target="published", organization_id=organization_id)
            return (
                self._agent_registry.get_agent_document(request.agent_id, target="published", organization_id=organization_id),
                request.agent_id,
                version_id,
            )

    def _deterministic_document_from_brief(self, brief: str) -> AgentDocument:
        from .agent_document import Scenario, Step, StepCompletion

        return AgentDocument(
            start_scenario_id="draft",
            scenarios=[
                Scenario(
                    id="draft",
                    name="Draft workflow",
                    start_step_id="start",
                    steps=[
                        Step(
                            id="start",
                            name="Start",
                            say=brief[:300] or "How can I help?",
                            completion=StepCompletion(
                                disposition="completed",
                                summary="Draft workflow completed for readiness evaluation.",
                            ),
                        )
                    ],
                )
            ],
        )

    def _document_from_brief(
        self,
        brief: str,
        *,
        gateway: AtlasModelGateway | None = None,
        provider_policy: AtlasReadinessProviderPolicy = "deterministic",
        provider_invocations: list[AtlasProviderInvocationMetadata] | None = None,
    ) -> AgentDocument:
        fallback_document = self._deterministic_document_from_brief(brief)
        if gateway is None or not self._should_use_provider_roles(provider_policy):
            return fallback_document
        safe_brief = self._provider_prompt_text(brief)
        understanding, understanding_metadata = gateway.generate_structured(
            role="workflow_understanding",
            schema_name="AtlasWorkflowUnderstanding",
            prompt=(
                "Understand this customer-support workflow brief for a deterministic "
                f"Ruhu AgentDocument. Brief:\n{safe_brief}"
            ),
            response_model=_AtlasWorkflowUnderstandingPayload,
            trace_context={
                "deterministic_response": {
                    "workflow_name": "Draft workflow",
                    "summary": safe_brief[:500],
                    "required_capabilities": ["answer_customer", "complete_workflow"],
                    "risk_tags": [],
                }
            },
            temperature_policy="deterministic",
        )
        if provider_invocations is not None:
            provider_invocations.append(understanding_metadata)
        draft, draft_metadata = gateway.generate_structured(
            role="draft_generator",
            schema_name="AtlasAgentDocumentDraft",
            prompt=(
                "Generate a minimal valid Ruhu AgentDocument JSON draft from the workflow "
                "understanding. The document must use deterministic scenarios, steps, "
                "transitions, and completion/handoff fields only.\n"
                f"Understanding:\n{self._provider_prompt_json(understanding.model_dump(mode='json'))}"
            ),
            response_model=_AtlasDraftDocumentPayload,
            trace_context={
                "deterministic_response": {
                    "agent_document": fallback_document.model_dump(mode="json"),
                    "summary": "Deterministic fallback draft generated from workflow brief.",
                }
            },
            temperature_policy="deterministic",
        )
        if provider_invocations is not None:
            provider_invocations.append(draft_metadata)
        try:
            candidate = AgentDocument.model_validate(draft.agent_document)
            validation = validate_agent_document(candidate)
            if any(issue.severity == "error" for issue in validation.issues):
                return fallback_document
            return candidate
        except (ValidationError, ValueError, TypeError):
            logger.warning("atlas draft generator returned invalid AgentDocument; using deterministic fallback")
            return fallback_document

    def _resolve_case_set(
        self,
        request: AtlasReadinessRunRequest,
        *,
        document: AgentDocument,
        provider_policy: AtlasReadinessProviderPolicy,
        organization_id: str | None,
        gateway: AtlasModelGateway | None = None,
        provider_invocations: list[AtlasProviderInvocationMetadata] | None = None,
    ) -> AtlasReadinessCaseSet:
        if request.reuse_case_set_id:
            case_set = self._readiness_store.get_case_set(request.reuse_case_set_id, organization_id=organization_id)
            if case_set is None:
                raise KeyError(f"unknown atlas readiness case set: {request.reuse_case_set_id}")
            return case_set
        cases = self._generate_cases(
            document=document,
            request=request,
            provider_policy=provider_policy,
            gateway=gateway,
            provider_invocations=provider_invocations,
        )
        return AtlasReadinessCaseSet(
            case_set_id=new_atlas_readiness_case_set_id(),
            organization_id=organization_id,
            agent_id=request.agent_id,
            seed=request.seed,
            provider_policy=provider_policy,
            cases=cases,
            created_at=_utcnow(),
        )

    def _generate_cases(
        self,
        *,
        document: AgentDocument,
        request: AtlasReadinessRunRequest,
        provider_policy: AtlasReadinessProviderPolicy = "deterministic",
        gateway: AtlasModelGateway | None = None,
        provider_invocations: list[AtlasProviderInvocationMetadata] | None = None,
    ) -> list[AtlasReadinessCase]:
        if self._demo_case_set or request.demo_case_set:
            logger.info("atlas readiness using the microfinance demo case set (demo_case_set=True)")
            fallback_cases = self._demo_cases(document=document, request=request)
        else:
            fallback_cases = self._document_derived_cases(document=document, request=request)
        if gateway is None or not self._should_use_provider_roles(provider_policy):
            return fallback_cases
        payload, metadata = gateway.generate_structured(
            role="case_generator",
            schema_name="AtlasReadinessCaseGeneration",
            prompt=(
                "Generate realistic readiness/evaluation cases for this deterministic "
                "Ruhu AgentDocument. Include African customer-support edge cases when "
                "the workflow is relevant. Cases must be valid AtlasReadinessCase JSON.\n"
                f"AgentDocument:\n{self._provider_prompt_json(document.model_dump(mode='json', by_alias=True))}"
            ),
            response_model=_AtlasCaseGenerationPayload,
            trace_context={
                "deterministic_response": {
                    "cases": [case.model_dump(mode="json") for case in fallback_cases],
                    "generation_summary": "Deterministic fallback cases.",
                }
            },
            temperature_policy="diverse",
        )
        if provider_invocations is not None:
            provider_invocations.append(metadata)
        cases = payload.cases[: request.case_limit]
        return cases or fallback_cases

    def _document_derived_cases(
        self, *, document: AgentDocument, request: AtlasReadinessRunRequest
    ) -> list[AtlasReadinessCase]:
        """Derive smoke cases from the agent's own document (AR-4.2).

        Each scenario yields a case that expects the runtime to end at a step
        the document actually defines (its start step or any completion step),
        so a well-formed agent is evaluated against its real structure rather
        than against unrelated microfinance expectations. This is a structural
        smoke check, not a full behavioural suite (LLM-driven case generation
        is a separate, larger piece of work).
        """
        rng = random.Random(request.seed)
        cases: list[AtlasReadinessCase] = []
        scenarios = list(document.scenarios)
        for index, scenario in enumerate(scenarios[: request.case_limit]):
            terminal_step_ids = [
                step.id for step in scenario.steps if getattr(step, "completion", None) is not None
            ]
            expected = list(dict.fromkeys([scenario.start_step_id, *terminal_step_ids]))
            channel = "voice" if index < request.voice_case_count else "chat"
            prompt = (request.workflow_brief or "").strip() or f"I need help with {scenario.name}."
            cases.append(
                AtlasReadinessCase(
                    case_id=new_atlas_readiness_case_id(),
                    test_profile=AtlasSyntheticTestProfile(
                        profile_id=f"profile_{scenario.id}",
                        locale="en-US",
                        channel=channel,  # type: ignore[arg-type]
                        language_style="plain",
                        emotional_state="neutral",
                        goal=f"Exercise scenario '{scenario.name}'.",
                    ),
                    scenario_summary=f"Document-derived smoke case for scenario '{scenario.name}'.",
                    utterances=[prompt],
                    expected_final_step_ids=expected,
                    required_trace_events=["start", "complete"],
                    voice_input=self._voice_input_for_case(request) if channel == "voice" else None,
                )
            )
        if not cases:
            # No scenarios at all — fall back to a single start-step smoke case.
            start_step = document.scenarios[0].start_step_id if document.scenarios else "start"
            cases.append(
                AtlasReadinessCase(
                    case_id=new_atlas_readiness_case_id(),
                    test_profile=AtlasSyntheticTestProfile(
                        profile_id=f"profile_{uuid4().hex}",
                        locale="en-US",
                        channel="chat",
                        language_style="plain",
                        emotional_state="neutral",
                        goal=request.workflow_brief or "Validate the workflow.",
                    ),
                    scenario_summary=request.workflow_brief or "Validate the workflow.",
                    utterances=[request.workflow_brief or "I need help"],
                    expected_final_step_ids=[start_step],
                    required_trace_events=["start", "complete"],
                    voice_input=self._voice_input_for_case(request) if request.voice_case_count > 0 else None,
                )
            )
        rng.shuffle(cases)
        return cases

    def _demo_cases(self, *, document: AgentDocument, request: AtlasReadinessRunRequest) -> list[AtlasReadinessCase]:
        source_cases = microfinance_repayment_readiness_cases()
        rng = random.Random(request.seed)
        rng.shuffle(source_cases)
        selected = source_cases[: request.case_limit]
        cases: list[AtlasReadinessCase] = []
        for index, old_case in enumerate(selected):
            channel = "voice" if index < request.voice_case_count else ("whatsapp" if "pidgin" in old_case.tags else "chat")
            cases.append(
                AtlasReadinessCase(
                    case_id=old_case.case_id or new_atlas_readiness_case_id(),
                    test_profile=AtlasSyntheticTestProfile(
                        profile_id=f"profile_{old_case.case_id}",
                        locale="en-NG",
                        channel=channel,  # type: ignore[arg-type]
                        language_style="pidgin" if "pidgin" in old_case.tags else "plain",
                        emotional_state="angry" if "angry" in old_case.persona.lower() else "concerned",
                        goal=old_case.description,
                        risk_tags=list(old_case.tags),
                    ),
                    scenario_summary=old_case.description,
                    utterances=list(old_case.utterances),
                    expected_final_step_ids=list(old_case.expected_final_step_ids),
                    expected_facts=dict(old_case.expected_final_facts),
                    forbidden_reply_terms=list(old_case.forbidden_reply_terms),
                    required_trace_events=["start", "complete"],
                    voice_input=self._voice_input_for_case(request) if channel == "voice" else None,
                )
            )
        if not cases:
            start_step = document.scenarios[0].start_step_id if document.scenarios else "start"
            cases.append(
                AtlasReadinessCase(
                    case_id=new_atlas_readiness_case_id(),
                    test_profile=AtlasSyntheticTestProfile(
                        profile_id=f"profile_{uuid4().hex}",
                        locale="en-US",
                        channel="chat",
                        language_style="plain",
                        emotional_state="neutral",
                        goal=request.workflow_brief or "Validate the workflow.",
                    ),
                    scenario_summary=request.workflow_brief or "Validate the workflow.",
                    utterances=[request.workflow_brief or "I need help"],
                    expected_final_step_ids=[start_step],
                    required_trace_events=["start", "complete"],
                    voice_input=self._voice_input_for_case(request) if request.voice_case_count > 0 else None,
                )
            )
        return cases

    def _voice_input_for_case(self, request: AtlasReadinessRunRequest) -> dict[str, object]:
        voice_input: dict[str, object] = {"mode": "deterministic_stub"}
        if request.voice_audio_uri:
            voice_input["audio_uri"] = request.voice_audio_uri
            voice_input["mode"] = "google_stt_audio_uri"
        if request.voice_language:
            voice_input["language"] = request.voice_language
        return voice_input

    def _run_case(
        self,
        document: AgentDocument,
        case: AtlasReadinessCase,
        *,
        agent_id: str,
        agent_name: str,
    ) -> AtlasReadinessTrace:
        run = simulate_transcript(
            document,
            case.utterances,
            conversation_id=f"atlas-readiness:{case.case_id}:{uuid4().hex}",
            interpreter=self._interpreter,
            agent_id=agent_id,
            agent_name=agent_name,
        )
        return self._trace_from_simulation(run, case_id=case.case_id)

    def _trace_from_simulation(self, run: SimulationRun, *, case_id: str) -> AtlasReadinessTrace:
        replies = [
            message.text
            for turn in run.turns
            for message in turn.emitted_messages
            if message.text
        ]
        step_path: list[str] = []
        tool_calls: list[dict[str, object]] = []
        handoff_decisions: list[dict[str, object]] = []
        for turn in run.turns:
            if not step_path or step_path[-1] != turn.step_before:
                step_path.append(turn.step_before)
            if turn.step_after != step_path[-1]:
                step_path.append(turn.step_after)
            tool_calls.extend([call.model_dump(mode="json") for call in turn.tool_calls])
            action = turn.chosen_action.model_dump(mode="json")
            if action.get("kind") == "handoff":
                handoff_decisions.append(action)
        return AtlasReadinessTrace(
            case_id=case_id,
            conversation_id=run.start.conversation_id,
            final_step_id=run.final_step_id,
            completion_status=getattr(run.turns[-1].chosen_action, "kind", None) if run.turns else None,
            step_path=step_path,
            extracted_facts=dict(run.final_facts),
            tool_calls=tool_calls,
            replies=replies,
            handoff_decisions=handoff_decisions,
        )

    def _score_case(self, case: AtlasReadinessCase, trace: AtlasReadinessTrace) -> AtlasReadinessScore:
        failures: list[str] = []
        blockers: list[str] = []
        containment_score = 1.0
        if case.expected_final_step_ids and trace.final_step_id not in case.expected_final_step_ids:
            containment_score = 0.0
            failures.append(f"expected final step in {case.expected_final_step_ids}, got {trace.final_step_id!r}")
            blockers.append("completion")
        lower_replies = "\n".join(trace.replies).lower()
        forbidden_hits = [term for term in case.forbidden_reply_terms if term.lower() in lower_replies]
        safety_score = 1.0 if not forbidden_hits else 0.0
        for term in forbidden_hits:
            failures.append(f"forbidden reply term appeared: {term!r}")
            blockers.append("safety")
        traceability_score = 1.0
        fact_failures = self._fact_failures(case, trace)
        if fact_failures:
            traceability_score = max(0.0, 1.0 - 0.35 * len(fact_failures))
            failures.extend(fact_failures)
            blockers.append("traceability")
        # AR-4.3: a trace that didn't produce the required events (e.g. never
        # reached a terminal action) must not score traceability 1.0 just
        # because its facts happened to match — that's a fail-open gap.
        missing_events = [
            event for event in case.required_trace_events if event not in self._trace_events(trace)
        ]
        if missing_events:
            traceability_score = min(traceability_score, 0.5)
            failures.append(f"missing required trace events: {missing_events}")
            if "traceability" not in blockers:
                blockers.append("traceability")
        operational_score = 1.0
        if "tool_failure" in case.test_profile.risk_tags and not trace.tool_calls:
            operational_score = 0.75
            failures.append("tool-failure scenario has no tool-call or fallback trace evidence")
        trajectory_score = 1.0 if len(trace.tool_calls) <= 5 else 0.7
        voice_score = None
        if case.voice_input is not None:
            voice_score = 1.0 if trace.voice_metrics.get("tts_artifact_generated", False) else 0.6
        improvement_score = 1.0 if not failures or self._can_map_failures_to_deltas(failures) else 0.4
        case_score = self._weighted_case_score(
            containment=containment_score,
            safety=safety_score,
            traceability=traceability_score,
            operational=operational_score,
            trajectory=trajectory_score,
            improvement=improvement_score,
            voice=voice_score,
        )
        category_blockers = []
        if containment_score < 0.80:
            category_blockers.append("containment")
        if safety_score < 0.95:
            category_blockers.append("safety")
        if traceability_score < 0.85:
            category_blockers.append("traceability")
        if operational_score < 0.80:
            category_blockers.append("operational_readiness")
        if trajectory_score < 0.85:
            category_blockers.append("trajectory")
        if voice_score is not None and voice_score < 0.80:
            category_blockers.append("voice_reliability")
        # AR-4.3: the rubric defines an improvement-potential blocker below 0.70
        # (failures that can't be mapped to fixable deltas).
        if improvement_score < 0.70:
            category_blockers.append("improvement_potential")
        return AtlasReadinessScore(
            case_id=case.case_id,
            passed=not category_blockers and not blockers,
            score_source="deterministic",
            containment_score=containment_score,
            safety_score=safety_score,
            traceability_score=round(traceability_score, 4),
            voice_reliability_score=voice_score,
            operational_readiness_score=operational_score,
            improvement_potential_score=improvement_score,
            trajectory_score=trajectory_score,
            case_score=case_score,
            failures=failures,
            blockers=sorted(set([*blockers, *category_blockers])),
        )

    @staticmethod
    def _trace_events(trace: AtlasReadinessTrace) -> set[str]:
        """Derive the set of trace events a run produced (AR-4.3).

        The trace doesn't carry an explicit event log, so events are inferred:
        ``start`` once the run took at least one step, ``complete`` once the
        kernel recorded a terminal action (rather than stalling with no
        decision).
        """
        events: set[str] = set()
        if trace.step_path:
            events.add("start")
        if trace.completion_status:
            events.add("complete")
        return events

    @staticmethod
    def _normalize_fact_value(value: object) -> object:
        """Normalize a fact value for `capture_normalized` comparison.

        Scalars are compared as normalized strings (so an expected ``"5000"``
        matches a captured ``5000``); dicts/lists normalize element-wise.
        """
        if isinstance(value, dict):
            return {str(k): AtlasReadinessService._normalize_fact_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [AtlasReadinessService._normalize_fact_value(v) for v in value]
        if value is None:
            return None
        return str(value).strip().lower()

    @classmethod
    def _facts_match_normalized(cls, expected: object, actual: object) -> bool:
        # A scalar expectation against a typed capture (e.g. money/contact
        # captured as {"value": 5000, "currency": "NGN"}) compares to the
        # captured ``value`` rather than the whole structure.
        if not isinstance(expected, dict) and isinstance(actual, dict) and "value" in actual:
            return cls._normalize_fact_value(expected) == cls._normalize_fact_value(actual["value"])
        return cls._normalize_fact_value(expected) == cls._normalize_fact_value(actual)

    def _fact_failures(self, case: AtlasReadinessCase, trace: AtlasReadinessTrace) -> list[str]:
        failures: list[str] = []
        for key, expected in case.expected_facts.items():
            actual = trace.extracted_facts.get(key)
            if case.fact_comparison_policy == "subset" and isinstance(expected, dict) and isinstance(actual, dict):
                for subkey, subexpected in expected.items():
                    if actual.get(subkey) != subexpected:
                        failures.append(f"expected fact {key}.{subkey}={subexpected!r}, got {actual.get(subkey)!r}")
            elif case.fact_comparison_policy == "capture_normalized":
                # AR-4.3: capture_normalized (the default) previously fell
                # through to raw equality. Normalize both sides through the
                # capture shape before comparing.
                if not self._facts_match_normalized(expected, actual):
                    failures.append(f"expected fact {key}={expected!r}, got {actual!r}")
            elif actual != expected:
                failures.append(f"expected fact {key}={expected!r}, got {actual!r}")
        return failures

    def _weighted_case_score(
        self,
        *,
        containment: float,
        safety: float,
        traceability: float,
        operational: float,
        trajectory: float,
        improvement: float,
        voice: float | None,
    ) -> float:
        if voice is None:
            score = (
                0.20 * containment
                + 0.20 * safety
                + 0.20 * traceability
                + 0.15 * operational
                + 0.15 * trajectory
                + 0.10 * improvement
            )
        else:
            score = (
                0.20 * containment
                + 0.20 * safety
                + 0.20 * traceability
                + 0.15 * operational
                + 0.15 * trajectory
                + 0.05 * voice
                + 0.05 * improvement
            )
        return round(max(0.0, min(1.0, score)), 4)

    def _can_map_failures_to_deltas(self, failures: list[str]) -> bool:
        return all(
            "expected final step" in failure
            or "forbidden reply term" in failure
            or "expected fact" in failure
            or "tool-failure" in failure
            for failure in failures
        )

    def _propose_deltas(
        self,
        *,
        run_id: str,
        agent_id: str,
        document: AgentDocument,
        document_hash: str,
        cases: list[AtlasReadinessCase],
        scores: list[AtlasReadinessScore],
        organization_id: str | None = None,
        provider_policy: AtlasReadinessProviderPolicy = "deterministic",
        gateway: AtlasModelGateway | None = None,
        provider_invocations: list[AtlasProviderInvocationMetadata] | None = None,
    ) -> AtlasProposedChanges:
        step_deltas: list[StepDelta] = []
        case_by_id = {case.case_id: case for case in cases}
        diagnosis = self._diagnose_failures(
            document=document,
            cases=cases,
            scores=scores,
            provider_policy=provider_policy,
            gateway=gateway,
            provider_invocations=provider_invocations,
        )
        rationale_by_case = diagnosis.patch_rationales
        start_scenario = next((item for item in document.scenarios if item.id == document.start_scenario_id), None)
        if start_scenario is None:
            return AtlasProposedChanges()
        start_step_id = start_scenario.start_step_id
        for score in scores:
            if score.passed:
                continue
            case = case_by_id.get(score.case_id)
            if case is None:
                continue
            for failure in score.failures:
                if "expected final step" in failure and case.expected_final_step_ids:
                    target_step_id = case.expected_final_step_ids[0]
                    step_deltas.append(
                        StepDelta(
                            agent_id=agent_id,
                            scenario_id=start_scenario.id,
                            step_id=start_step_id,
                            delta_id=f"atlas_readiness_delta_{uuid4().hex}",
                            operation="update",
                            change_type="add_step_transition",
                            payload={
                                "source_run_id": run_id,
                                "source_case_id": case.case_id,
                                "source_document_hash": document_hash,
                                "trace_diagnosis": diagnosis.diagnosis_summary,
                                "patch_rationale": rationale_by_case.get(case.case_id) or failure,
                                "transition": {
                                    "id": f"readiness_{case.case_id[:32]}",
                                    "when": {
                                        "kind": "outcome",
                                        "event": self._event_name_from_case(case),
                                        "description": case.scenario_summary,
                                    },
                                    "to_step_id": target_step_id,
                                    "priority": 20,
                                },
                            },
                            summary=(
                                rationale_by_case.get(case.case_id)
                                or f"Route readiness case '{case.case_id}' toward '{target_step_id}'."
                            )[:500],
                        )
                    )
                elif "forbidden reply term" in failure:
                    step_deltas.append(
                        StepDelta(
                            agent_id=agent_id,
                            scenario_id=start_scenario.id,
                            step_id=start_step_id,
                            delta_id=f"atlas_readiness_delta_{uuid4().hex}",
                            operation="update",
                            change_type="update_response_policy",
                            payload={
                                "source_run_id": run_id,
                                "source_case_id": case.case_id,
                                "source_document_hash": document_hash,
                                "trace_diagnosis": diagnosis.diagnosis_summary,
                                "patch_rationale": rationale_by_case.get(case.case_id) or failure,
                                "response_policy": {
                                    "direct_answer_prompt": (
                                        "Avoid unsafe commitments unless backed by a verified tool result. "
                                        "Escalate payment, legal, or account-state uncertainty to a human reviewer."
                                    ),
                                    "response_max_sentences": 3,
                                },
                            },
                            summary=(
                                rationale_by_case.get(case.case_id)
                                or f"Tighten response policy for readiness case '{case.case_id}'."
                            )[:500],
                        )
                    )
        valid, dropped = self._validate_step_deltas(document, step_deltas)
        if dropped:
            # AR-4.7: validate generated deltas against the document before they
            # are stored for review — a delta referencing a step the document
            # doesn't have (e.g. a transition to a non-existent target) must not
            # enter the review set.
            self._event(
                run_id,
                "invalid_deltas_dropped",
                {"dropped_count": dropped, "kept_count": len(valid)},
                organization_id,
            )
            logger.warning(
                "atlas readiness dropped invalid proposed deltas",
                extra={"run_id": run_id, "dropped": dropped},
            )
        return AtlasProposedChanges(step_deltas=valid)

    def _diagnose_failures(
        self,
        *,
        document: AgentDocument,
        cases: list[AtlasReadinessCase],
        scores: list[AtlasReadinessScore],
        provider_policy: AtlasReadinessProviderPolicy,
        gateway: AtlasModelGateway | None,
        provider_invocations: list[AtlasProviderInvocationMetadata] | None,
    ) -> _AtlasTraceDiagnosisPayload:
        failing = [score for score in scores if not score.passed]
        fallback = _AtlasTraceDiagnosisPayload(
            diagnosis_summary=(
                "Atlas found readiness failures in "
                f"{len(failing)} case(s); proposed deltas must stay in the typed review flow."
            ),
            patch_rationales={
                score.case_id: "; ".join(score.failures[:3]) or "Readiness case failed."
                for score in failing
            },
            next_safe_action="review_deltas" if failing else "publish_report",
        )
        if not failing or gateway is None or not self._should_use_provider_roles(provider_policy):
            return fallback
        payload, metadata = gateway.generate_structured(
            role="trace_repair_planner",
            schema_name="AtlasTraceDiagnosis",
            prompt=(
                "Diagnose readiness trace failures and produce concise patch rationale "
                "by case_id. Do not invent state outside the supplied scores. "
                "Recommend only typed AgentDocument deltas that will pass human review.\n"
                f"AgentDocument:\n{self._provider_prompt_json(document.model_dump(mode='json', by_alias=True))}\n"
                f"Cases:\n{self._provider_prompt_json([case.model_dump(mode='json') for case in cases])}\n"
                f"Scores:\n{self._provider_prompt_json([score.model_dump(mode='json') for score in scores])}"
            ),
            response_model=_AtlasTraceDiagnosisPayload,
            trace_context={"deterministic_response": fallback.model_dump(mode="json")},
            temperature_policy="deterministic",
        )
        if provider_invocations is not None:
            provider_invocations.append(metadata)
        rationale_payload, rationale_metadata = gateway.generate_structured(
            role="patch_rationale",
            schema_name="AtlasPatchRationale",
            prompt=(
                "Convert the trace diagnosis into human-reviewable patch rationale "
                "by case_id. Keep the rationale grounded in the supplied failures.\n"
                f"Trace diagnosis:\n{self._provider_prompt_json(payload.model_dump(mode='json'))}"
            ),
            response_model=_AtlasTraceDiagnosisPayload,
            trace_context={"deterministic_response": payload.model_dump(mode="json")},
            temperature_policy="deterministic",
        )
        if provider_invocations is not None:
            provider_invocations.append(rationale_metadata)
        return rationale_payload

    @staticmethod
    def _validate_step_deltas(
        document: AgentDocument, step_deltas: list[StepDelta]
    ) -> tuple[list[StepDelta], int]:
        """Keep only deltas whose document references resolve (AR-4.7).

        Returns (valid_deltas, dropped_count). Checks the delta's scenario/step
        exist and, for add_step_transition, that the transition target is a real
        step in the document.
        """
        scenarios_by_id = {scenario.id: scenario for scenario in document.scenarios}
        all_step_ids = {step.id for scenario in document.scenarios for step in scenario.steps}
        valid: list[StepDelta] = []
        dropped = 0
        for delta in step_deltas:
            scenario = scenarios_by_id.get(delta.scenario_id)
            if scenario is None or (delta.step_id is not None and delta.step_id not in all_step_ids):
                dropped += 1
                continue
            if delta.change_type == "add_step_transition":
                target = ((delta.payload.get("transition") or {}).get("to_step_id"))
                if target is not None and target not in all_step_ids:
                    dropped += 1
                    continue
            valid.append(delta)
        return valid, dropped

    def _event_name_from_case(self, case: AtlasReadinessCase) -> str:
        tags = case.test_profile.risk_tags
        for tag in tags:
            if tag not in {"pidgin", "missing_reference"}:
                return tag
        return case.case_id

    def _ensure_atlas_session(
        self,
        *,
        run: AtlasReadinessRun,
        agent_id: str | None,
        agent_version_id: str | None,
        organization_id: str | None,
        user_id: str | None,
    ) -> AtlasSession:
        if run.atlas_session_id:
            existing = self._atlas_store.get_session(run.atlas_session_id, organization_id=organization_id)
            if existing is not None:
                return existing
        if agent_id is None:
            raise ValueError("readiness fix proposals require an agent_id")
        now = _utcnow()
        return self._atlas_store.create_session(
            AtlasSession(
                session_id=new_atlas_session_id(),
                organization_id=organization_id,
                scope="validation",
                status="active",
                agent_id=agent_id,
                agent_version_id=agent_version_id,
                title="Atlas readiness fixes",
                summary=f"Readiness proposals for run {run.run_id}",
                created_by=user_id,
                created_at=now,
                updated_at=now,
            )
        )

    def _build_report(
        self,
        *,
        run_id: str,
        agent_id: str | None,
        scores: list[AtlasReadinessScore],
        proposed_changes: AtlasProposedChanges,
        provider_policy: AtlasReadinessProviderPolicy,
        gateway: AtlasModelGateway,
        request: AtlasReadinessRunRequest | None = None,
        orchestration_plan: dict[str, object] | None = None,
        provider_invocations: list[AtlasProviderInvocationMetadata] | None = None,
    ) -> AtlasReadinessReport:
        if not scores:
            run_score = 0.0
        else:
            run_score = round(sum(score.case_score for score in scores) / len(scores), 4)
        blocking_case_count = sum(1 for score in scores if score.blockers)
        critical_blocker_count = sum(
            1
            for score in scores
            if any(blocker in {"safety", "traceability", "completion", "handoff", "containment"} for blocker in score.blockers)
        )
        if run_score >= 0.90 and blocking_case_count == 0 and critical_blocker_count == 0:
            recommendation = "publish"
        elif run_score >= 0.75 and critical_blocker_count == 0:
            recommendation = "needs_review"
        else:
            recommendation = "do_not_publish"
        blockers = [
            AtlasBlocker(
                code=blocker,
                message=f"{count} readiness case(s) triggered {blocker}.",
                reference_ids=[score.case_id for score in scores if blocker in score.blockers],
            )
            for blocker, count in self._blocker_counts(scores).items()
        ]
        next_steps: list[str]
        if recommendation == "publish":
            next_steps = ["Review the report and proceed to publish if business signoff is complete."]
        elif proposed_changes.step_deltas:
            next_steps = ["Review readiness-generated deltas, request apply permission, apply, then rerun the same case set."]
        else:
            next_steps = ["Address blockers and rerun readiness against the same cached case set."]
        cloud_evidence = self._cloud_evidence_context(request=request)
        deterministic_narrative = _AtlasReportWriterPayload(
            executive_summary=(
                f"Atlas readiness scored {len(scores)} case(s) with recommendation {recommendation}."
            ),
            before_after_summary=(
                f"Run score {run_score}; {blocking_case_count} case(s) have blockers."
            ),
            residual_risks=[blocker.message for blocker in blockers],
            evidence_summary=(
                "Evidence is stored in the Atlas readiness report artifact when an artifact store is configured."
            ),
        )
        payload, provider_metadata = gateway.generate_structured(
            role="report_writer",
            schema_name="AtlasReadinessReportNarrative",
            prompt=(
                "Write a concise publish-readiness report narrative from deterministic "
                "score data. Do not override the deterministic publish recommendation. "
                "Mention residual risks and available evidence artifacts.\n"
                f"Recommendation: {recommendation}\n"
                f"Scores: {self._provider_prompt_json([score.model_dump(mode='json') for score in scores])}\n"
                f"Blockers: {self._provider_prompt_json([blocker.model_dump(mode='json') for blocker in blockers])}\n"
                f"Proposed changes: {self._provider_prompt_json(proposed_changes.model_dump(mode='json'))}\n"
                f"ADK bounded plan: {self._provider_prompt_json(orchestration_plan or {})}\n"
                f"Cloud evidence: {self._provider_prompt_json(cloud_evidence)}"
            ),
            response_model=_AtlasReportWriterPayload,
            trace_context=self.scrub_for_export(
                {"deterministic_response": deterministic_narrative.model_dump(mode="json")}
            ),
            temperature_policy="deterministic",
        )
        all_invocations = [*(provider_invocations or []), provider_metadata]
        return AtlasReadinessReport(
            run_id=run_id,
            agent_id=agent_id,
            before_scores=scores,
            proposed_changes=proposed_changes,
            publish_recommendation=recommendation,  # type: ignore[arg-type]
            blockers=blockers,
            next_steps=next_steps,
            provider_invocations=all_invocations,
            estimated_cost_usd=Decimal("0") if provider_policy == "deterministic" else None,
            observed_cost_usd=Decimal("0") if provider_policy == "deterministic" else None,
            narrative=payload.model_dump(mode="json"),
            evidence=cloud_evidence,
            score_breakdown={
                "run_score": run_score,
                "blocking_case_count": blocking_case_count,
                "critical_blocker_count": critical_blocker_count,
                "score_source": "deterministic",
                "adk_bounded_plan": orchestration_plan or {},
            },
        )

    def _cloud_evidence_context(self, *, request: AtlasReadinessRunRequest | None) -> dict[str, object]:
        if request is not None and not request.cloud_evidence:
            return {"enabled": False}
        return {
            "enabled": True,
            "hosting_target": "cloud_run",
            "storage_target": "google_cloud_storage" if self._artifact_store is not None else "not_configured",
            "logging_target": "cloud_logging",
            "google_cloud_project": (
                os.getenv("GOOGLE_CLOUD_PROJECT")
                or os.getenv("RUHU_ATLAS_GOOGLE_VERTEX_PROJECT")
                or os.getenv("VERTEX_AI_PROJECT")
                or ""
            ),
            "cloud_run_service": os.getenv("K_SERVICE") or os.getenv("RUHU_CLOUD_RUN_SERVICE") or "",
        }

    def _export_report_artifact(
        self,
        report: AtlasReadinessReport,
        *,
        organization_id: str | None,
    ) -> AtlasReadinessReport:
        if self._artifact_store is None:
            return report
        key_org = organization_id or "unknown-org"
        key = f"atlas-readiness/{key_org}/{report.run_id}/report.json"
        try:
            ref = self._artifact_store.put_blob(
                key=key,
                # Scrub before the report leaves for durable object storage —
                # score failure strings embed expected/actual fact values.
                content=json.dumps(
                    self._privacy.scrub(report.model_dump(mode="json")), sort_keys=True
                ).encode("utf-8"),
                content_type="application/json",
                metadata={
                    "run_id": report.run_id,
                    "agent_id": report.agent_id or "",
                    "artifact_type": "atlas_readiness_report",
                },
            )
        except Exception as exc:
            logger.warning(
                "atlas readiness report artifact export failed",
                extra={"run_id": report.run_id, "organization_id": organization_id, "error": str(exc)},
            )
            return report.model_copy(
                update={
                    "score_breakdown": {
                        **report.score_breakdown,
                        "artifact_export_error": str(exc) or exc.__class__.__name__,
                    }
                }
            )
        exported = report.model_copy(
            update={
                "score_breakdown": {
                    **report.score_breakdown,
                    "artifact_uri": ref.uri(),
                    "artifact_backend": ref.backend,
                    "artifact_size_bytes": ref.size_bytes,
                }
            }
        )
        self._event(report.run_id, "report_artifact_written", {"artifact_uri": ref.uri()}, organization_id)
        return exported

    def _export_voice_artifacts(
        self,
        *,
        run_id: str,
        artifacts: list,
        blob_payloads: dict[str, tuple[bytes, str]],
        organization_id: str | None,
    ) -> list:
        if self._artifact_store is None or not blob_payloads:
            return artifacts
        key_org = organization_id or "unknown-org"
        exported = []
        for artifact in artifacts:
            payload = blob_payloads.get(artifact.artifact_id)
            if payload is None:
                exported.append(artifact)
                continue
            content, content_type = payload
            key = f"atlas-readiness/{key_org}/{run_id}/voice/{artifact.case_id}/{artifact.artifact_id}"
            try:
                ref = self._artifact_store.put_blob(
                    key=key,
                    content=content,
                    content_type=content_type,
                    metadata={
                        "run_id": run_id,
                        "case_id": artifact.case_id,
                        "artifact_type": artifact.artifact_type,
                        "provider": artifact.provider,
                    },
                )
                exported_artifact = artifact.model_copy(
                    update={
                        "uri": ref.uri(),
                        "metadata": {
                            **artifact.metadata,
                            "artifact_backend": ref.backend,
                            "artifact_size_bytes": ref.size_bytes,
                        },
                    }
                )
                self._event(run_id, "voice_artifact_written", {"case_id": artifact.case_id, "artifact_uri": ref.uri()}, organization_id)
                exported.append(exported_artifact)
            except Exception as exc:
                logger.warning(
                    "atlas readiness voice artifact export failed",
                    extra={"run_id": run_id, "organization_id": organization_id, "artifact_id": artifact.artifact_id, "error": str(exc)},
                )
                exported.append(
                    artifact.model_copy(
                        update={
                            "metadata": {
                                **artifact.metadata,
                                "artifact_export_error": str(exc) or exc.__class__.__name__,
                            }
                        }
                    )
                )
        return exported

    def _enforce_real_voice_io(
        self,
        *,
        request: AtlasReadinessRunRequest,
        case: AtlasReadinessCase,
        trace: AtlasReadinessTrace,
        artifacts: list,
    ) -> None:
        if not request.require_real_voice_io:
            return
        voice_input = dict(case.voice_input or {})
        if not voice_input.get("audio_uri"):
            raise ValueError("real_voice_io_required: voice_audio_uri is required for strict Google voice readiness")
        if trace.voice_metrics.get("stt_fallback_reason"):
            raise ValueError(f"real_voice_io_required: STT fallback occurred ({trace.voice_metrics.get('stt_fallback_reason')})")
        if trace.voice_metrics.get("tts_fallback_reason"):
            raise ValueError(f"real_voice_io_required: TTS fallback occurred ({trace.voice_metrics.get('tts_fallback_reason')})")
        if not trace.voice_metrics.get("tts_artifact_generated"):
            raise ValueError("real_voice_io_required: TTS artifact was not generated")
        if not any(getattr(artifact, "artifact_type", None) == "tts_audio" and getattr(artifact, "uri", None) for artifact in artifacts):
            raise ValueError("real_voice_io_required: TTS audio artifact was not exported to the artifact store")

    def _blocker_counts(self, scores: list[AtlasReadinessScore]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for score in scores:
            for blocker in score.blockers:
                counts[blocker] = counts.get(blocker, 0) + 1
        return counts

    def _agent_name(self, agent_id: str | None, *, organization_id: str | None) -> str:
        if agent_id is None:
            return "Readiness Draft"
        try:
            return self._agent_registry.get_agent_registration(agent_id, organization_id=organization_id).name
        except Exception:
            return agent_id

    def _policy_hash(self, request: AtlasReadinessRunRequest) -> str:
        payload = request.model_dump_json()
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _provider_config_hash(self, provider_policy: AtlasReadinessProviderPolicy) -> str:
        return hashlib.sha256(provider_policy.encode("utf-8")).hexdigest()
