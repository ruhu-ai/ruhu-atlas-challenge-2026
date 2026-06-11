from __future__ import annotations

import os

import pytest

from ruhu.atlas_generator import AtlasGeneratorContext, AtlasProposalGenerator
from ruhu.atlas_protocol import AtlasProposedChanges


def _context() -> AtlasGeneratorContext:
    return AtlasGeneratorContext(
        agent_id="sales",
        scope="agent_authoring",
        user_message='Rename this step to "Qualified lead".',
        selected_scenario_id="main",
        selected_scenario_name="Main",
        selected_step_id="discover",
        selected_step_name="Discover",
        scenario_ids=["main"],
        step_ids=["discover", "answer_pricing"],
        fact_names=["email", "preferred_time"],
        tool_refs=["knowledge.lookup"],
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not ((os.getenv("RUHU_ATLAS_GENERATOR_API_KEY") or "").strip() or (os.getenv("ANTHROPIC_API_KEY") or "").strip()),
    reason="Anthropic credentials not configured for Atlas generator integration test",
)
def test_atlas_generator_anthropic_integration_smoke() -> None:
    generator = AtlasProposalGenerator.from_env(
        # generate() calls fallback_generate(context, compiled_document) — the
        # callback must accept both, or the fallback branch raises TypeError.
        fallback_generate=lambda context, compiled_document=None: AtlasProposedChanges(),
    )

    output = generator.generate(_context())

    assert output.generation_mode in {"anthropic", "fallback"}
    assert output.proposed_changes is not None
