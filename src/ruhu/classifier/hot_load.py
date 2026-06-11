"""WI-6.6 — vLLM hot-load wiring.

When the LoRA registry flips ``status="production"`` (via
``promotion_api`` after the eval gate passes), vLLM needs to load the
new adapter into its in-memory pool. vLLM exposes
``POST /v1/load_lora_adapter`` for this purpose.

This module wraps the load + unload calls behind a single
``VLLMHotLoadClient`` so:

- The promotion API can call it inside the same request that flips
  status, *or* an out-of-band reconciler can pick up production rows
  and load them lazily — both call the same client.
- Tests inject the http_post callable so we exercise the full wire
  shape without a live vLLM cluster.
- Errors are coerced into ``HotLoadResult`` rather than raising, so a
  failed load doesn't undo the registry promotion (the operator can
  see a row with ``status="production"`` plus a hot-load failure event
  and reconcile manually).

vLLM cluster prerequisites (deployed via WI-3.1 Helm/Terraform):

- ``--enable-lora --max-loras 32 --max-lora-rank 32`` startup args
- A LoRA storage URI mounted readable by the vLLM pods
  (``RUHU_CLASSIFIER_LORA_STORAGE_URI``)
- Network reachability from the runtime API to the vLLM admin port

Spec: ``docs/pre-fill-intent-classifier-design/07-work-items.md``
§WI-6.6, ``04-runtime-spec.md`` §Deployment topology.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_LOAD_PATH = "/v1/load_lora_adapter"
DEFAULT_UNLOAD_PATH = "/v1/unload_lora_adapter"


HotLoadOutcome = Literal["loaded", "already_loaded", "error"]
HotUnloadOutcome = Literal["unloaded", "not_loaded", "error"]


@dataclass(slots=True, frozen=True)
class HotLoadResult:
    """Result of one ``load_lora_adapter`` call."""

    lora_name: str
    outcome: HotLoadOutcome
    elapsed_ms: int
    detail: str = ""


@dataclass(slots=True, frozen=True)
class HotUnloadResult:
    """Result of one ``unload_lora_adapter`` call."""

    lora_name: str
    outcome: HotUnloadOutcome
    elapsed_ms: int
    detail: str = ""


@dataclass(slots=True)
class VLLMHotLoadClient:
    """Thin httpx-based client for vLLM's LoRA admin endpoints.

    ``http_post`` is injectable — production wiring uses httpx; tests
    pass a fake. The client doesn't take a hard httpx import for
    callers that mock it.
    """

    base_url: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    load_path: str = DEFAULT_LOAD_PATH
    unload_path: str = DEFAULT_UNLOAD_PATH
    http_post: Callable[..., Any] | None = None
    access_token_loader: Callable[[], str | None] | None = None

    def load(self, *, lora_name: str, model_uri: str) -> HotLoadResult:
        """Tell vLLM to load ``lora_name`` from ``model_uri`` into its pool."""
        payload = {"lora_name": lora_name, "lora_path": model_uri}
        url = self.base_url.rstrip("/") + self.load_path
        return self._dispatch_load(payload, url=url, lora_name=lora_name)

    def unload(self, *, lora_name: str) -> HotUnloadResult:
        """Tell vLLM to evict ``lora_name`` from its pool."""
        payload = {"lora_name": lora_name}
        url = self.base_url.rstrip("/") + self.unload_path
        return self._dispatch_unload(payload, url=url, lora_name=lora_name)

    # ── internals ──────────────────────────────────────────────────────────

    def _dispatch_load(
        self,
        payload: dict[str, Any],
        *,
        url: str,
        lora_name: str,
    ) -> HotLoadResult:
        start = time.perf_counter()
        try:
            response = self._post(url=url, json=payload)
        except Exception as exc:
            return HotLoadResult(
                lora_name=lora_name,
                outcome="error",
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                detail=_classify_exception(exc),
            )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        outcome = self._parse_load_outcome(response)
        detail = _excerpt(response)
        return HotLoadResult(
            lora_name=lora_name,
            outcome=outcome,
            elapsed_ms=elapsed_ms,
            detail=detail,
        )

    def _dispatch_unload(
        self,
        payload: dict[str, Any],
        *,
        url: str,
        lora_name: str,
    ) -> HotUnloadResult:
        start = time.perf_counter()
        try:
            response = self._post(url=url, json=payload)
        except Exception as exc:
            return HotUnloadResult(
                lora_name=lora_name,
                outcome="error",
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                detail=_classify_exception(exc),
            )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        outcome = self._parse_unload_outcome(response)
        detail = _excerpt(response)
        return HotUnloadResult(
            lora_name=lora_name,
            outcome=outcome,
            elapsed_ms=elapsed_ms,
            detail=detail,
        )

    def _post(self, *, url: str, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        if self.http_post is not None:
            return self.http_post(url=url, json=json, headers=self._headers(), timeout=self.timeout_seconds)
        import httpx  # type: ignore[import-not-found]

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, json=json, headers=self._headers())
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return {"_raw_text": response.text}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token_loader is not None:
            token = self.access_token_loader()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _parse_load_outcome(response: dict[str, Any] | None) -> HotLoadOutcome:
        """vLLM's load_lora_adapter returns 200 + a status string.

        Across vLLM versions the body shape varies:
        - {"status": "ok"}                       → loaded
        - {"status": "already_loaded"}           → already_loaded
        - {"message": "...already loaded..."}    → already_loaded
        - "" or empty body                       → loaded (assume 200 = success)
        """
        if not response:
            return "loaded"
        status = str(response.get("status") or "").lower()
        message = str(response.get("message") or "").lower()
        if status == "already_loaded" or "already loaded" in message:
            return "already_loaded"
        return "loaded"

    @staticmethod
    def _parse_unload_outcome(response: dict[str, Any] | None) -> HotUnloadOutcome:
        if not response:
            return "unloaded"
        status = str(response.get("status") or "").lower()
        message = str(response.get("message") or "").lower()
        if status == "not_loaded" or "not loaded" in message or "no such" in message:
            return "not_loaded"
        return "unloaded"


def _classify_exception(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc).lower()
    if name in {"TimeoutException", "ReadTimeout", "ConnectTimeout"} or "timeout" in text:
        return "timeout"
    if name in {"ConnectError", "ConnectionError", "RemoteProtocolError"}:
        return "connection_error"
    if "5" in text and "status" in text:
        return "5xx"
    return f"{name.lower()}: {exc}"


def _excerpt(response: dict[str, Any] | None) -> str:
    if not response:
        return ""
    import json as _json

    try:
        return _json.dumps(response, ensure_ascii=False)[:200]
    except Exception:
        return ""


__all__ = [
    "DEFAULT_LOAD_PATH",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_UNLOAD_PATH",
    "HotLoadOutcome",
    "HotLoadResult",
    "HotUnloadOutcome",
    "HotUnloadResult",
    "VLLMHotLoadClient",
]
