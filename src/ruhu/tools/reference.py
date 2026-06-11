from __future__ import annotations

from dataclasses import dataclass

from .authorizer import DefaultToolAuthorizer
from .executors.builtin import BuiltinExecutor
from .integration_runtime import ToolIntegrationRuntime
from .registry import ToolRegistry
from .runtime import ToolRuntime
from .store import InMemoryToolInvocationStore
from .specs import ToolAnnotations, ToolSpec


@dataclass
class ReferenceToolBackend:
    def knowledge_lookup(self, query: str, mode: str = "standard") -> dict[str, object]:
        normalized = query.strip().lower()
        if "pricing" in normalized:
            message = "Ruhu offers flexible pricing based on channels, automation volume, and support needs."
        elif "workflow" in normalized or "integration" in normalized:
            message = (
                "Ruhu includes a visual workflow layer with triggers, actions, conditions, and external integrations."
            )
        else:
            message = (
                "Ruhu helps businesses build phone, WhatsApp, web chat, and workflow automation agents."
            )
        return {
            "message": message,
            "context_block": f"Question: {query}\n1. Reference knowledge: {message}",
            "retrieval_mode": "deep" if mode == "deep" else "standard",
            "retrieval_queries": [query],
            "retrieval_steps": [{"query": query, "mode": "deep" if mode == "deep" else "standard", "hit_count": 1}],
            "facts": {
                "last_knowledge_message": message,
                "last_knowledge_query": query,
            },
        }


def reference_tool_specs() -> list[ToolSpec]:
    """Reference knowledge lookup tool.

    This tool demonstrates proper schema validation:
    - Input schema: strictly validates that 'query' is a non-empty string
    - Output schema: validates that tool returns message and facts
    """
    return [
        ToolSpec(
            ref="knowledge.lookup",
            kind="builtin",
            display_name="Knowledge Lookup",
            description="Search the reference knowledge layer for product, workflow, integration, or pricing answers.",
            purpose="Retrieve grounded reference knowledge before answering product, workflow, or pricing questions in demo and test flows.",
            when_to_use=[
                "Use when the user asks for product, workflow, integration, or pricing information that should be grounded in reference knowledge.",
            ],
            when_not_to_use=[
                "Do not use for side-effecting actions, customer-specific mutations, or questions that require live external account data.",
            ],
            input_examples=[
                {
                    "name": "workflow_question",
                    "description": "Looks up workflow guidance for a grounded support answer.",
                    "args": {"query": "How do Ruhu workflow automations work?"},
                }
            ],
            failure_modes=[
                {
                    "kind": "permanent_upstream_error",
                    "description": "Reference knowledge cannot answer because the query falls outside the available demo content.",
                    "retryable": False,
                }
            ],
            output_validation_mode="strict",
            annotations=ToolAnnotations(read_only=True, side_effect_free=True, idempotent=True),
            timeout_ms=800,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Question or topic to search for.",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["standard", "deep"],
                        "description": "Use 'deep' for harder multi-part or exploratory questions.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Answer or information retrieved.",
                    },
                    "context_block": {
                        "type": "string",
                        "description": "Compact grounded context assembled from retrieved knowledge.",
                    },
                    "retrieval_mode": {
                        "type": "string",
                        "enum": ["standard", "deep"],
                    },
                    "retrieval_queries": {
                        "type": "array",
                    },
                    "retrieval_steps": {
                        "type": "array",
                    },
                    "facts": {
                        "type": "object",
                        "description": "Structured facts from the knowledge base.",
                    },
                },
                "required": ["message", "facts"],
                "additionalProperties": False,
            },
        ),
    ]


def build_reference_tool_runtime(
    *,
    backend: ReferenceToolBackend | None = None,
) -> tuple[ToolRuntime, ReferenceToolBackend]:
    effective_backend = backend or ReferenceToolBackend()
    registry = ToolRegistry(reference_tool_specs())
    invocation_store = InMemoryToolInvocationStore()
    executor = BuiltinExecutor(
        {
            "knowledge.lookup": lambda call, _spec: effective_backend.knowledge_lookup(
                str(call.args.get("query") or ""),
                str(call.args.get("mode") or "standard"),
            ),
        }
    )
    runtime = ToolRuntime(
        registry,
        authorizer=DefaultToolAuthorizer(),
        store=invocation_store,
        executors={"builtin": executor},
        integration_runtime=ToolIntegrationRuntime(invocation_store=invocation_store),
    )
    return runtime, effective_backend
