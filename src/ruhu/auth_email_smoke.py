from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

import httpx
from fastapi import FastAPI

from .auth import AuthService, ConflictError
from .email_normalization import normalize_email
from .email_transport import EmailDeliveryState
from .env_files import load_env_file
from .identity import Organization, OrganizationMembership, OrganizationRole, User


@dataclass(frozen=True, slots=True)
class AuthEmailSmokeResult:
    kind: Literal["organization_invitation", "magic_link"]
    email: str
    http_status: int
    transport: Literal["smtp", "dev_outbox"]
    delivery_id: str | None
    initial_status: Literal["sent", "queued", "failed"]
    final_status: Literal["sent", "queued", "failed"]
    attempt_count: int
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_auth_email_smoke_identity(
    *,
    auth_service: AuthService,
    organization_id: str,
    organization_slug: str,
    organization_name: str,
    admin_email: str,
    admin_display_name: str = "Smoke Admin",
    magic_link_email: str | None = None,
    magic_link_role: OrganizationRole = "developer",
) -> tuple[Organization, User]:
    identity_store = auth_service.identity_store
    organization = identity_store.get_organization(organization_id)
    if organization is None:
        organization = identity_store.save_organization(
            Organization(
                organization_id=organization_id,
                slug=organization_slug,
                name=organization_name,
            )
        )

    normalized_admin_email = normalize_email(admin_email)
    admin_user = identity_store.get_user_by_email(normalized_admin_email)
    if admin_user is None:
        admin_user = identity_store.save_user(
            User(
                user_id=f"smoke-admin-{organization_id}",
                email=normalized_admin_email,
                display_name=admin_display_name,
                is_superuser=True,
            )
        )
    else:
        admin_updates: dict[str, object] = {}
        if not admin_user.is_superuser:
            admin_updates["is_superuser"] = True
        if admin_user.display_name is None:
            admin_updates["display_name"] = admin_display_name
        if admin_updates:
            admin_user = identity_store.save_user(admin_user.model_copy(update=admin_updates))

    admin_membership = identity_store.get_organization_membership(admin_user.user_id, organization.organization_id)
    if admin_membership is None:
        identity_store.add_organization_membership(
            OrganizationMembership(
                user_id=admin_user.user_id,
                organization_id=organization.organization_id,
                role="admin",
                is_account_owner=True,
            )
        )
    elif admin_membership.role != "admin" or not admin_membership.is_account_owner:
        identity_store.add_organization_membership(
            admin_membership.model_copy(update={"role": "admin", "is_account_owner": True})
        )

    if magic_link_email is not None:
        normalized_magic_email = normalize_email(magic_link_email)
        magic_user = identity_store.get_user_by_email(normalized_magic_email)
        if magic_user is None:
            magic_user = identity_store.save_user(
                User(
                    user_id=f"smoke-user-{organization_id}-{normalized_magic_email.replace('@', '-at-')}",
                    email=normalized_magic_email,
                    display_name=AuthService._default_display_name_for_email(normalized_magic_email),
                )
            )
        membership = identity_store.get_organization_membership(magic_user.user_id, organization.organization_id)
        if membership is None:
            identity_store.add_organization_membership(
                OrganizationMembership(
                    user_id=magic_user.user_id,
                    organization_id=organization.organization_id,
                    role=magic_link_role,
                )
            )

    return organization, admin_user


def revoke_existing_active_invitation(
    *,
    auth_service: AuthService,
    organization_id: str,
    email: str,
    revoked_by_user_id: str,
) -> None:
    invitation = auth_service.identity_store.get_active_organization_invitation_by_email(
        organization_id,
        normalize_email(email),
    )
    if invitation is None:
        return
    auth_service.revoke_organization_invitation(
        invitation_id=invitation.invitation_id,
        organization_id=organization_id,
        revoked_by_user_id=revoked_by_user_id,
    )


