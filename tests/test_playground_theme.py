from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ruhu.api import create_app
from ruhu.composition import build_minimal_runtime
from ruhu.kernel import ConversationKernel
from ruhu.registry import FileAgentRegistry


def test_playground_route_uses_offline_theme_tokens() -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = create_app(
            build_minimal_runtime(
                kernel=ConversationKernel(),
                agent_registry=FileAgentRegistry(agent_root_path),
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/playground")
            assert response.status_code == 200

        html = response.text
        assert "Ruhu Runtime Playground" in html
        assert "fonts.googleapis.com" not in html
        assert "fonts.gstatic.com" not in html
        assert '--sans: "Inter", system-ui' in html
        assert '--mono: "JetBrains Mono", "IBM Plex Mono"' in html
        assert "--primary: 14 82% 45%;" in html
        assert "--text-sm: 0.875rem;" in html
        assert "font-family: var(--sans);" in html

    asyncio.run(run())


def test_playground_route_exposes_inline_playground_controls() -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = create_app(
            build_minimal_runtime(
                kernel=ConversationKernel(),
                agent_registry=FileAgentRegistry(agent_root_path),
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/playground")
            assert response.status_code == 200

        html = response.text
        assert 'id="feedback-banner"' in html
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html
        assert 'id="copy-conversation-button"' in html
        assert 'id="clear-transcript-button"' in html
        assert 'const PREFERENCES_KEY = "ruhu.playground.preferences.v1";' in html
        assert "navigator.clipboard" in html
        assert 'Raw trace JSON' in html
        # WI-5.4 dropped the `policy:` row from the fallback summary; the
        # panel now shows actual fallback usage + controller of record.
        assert 'actual:' in html
        assert 'controller:' in html
        assert "alert(" not in html

    asyncio.run(run())
