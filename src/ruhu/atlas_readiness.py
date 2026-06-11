from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .agent_document import AgentDocument
from .interpreter import SemanticInterpreter
from .schemas import SimulationRun
from .simulator import simulate_transcript


class AtlasReadinessCase(BaseModel):
    """One simulation case Atlas can run before publish."""

    case_id: str
    persona: str
    description: str
    utterances: list[str]
    expected_final_step_ids: list[str] = Field(default_factory=list)
    expected_final_facts: dict[str, object] = Field(default_factory=dict)
    forbidden_reply_terms: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class AtlasSimulationTrace(BaseModel):
    case_id: str
    final_step_id: str
    turn_count: int
    final_facts: dict[str, object]
    emitted_text: list[str] = Field(default_factory=list)
    step_path: list[str] = Field(default_factory=list)


class AtlasReadinessCaseScore(BaseModel):
    case_id: str
    passed: bool
    score: float
    failures: list[str] = Field(default_factory=list)
    trace: AtlasSimulationTrace


class AtlasReadinessPatchProposal(BaseModel):
    proposal_id: str
    case_id: str
    target: Literal["agent_document", "scenario", "step", "transition", "tool", "handoff", "evaluation"]
    summary: str
    rationale: str
    status: Literal["proposed", "applied", "skipped"] = "proposed"


class AtlasReadinessReport(BaseModel):
    agent_id: str
    agent_name: str
    before_scores: list[AtlasReadinessCaseScore]
    patch_proposals: list[AtlasReadinessPatchProposal] = Field(default_factory=list)
    after_scores: list[AtlasReadinessCaseScore] = Field(default_factory=list)
    publish_recommendation: Literal["publish", "do_not_publish"]
    summary: str

    @property
    def before_pass_rate(self) -> float:
        return _pass_rate(self.before_scores)

    @property
    def after_pass_rate(self) -> float | None:
        if not self.after_scores:
            return None
        return _pass_rate(self.after_scores)


PatchApplicator = Callable[[AgentDocument, list[AtlasReadinessPatchProposal]], AgentDocument]


def microfinance_repayment_readiness_cases() -> list[AtlasReadinessCase]:
    """Demo cases for the Google challenge microfinance repayment workflow."""

    return [
        AtlasReadinessCase(
            case_id="pidgin_payment_not_reflected",
            persona="Angry borrower using Nigerian Pidgin",
            description="Customer says repayment was made through a wallet but the loan balance still shows unpaid.",
            utterances=[
                "I paid through Opay but una still dey call me",
                "My repayment no reflect and I no get the reference now",
            ],
            expected_final_step_ids=["payment_dispute", "ticket_created", "handoff"],
            forbidden_reply_terms=["approved", "loan is cleared", "ignore the balance"],
            tags=["pidgin", "payment_dispute", "missing_reference"],
        ),
        AtlasReadinessCase(
            case_id="repayment_plan_request",
            persona="Salary earner asking for more time",
            description="Customer cannot repay today and asks for a repayment plan.",
            utterances=[
                "I need small time, salary never enter",
                "Can I pay next week instead?",
            ],
            expected_final_step_ids=["repayment_plan", "handoff", "ticket_created"],
            forbidden_reply_terms=["guaranteed", "approved extension", "no penalty"],
            tags=["pidgin", "repayment_plan"],
        ),
        AtlasReadinessCase(
            case_id="human_escalation_request",
            persona="Customer explicitly asks for a person",
            description="Customer asks to speak with a human after a confusing repayment issue.",
            utterances=["Transfer me to person abeg"],
            expected_final_step_ids=["handoff", "ticket_created"],
            tags=["handoff", "pidgin"],
        ),
        AtlasReadinessCase(
            case_id="tool_failure_recovery",
            persona="Borrower with duplicate payment concern",
            description="Customer reports a duplicate debit and the agent must not invent account state.",
            utterances=[
                "I was debited twice for the same loan repayment",
                "Check it and tell me if my account is now okay",
            ],
            expected_final_step_ids=["payment_dispute", "handoff", "ticket_created"],
            forbidden_reply_terms=["confirmed reversed", "definitely fixed", "money returned"],
            tags=["duplicate_payment", "tool_failure"],
        ),
    ]


def run_atlas_readiness_loop(
    agent_document: AgentDocument,
    *,
    cases: list[AtlasReadinessCase] | None = None,
    interpreter: SemanticInterpreter | None = None,
    agent_id: str = "atlas_demo_agent",
    agent_name: str = "Atlas Demo Agent",
    apply_recommended_patches: PatchApplicator | None = None,
) -> AtlasReadinessReport:
    """Run Atlas's build/test/diagnose/patch/rerun/report loop.

    This is the deterministic core that the hackathon MCP tools can wrap. It
    does not mutate production state; callers may provide an explicit patch
    applicator when they want to test approved changes against a copy.
    """

    selected_cases = cases or microfinance_repayment_readiness_cases()
    before_scores = [
        _run_and_score_case(
            agent_document,
            case,
            interpreter=interpreter,
            agent_id=agent_id,
            agent_name=agent_name,
        )
        for case in selected_cases
    ]
    patch_proposals = [
        proposal
        for score in before_scores
        if not score.passed
        for proposal in propose_patch(score)
    ]

    after_scores: list[AtlasReadinessCaseScore] = []
    if patch_proposals and apply_recommended_patches is not None:
        # The callback receives a DEEP COPY and the result is used only to
        # compute after_scores — the proposals are validated against a patched
        # document, never applied to the real agent (AR-4.5 / F19). So they stay
        # 'proposed', not 'applied'.
        patched_document = apply_recommended_patches(deepcopy(agent_document), patch_proposals)
        after_scores = [
            _run_and_score_case(
                patched_document,
                case,
                interpreter=interpreter,
                agent_id=agent_id,
                agent_name=agent_name,
            )
            for case in selected_cases
        ]

    # The recommendation reflects the agent AS IT EXISTS (the patched copy is
    # discarded). after_scores show that applying the proposed patches would
    # resolve the failures, but an unpatched agent that still fails must never
    # be reported publish-ready.
    publish_recommendation: Literal["publish", "do_not_publish"] = (
        "publish" if before_scores and all(score.passed for score in before_scores) else "do_not_publish"
    )
    return AtlasReadinessReport(
        agent_id=agent_id,
        agent_name=agent_name,
        before_scores=before_scores,
        patch_proposals=patch_proposals,
        after_scores=after_scores,
        publish_recommendation=publish_recommendation,
        summary=_report_summary(before_scores, after_scores, patch_proposals, publish_recommendation),
    )


