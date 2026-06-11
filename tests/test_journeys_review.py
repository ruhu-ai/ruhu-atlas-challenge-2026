from __future__ import annotations

from ruhu.agent_document import AgentDocument, Scenario, Step, StepTransition
from ruhu.journeys import (
    JourneyDefinition,
    JourneyDefinitionRules,
    JourneyDefinitionVersion,
    JourneyMilestoneRule,
    JourneyRulePredicate,
    SubjectKeyStrategy,
    build_definition_review,
    validate_definition_rules,
)
from ruhu.schemas import Condition, OtherwiseCondition, ToolBinding


def test_build_definition_review_flags_invalid_rules() -> None:
    definition = JourneyDefinition(
        definition_id="journey-def-1",
        organization_id="org-1",
        slug="demo-booking",
        name="Demo booking",
        subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
    )
    version = JourneyDefinitionVersion(
        definition_version_id="journey-ver-1",
        organization_id="org-1",
        definition_id=definition.definition_id,
        version_number=1,
        rules=JourneyDefinitionRules(
            entry_rules=[JourneyRulePredicate(kind="conversation_started")],
            milestones=[
                JourneyMilestoneRule(
                    milestone_id="discover",
                    name="Discover",
                    order_index=1,
                    enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
                ),
                JourneyMilestoneRule(
                    milestone_id="discover",
                    name="Duplicate",
                    order_index=1,
                    enter_when=[JourneyRulePredicate(kind="fact_present", value="email")],
                ),
            ],
            outcome_rules={"unexpected": [JourneyRulePredicate(kind="fact_equals", value="done")]},
        ),
    )

    review = build_definition_review(definition, version)

    assert review.can_publish is False
    blocker_codes = {item.code for item in review.blockers}
    assert "journey.milestones.duplicate_id" in blocker_codes
    assert "journey.milestones.duplicate_order" in blocker_codes
    assert "journey.outcome_rules.invalid_key" in blocker_codes


def test_validate_definition_rules_checkpoint_milestone_and_outcome_warning() -> None:
    issues = validate_definition_rules(
        JourneyDefinitionRules(
            entry_rules=[JourneyRulePredicate(kind="conversation_started")],
            milestones=[
                JourneyMilestoneRule(
                    milestone_id="qualified",
                    name="Qualified",
                    order_index=1,
                    enter_when=[JourneyRulePredicate(kind="fact_present", value="lead_score")],
                )
            ],
        )
    )

    warning_codes = {item.code for item in issues if item.severity == "warning"}
    assert "journey.outcome_rules.missing" in warning_codes


def _sales_agent_document() -> AgentDocument:
    return AgentDocument(
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Sales Agent",
                start_step_id="entry",
                steps=[
                    Step(
                        id="entry",
                        name="Entry",
                        transitions=[
                            StepTransition(
                                id="t-entry",
                                when=OtherwiseCondition(),
                                to_step_id="discover",
                            )
                        ],
                    ),
                    Step(
                        id="discover",
                        name="Discover",
                        tool_policy=[
                            ToolBinding(
                                ref="knowledge.lookup",
                                mode="required",
                                invocation_strategy="always",
                            )
                        ],
                        transitions=[
                            StepTransition(
                                id="t-stay",
                                when=OtherwiseCondition(),
                                to_step_id="discover",
                            )
                        ],
                    ),
                ],
            )
        ],
    )


def test_build_definition_review_validates_scoped_agent_step_tool_fact_and_conflicting_outcomes() -> None:
    definition = JourneyDefinition(
        definition_id="journey-def-2",
        organization_id="org-1",
        slug="scoped-booking",
        name="Scoped booking",
        subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        scope={"agent_ids": ["sales_agent"]},
    )
    version = JourneyDefinitionVersion(
        definition_version_id="journey-ver-2",
        organization_id="org-1",
        definition_id=definition.definition_id,
        version_number=1,
        rules=JourneyDefinitionRules(
            entry_rules=[JourneyRulePredicate(kind="conversation_started")],
            milestones=[
                JourneyMilestoneRule(
                    milestone_id="qualify",
                    name="Qualify",
                    order_index=1,
                    enter_when=[
                        JourneyRulePredicate(kind="step_entered", value="missing_step"),
                        JourneyRulePredicate(kind="tool_succeeded", value="missing.tool"),
                    ],
                    complete_when=[JourneyRulePredicate(kind="fact_present", value="booking_id")],
                )
            ],
            outcome_rules={
                "completed": [JourneyRulePredicate(kind="realtime_event", value="handoff:transferred")],
                "failed": [JourneyRulePredicate(kind="realtime_event", value="handoff:transferred")],
            },
        ),
    )

    review = build_definition_review(
        definition,
        version,
        scoped_agent_documents=[_sales_agent_document()],
        available_tool_refs=["knowledge.lookup"],
    )

    blocker_codes = {item.code for item in review.blockers}
    warning_codes = {item.code for item in review.warnings}
    assert review.can_publish is False
    assert "journey.references.state_missing_in_scope" in blocker_codes
    assert "journey.references.tool_missing_runtime" in blocker_codes
    assert "journey.milestone.unreachable" in blocker_codes
    assert "journey.outcome_rules.conflicting_predicates" in blocker_codes
    assert "journey.references.fact_undeclared_in_scope" in warning_codes


def test_build_definition_review_validates_touchpoint_rule_references() -> None:
    definition = JourneyDefinition(
        definition_id="journey-def-touchpoint",
        organization_id="org-1",
        slug="touchpoint-booking",
        name="Touchpoint booking",
        subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        scope={"agent_ids": ["sales_agent"]},
    )
    version = JourneyDefinitionVersion(
        definition_version_id="journey-ver-touchpoint",
        organization_id="org-1",
        definition_id=definition.definition_id,
        version_number=1,
        rules=JourneyDefinitionRules(
            entry_rules=[JourneyRulePredicate(kind="conversation_started")],
            touchpoint_rules=[JourneyRulePredicate(kind="tool_succeeded", value="missing.tool")],
            milestones=[
                JourneyMilestoneRule(
                    milestone_id="discover",
                    name="Discover",
                    order_index=1,
                    enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
                )
            ],
        ),
    )

    review = build_definition_review(
        definition,
        version,
        scoped_agent_documents=[_sales_agent_document()],
        available_tool_refs=["knowledge.lookup"],
    )

    blocker_codes = {item.code for item in review.blockers}
    assert "journey.references.tool_missing_runtime" in blocker_codes


def test_build_definition_review_flags_unknown_scoped_agent_ids() -> None:
    definition = JourneyDefinition(
        definition_id="journey-def-3",
        organization_id="org-1",
        slug="missing-agent",
        name="Missing agent",
        subject_strategy=SubjectKeyStrategy(kind="fact_name", value="customer_id"),
        scope={"agent_ids": ["missing_agent"]},
    )
    version = JourneyDefinitionVersion(
        definition_version_id="journey-ver-3",
        organization_id="org-1",
        definition_id=definition.definition_id,
        version_number=1,
        rules=JourneyDefinitionRules(
            entry_rules=[JourneyRulePredicate(kind="conversation_started")],
            milestones=[
                JourneyMilestoneRule(
                    milestone_id="discover",
                    name="Discover",
                    order_index=1,
                    enter_when=[JourneyRulePredicate(kind="step_entered", value="discover")],
                )
            ],
        ),
    )

    review = build_definition_review(
        definition,
        version,
        missing_agent_ids=["missing_agent"],
    )

    blocker_codes = {item.code for item in review.blockers}
    assert "journey.scope.agent_missing" in blocker_codes
