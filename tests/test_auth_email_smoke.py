from __future__ import annotations

import asyncio
from pathlib import Path

from ruhu.api import create_app
from ruhu.api_auth import AuthContextResolver
from ruhu.composition import build_minimal_runtime
from ruhu.services.api_services import ApiServices
from ruhu.auth import AuthService, JWTCodec
from ruhu.auth_email_smoke import ensure_auth_email_smoke_identity, send_auth_email_smoke
from ruhu.identity import InMemoryIdentityStore
from ruhu.kernel import ConversationKernel
from ruhu.registry import FileAgentRegistry
from ruhu.runtime_config import RuntimeSettings

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


def _build_auth_app() -> tuple[object, AuthService]:
    agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
    identity_store = InMemoryIdentityStore()
    auth_service = AuthService(
        identity_store=identity_store,
        jwt_codec=JWTCodec(secret=TEST_HS256_SECRET),
    )
    app = create_app(
        build_minimal_runtime(
            kernel=ConversationKernel(),
            agent_registry=FileAgentRegistry(agent_root_path),
        ),
        ApiServices(
            auth_resolver=AuthContextResolver(auth_service=auth_service),
            identity_store=identity_store,
            auth_service=auth_service,
        ),
        settings=RuntimeSettings(auth_allowed_redirect_origins=["http://smoke.local"]),
    )
    return app, auth_service


def test_send_auth_email_smoke_uses_live_routes_with_dev_outbox() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        organization, admin_user = ensure_auth_email_smoke_identity(
            auth_service=auth_service,
            organization_id="org-smoke",
            organization_slug="smoke",
            organization_name="Smoke Org",
            admin_email="admin@example.com",
            magic_link_email="member@example.com",
        )

        results = await send_auth_email_smoke(
            app=app,
            auth_service=auth_service,
            organization_id=organization.organization_id,
            admin_user_id=admin_user.user_id,
            invite_email="invitee@example.com",
            magic_link_email="member@example.com",
        )

        assert len(results) == 2
        assert [item.kind for item in results] == ["organization_invitation", "magic_link"]
        assert all(item.transport == "dev_outbox" for item in results)
        assert all(item.final_status == "sent" for item in results)

        outbox = getattr(app.state, "email_outbox")
        assert len(outbox) == 2
        assert outbox[0].to_email == "invitee@example.com"
        assert outbox[1].to_email == "member@example.com"

    asyncio.run(run())


def test_send_auth_email_smoke_requires_distinct_invite_and_magic_link_recipients() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        organization, admin_user = ensure_auth_email_smoke_identity(
            auth_service=auth_service,
            organization_id="org-smoke",
            organization_slug="smoke",
            organization_name="Smoke Org",
            admin_email="admin@example.com",
            magic_link_email="same@example.com",
        )

        try:
            await send_auth_email_smoke(
                app=app,
                auth_service=auth_service,
                organization_id=organization.organization_id,
                admin_user_id=admin_user.user_id,
                invite_email="same@example.com",
                magic_link_email="same@example.com",
            )
        except ValueError as exc:
            assert "must be different" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")

    asyncio.run(run())