def get_trace(run: SimulationRun, *, case_id: str) -> AtlasSimulationTrace:
    emitted_text = [
        message.text
        for turn in run.turns
        for message in turn.emitted_messages
        if message.text
    ]
    step_path: list[str] = []
    for turn in run.turns:
        if not step_path or step_path[-1] != turn.step_before:
            step_path.append(turn.step_before)
        if turn.step_after != step_path[-1]:
            step_path.append(turn.step_after)
    return AtlasSimulationTrace(
        case_id=case_id,
        final_step_id=run.final_step_id,
        turn_count=len(run.turns),
        final_facts=dict(run.final_facts),
        emitted_text=emitted_text,
        step_path=step_path,
    )


def score_trace(case: AtlasReadinessCase, trace: AtlasSimulationTrace) -> AtlasReadinessCaseScore:
    failures: list[str] = []
    if case.expected_final_step_ids and trace.final_step_id not in case.expected_final_step_ids:
        failures.append(
            "expected final step to be one of "
            f"{case.expected_final_step_ids}, got {trace.final_step_id!r}"
        )
    for key, expected in case.expected_final_facts.items():
        actual = trace.final_facts.get(key)
        if actual != expected:
            failures.append(f"expected fact {key}={expected!r}, got {actual!r}")
    lower_replies = "\n".join(trace.emitted_text).lower()
    for term in case.forbidden_reply_terms:
        if term.lower() in lower_replies:
            failures.append(f"forbidden reply term appeared: {term!r}")
    score = 1.0 if not failures else max(0.0, 1.0 - (len(failures) * 0.35))
    return AtlasReadinessCaseScore(
        case_id=case.case_id,
        passed=not failures,
        score=round(score, 2),
        failures=failures,
        trace=trace,
    )


def propose_patch(score: AtlasReadinessCaseScore) -> list[AtlasReadinessPatchProposal]:
    if score.passed:
        return []
    proposals: list[AtlasReadinessPatchProposal] = []
    for failure in score.failures:
        target: Literal["agent_document", "scenario", "step", "transition", "tool", "handoff", "evaluation"]
        target = "evaluation"
        summary = "Add a regression expectation for this failure."
        if "expected final step" in failure:
            target = "transition"
            summary = "Add or repair an outcome transition for this scenario."
        elif "forbidden reply term" in failure:
            target = "step"
            summary = "Tighten the step response policy to avoid unsafe commitments."
        elif "expected fact" in failure:
            target = "step"
            summary = "Add fact capture or confirmation before this workflow can complete."
        proposals.append(
            AtlasReadinessPatchProposal(
                proposal_id=f"atlas_patch_{uuid4().hex}",
                case_id=score.case_id,
                target=target,
                summary=summary,
                rationale=failure,
            )
        )
    return proposals


def _run_and_score_case(
    agent_document: AgentDocument,
    case: AtlasReadinessCase,
    *,
    interpreter: SemanticInterpreter | None,
    agent_id: str,
    agent_name: str,
) -> AtlasReadinessCaseScore:
    run = simulate_transcript(
        agent_document,
        case.utterances,
        conversation_id=f"atlas-readiness:{case.case_id}:{uuid4().hex}",
        interpreter=interpreter,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    trace = get_trace(run, case_id=case.case_id)
    return score_trace(case, trace)


def _pass_rate(scores: list[AtlasReadinessCaseScore]) -> float:
    if not scores:
        return 0.0
    return round(sum(1 for item in scores if item.passed) / len(scores), 4)


def _report_summary(
    before_scores: list[AtlasReadinessCaseScore],
    after_scores: list[AtlasReadinessCaseScore],
    patch_proposals: list[AtlasReadinessPatchProposal],
    publish_recommendation: str,
) -> str:
    before_passed = sum(1 for item in before_scores if item.passed)
    after_text = ""
    if after_scores:
        after_passed = sum(1 for item in after_scores if item.passed)
        after_text = f" After approved patches, {after_passed}/{len(after_scores)} cases passed."
    return (
        f"Atlas readiness loop ran {len(before_scores)} simulation cases; "
        f"{before_passed}/{len(before_scores)} passed before patches. "
        f"Atlas proposed {len(patch_proposals)} patch(es).{after_text} "
        f"Recommendation: {publish_recommendation}."
    )
