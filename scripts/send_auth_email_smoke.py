#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ruhu.api import build_default_app
from ruhu.auth_email_smoke import (
    ensure_auth_email_smoke_identity,
    load_env_file,
    send_auth_email_smoke,
)
from ruhu.email_transport import DevOutboxEmailSender


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send live organization invitation and magic-link smoke emails using the new Ruhu auth shell.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env.development"))
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-display-name", default="Smoke Admin")
    parser.add_argument("--organization-id", default="smoke-org")
    parser.add_argument("--organization-slug", default="smoke")
    parser.add_argument("--organization-name", default="Ruhu Smoke")
    parser.add_argument("--invite-email")
    parser.add_argument("--invite-role", default="developer", choices=["admin", "developer", "analyst"])
    parser.add_argument("--magic-link-email")
    parser.add_argument("--wait-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=0.25)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.invite_email is None and args.magic_link_email is None:
        parser.error("at least one of --invite-email or --magic-link-email is required")
    if args.env_file is not None:
        load_env_file(args.env_file)

    repo_root = Path(__file__).resolve().parents[1]
    app = build_default_app(graph_root=repo_root / "examples" / "graphs")
    auth_service = getattr(app.state, "auth_service", None)
    identity_store = getattr(app.state, "identity_store", None)
    email_sender = getattr(app.state, "email_sender", None)
    if auth_service is None or identity_store is None:
        raise SystemExit("auth is not configured; set RUHU_AUTH_DATABASE_URL and RUHU_AUTH_JWT_SECRET")
    if email_sender is None or isinstance(email_sender, DevOutboxEmailSender):
        raise SystemExit("SMTP email is not configured; current sender is dev outbox")

    organization, admin_user = ensure_auth_email_smoke_identity(
        auth_service=auth_service,
        organization_id=args.organization_id,
        organization_slug=args.organization_slug,
        organization_name=args.organization_name,
        admin_email=args.admin_email,
        admin_display_name=args.admin_display_name,
        magic_link_email=args.magic_link_email,
    )
    results = asyncio.run(
        send_auth_email_smoke(
            app=app,
            auth_service=auth_service,
            organization_id=organization.organization_id,
            admin_user_id=admin_user.user_id,
            invite_email=args.invite_email,
            invite_role=args.invite_role,
            magic_link_email=args.magic_link_email,
            wait_timeout_seconds=args.wait_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    )
    print(json.dumps([item.as_dict() for item in results], indent=2, default=str))
    if any(item.final_status != "sent" for item in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
