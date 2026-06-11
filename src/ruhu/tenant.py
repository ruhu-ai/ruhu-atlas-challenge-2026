from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .identity import IdentityStore, Organization, OrganizationMemberRecord, OrganizationMembership


class TenantScope(BaseModel):
    organization_id: str


class TenantIdentityRepository:
    def __init__(self, *, identity_store: IdentityStore, scope: TenantScope) -> None:
        self.identity_store = identity_store
        self.scope = scope

    def get_organization(self) -> Organization | None:
        return self.identity_store.get_organization(self.scope.organization_id)

    def save_organization(self, organization: Organization) -> Organization:
        if organization.organization_id != self.scope.organization_id:
            raise ValueError("organization scope mismatch")
        return self.identity_store.save_organization(organization)

    def get_organization_membership(self, user_id: str) -> OrganizationMembership | None:
        return self.identity_store.get_organization_membership(user_id, self.scope.organization_id)

    def add_organization_membership(self, membership: OrganizationMembership) -> OrganizationMembership:
        if membership.organization_id != self.scope.organization_id:
            raise ValueError("organization scope mismatch")
        return self.identity_store.add_organization_membership(membership)

    def list_organization_members(self) -> list[OrganizationMemberRecord]:
        return self.identity_store.list_organization_members(self.scope.organization_id)

    def remove_organization_membership(self, user_id: str) -> OrganizationMembership | None:
        return self.identity_store.remove_organization_membership(user_id, self.scope.organization_id)


class TenantIdentityRepositoryFactory:
    def __init__(self, *, identity_store: IdentityStore) -> None:
        self.identity_store = identity_store

    def for_scope(self, *, organization_id: str) -> TenantIdentityRepository:
        return TenantIdentityRepository(
            identity_store=self.identity_store,
            scope=TenantScope(organization_id=organization_id),
        )

    def from_scope(self, scope: TenantScope) -> TenantIdentityRepository:
        return TenantIdentityRepository(identity_store=self.identity_store, scope=scope)


class TenantContextStrategy(Protocol):
    def describe(self) -> str: ...

    def session_sql(self, *, scope: TenantScope, user_id: str) -> list[tuple[str, dict[str, str]]]: ...


class PostgresRLSTenantContextStrategy:
    def describe(self) -> str:
        return "Postgres enforces tenant scope via transaction-scoped set_config() values and row-level security policies."

    def session_sql(self, *, scope: TenantScope, user_id: str) -> list[tuple[str, dict[str, str]]]:
        return [
            (
                "SELECT set_config('app.current_organization_id', :org_id, true)",
                {"org_id": scope.organization_id},
            ),
            (
                "SELECT set_config('app.current_user_id', :user_id, true)",
                {"user_id": user_id},
            ),
        ]


def apply_tenant_session_context(
    session: Session,
    *,
    scope: TenantScope,
    user_id: str,
    strategy: TenantContextStrategy | None = None,
) -> None:
    active_strategy = strategy or PostgresRLSTenantContextStrategy()
    for statement, parameters in active_strategy.session_sql(scope=scope, user_id=user_id):
        session.execute(text(statement), parameters)
