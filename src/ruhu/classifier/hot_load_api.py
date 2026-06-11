"""WI-6.6 — admin endpoint that drives vLLM hot-load reconciliation.

Two endpoints:

- ``POST /classifier/loras/{lora_id}/hot-load`` — load the row's
  artifact into the connected vLLM cluster. Idempotent: if vLLM already
  has the LoRA loaded, returns ``already_loaded``.

- ``POST /classifier/loras/{lora_id}/hot-unload`` — evict the LoRA
  from vLLM. Used during shadow-aging cleanup once the 7-day retention
  on demoted production LoRAs expires.

The endpoints are admin-grade (no auth gating in this module — the
deploy wraps them with the same auth boundary used for other ops
admin paths). They wrap ``classifier.hot_load.VLLMHotLoadClient`` and
do the registry-row lookup so callers (typically the promotion
pipeline or an out-of-band reconciler) only need the lora_id.

Wiring: ``install_hot_load_router(app, *, hot_load_client,
session_factory)``. Mountable from ``api.py`` alongside the other
``install_*_router`` calls without touching the rest of the file.
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from .hot_load import (
    HotLoadResult,
    HotUnloadResult,
    VLLMHotLoadClient,
)

SessionFactory = Callable[[], object]


class HotLoadResponse(BaseModel):
    lora_id: str
    lora_name: str
    outcome: str
    elapsed_ms: int
    detail: str


class HotUnloadResponse(BaseModel):
    lora_id: str
    lora_name: str
    outcome: str
    elapsed_ms: int
    detail: str


def install_hot_load_router(
    app: FastAPI,
    *,
    hot_load_client: VLLMHotLoadClient,
    session_factory: SessionFactory,
) -> None:
    """Mount the hot-load admin endpoints on an existing FastAPI app."""
    router = APIRouter(prefix="/classifier", tags=["classifier"])

    @router.post(
        "/loras/{lora_id}/hot-load",
        response_model=HotLoadResponse,
    )
    def hot_load(lora_id: str) -> HotLoadResponse:
        from ..db_models import ClassifierLoraRecord

        session = session_factory()
        try:
            record = session.get(ClassifierLoraRecord, lora_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"lora_id {lora_id!r} not found")
            lora_name = record.lora_name
            model_uri = record.model_uri
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

        result: HotLoadResult = hot_load_client.load(
            lora_name=lora_name, model_uri=model_uri
        )
        return HotLoadResponse(
            lora_id=lora_id,
            lora_name=result.lora_name,
            outcome=result.outcome,
            elapsed_ms=result.elapsed_ms,
            detail=result.detail,
        )

    @router.post(
        "/loras/{lora_id}/hot-unload",
        response_model=HotUnloadResponse,
    )
    def hot_unload(lora_id: str) -> HotUnloadResponse:
        from ..db_models import ClassifierLoraRecord

        session = session_factory()
        try:
            record = session.get(ClassifierLoraRecord, lora_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"lora_id {lora_id!r} not found")
            lora_name = record.lora_name
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

        result: HotUnloadResult = hot_load_client.unload(lora_name=lora_name)
        return HotUnloadResponse(
            lora_id=lora_id,
            lora_name=result.lora_name,
            outcome=result.outcome,
            elapsed_ms=result.elapsed_ms,
            detail=result.detail,
        )

    app.include_router(router)


__all__ = [
    "HotLoadResponse",
    "HotUnloadResponse",
    "install_hot_load_router",
]
