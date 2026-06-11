import pytest
from pydantic import ValidationError

from ruhu.agent_document import (
    AgentDocument,
    ScenarioRoute,
    Scenario,
    Step,
    StepCompletion,
    StepHandoff,
    StepTransition,
    build_step_runtime_entry,
    select_start_scenario_id,
    validate_agent_document,
)
from ruhu.schemas import (
    ActionConfig,
    AgentCapabilityManifest,
    FactDef,
    FactMissingCondition,
    FactPresentCondition,
    OtherwiseCondition,
    OutcomeCondition,
    ToolBinding,
    ToolOutcomeCondition,
)


def _document() -> AgentDocument:
    return AgentDocument(
        start_scenario_id="sales",
        fact_schema=[
            FactDef(name="email", type="string", required=False),
        ],
        agent_capability_manifest=AgentCapabilityManifest(
            assistant_identity="I'm Ruhu's sales assistant.",
            capabilities=["answer product questions", "book demos"],
            limitations=["I only use configured tools and knowledge."],
        ),
        scenarios=[
            Scenario(
                id="sales",
                name="Sales",
                start_step_id="start",
                steps=[
                    Step(
                        id="start",
                        name="Start",
                        say="Hi there! What brings you here today?",
                        transitions=[
                            StepTransition(
                                id="to_intake",
                                when=OtherwiseCondition(),
                                to_step_id="intake",
                            )
                        ],
                    ),
                    Step(
                        id="intake",
                        name="Intake",
                        transitions=[
                            StepTransition(
                                id="to_collect",
                                when=FactMissingCondition(fact_name="email"),
                                to_step_id="collect_email",
                            )
                        ],
                    ),
                    Step(
                        id="collect_email",
                        name="Collect Email",
                        say="Could you share your email?",
                        fact_requirements=[{"name": "email", "purpose": "Needed to send the invite."}],
                        transitions=[
                            StepTransition(
                                id="to_book",
                                when=FactPresentCondition(fact_name="email"),
                                to_step_id="book_demo",
                            )
                        ],
                    ),
                    Step(
                        id="book_demo",
                        name="Book Demo",
                        say="I'll book that now.",
                        action_config=ActionConfig(code="result = {'status': 'ok'}"),
                        transitions=[
                            StepTransition(
                                id="to_done",
                                when=OtherwiseCondition(),
                                to_step_id="done",
                            )
                        ],
                    ),
                    Step(
                        id="done",
                        name="Done",
                        say="You're all set.",
                        completion=StepCompletion(disposition="resolved"),
                    ),
                    Step(
                        id="handoff",
                        name="Handoff",
                        say="Let me connect you to a specialist.",
                        handoff=StepHandoff(target_type="queue", target="sales"),
                    ),
                ],
            )
        ],
    )


def test_validate_agent_document_accepts_generic_steps() -> None:
    document = _document()
    report = validate_agent_document(document)
    assert report.valid is True
    assert report.error_count == 0


def test_validate_agent_document_rejects_workflow_capture_scope() -> None:
    document = _document()
    document.fact_schema[0].storage_policy.scope = "workflow"

    report = validate_agent_document(document)

    assert report.valid is False
    assert any(
        issue.code == "fact.workflow_storage_unavailable"
        and issue.fact_name == "email"
        for issue in report.issues
    )


def test_agent_document_allows_incomplete_draft_step_but_publish_validation_blocks_it() -> None:
    document = AgentDocument(
        start_scenario_id="draft",
        scenarios=[
            Scenario(
                id="draft",
                name="Draft",
                start_step_id="start",
                steps=[
                    Step(id="start", name="Start"),
                ],
            )
        ],
    )

    report = validate_agent_document(document)
    assert report.valid is False
    assert any(
        issue.code == "step.non_terminal_without_transition"
        and issue.severity == "error"
        and issue.step_id == "start"
        for issue in report.issues
    )


def test_build_step_runtime_entry_derives_runtime_flags_from_step_fields() -> None:
    document = _document()

    intake = build_step_runtime_entry(document, current_step_id="intake")
    collect = build_step_runtime_entry(document, current_step_id="collect_email")
    tool = build_step_runtime_entry(document, current_step_id="book_demo", facts={"email": "a@b.com"})
    done = build_step_runtime_entry(document, current_step_id="done")
    handoff = build_step_runtime_entry(document, current_step_id="handoff")
    repair = build_step_runtime_entry(document, current_step_id="intake", active_repair=True)

    assert intake.collects_missing_details is False
    assert intake.uses_tooling is False
    assert intake.hands_off is False
    assert intake.completes is False
    assert intake.active_repair is False
    assert intake.pending_execution is False
    assert collect.collects_missing_details is True
    assert collect.missing_facts == ["email"]
    assert tool.uses_tooling is True
    assert done.completes is True
    assert handoff.hands_off is True
    assert repair.active_repair is True


