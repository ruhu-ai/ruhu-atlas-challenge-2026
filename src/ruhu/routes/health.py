"""Health, liveness, readiness and JWKS routes — extracted from api.py (RP-3.1 step 2).

No ``tags=`` / ``prefix=`` and unchanged handler names: these routes were
registered untagged at the app root, and operation ids derive from the
function names (hazard H1 — schema neutrality).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from fastapi import APIRouter, Response, status

from ..services.readiness import run_readiness_probes

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

    from ..auth import JWTCodec
    from ..runtime_config import RuntimeSettings


def build_health_router(
    *,
    runtime_session_factory: sessionmaker | None,
    settings: RuntimeSettings,
    jwt_codec_provider: Callable[[], "JWTCodec | None"],
) -> APIRouter:
    """Build the health/JWKS router.

    ``jwt_codec_provider`` is a zero-arg callable (rather than the codec
    itself) so the router stays correct if the auth service is absent —
    the JWKS endpoint then serves an empty key set, matching the previous
    inline behaviour.
    """
    router = APIRouter()

    @router.get("/live")
    def live() -> dict[str, str]:
        # Liveness: the process is not wedged. K8s restarts the pod if this
        # fails. Deliberately shallow — no dependency checks — so a transient
        # DB hiccup doesn't trigger a restart storm.
        return {"status": "ok"}

    @router.get("/health")
    def health() -> dict[str, str]:
        # Historical shallow health contract used by tests and simple clients.
        # Dependency-heavy readiness lives at /ready.
        return {"status": "ok"}

    @router.get("/ready")
    async def ready(response: Response) -> dict[str, object]:
        # Readiness: process can serve traffic. Routing stops when this
        # fails. Probes (DB pools, Redis when configured, credential cipher
        # outside development) live in services.readiness, each bounded so a
        # slow dependency degrades gracefully instead of cascading.
        overall_ok, probes = await run_readiness_probes(
            session_factory=runtime_session_factory,
            settings=settings,
        )
        if not overall_ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ok" if overall_ok else "degraded", "probes": probes}

    @router.get("/.well-known/jwks.json")
    def jwks(response: Response) -> dict[str, list[dict[str, object]]]:
        response.headers["Cache-Control"] = "public, max-age=300"
        codec = jwt_codec_provider()
        if codec is None:
            return {"keys": []}
        return codec.public_jwks()

    return router
