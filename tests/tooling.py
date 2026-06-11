from __future__ import annotations

from ruhu.tools.executors.builtin import BuiltinExecutor
from ruhu.tools.production import production_tool_specs
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.runtime import ToolRuntime


def build_demo_tool_runtime() -> ToolRuntime:
    def knowledge_lookup(call, _spec):
        query = str(call.args.get("query") or "").lower()
        if "pricing" in query:
            message = "Ruhu offers flexible pricing based on channels, automation volume, support needs, and deployment requirements."
        elif "workflow" in query or "integration" in query:
            message = "Ruhu includes a workflow builder with triggers, actions, conditions, and integrations."
        else:
            message = "Ruhu helps businesses build phone, WhatsApp, and web chat agents with shared workflows."
        return {
            "message": message,
            "facts": {"last_knowledge_query": call.args.get("query")},
        }

    return ToolRuntime(
        ToolRegistry(production_tool_specs()),
        executors={
            "builtin": BuiltinExecutor(
                {
                    "knowledge.lookup": knowledge_lookup,
                }
            )
        },
    )