def test_agent_document_exposes_scenario_helpers() -> None:
    document = _document()

    assert document.start_scenario_id == "sales"
    assert document.scenario_ids == {"sales"}
    assert document.scenario_by_id("sales").name == "Sales"
    assert document.scenario_for_step_id("collect_email").id == "sales"


def test_validate_agent_document_flags_unreachable_and_invalid_terminal_routes() -> None:
    document = AgentDocument(
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Main",
                start_step_id="start",
                steps=[
                    Step(
                        id="start",
                        name="Start",
                        transitions=[
                            StepTransition(
                                id="to_done",
                                when=OtherwiseCondition(),
                                to_step_id="done",
                            )
                        ],
                    ),
                    Step(
                        id="done",
                        name="Done",
                        completion=StepCompletion(disposition="resolved"),
                        transitions=[
                            StepTransition(
                                id="bad_exit",
                                when=OtherwiseCondition(),
                                to_step_id="start",
                            )
                        ],
                    ),
                    Step(
                        id="orphan",
                        name="Orphan",
                        transitions=[
                            StepTransition(
                                id="stay_orphan",
                                when=OtherwiseCondition(),
                                to_step_id="orphan",
                            )
                        ],
                    ),
                ],
            )
        ],
    )

    report = validate_agent_document(document)
    codes = {issue.code for issue in report.issues}
    assert report.valid is False
    assert "step.terminal_with_transitions" in codes
    assert "step.unreachable" in codes


def test_agent_document_accepts_cross_scenario_step_transition() -> None:
    """Cross-scenario step transitions are valid — the kernel keeps
    current_scenario_id in sync when navigation crosses scenarios.
    See ConversationKernel._process_step_turn."""
    document = AgentDocument(
        start_scenario_id="sales",
        scenarios=[
            Scenario(
                id="sales",
                name="Sales",
                start_step_id="sales_start",
                steps=[
                    Step(
                        id="sales_start",
                        name="Sales Start",
                        transitions=[
                            StepTransition(
                                id="to_pricing_step",
                                when=OtherwiseCondition(),
                                to_step_id="pricing_start",
                            )
                        ],
                    )
                ],
            ),
            Scenario(
                id="pricing",
                name="Pricing",
                start_step_id="pricing_start",
                steps=[
                    Step(
                        id="pricing_start",
                        name="Pricing Start",
                        completion=StepCompletion(disposition="resolved"),
                    )
                ],
            ),
        ],
    )
    # Validator should NOT flag cross-scenario transitions any more.
    report = validate_agent_document(document)
    cross_scenario_codes = [
        issue.code for issue in report.issues
        if issue.code == "transition.cross_scenario_not_allowed"
    ]
    assert cross_scenario_codes == []


def test_validate_agent_document_accepts_scenario_routes() -> None:
    document = AgentDocument(
        start_scenario_id="sales",
        scenarios=[
            Scenario(
                id="sales",
                name="Sales",
                start_step_id="sales_start",
                steps=[
                    Step(
                        id="sales_start",
                        name="Sales Start",
                        transitions=[
                            StepTransition(
                                id="stay_sales",
                                when=OtherwiseCondition(),
                                to_step_id="sales_start",
                            )
                        ],
                    )
                ],
            ),
            Scenario(
                id="pricing",
                name="Pricing",
                start_step_id="pricing_start",
                steps=[
                    Step(
                        id="pricing_start",
                        name="Pricing Start",
                        completion=StepCompletion(disposition="resolved"),
                    )
                ],
            ),
        ],
        scenario_routes=[
            ScenarioRoute(
                id="route_to_pricing",
                from_scenario_id="sales",
                when=OtherwiseCondition(),
                to_scenario_id="pricing",
            )
        ],
    )

    report = validate_agent_document(document)
    assert report.valid is True


def test_select_start_scenario_id_prefers_matching_entry_channel() -> None:
    document = AgentDocument(
        start_scenario_id="sales",
        scenarios=[
            Scenario(
                id="sales",
                name="Sales",
                start_step_id="sales_start",
                steps=[
                    Step(
                        id="sales_start",
                        name="Sales Start",
                        completion=StepCompletion(disposition="resolved"),
                    )
                ],
            ),
            Scenario(
                id="voice_support",
                name="Voice Support",
                start_step_id="support_start",
                entry_channels=["voice"],
                steps=[
                    Step(
                        id="support_start",
                        name="Support Start",
                        completion=StepCompletion(disposition="resolved"),
                    )
                ],
            ),
        ],
    )

    assert select_start_scenario_id(document, channel="voice") == "voice_support"
    assert select_start_scenario_id(document, channel="web_chat") == "sales"


