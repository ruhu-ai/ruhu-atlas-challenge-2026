from __future__ import annotations

from typing import Any


def _object_schema(properties: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": True,
    }


ATLAS_MCP_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_agent_document",
        "description": "Read a draft or published Ruhu AgentDocument for readiness inspection.",
        "inputSchema": _object_schema(
            {
                "agent_id": {"type": "string"},
                "version_target": {"type": "string", "enum": ["draft", "published"]},
            },
            required=["agent_id"],
        ),
    },
    {
        "name": "generate_evaluation_cases",
        "description": "Generate bounded synthetic readiness cases for an AgentDocument or workflow brief.",
        "inputSchema": _object_schema(
            {
                "agent_id": {"type": "string"},
                "workflow_brief": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 50},
                "case_limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "voice_case_count": {"type": "integer", "minimum": 0, "maximum": 10},
                "provider_policy": {"type": "string", "enum": ["deterministic", "google_only", "hybrid"]},
                "seed": {"type": ["integer", "null"]},
            },
        ),
    },
    {
        "name": "run_simulation",
        "description": "Run one synthetic case through the deterministic Ruhu simulator.",
        "inputSchema": _object_schema(
            {
                "agent_id": {"type": "string"},
                "case": {"type": "object"},
            },
            required=["agent_id", "case"],
        ),
    },
    {
        "name": "run_voice_simulation",
        "description": "Run an evaluation-only voice case; live Google voice requires an explicit permission grant.",
        "inputSchema": _object_schema(
            {
                "agent_id": {"type": "string"},
                "case": {"type": "object"},
                "provider_policy": {"type": "string", "enum": ["deterministic", "google_only", "hybrid"]},
            },
            required=["agent_id", "case"],
        ),
    },
    {
        "name": "get_trace",
        "description": "Return a provided trace or preview a trace by simulating agent_id + case.",
        "inputSchema": _object_schema(
            {
                "trace": {"type": "object"},
                "agent_id": {"type": "string"},
                "case": {"type": "object"},
            },
        ),
    },
    {
        "name": "score_trace",
        "description": "Score a readiness trace against a synthetic case.",
        "inputSchema": _object_schema(
            {
                "case": {"type": "object"},
                "trace": {"type": "object"},
            },
            required=["case", "trace"],
        ),
    },
    {
        "name": "propose_agent_document_deltas",
        "description": "Create reviewable typed AgentDocument deltas for a fix-scope readiness run.",
        "inputSchema": _object_schema(
            {
                "agent_id": {"type": "string"},
                "case_limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "voice_case_count": {"type": "integer", "minimum": 0, "maximum": 10},
                "provider_policy": {"type": "string", "enum": ["deterministic", "google_only", "hybrid"]},
                "seed": {"type": ["integer", "null"]},
            },
            required=["agent_id"],
        ),
    },
    {
        "name": "patch_agent_document",
        "description": "Compatibility alias that returns review-required typed deltas instead of directly mutating a document.",
        "inputSchema": _object_schema(
            {
                "agent_id": {"type": "string"},
                "case_limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "provider_policy": {"type": "string", "enum": ["deterministic", "google_only", "hybrid"]},
            },
            required=["agent_id"],
        ),
    },
    {
        "name": "rerun_eval_suite",
        "description": "Rerun a previous Atlas readiness evaluation using its cached case set.",
        "inputSchema": _object_schema({"run_id": {"type": "string"}}, required=["run_id"]),
    },
    {
        "name": "create_publish_report",
        "description": "Read the publish-readiness report for a run or latest agent report.",
        "inputSchema": _object_schema(
            {
                "run_id": {"type": "string"},
                "agent_id": {"type": "string"},
            },
        ),
    },
]


ATLAS_MCP_TOOL_NAMES: frozenset[str] = frozenset(tool["name"] for tool in ATLAS_MCP_TOOL_SCHEMAS)