def _wait_for_delivery(
    app: FastAPI,
    *,
    delivery_id: str | None,
    initial_status: Literal["sent", "queued", "failed"],
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> EmailDeliveryState | None:
    if delivery_id is None or initial_status != "queued":
        return None
    sender = getattr(app.state, "email_delivery_sender", None)
    if sender is None:
        return None
    deadline = time.monotonic() + timeout_seconds
    last_state = sender.get_delivery(delivery_id)
    while time.monotonic() < deadline:
        current_delivery = sender.get_delivery(delivery_id)
        if current_delivery is not None:
            last_state = current_delivery
            if current_delivery.status in {"sent", "failed"}:
                return current_delivery
        time.sleep(poll_interval_seconds)
    return last_state


async def send_auth_email_smoke(
    *,
    app: FastAPI,
    auth_service: AuthService,
    organization_id: str,
    admin_user_id: str,
    invite_email: str | None = None,
    invite_role: OrganizationRole = "developer",
    magic_link_email: str | None = None,
    wait_timeout_seconds: float = 8.0,
    poll_interval_seconds: float = 0.25,
) -> list[AuthEmailSmokeResult]:
    results: list[AuthEmailSmokeResult] = []
    normalized_invite_email = None if invite_email is None else normalize_email(invite_email)
    normalized_magic_email = None if magic_link_email is None else normalize_email(magic_link_email)
    if normalized_invite_email and normalized_magic_email and normalized_invite_email == normalized_magic_email:
        raise ValueError("invite_email and magic_link_email must be different for smoke sends")

    transport = httpx.ASGITransport(app=app)
    admin_session = auth_service.issue_browser_session(
        user_id=admin_user_id,
        organization_id=organization_id,
    )
    headers = {"Authorization": f"Bearer {admin_session.access_token}"}
    async with httpx.AsyncClient(transport=transport, base_url="http://smoke.local") as client:
        if normalized_invite_email is not None:
            existing_user = auth_service.identity_store.get_user_by_email(normalized_invite_email)
            if existing_user is not None:
                membership = auth_service.identity_store.get_organization_membership(
                    existing_user.user_id,
                    organization_id,
                )
                if membership is not None:
                    raise ConflictError("invite recipient is already an organization member")
            revoke_existing_active_invitation(
                auth_service=auth_service,
                organization_id=organization_id,
                email=normalized_invite_email,
                revoked_by_user_id=admin_user_id,
            )
            invite_response = await client.post(
                "/organization/invitations",
                headers=headers,
                json={"email": normalized_invite_email, "role": invite_role},
            )
            invite_payload = invite_response.json()
            if invite_response.status_code != 200:
                raise RuntimeError(
                    f"organization invitation send failed ({invite_response.status_code}): "
                    f"{invite_payload.get('detail', invite_payload)}"
                )
            invite_delivery = invite_payload["delivery"]
            invite_state = _wait_for_delivery(
                app,
                delivery_id=invite_delivery.get("delivery_id"),
                initial_status=invite_delivery["status"],
                timeout_seconds=wait_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            results.append(
                AuthEmailSmokeResult(
                    kind="organization_invitation",
                    email=normalized_invite_email,
                    http_status=invite_response.status_code,
                    transport=invite_delivery["transport"],
                    delivery_id=invite_delivery.get("delivery_id"),
                    initial_status=invite_delivery["status"],
                    final_status=invite_delivery["status"] if invite_state is None else invite_state.status,
                    attempt_count=invite_delivery.get("attempt_count", 1)
                    if invite_state is None
                    else invite_state.attempt_count,
                    last_error=None if invite_state is None else invite_state.last_error,
                )
            )

        if normalized_magic_email is not None:
            magic_response = await client.post(
                "/auth/magic-link/request",
                json={"email": normalized_magic_email, "organization_id": organization_id},
            )
            magic_payload = magic_response.json()
            if magic_response.status_code != 200:
                raise RuntimeError(
                    f"magic link send failed ({magic_response.status_code}): "
                    f"{magic_payload.get('detail', magic_payload)}"
                )
            magic_delivery = magic_payload["delivery"]
            magic_state = _wait_for_delivery(
                app,
                delivery_id=magic_delivery.get("delivery_id"),
                initial_status=magic_delivery["status"],
                timeout_seconds=wait_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            results.append(
                AuthEmailSmokeResult(
                    kind="magic_link",
                    email=normalized_magic_email,
                    http_status=magic_response.status_code,
                    transport=magic_delivery["transport"],
                    delivery_id=magic_delivery.get("delivery_id"),
                    initial_status=magic_delivery["status"],
                    final_status=magic_delivery["status"] if magic_state is None else magic_state.status,
                    attempt_count=magic_delivery.get("attempt_count", 1)
                    if magic_state is None
                    else magic_state.attempt_count,
                    last_error=None if magic_state is None else magic_state.last_error,
                )
            )

    return results
