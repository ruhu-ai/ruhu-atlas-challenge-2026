"""Generic template loader for tests.

Loads a shipped starter template JSON from ``src/ruhu/templates/system/``
and returns the embedded canonical ``AgentDocument``. Tests use this when
they need a realistic agent; there is no per-template Python wrapper.
"""
from __future__ import annotations

import json
from pathlib import Path

from ruhu.agent_document import AgentDocument

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "ruhu" / "templates" / "system"


def load_template_agent_document(filename: str) -> AgentDocument:
    """Load ``agent_document`` from the named template JSON file.

    Example::

        document = load_template_agent_document("sales-agent.json")
    """
    path = _TEMPLATES_DIR / filename
    payload = json.loads(path.read_text())
    return AgentDocument.model_validate(payload["agent_document"])