def test_select_start_scenario_id_rejects_requested_channel_mismatch() -> None:
    document = AgentDocument(
        start_scenario_id="sales",
        scenarios=[
            Scenario(
                id="sales",
                name="Sales",
                start_step_id="sales_start",
                entry_channels=["web_chat"],
                steps=[
                    Step(
                        id="sales_start",
                        name="Sales Start",
                        completion=StepCompletion(disposition="resolved"),
                    )
                ],
            )
        ],
    )

    try:
        select_start_scenario_id(
            document,
            requested_scenario_id="sales",
            channel="voice",
        )
    except ValueError as exc:
        assert "does not allow channel" in str(exc)
    else:
        raise AssertionError("expected channel mismatch to be rejected")


# ── Edge-owned outcomes — schema regressions ─────────────────────────────────
#
# These tests pin the contract introduced when we replaced
# ``Step.event_hints`` + ``intent_detected:*`` event values with a discriminated
# Condition union and an ``OutcomeCondition`` that owns its own stable
# ``event`` token plus LLM-evaluated ``description``. Every test below either
# protects an invariant the kernel relies on or rejects a legacy shape so
# nothing silently re-introduces the indirection we just removed.


class TestOutcomeConditionShape:
    """``OutcomeCondition.event`` is the analytics/training/trace key. It must
    stay slug-shaped so historical data, prefill LoRAs, and metric labels all
    line up. ``description`` is what the LLM sees; it must be substantive
    enough to be evaluable (not blank, not a single word)."""

    def test_accepts_well_formed_outcome(self) -> None:
        cond = OutcomeCondition(
            event="pricing_question",
            description="The user asks about pricing, plans, or quotes.",
        )
        assert cond.kind == "outcome"
        assert cond.event == "pricing_question"

    def test_rejects_uppercase_event(self) -> None:
        with pytest.raises(ValidationError, match="must match"):
            OutcomeCondition(event="Pricing", description="The user asks about pricing.")

    def test_rejects_event_with_namespace_colon(self) -> None:
        # Belt-and-suspenders: the legacy ``intent_detected:foo`` shape
        # cannot be re-introduced by accident.
        with pytest.raises(ValidationError, match="must match"):
            OutcomeCondition(
                event="intent_detected:pricing",
                description="The user asks about pricing.",
            )

    def test_rejects_event_too_short(self) -> None:
        with pytest.raises(ValidationError):
            OutcomeCondition(event="ab", description="The user asks about pricing.")

    def test_rejects_blank_description(self) -> None:
        with pytest.raises(ValidationError):
            OutcomeCondition(event="pricing_question", description="")


class TestStepValidators:
    """Step-level invariants that keep routing unambiguous."""

    def _step(
        self,
        *,
        transitions: list[StepTransition],
        tool_policy: list[ToolBinding] | None = None,
    ) -> Step:
        return Step(
            id="qa",
            name="Q&A",
            tool_policy=tool_policy or [],
            transitions=transitions,
        )

    def test_rejects_duplicate_transition_ids(self) -> None:
        with pytest.raises(ValidationError, match="duplicate transition id"):
            self._step(
                transitions=[
                    StepTransition(
                        id="t_dup",
                        when=OutcomeCondition(
                            event="pricing_question",
                            description="User asks about pricing.",
                        ),
                        to_step_id="qa",
                    ),
                    StepTransition(
                        id="t_dup",
                        when=OtherwiseCondition(),
                        to_step_id="qa",
                    ),
                ],
            )

    def test_rejects_more_than_one_otherwise(self) -> None:
        with pytest.raises(ValidationError, match="at most one"):
            self._step(
                transitions=[
                    StepTransition(id="t1", when=OtherwiseCondition(), to_step_id="qa"),
                    StepTransition(id="t2", when=OtherwiseCondition(), to_step_id="qa"),
                ],
            )

    def test_rejects_duplicate_outcome_events(self) -> None:
        # Two transitions with the same outcome event would produce a
        # multiset for the prefill ``guided_choice`` FSM — they'd shadow
        # each other and routing would be non-deterministic.
        with pytest.raises(ValidationError, match="duplicate OutcomeCondition.event"):
            self._step(
                transitions=[
                    StepTransition(
                        id="t_a",
                        when=OutcomeCondition(
                            event="pricing_question",
                            description="User asks about pricing.",
                        ),
                        to_step_id="qa",
                    ),
                    StepTransition(
                        id="t_b",
                        when=OutcomeCondition(
                            event="pricing_question",
                            description="User asks about plans.",
                        ),
                        to_step_id="qa",
                    ),
                ],
            )

    def test_tool_outcome_without_ref_allowed_when_single_tool(self) -> None:
        step = self._step(
            tool_policy=[ToolBinding(ref="knowledge.lookup", mode="optional")],
            transitions=[
                StepTransition(
                    id="t_ok",
                    when=ToolOutcomeCondition(outcome="success"),
                    to_step_id="qa",
                ),
            ],
        )
        # Implicit assertion: validation succeeded.
        assert step.transitions[0].when.tool_ref is None

    def test_tool_outcome_requires_ref_when_multiple_tools(self) -> None:
        with pytest.raises(ValidationError, match="set ``tool_ref`` explicitly"):
            self._step(
                tool_policy=[
                    ToolBinding(ref="knowledge.lookup", mode="optional"),
                    ToolBinding(ref="calendar.create_event", mode="optional"),
                ],
                transitions=[
                    StepTransition(
                        id="t_ambig",
                        when=ToolOutcomeCondition(outcome="success"),
                        to_step_id="qa",
                    ),
                ],
            )

    def test_tool_outcome_rejects_unknown_tool_ref(self) -> None:
        with pytest.raises(ValidationError, match="not in the step's"):
            self._step(
                tool_policy=[ToolBinding(ref="knowledge.lookup", mode="optional")],
                transitions=[
                    StepTransition(
                        id="t_bad_ref",
                        when=ToolOutcomeCondition(
                            tool_ref="calendar.create_event",
                            outcome="success",
                        ),
                        to_step_id="qa",
                    ),
                ],
            )


