from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ruhu.db import build_session_factory
from ruhu.identity import (
    AuthChallenge,
    AuthSession,
    EnterpriseSSOConfiguration,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    User,
)
from ruhu.identity_sqlalchemy import SQLAlchemyIdentityStore


def test_sqlalchemy_identity_store_round_trips_org_only_membership_shape(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyIdentityStore(session_factory)

    user = store.save_user(User(user_id="user-1", email="person@example.com", display_name="Person"))
    organization = store.save_organization(
        Organization(
            organization_id="org-1",
            slug="acme",
            name="Acme",
            email="team@example.com",
            icon_url="https://example.com/icon.png",
        )
    )
    membership = store.add_organization_membership(
        OrganizationMembership(
            user_id=user.user_id,
            organization_id=organization.organization_id,
            role="admin",
            is_account_owner=True,
        )
    )

    assert store.get_user_by_email("PERSON@example.com").user_id == "user-1"
    assert store.get_organization("org-1").name == "Acme"
    assert store.get_organization_membership("user-1", "org-1").role == "admin"
    assert membership.is_account_owner is True

    members = store.list_organization_members("org-1")
    assert len(members) == 1
    assert members[0].user.user_id == "user-1"
    assert members[0].membership.organization_id == "org-1"


def test_sqlalchemy_identity_store_round_trips_session_audit_and_invitations(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyIdentityStore(session_factory)

    store.save_user(User(user_id="user-1", email="owner@example.com", display_name="Owner"))
    store.save_organization(Organization(organization_id="org-1", slug="acme", name="Acme"))
    store.add_organization_membership(
        OrganizationMembership(
            user_id="user-1",
            organization_id="org-1",
            role="admin",
            is_account_owner=True,
        )
    )

    issued_at = datetime.now(timezone.utc)
    saved_session = store.save_session(
        AuthSession(
            session_id="session-1",
            user_id="user-1",
            organization_id="org-1",
            issued_at=issued_at,
            expires_at=issued_at + timedelta(hours=1),
            last_seen_at=issued_at + timedelta(minutes=5),
            created_ip="203.0.113.10",
            last_seen_ip="203.0.113.11",
            user_agent="browser-a",
        )
    )
    assert saved_session.created_ip == "203.0.113.10"
    assert store.list_sessions_for_user("user-1", "org-1")[0].last_seen_ip == "203.0.113.11"

    invitation = store.save_organization_invitation(
        OrganizationInvitation(
            invitation_id="invite-1",
            organization_id="org-1",
            email="invitee@example.com",
            role="developer",
            invited_by_user_id="user-1",
            token_hash="token-hash-1",
            created_at=issued_at,
            expires_at=issued_at + timedelta(days=7),
        )
    )
    assert invitation.invitation_id == "invite-1"
    assert store.get_organization_invitation("invite-1").email == "invitee@example.com"
    assert store.get_organization_invitation_by_token_hash("token-hash-1").organization_id == "org-1"
    assert store.get_active_organization_invitation_by_email("org-1", "INVITEE@example.com").invitation_id == "invite-1"


def test_sqlalchemy_identity_store_round_trips_sso_config_and_auth_challenge(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyIdentityStore(session_factory)

    issued_at = datetime.now(timezone.utc)
    store.save_organization(Organization(organization_id="org-1", slug="acme", name="Acme"))
    configuration = store.save_enterprise_sso_configuration(
        EnterpriseSSOConfiguration(
            sso_configuration_id="sso-1",
            organization_id="org-1",
            issuer_url="https://sso.example.com",
            client_id="client-id",
            client_secret_ref="env:RUHU_SSO_CLIENT_SECRET__ACME",
            allowed_domains=["acme.com"],
            scopes=["openid", "profile", "email"],
            is_active=True,
            enforce_sso=True,
            jit_provisioning_enabled=True,
            created_at=issued_at,
            updated_at=issued_at,
        )
    )
    assert configuration.sso_configuration_id == "sso-1"
    assert store.get_enterprise_sso_configuration("sso-1").organization_id == "org-1"
    assert store.get_enterprise_sso_configuration_for_organization("org-1").issuer_url == "https://sso.example.com"

    challenge = store.save_auth_challenge(
        AuthChallenge(
            challenge_id="challenge-1",
            kind="magic_link_existing",
            email="person@example.com",
            user_id="user-1",
            organization_id="org-1",
            token_hash="challenge-token-hash",
            created_at=issued_at,
            expires_at=issued_at + timedelta(minutes=15),
        )
    )
    assert challenge.challenge_id == "challenge-1"
    assert store.get_auth_challenge("challenge-1").email == "person@example.com"
    assert store.get_auth_challenge_by_token_hash("challenge-token-hash").organization_id == "org-1"
