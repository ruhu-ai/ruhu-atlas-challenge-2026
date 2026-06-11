from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .atlas_readiness_models import (
    AtlasReadinessCase,
    AtlasReadinessRunRequest,
    AtlasReadinessTrace,
)
from .atlas_readiness_service import AtlasReadinessService


@dataclass(frozen=True)
class AtlasReadinessMCPContext:
    tenant_id: str
    user_id: str
    run_id: str | None = None
    scope: str = "validate"
    permission_grant_ids: tuple[str, ...] = ()


class AtlasReadinessMCPAdapter:
    """MCP-compatible tool adapter for Atlas readiness orchestration.

    This is intentionally a boundary layer, not an alternate mutation path.
    State-changing operations call `AtlasReadinessService`, which in turn writes
    typed deltas into the normal Atlas review/apply flow.
    """

    server_name = "ruhu_atlas_readiness"

    def __init__(self, *, service: AtlasReadinessService, agent_registry: Any, context: AtlasReadinessMCPContext) -> None:
        if not context.tenant_id:
            raise ValueError("Atlas readiness MCP context requires tenant_id")
        if not context.user_id:
            raise ValueError("Atlas readiness MCP context requires user_id")
        self._service = service
        self._agent_registry = agent_registry
        self._context = context

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if server_name and server_name != self.server_name:
            raise ValueError(f"unknown Atlas readiness MCP server: {server_name}")
        handler = {
            "get_agent_document": self.get_agent_document,
            "generate_evaluation_cases": self.generate_evaluation_cases,
            "run_simulation": self.run_simulation,
            "run_voice_simulation": self.run_voice_simulation,
            "get_trace": self.get_trace,
            "score_trace": self.score_trace,
            "propose_agent_document_deltas": self.propose_agent_document_deltas,
            "patch_agent_document": self.patch_agent_document,
            "rerun_eval_suite": self.rerun_eval_suite,
            "create_publish_report": self.create_publish_report,
        }.get(tool_name)
        if handler is None:
            raise ValueError(f"unsupported Atlas readiness MCP tool: {tool_name}")
        # AR-2.5: the MCP orchestrator is an LLM/provider boundary — redact
        # PII and secrets from every tool result before it leaves the process
        # (traces carry replies + extracted facts; reports carry fact values).
        return self._service.scrub_for_export(handler(arguments))

    def get_agent_document(self, arguments: dict[str, Any]) -> dict[str, Any]:
        agent_id = _required_str(arguments, "agent_id")
        version_target = str(arguments.get("version_target") or "draft")
        document = self._agent_registry.get_agent_document(
            agent_id,
            target=version_target,
            organization_id=self._context.tenant_id,
        )
        return {"agent_id": agent_id, "version_target": version_target, "agent_document": document.model_dump(mode="json")}

    def generate_evaluation_cases(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request = AtlasReadinessRunRequest(
            agent_id=arguments.get("agent_id"),
            workflow_brief=arguments.get("workflow_brief"),
            provider_policy=str(arguments.get("provider_policy") or "deterministic"),  # type: ignore[arg-type]
            case_limit=int(arguments.get("count") or arguments.get("case_limit") or 12),
            voice_case_count=int(arguments.get("voice_case_count") or 0),
            seed=arguments.get("seed"),
        )
        document, _agent_id, _version_id = self._service._resolve_document(  # noqa: SLF001 - MCP boundary delegates to service internals.
            request,
            organization_id=self._context.tenant_id,
        )
        cases = self._service._generate_cases(document=document, request=request)  # noqa: SLF001
        return {"cases": [case.model_dump(mode="json") for case in cases]}

    def run_simulation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        agent_id = _required_str(arguments, "agent_id")
        case = AtlasReadinessCase.model_validate(arguments.get("case") or {})
        document = self._agent_registry.get_agent_document(agent_id, target="draft", organization_id=self._context.tenant_id)
        trace = self._service._run_case(  # noqa: SLF001
            document,
            case,
            agent_id=agent_id,
            agent_name=agent_id,
        )
        return {"trace": trace.model_dump(mode="json")}

    def get_trace(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if isinstance(arguments.get("trace"), dict):
            return {"trace": arguments["trace"]}
        if arguments.get("agent_id") and arguments.get("case"):
            return self.run_simulation(arguments)
        raise ValueError("get_trace requires a trace payload or agent_id + case for readiness preview")

    def run_voice_simulation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        # AR-2.6: a non-deterministic harness makes real (billable) Google
        # STT/TTS calls — require a permission grant, else force deterministic.
        requested_policy = str(arguments.get("provider_policy") or "deterministic")
        if requested_policy != "deterministic" and not self._context.permission_grant_ids:
            return {"status": "blocked", "error": "permission_grant_required_for_live_voice"}
        simulation = self.run_simulation(arguments)
        trace = AtlasReadinessTrace.model_validate(simulation["trace"])
        case = AtlasReadinessCase.model_validate(arguments.get("case") or {})
        harness = self._service._resolve_voice_harness(requested_policy)  # type: ignore[arg-type]  # noqa: SLF001
        result = harness.run_voice_case(run_id=self._context.run_id or "mcp_preview_run", case=case, trace=trace)
        trace.voice_metrics.update(result.metrics)
        return {
            "trace": trace.model_dump(mode="json"),
            "voice_artifacts": [artifact.model_dump(mode="json") for artifact in result.artifacts],
        }

    def score_trace(self, arguments: dict[str, Any]) -> dict[str, Any]:
        case = AtlasReadinessCase.model_validate(arguments.get("case") or {})
        trace = AtlasReadinessTrace.model_validate(arguments.get("trace") or {})
        score = self._service._score_case(case, trace)  # noqa: SLF001
        return {"score": score.model_dump(mode="json")}

    def propose_agent_document_deltas(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._context.permission_grant_ids:
            return {"status": "blocked", "error": "permission_grant_required"}
        request = AtlasReadinessRunRequest(
            agent_id=_required_str(arguments, "agent_id"),
            scope="fix",
            provider_policy=str(arguments.get("provider_policy") or "deterministic"),  # type: ignore[arg-type]
            case_limit=int(arguments.get("case_limit") or 12),
            voice_case_count=int(arguments.get("voice_case_count") or 0),
            seed=arguments.get("seed"),
        )
        summary = self._service.start_run(
            request,
            organization_id=self._context.tenant_id,
            user_id=self._context.user_id,
        )
        return summary.model_dump(mode="json")

    def patch_agent_document(self, arguments: dict[str, Any]) -> dict[str, Any]:
        # Atlas never mutates AgentDocument directly from an MCP call. This
        # compatibility tool creates reviewable typed deltas and returns the
        # Atlas validation session that must be approved/applied by Ruhu.
        result = self.propose_agent_document_deltas(arguments)
        if isinstance(result, dict) and result.get("status") == "blocked":
            return result
        return {"status": "review_required", "review_run": result}

    def rerun_eval_suite(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _required_str(arguments, "run_id")
        # AR-2.6: rerun replays the source run's request verbatim — including
        # scope="fix", which writes new deltas into the review session and takes
        # an apply lock. A fix-scope rerun therefore needs the same grant gate as
        # propose_agent_document_deltas, which this tool would otherwise bypass.
        source = self._service.get_run_summary(run_id, organization_id=self._context.tenant_id)
        if source is None:
            raise KeyError(f"unknown atlas readiness run: {run_id}")
        if source.run.scope == "fix" and not self._context.permission_grant_ids:
            return {"status": "blocked", "error": "permission_grant_required"}
        summary = self._service.rerun(run_id, organization_id=self._context.tenant_id, user_id=self._context.user_id)
        return summary.model_dump(mode="json")

    def create_publish_report(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = str(arguments.get("run_id") or "").strip()
        if run_id:
            report = self._service._readiness_store.get_report(run_id, organization_id=self._context.tenant_id)  # noqa: SLF001
        else:
            agent_id = _required_str(arguments, "agent_id")
            report = self._service._readiness_store.latest_report_for_agent(agent_id, organization_id=self._context.tenant_id)  # noqa: SLF001
        if report is None:
            raise KeyError("unknown atlas readiness report")
        return report.model_dump(mode="json")


def _required_str(arguments: dict[str, Any], key: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value
