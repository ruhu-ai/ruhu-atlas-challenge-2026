from __future__ import annotations

from ruhu.db import build_session_factory
from ruhu.identity import Organization, OrganizationMembership, User
from ruhu.identity_sqlalchemy import SQLAlchemyIdentityStore


def test_sqlalchemy_identity_store_round_trips_org_only_membership_shape(postgres_database_url_factory) -> None:
    store = SQLAlchemyIdentityStore(build_session_factory(postgres_database_url_factory()))
    store.save_user(User(user_id="user-1", email=" Owner@Example.com ", display_name="Owner"))
    store.save_organization(
        Organization(
            organization_id="org-1",
            slug="acme",
            name="Acme",
            email=" Team@Acme.example.com ",
            icon_url="https://cdn.example.com/acme-icon.png",
            brand_color="#1254ff",
        )
    )
    store.add_organization_membership(
        OrganizationMembership(
            user_id="user-1",
            organization_id="org-1",
            role="developer",
            is_account_owner=True,
        )
    )

    user = store.get_user_by_email("owner@example.com")
    organization = store.get_organization("org-1")
    organization_membership = store.get_organization_membership("user-1", "org-1")

    assert user is not None
    assert user.email == "owner@example.com"
    assert organization is not None
    assert organization.email == "team@acme.example.com"
    assert organization_membership is not None
    assert organization_membership.role == "developer"
    assert organization_membership.is_account_owner is True