class TestLegacyShapeRejection:
    """Belt-and-suspenders: parsing a JSON payload in either of the two
    legacy shapes (``Step.event_hints`` dict, or ``Condition`` with
    ``kind="event"`` and ``intent_detected:*`` value) must fail. A future
    template, migration, or copy-paste from old docs cannot silently
    produce a degraded runtime."""

    def test_event_hints_is_no_longer_accepted_on_step(self) -> None:
        # Pydantic models default to ``model_config.extra="ignore"`` but
        # the schema's removal is verified by attribute absence — author
        # data carrying ``event_hints`` is dropped on parse.
        step = Step.model_validate(
            {
                "id": "qa",
                "name": "Q&A",
                "event_hints": {
                    "pricing_question": "User asks about pricing.",
                },
            }
        )
        assert not hasattr(step, "event_hints")

    def test_legacy_event_kind_rejected(self) -> None:
        # The discriminator no longer accepts ``"event"``; no member
        # claims it. Pydantic raises with the discriminator name.
        with pytest.raises(ValidationError):
            StepTransition.model_validate(
                {
                    "id": "t_legacy",
                    "to_step_id": "qa",
                    "when": {
                        "kind": "event",
                        "value": "intent_detected:pricing_question",
                    },
                }
            )

    def test_outcome_event_with_legacy_namespace_colon_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StepTransition.model_validate(
                {
                    "id": "t_legacy",
                    "to_step_id": "qa",
                    "when": {
                        "kind": "outcome",
                        "event": "intent_detected:pricing_question",
                        "description": "User asks about pricing.",
                    },
                }
            )


class TestConditionRoundTrip:
    """Each Condition kind round-trips through JSON serialization with
    field names preserved. The frontend canvas, the migration script, and
    the live DB all read these JSON shapes; drift here breaks all three."""

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "kind": "outcome",
                "event": "pricing_question",
                "description": "User asks about pricing or plans.",
            },
            {"kind": "fact_present", "fact_name": "email"},
            {"kind": "fact_equals", "fact_name": "channel", "expected": "voice"},
            {"kind": "fact_missing", "fact_name": "email"},
            {"kind": "all_required_facts_present"},
            {"kind": "guard_failure", "guard_id": "rate_limit"},
            {"kind": "tool_outcome", "tool_ref": "knowledge.lookup", "outcome": "success"},
            {"kind": "attachment_present", "any_of_kinds": ["image"]},
            {"kind": "view_ready", "view_kind": "form_complete"},
            {"kind": "otherwise"},
        ],
    )
    def test_each_kind_round_trips(self, payload: dict) -> None:
        transition = StepTransition.model_validate(
            {"id": "t", "to_step_id": "qa", "when": payload}
        )
        dumped = transition.model_dump(mode="json")["when"]
        # Discriminator + every author-supplied field survives a round trip.
        for key, value in payload.items():
            assert dumped[key] == value
