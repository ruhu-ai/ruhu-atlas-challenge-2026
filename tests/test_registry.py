from __future__ import annotations

from pathlib import Path

from ruhu.db import build_session_factory
from tests._fixtures.templates import load_template_agent_document
from ruhu.registry import FileAgentRegistry, SQLAlchemyAgentRegistry


def test_file_agent_registry_loads_agents_from_directory() -> None:
    registry = FileAgentRegistry(Path(__file__).resolve().parent / "_fixtures" / "data" / "agents")

    ids = [agent.agent_id for agent in registry.list_agents()]
    assert "sales" in ids
    assert "support_triage" in ids
    assert registry.get_agent_document("sales", target="published").start_scenario.start_step_id == "entry"


def test_sqlalchemy_agent_registry_seeds_versions_and_publishes(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    registry = SQLAlchemyAgentRegistry(session_factory)

    registry.ensure_seeded_document(
        agent_id="sales_agent",
        agent_name="Sales Agent",
        document=load_template_agent_document("sales-agent.json"),
    )

    agents = registry.list_agents()
    assert len(agents) == 1
    assert agents[0].agent_id == "sales_agent"
    assert agents[0].current_published_version_id is not None
    assert agents[0].current_draft_version_id is not None

    published = registry.get_agent_document("sales_agent", target="published")
    draft = registry.get_agent_document("sales_agent", target="draft")
    assert published.start_scenario_id == "main"
    assert draft.start_scenario_id == "main"

    published_versions = registry.list_versions("sales_agent")
    assert len(published_versions) == 2
    assert {item.status for item in published_versions} == {"draft", "published"}

    first_draft = next(item for item in published_versions if item.is_current_draft)
    published_snapshot = registry.publish("sales_agent")
    assert published_snapshot.status == "published"
    assert published_snapshot.version_id == first_draft.version_id

    created_draft = registry.create_draft("sales_agent")
    assert created_draft.status == "draft"
    assert created_draft.based_on_version_id == published_snapshot.version_id
