"""Console pages and static widget assets — extracted from api.py (RP-3.1 step 2).

Covers the built-in HTML consoles (/playground, /kpi, /intent-tags,
/widget-preview) and the widget embed/LiveKit client scripts. Page-renderer
imports stay module-level (the renderers are pure functions over shipped
assets). No ``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ..api_auth import get_request_auth_context
from ..intent_tags_console import intent_tags_console_html
from ..kpi_console import kpi_console_html
from ..playground import playground_html
from ..public_widget import widget_embed_script, widget_preview_html
from ..services.widget_assets import widget_livekit_client_asset_path


def build_console_pages_router(*, auth_enabled: bool) -> APIRouter:
    """Build the console/static-assets router.

    ``auth_enabled`` mirrors create_app()'s derived flag (auth resolver and
    auth service both present): the KPI and intent-tags consoles redirect
    anonymous visitors to /login only when auth is enabled.
    """
    router = APIRouter()

    @router.get("/playground", response_class=HTMLResponse)
    def playground() -> str:
        return playground_html()

    @router.get("/kpi", response_class=HTMLResponse)
    def kpi_console_page(request: Request) -> HTMLResponse:
        if auth_enabled:
            context = get_request_auth_context(request)
            if context.principal is None:
                return RedirectResponse(url="/login", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return HTMLResponse(kpi_console_html())

    @router.get("/intent-tags", response_class=HTMLResponse)
    def intent_tags_console_page(request: Request) -> HTMLResponse:
        if auth_enabled:
            context = get_request_auth_context(request)
            if context.principal is None:
                return RedirectResponse(url="/login", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return HTMLResponse(intent_tags_console_html())

    @router.get("/widget-preview", response_class=HTMLResponse)
    def widget_preview(agent_id: str = Query(...)) -> str:
        return widget_preview_html(agent_id)

    @router.get("/widget.js")
    def widget_script() -> Response:
        return Response(content=widget_embed_script(), media_type="application/javascript")

    @router.get("/widget-livekit-client.js")
    def widget_livekit_client_script() -> FileResponse:
        asset_path = widget_livekit_client_asset_path()
        if asset_path is None:
            raise HTTPException(status_code=404, detail="widget livekit client asset is unavailable")
        return FileResponse(asset_path, media_type="application/javascript")

    return router
