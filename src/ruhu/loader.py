from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_document import AgentDocument

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None


def load_agent_document(path: str | Path) -> AgentDocument:
    file_path = Path(path)
    data = _load_data(file_path)
    if not isinstance(data, dict):
        raise ValueError(f"agent document file must contain an object: {file_path}")
    if isinstance(data.get("agent_document"), dict):
        return AgentDocument.model_validate(data["agent_document"])
    if isinstance(data.get("start_scenario_id"), str) and isinstance(data.get("scenarios"), list):
        return AgentDocument.model_validate(data)
    raise ValueError(f"agent document file must contain 'agent_document' or top-level AgentDocument fields: {file_path}")


def load_agent_document_source(path: str | Path) -> tuple[AgentDocument, str, str]:
    file_path = Path(path)
    data = _load_data(file_path)
    if not isinstance(data, dict):
        raise ValueError(f"agent document file must contain an object: {file_path}")
    agent_id = str(data.get("agent_id") or data.get("id") or file_path.stem.replace("-", "_"))
    agent_name = str(data.get("agent_name") or data.get("name") or file_path.stem)
    if isinstance(data.get("agent_document"), dict):
        document = AgentDocument.model_validate(data["agent_document"])
        return document, agent_id, agent_name
    if isinstance(data.get("start_scenario_id"), str) and isinstance(data.get("scenarios"), list):
        document = AgentDocument.model_validate(data)
        return document, agent_id, agent_name
    raise ValueError(f"agent document file must contain 'agent_document' or top-level AgentDocument fields: {file_path}")


def load_transcript(path: str | Path) -> list[str]:
    file_path = Path(path)
    if file_path.suffix.lower() in {".json", ".yaml", ".yml"}:
        data = _load_data(file_path)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
        if isinstance(data, dict) and "utterances" in data and isinstance(data["utterances"], list):
            return [str(item).strip() for item in data["utterances"] if str(item).strip()]
        raise ValueError(f"transcript file must contain a list or {{'utterances': [...]}}: {file_path}")

    lines = [line.strip() for line in file_path.read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line]


def _load_data(path: Path) -> Any:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        return json.loads(text)
    if suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML files")
        return yaml.safe_load(text)
    raise ValueError(f"unsupported file type: {path}")
