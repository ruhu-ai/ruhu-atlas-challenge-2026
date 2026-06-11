from pathlib import Path

from ruhu.loader import load_agent_document, load_agent_document_source, load_transcript

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_agent_document_from_json_file() -> None:
    document = load_agent_document(_REPO_ROOT / "tests" / "_fixtures" / "data" / "agents" / "sales.json")
    assert document.start_scenario_id == "main"
    assert document.start_step_id == "entry"


def test_load_agent_document_source_returns_envelope_metadata() -> None:
    document, agent_id, agent_name = load_agent_document_source(
        _REPO_ROOT / "tests" / "_fixtures" / "data" / "agents" / "sales.json"
    )
    assert agent_id == "sales"
    assert agent_name == "Sales Agent"
    assert document.start_step_id == "entry"


def test_load_transcript_from_json_file() -> None:
    utterances = load_transcript(_REPO_ROOT / "tests" / "_fixtures" / "data" / "transcripts" / "sales.json")
    assert utterances == [
        "Can you explain what the product does?",
        "I also want a demo",
    ]
