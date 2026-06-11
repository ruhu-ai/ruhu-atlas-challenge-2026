"""WI-6.7 — training-scheduler FastAPI router.

Exposes:

- ``POST /classifier/agents/{agent_id}/train`` — manual training-run
  enqueue per spec §Manual training runs. Body carries the same
  trigger inputs the scheduler builds for auto runs; the API layer
  applies the same cool-down logic and returns the decision.

- ``GET /classifier/agents/{agent_id}/training-status`` — dry-run
  evaluation of the auto-trigger predicates against caller-supplied
  inputs. Useful for ops and for the eventual ``training_worker.py``
  that polls.

The runtime's job ends at "decision". The actual training run lives
in ``ruhu-ai-training/qwen``; the training pipeline reads the
decision (e.g. via a separate notification or a job queue the
runtime writes to) and dispatches the real work. Keeping the API
narrow keeps the runtime surface small.
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from .training_scheduler import (
    TrainingScheduleThresholds,
    TrainingTriggerInputs,
    TriggerCheck,
    TriggerDecision,
    evaluate_manual_request,
    evaluate_triggers,
)


class TriggerCheckResponse(BaseModel):
    kind: str
    fired: bool
    detail: str


class TriggerDecisionResponse(BaseModel):
    agent_id: str
    should_train: bool
    cooldown_active: bool
    cooldown_until: str | None
    triggers: list[TriggerCheckResponse]


class ManualTrainRequest(BaseModel):
    """Body for the manual-train endpoint.

    ``override_cooldown`` lets ops force a run during the 24h cool-down
    window (logged in the response, audited via the ``triggers`` list
    where the manual entry is always present).
    """

    inputs: TrainingTriggerInputs
    override_cooldown: bool = False


class AutoEvaluateRequest(BaseModel):
    """Body for the dry-run / status endpoint."""

    inputs: TrainingTriggerInputs


def install_training_router(
    app: FastAPI,
    *,
    thresholds: TrainingScheduleThresholds | None = None,
) -> None:
    """Mount the scheduler endpoints on an existing FastAPI app."""
    router = APIRouter(prefix="/classifier", tags=["classifier"])

    @router.post(
        "/agents/{agent_id}/train",
        response_model=TriggerDecisionResponse,
    )
    def manual_train(
        agent_id: str,
        request: ManualTrainRequest,
    ) -> TriggerDecisionResponse:
        # Honour the path agent_id over the body's — keeps the URL
        # canonical even if a caller's body drifts.
        inputs = request.inputs.model_copy(update={"agent_id": agent_id})
        decision = evaluate_manual_request(
            inputs,
            thresholds=thresholds,
            override_cooldown=request.override_cooldown,
        )
        return _to_response(decision)

    @router.post(
        "/agents/{agent_id}/training-status",
        response_model=TriggerDecisionResponse,
    )
    def auto_evaluate(
        agent_id: str,
        request: AutoEvaluateRequest,
    ) -> TriggerDecisionResponse:
        inputs = request.inputs.model_copy(update={"agent_id": agent_id})
        decision = evaluate_triggers(inputs, thresholds=thresholds)
        return _to_response(decision)

    app.include_router(router)


def _to_response(decision: TriggerDecision) -> TriggerDecisionResponse:
    return TriggerDecisionResponse(
        agent_id=decision.agent_id,
        should_train=decision.should_train,
        cooldown_active=decision.cooldown_active,
        cooldown_until=(
            decision.cooldown_until.isoformat()
            if decision.cooldown_until is not None
            else None
        ),
        triggers=[
            TriggerCheckResponse(kind=t.kind, fired=t.fired, detail=t.detail)
            for t in decision.triggers
        ],
    )


__all__ = [
    "AutoEvaluateRequest",
    "ManualTrainRequest",
    "TriggerCheckResponse",
    "TriggerDecisionResponse",
    "install_training_router",
]
