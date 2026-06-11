"""Evaluation run endpoints — extracted from api.py (RP-3.1 step 4).

Covers /agents/{agent_id}/evaluation-runs, /evaluation-runs/* (get, results,
case review, stop), /evaluation-runtime/status and
/agents/{agent_id}/latest-qualified-run, in the original inline order.

The ``EvaluationRunCreateRequest`` DTO still lives in ``ruhu.api``, so this
module is imported by ``create_app()`` AT THE MOUNT SITE rather than at
api.py's module top — a top-level import would be circular while api.py is
still mid-import. No ``tags=`` / ``prefix=`` and unchanged handler names
(hazard H1).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from fastapi import APIRouter, Depends, HTTPException, Request

# DTOs at module top (hazard H7: PEP 563 return annotations resolve against
# this module's globals).
from ..api import EvaluationRunCreateRequest
from ..api_auth import RequestAuthContext
from ..auth_deps import make_reviewer_context_dep
from ..services.org_scope import (
    make_organization_id_for_request,
    user_id_for_context,
)
from ..simulation_eval import (
    EvaluationCaseResult,
    EvaluationCaseReview,
    EvaluationPolicyConfig,
    EvaluationRun,
    EvaluationRuntimeStatus,
    SimulationFixture,
)

if TYPE_CHECKING:
    from ..registry import SQLAlchemyAgentRegistry


def build_evaluation_runs_router(
    *,
    simulation_fixture_store,
    evaluation_service,
    evaluation_runtime,
    evaluation_run_store,
    agent_registry: "SQLAlchemyAgentRegistry",
    resolve_agent_snapshot: Callable,
    agent_evaluation_policy: Callable[..., EvaluationPolicyConfig],
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> APIRouter:
    """Build the evaluation-runs router.

    ``resolve_agent_snapshot`` / ``agent_evaluation_policy`` are
    create_app() closures shared with the agents publish-review surface —
    they stay in api.py until the agents-core extraction at blueprint
    step 10.
    """
    router = APIRouter()

    _require_runtime_reviewer_context = make_reviewer_context_dep(auth_enabled)
    _organization_id_for_request = make_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _user_id_for_context = user_id_for_context

    def _resolved_evaluation_policy(
        agent_id: str,
        *,
        organization_id: str | None,
        minimum_pass_rate_ratio: float | None = None,
        allow_warning_failures: bool | None = None,
        max_qualified_run_age_hours: int | None = None,
    ) -> EvaluationPolicyConfig:
        policy = agent_evaluation_policy(agent_id, organization_id=organization_id)
        updates: dict[str, object] = {}
        if minimum_pass_rate_ratio is not None:
            updates["minimum_pass_rate_ratio"] = minimum_pass_rate_ratio
        if allow_warning_failures is not None:
            updates["allow_warning_failures"] = allow_warning_failures
        if max_qualified_run_age_hours is not None:
            updates["max_qualified_run_age_hours"] = max_qualified_run_age_hours
        if updates:
            policy = policy.model_copy(update=updates)
        return policy

    @router.post("/agents/{agent_id}/evaluation-runs", response_model=EvaluationRun)
    def create_evaluation_run(
        agent_id: str,
        payload: EvaluationRunCreateRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> EvaluationRun:
        snapshot, organization_id = resolve_agent_snapshot(
            request,
            agent_id,
            target="draft",
            agent_version_id=payload.agent_version_id,
        )
        if payload.fixture_ids:
            fixtures: list[SimulationFixture] = []
            for fixture_id in payload.fixture_ids:
                fixture = simulation_fixture_store.load(fixture_id, organization_id=organization_id)
                if fixture is None:
                    raise HTTPException(status_code=404, detail=f"unknown simulation fixture: {fixture_id}")
                if fixture.agent_id != agent_id:
                    raise HTTPException(status_code=409, detail=f"fixture {fixture_id} belongs to a different agent")
                fixtures.append(fixture)
        else:
            fixtures = simulation_fixture_store.list_for_agent(
                agent_id,
                organization_id=organization_id,
                is_active=True,
            )
        if not fixtures:
            raise HTTPException(status_code=409, detail="no simulation fixtures selected for evaluation")
        policy = _resolved_evaluation_policy(
            agent_id,
            organization_id=organization_id,
            minimum_pass_rate_ratio=payload.minimum_pass_rate_ratio,
            allow_warning_failures=payload.allow_warning_failures,
        )
        if payload.execution_mode == "sync":
            return evaluation_service.run(
                snapshot,
                fixtures,
                mode=payload.mode,
                source=payload.source,
                organization_id=organization_id,
                gate_eligible=payload.gate_eligible,
                triggered_by_user_id=_user_id_for_context(context),
                minimum_pass_rate_ratio=policy.minimum_pass_rate_ratio,
                allow_warning_failures=policy.allow_warning_failures,
            )
        return evaluation_runtime.schedule_run(
            snapshot,
            fixtures,
            mode=payload.mode,
            source=payload.source,
            organization_id=organization_id,
            gate_eligible=payload.gate_eligible,
            triggered_by_user_id=_user_id_for_context(context),
            minimum_pass_rate_ratio=policy.minimum_pass_rate_ratio,
            allow_warning_failures=policy.allow_warning_failures,
        )

    @router.get("/agents/{agent_id}/evaluation-runs", response_model=list[EvaluationRun])
    def list_evaluation_runs(
        agent_id: str,
        request: Request,
        agent_version_id: str | None = None,
        gate_eligible: bool | None = None,
    ) -> list[EvaluationRun]:
        organization_id = _organization_id_for_request(request)
        return evaluation_run_store.list_for_agent(
            agent_id,
            organization_id=organization_id,
            agent_version_id=agent_version_id,
            gate_eligible=gate_eligible,
        )

    @router.get("/evaluation-runs/{evaluation_run_id}", response_model=EvaluationRun)
    def get_evaluation_run(evaluation_run_id: str, request: Request) -> EvaluationRun:
        organization_id = _organization_id_for_request(request)
        run = evaluation_run_store.load(evaluation_run_id, organization_id=organization_id)
        if run is None:
            raise HTTPException(status_code=404, detail="unknown evaluation run")
        return run

    @router.get("/evaluation-runs/{evaluation_run_id}/results", response_model=list[EvaluationCaseResult])
    def list_evaluation_run_results(evaluation_run_id: str, request: Request) -> list[EvaluationCaseResult]:
        organization_id = _organization_id_for_request(request)
        run = evaluation_run_store.load(evaluation_run_id, organization_id=organization_id)
        if run is None:
            raise HTTPException(status_code=404, detail="unknown evaluation run")
        return run.results

    @router.get("/evaluation-runs/{evaluation_run_id}/results/{case_result_id}", response_model=EvaluationCaseResult)
    def get_evaluation_run_result(
        evaluation_run_id: str,
        case_result_id: str,
        request: Request,
    ) -> EvaluationCaseResult:
        organization_id = _organization_id_for_request(request)
        run = evaluation_run_store.load(evaluation_run_id, organization_id=organization_id)
        if run is None:
            raise HTTPException(status_code=404, detail="unknown evaluation run")
        for result in run.results:
            if result.case_result_id == case_result_id:
                return result
        raise HTTPException(status_code=404, detail="unknown evaluation case result")

    @router.get("/evaluation-runs/{evaluation_run_id}/results/{case_result_id}/review", response_model=EvaluationCaseReview)
    def get_evaluation_case_review(
        evaluation_run_id: str,
        case_result_id: str,
        request: Request,
    ) -> EvaluationCaseReview:
        organization_id = _organization_id_for_request(request)
        review = evaluation_service.build_case_review(
            evaluation_run_id,
            case_result_id,
            organization_id=organization_id,
        )
        if review is None:
            raise HTTPException(status_code=404, detail="unknown evaluation case review")
        return review

    @router.post("/evaluation-runs/{evaluation_run_id}/stop", response_model=EvaluationRun)
    def stop_evaluation_run(
        evaluation_run_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> EvaluationRun:
        organization_id = _organization_id_for_request(request)
        run = evaluation_service.request_stop(evaluation_run_id, organization_id=organization_id)
        if run is None:
            raise HTTPException(status_code=404, detail="unknown evaluation run")
        return run

    @router.get("/evaluation-runtime/status", response_model=EvaluationRuntimeStatus)
    def get_evaluation_runtime_status() -> EvaluationRuntimeStatus:
        return evaluation_runtime.status()

    @router.get("/agents/{agent_id}/latest-qualified-run", response_model=EvaluationRun)
    def get_latest_qualified_run(
        agent_id: str,
        request: Request,
        agent_version_id: str | None = None,
    ) -> EvaluationRun:
        organization_id = _organization_id_for_request(request)
        try:
            if agent_version_id is not None:
                snapshot = agent_registry.get_version_snapshot(
                    agent_version_id,
                    organization_id=organization_id,
                )
                if snapshot.agent_id != agent_id:
                    raise HTTPException(status_code=409, detail="agent_version_id belongs to a different agent")
                resolved_version_id = agent_version_id
            else:
                resolved_version_id = agent_registry.resolve_version_id(
                    agent_id,
                    target="draft",
                    organization_id=organization_id,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        run = evaluation_run_store.latest_qualified(
            agent_id,
            resolved_version_id,
            organization_id=organization_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="no qualified evaluation run found")
        return run

    return router
