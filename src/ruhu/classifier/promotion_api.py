"""WI-6.9 — promotion-gate FastAPI router.

Exposes ``POST /classifier/loras/{lora_id}/evaluate`` so the training
pipeline (running in ``ruhu-ai-training/qwen``) can submit a candidate
LoRA's eval report and have the runtime apply the promotion gates +
flip ``status="production"`` on pass. On fail, the candidate stays as
``status="candidate"``.

Wiring: an ``api.py``-level installer is provided
(``install_promotion_router``) so this can be mounted alongside the
existing routers without bringing the rest of api.py into this module.
The training pipeline POSTs JSON shaped per
``EvaluatePromotionRequest`` and reads the ``PromotionDecision`` shape
back.

Design choices:

- The router is *idempotent* on already-production rows (re-applying
  the same eval report against the same lora_id returns the
  current decision; doesn't re-flip status).
- The router does *not* construct training-side state — it consumes a
  finished eval report. Producing the report lives in
  ``ruhu-ai-training/qwen`` per the project split.
- Promotion is gated on the registry record's existence + the eval
  payload; ``vLLM hot-load wiring (WI-6.6)`` is a separate concern
  triggered by an external reconciler that watches registry status.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .promotion import (
    BaselineReport,
    EvalReport,
    PromotionDecision,
    PromotionGateThresholds,
    evaluate,
)
from .registry import promote_to_production, to_entry

SessionFactory = Callable[[], object]


class EvaluatePromotionRequest(BaseModel):
    """Payload from the training pipeline."""

    eval_report: EvalReport
    baseline: BaselineReport | None = None
    base_model_macro_f1: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Cold-start regime: macro-F1 of the base model (no LoRA) on the "
            "same eval set. Required when no prior production LoRA exists."
        ),
    )


class GateCheckResponse(BaseModel):
    name: str
    outcome: str
    detail: str


class EvaluatePromotionResponse(BaseModel):
    lora_id: str
    promote: bool
    promoted: bool = Field(
        description="True iff the registry was actually flipped to production."
    )
    regime: str
    status: str = Field(description="The post-call registry status of the row.")
    checks: list[GateCheckResponse]


def install_promotion_router(
    app: FastAPI,
    *,
    session_factory: SessionFactory,
    thresholds: PromotionGateThresholds | None = None,
) -> None:
    """Mount the promotion router onto an existing FastAPI app."""
    router = APIRouter(prefix="/classifier", tags=["classifier"])

    @router.post(
        "/loras/{lora_id}/evaluate",
        response_model=EvaluatePromotionResponse,
    )
    def evaluate_promotion(
        lora_id: str,
        request: EvaluatePromotionRequest,
    ) -> EvaluatePromotionResponse:
        if request.baseline is None and request.base_model_macro_f1 is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "promotion request requires either baseline (steady-state) "
                    "or base_model_macro_f1 (cold-start)"
                ),
            )

        decision = evaluate(
            request.eval_report,
            baseline=request.baseline,
            base_model_macro_f1=request.base_model_macro_f1,
            thresholds=thresholds,
        )

        session = session_factory()
        promoted = False
        try:
            from ..db_models import ClassifierLoraRecord

            record = session.get(ClassifierLoraRecord, lora_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"lora_id {lora_id!r} not found")

            if decision.promote and record.status == "candidate":
                record = promote_to_production(session, lora_id=lora_id)
                session.commit()
                promoted = True
            current_status = record.status
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

        return EvaluatePromotionResponse(
            lora_id=lora_id,
            promote=decision.promote,
            promoted=promoted,
            regime=decision.regime,
            status=current_status,
            checks=[
                GateCheckResponse(name=c.name, outcome=c.outcome, detail=c.detail)
                for c in decision.checks
            ],
        )

    app.include_router(router)


__all__ = [
    "EvaluatePromotionRequest",
    "EvaluatePromotionResponse",
    "GateCheckResponse",
    "install_promotion_router",
]
