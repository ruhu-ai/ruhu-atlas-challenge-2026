"""End-of-conversation sweep that fills the agent's analysis_schema.

The sweep delegates to the existing :class:`ruhu.capture.pipeline.FactPipeline`,
so every variable it fills inherits the same audit trail, arbitration rules,
and citation grounding as a turn-time capture. Authors declare what they want
extracted post-call via ``AgentDocument.analysis_schema``; the sweep iterates
each conversation turn and lets the pipeline accumulate evidence across the
whole transcript.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .agent_document import (
    AgentDocument,
    AnalysisVariableDef,
    CompiledAgentDocument,
    Scenario,
    Step,
    compile_agent_document,
)
from .capture.pipeline import FactPipeline
from .schemas import FactDef, FactRequirement


_SWEEP_STEP_ID = "__analysis_sweep__"
_SWEEP_SCENARIO_ID = "__analysis_sweep_scenario__"


@dataclass(slots=True)
class TurnTranscript:
    """A turn's user-facing text, keyed by its turn_id."""

    turn_id: str
    text: str


class AnalysisSweepResult(BaseModel):
    conversation_id: str
    variables_total: int = 0
    variables_filled: list[str] = Field(default_factory=list)
    variables_skipped_existing: list[str] = Field(default_factory=list)
    variables_unfilled: list[str] = Field(default_factory=list)


def run_analysis_sweep(
    *,
    conversation_id: str,
    organization_id: str | None,
    agent_document: CompiledAgentDocument,
    transcripts: list[TurnTranscript],
    existing_facts: dict[str, Any],
    existing_fact_metadata: dict[str, dict[str, Any]] | None,
    fact_pipeline: FactPipeline,
) -> AnalysisSweepResult:
    """Fill the agent's analysis_schema by replaying transcripts through the pipeline.

    Variables already present in ``existing_facts`` are skipped. The remaining
    variables become ``FactRequirement``s on a synthetic step, and each
    transcript turn is fed to ``fact_pipeline.extract`` in order. Audit rows
    written along the way are immediately citation-eligible.
    """

    schema = agent_document.analysis_schema
    if not schema:
        return AnalysisSweepResult(conversation_id=conversation_id)

    pending_vars = [var for var in schema if var.name not in existing_facts]
    skipped = [var.name for var in schema if var.name in existing_facts]

    result = AnalysisSweepResult(
        conversation_id=conversation_id,
        variables_total=len(schema),
        variables_skipped_existing=skipped,
    )

    if not pending_vars or not transcripts:
        result.variables_unfilled = [var.name for var in pending_vars]
        return result

    synthetic_fact_defs = [_to_fact_def(var) for var in pending_vars]
    synthetic_step = Step(
        id=_SWEEP_STEP_ID,
        name="Analysis Sweep",
        fact_requirements=[FactRequirement(name=fd.name) for fd in synthetic_fact_defs],
    )
    synthetic_doc = AgentDocument(
        start_scenario_id=_SWEEP_SCENARIO_ID,
        scenarios=[
            Scenario(
                id=_SWEEP_SCENARIO_ID,
                name="Analysis Sweep",
                start_step_id=synthetic_step.id,
                steps=[synthetic_step],
            )
        ],
        fact_schema=synthetic_fact_defs,
    )
    compiled = compile_agent_document(synthetic_doc)

    accumulated_facts: dict[str, Any] = dict(existing_facts)
    accumulated_metadata: dict[str, dict[str, Any]] = dict(existing_fact_metadata or {})

    for turn in transcripts:
        text = (turn.text or "").strip()
        if not text:
            continue
        extraction = fact_pipeline.extract(
            text=text,
            turn_id=turn.turn_id,
            step=synthetic_step,
            agent_document=compiled,
            existing_facts=accumulated_facts,
            existing_fact_metadata=accumulated_metadata,
            classifier_entity_slots=None,
            conversation_id=conversation_id,
            organization_id=organization_id,
        )
        for update in extraction.updates:
            accumulated_facts[update.name] = update.value
            if update.name in extraction.new_fact_metadata:
                accumulated_metadata[update.name] = extraction.new_fact_metadata[update.name]

    result.variables_filled = [
        var.name for var in pending_vars if var.name in accumulated_facts and var.name not in existing_facts
    ]
    result.variables_unfilled = [
        var.name for var in pending_vars if var.name not in accumulated_facts
    ]
    return result


def _to_fact_def(var: AnalysisVariableDef) -> FactDef:
    """Map an AnalysisVariableDef to a FactDef the capture pipeline can consume."""

    fact_type = _analysis_type_to_fact_type(var.type)
    validator_config: dict[str, Any] = {}
    if var.type == "category" and var.categories:
        validator_config["allowed_values"] = list(var.categories)

    capture_aliases: list[str] = []
    if var.description:
        capture_aliases.append(var.description.lower())

    # Analysis sweep variables are inherently model-allowed; the LLM extractor
    # is the typical source. The pipeline's default arbitration rules still
    # prefer deterministic / classifier / tool sources when available.
    return FactDef(
        name=var.name,
        type=fact_type,
        source_policy="model_allowed",
        confidence_threshold=0.6,
        capture_aliases=capture_aliases,
        validator_config=validator_config,
        llm_confidence_default=0.7,
        metadata={
            "origin": "analysis_schema",
            "analysis_type": var.type,
            "analysis_source": var.source,
            "description": var.description,
        },
    )


def _analysis_type_to_fact_type(analysis_type: str) -> str:
    return {
        "string": "string",
        "number": "string",
        "boolean": "boolean",
        "category": "enum",
        "array": "string",
    }.get(analysis_type, "string")
