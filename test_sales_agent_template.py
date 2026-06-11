#!/usr/bin/env python
"""Verify the bundled sales agent template against the current contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ruhu.agent_document import AgentDocument, validate_agent_document


def _fail(message: str) -> None:
    print(f"[fail] {message}")
    sys.exit(1)


print("\n" + "=" * 80)
print("SALES AGENT TEMPLATE VERIFICATION")
print("=" * 80 + "\n")

template_path = Path(__file__).parent / "src" / "ruhu" / "templates" / "system" / "sales-agent.json"
if not template_path.exists():
    _fail(f"template file not found: {template_path}")

print(f"[ok] Template file exists: {template_path.name}")

try:
    template_data = json.loads(template_path.read_text())
except Exception as exc:
    _fail(f"failed to parse template JSON: {exc}")

required_fields = [
    "template_id",
    "name",
    "slug",
    "description",
    "category",
    "default_agent_settings",
    "agent_document",
]
missing_fields = [field for field in required_fields if field not in template_data]
if missing_fields:
    _fail(f"missing required fields: {', '.join(missing_fields)}")

print("[ok] Template metadata is present")
print(f"  Template ID: {template_data['template_id']}")
print(f"  Template Name: {template_data['name']}")
print(f"  Category: {template_data.get('category')}")
print(f"  Featured: {template_data.get('is_featured', False)}")

try:
    agent_document = AgentDocument.model_validate(template_data["agent_document"])
except Exception as exc:
    _fail(f"agent document validation failed: {exc}")

validation = validate_agent_document(agent_document)
if not validation.valid:
    issues = "; ".join(f"{issue.severity}:{issue.code}:{issue.message}" for issue in validation.issues)
    _fail(f"agent document contract validation failed: {issues}")

print("[ok] Agent document validates")
print(f"  Version: {agent_document.version}")
print(f"  Start Scenario: {agent_document.start_scenario_id}")
print(f"  Total Scenarios: {len(agent_document.scenarios)}")
print(f"  Total Steps: {len(agent_document.steps)}")

settings = template_data["default_agent_settings"]
print("[ok] Default agent settings loaded")
print(f"  Agent Type: {settings.get('agent_type')}")
print(f"  LLM Model: {settings.get('llm_config', {}).get('model')}")
print(f"  Voice: {settings.get('voice_config', {}).get('voice_id')}")

start_scenario = agent_document.scenario_by_id(agent_document.start_scenario_id)
entry_step = agent_document.step_by_id(start_scenario.start_step_id)
print("[ok] Entry step found")
print(f"  Entry Step: {entry_step.id}")
print(f"  Prompt: {entry_step.say or entry_step.name}")

required_tools = template_data.get("required_tools") or []
document_tool_refs = {
    ref
    for step in agent_document.steps
    for ref in [
        *(policy.ref for policy in step.tool_policy),
        *(
            step.action_config.callable_api_refs
            if step.action_config is not None
            else []
        ),
        *(
            step.action_config.callable_system_refs
            if step.action_config is not None
            else []
        ),
        *(
            step.action_config.callable_integrations
            if step.action_config is not None
            else []
        ),
    ]
}
for step in agent_document.steps:
    if step.action_config is None:
        continue
    code = step.action_config.code or ""
    for integration in step.action_config.callable_integrations:
        for required_tool in required_tools:
            tool_ref = str(required_tool.get("tool_ref") or "")
            category, _, action = tool_ref.partition(".")
            if category == integration and action and f'action="{action}"' in code:
                document_tool_refs.add(tool_ref)

missing_required_refs = [
    item["tool_ref"]
    for item in required_tools
    if item.get("required") and item.get("tool_ref") not in document_tool_refs
]
if missing_required_refs:
    _fail(f"required tools not referenced by document: {', '.join(missing_required_refs)}")

print("[ok] Required tools are referenced by the agent document")

print("\n" + "=" * 80)
print("ALL SALES AGENT TEMPLATE CHECKS PASSED")
print("=" * 80)
