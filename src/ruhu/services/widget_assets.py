"""Static asset resolution for the embeddable customer widget."""

from __future__ import annotations

from pathlib import Path


def widget_livekit_client_asset_path() -> Path | None:
    widget_asset_root = Path(__file__).resolve().parents[3] / "frontend" / "public" / "widget"
    candidates = sorted(widget_asset_root.glob("widget-livekit-client.esm*.js"))
    if not candidates:
        return None
    return candidates[-1]
