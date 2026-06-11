from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .email_normalization import normalize_email


OrganizationRole = Literal["admin", "developer", "analyst"]
AuthChallengeKind = Literal["magic_link_existing", "magic_link_invitation"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    timezone: str = "UTC"
    language: str = "en"
    preferences: dict[str, object] = Field(default_factory=dict)
    is_superuser: bool = False
    is_active: bool = True
    deleted_at: datetime | None = None
    last_login_at: datetime | None = None
    last_active_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return normalize_email(value)


class Organization(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    organization_id: str
    slug: str
    name: str
    domain: str | None = None
    email: str | None = None
    phone: str | None = None
    icon_url: str | None = None
    description: str | None = None
    brand_color: str | None = None
    settings: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    is_active: bool = True
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    # Account closure state
    deletion_state: Literal["active", "scheduled", "deleting", "deleted", "cancelled"] = "active"
    deletion_scheduled_for: datetime | None = None
    deletion_requested_at: datetime | None = None
    deletion_requested_by: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_email(value)


class OrganizationMembership(BaseModel):
    user_id: str
    organization_id: str
    role: OrganizationRole = "developer"
    is_account_owner: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class OrganizationMemberRecord(BaseModel):
    user: User
    membership: OrganizationMembership


class SessionAuditContext(BaseModel):
    occurred_at: datetime | None = None
    ip: str | None = None
    user_agent: str | None = None


class AuthSession(BaseModel):
    session_id: str
    user_id: str
    organization_id: str
    issued_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None = None
    created_ip: str | None = None
    last_seen_ip: str | None = None
    user_agent: str | None = None
    revoked_at: datetime | None = None


class RefreshTokenFamily(BaseModel):
    family_id: str
    session_id: str
    user_id: str
    organization_id: str
    current_token_id: str
    current_token_hash: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    compromised_at: datetime | None = None


class ExternalIdentity(BaseModel):
    external_identity_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    organization_id: str
    provider_type: str
    provider_key: str
    subject: str
    email: str | None = None
    claims: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_email(value)


class EnterpriseSSOConfiguration(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sso_configuration_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    issuer_url: str
    client_id: str
    client_secret_ref: str
    allowed_domains: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    is_active: bool = True
    enforce_sso: bool = False
    jit_provisioning_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _normalize_allowed_domains(cls, value: list[str] | None) -> list[str]:
        if not value:
            return []
        normalized: list[str] = []
        for item in value:
            domain = str(item or "").strip().lower()
            if domain:
                normalized.append(domain)
        return normalized

    @field_validator("scopes", mode="before")
    @classmethod
    def _normalize_scopes(cls, value: list[str] | None) -> list[str]:
        if not value:
            return ["openid", "profile", "email"]
        normalized: list[str] = []
        for item in value:
            scope = str(item or "").strip()
            if scope:
                normalized.append(scope)
        return normalized or ["openid", "profile", "email"]


class OrganizationInvitation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    invitation_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    email: str
    role: OrganizationRole = "developer"
    is_account_owner: bool = False
    invited_by_user_id: str
    token_hash: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by_user_id: str | None = None
    revoked_at: datetime | None = None
    revoked_by_user_id: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return normalize_email(value)


class AuthChallenge(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    challenge_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: AuthChallengeKind
    email: str
    user_id: str | None = None
    organization_id: str | None = None
    invitation_id: str | None = None
    token_hash: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    consumed_at: datetime | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return normalize_email(value)


class IdentityStore(Protocol):
    def save_user(self, user: User) -> User: ...

    def get_user(self, user_id: str) -> User | None: ...

    def get_user_by_email(self, email: str) -> User | None: ...

    def list_users(self) -> list[User]: ...

    def save_organization(self, organization: Organization) -> Organization: ...

    def get_organization(self, organization_id: str) -> Organization | None: ...

    def list_organizations(self) -> list[Organization]: ...

    def add_organization_membership(self, membership: OrganizationMembership) -> OrganizationMembership: ...

    def get_organization_membership(
        self,
        user_id: str,
        organization_id: str,
    ) -> OrganizationMembership | None: ...

    def list_organization_memberships_for_user(self, user_id: str) -> list[OrganizationMembership]: ...

    def list_organization_members(self, organization_id: str) -> list[OrganizationMemberRecord]: ...

    def remove_organization_membership(
        self,
        user_id: str,
        organization_id: str,
    ) -> OrganizationMembership | None: ...

    def save_session(self, session: AuthSession) -> AuthSession: ...

    def get_session(self, session_id: str) -> AuthSession | None: ...

    def list_sessions_for_user(self, user_id: str, organization_id: str) -> list[AuthSession]: ...

    def revoke_session(self, session_id: str, *, revoked_at: datetime | None = None) -> AuthSession | None: ...

    def save_refresh_token_family(self, family: RefreshTokenFamily) -> RefreshTokenFamily: ...

    def get_refresh_token_family(self, family_id: str) -> RefreshTokenFamily | None: ...

    def list_refresh_token_families_for_session(self, session_id: str) -> list[RefreshTokenFamily]: ...

    def revoke_refresh_token_family(
        self,
        family_id: str,
        *,
        revoked_at: datetime | None = None,
        compromised_at: datetime | None = None,
    ) -> RefreshTokenFamily | None: ...

    def save_external_identity(self, identity: ExternalIdentity) -> ExternalIdentity: ...

    def get_external_identity(
        self,
        provider_type: str,
        provider_key: str,
        subject: str,
    ) -> ExternalIdentity | None: ...

    def list_external_identities_for_user(self, user_id: str) -> list[ExternalIdentity]: ...

    def save_enterprise_sso_configuration(
        self,
        configuration: EnterpriseSSOConfiguration,
    ) -> EnterpriseSSOConfiguration: ...

    def get_enterprise_sso_configuration(
        self,
        sso_configuration_id: str,
    ) -> EnterpriseSSOConfiguration | None: ...

    def get_enterprise_sso_configuration_for_organization(
        self,
        organization_id: str,
    ) -> EnterpriseSSOConfiguration | None: ...

    def list_enterprise_sso_configurations(self) -> list[EnterpriseSSOConfiguration]: ...

    def save_organization_invitation(self, invitation: OrganizationInvitation) -> OrganizationInvitation: ...

    def get_organization_invitation(self, invitation_id: str) -> OrganizationInvitation | None: ...

    def get_organization_invitation_by_token_hash(self, token_hash: str) -> OrganizationInvitation | None: ...

    def list_organization_invitations(self, organization_id: str) -> list[OrganizationInvitation]: ...

    def get_active_organization_invitation_by_email(
        self,
        organization_id: str,
        email: str,
    ) -> OrganizationInvitation | None: ...

    def save_auth_challenge(self, challenge: AuthChallenge) -> AuthChallenge: ...

    def get_auth_challenge(self, challenge_id: str) -> AuthChallenge | None: ...

    def get_auth_challenge_by_token_hash(self, token_hash: str) -> AuthChallenge | None: ...


def _copy_model(model: BaseModel) -> BaseModel:
    return model.model_copy(deep=True)


class InMemoryIdentityStore:
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._users_by_email: dict[str, str] = {}
        self._organizations: dict[str, Organization] = {}
        self._organization_memberships: dict[tuple[str, str], OrganizationMembership] = {}
        self._sessions: dict[str, AuthSession] = {}
        self._refresh_families: dict[str, RefreshTokenFamily] = {}
        self._external_identities: dict[tuple[str, str, str], ExternalIdentity] = {}
        self._enterprise_sso_configurations: dict[str, EnterpriseSSOConfiguration] = {}
        self._enterprise_sso_configurations_by_org: dict[str, str] = {}
        self._organization_invitations: dict[str, OrganizationInvitation] = {}
        self._organization_invitations_by_token_hash: dict[str, str] = {}
        self._auth_challenges: dict[str, AuthChallenge] = {}
        self._auth_challenges_by_token_hash: dict[str, str] = {}

    def save_user(self, user: User) -> User:
        stored = User.model_validate(user.model_dump())
        self._users[stored.user_id] = stored
        self._users_by_email[stored.email.casefold()] = stored.user_id
        return _copy_model(stored)

    def get_user(self, user_id: str) -> User | None:
        user = self._users.get(user_id)
        return None if user is None else _copy_model(user)

    def get_user_by_email(self, email: str) -> User | None:
        user_id = self._users_by_email.get(normalize_email(email).casefold())
        if user_id is None:
            return None
        return self.get_user(user_id)

    def list_users(self) -> list[User]:
        return [_copy_model(item) for item in sorted(self._users.values(), key=lambda item: (item.email, item.user_id))]

    def save_organization(self, organization: Organization) -> Organization:
        stored = Organization.model_validate(organization.model_dump())
        self._organizations[stored.organization_id] = stored
        return _copy_model(stored)

    def get_organization(self, organization_id: str) -> Organization | None:
        organization = self._organizations.get(organization_id)
        return None if organization is None else _copy_model(organization)

    def list_organizations(self) -> list[Organization]:
        return [
            _copy_model(item)
            for item in sorted(self._organizations.values(), key=lambda item: (item.name, item.organization_id))
        ]

    def add_organization_membership(self, membership: OrganizationMembership) -> OrganizationMembership:
        key = (membership.user_id, membership.organization_id)
        stored = _copy_model(membership)
        self._organization_memberships[key] = stored
        return _copy_model(stored)

    def get_organization_membership(
        self,
        user_id: str,
        organization_id: str,
    ) -> OrganizationMembership | None:
        membership = self._organization_memberships.get((user_id, organization_id))
        return None if membership is None else _copy_model(membership)

    def list_organization_memberships_for_user(self, user_id: str) -> list[OrganizationMembership]:
        items = [item for item in self._organization_memberships.values() if item.user_id == user_id]
        return [_copy_model(item) for item in sorted(items, key=lambda item: item.organization_id)]

    def list_organization_members(self, organization_id: str) -> list[OrganizationMemberRecord]:
        memberships = [
            membership
            for membership in self._organization_memberships.values()
            if membership.organization_id == organization_id
        ]
        records: list[OrganizationMemberRecord] = []
        for membership in sorted(memberships, key=lambda item: (item.created_at, item.user_id)):
            user = self._users.get(membership.user_id)
            if user is None:
                continue
            records.append(
                OrganizationMemberRecord(
                    user=_copy_model(user),
                    membership=_copy_model(membership),
                )
            )
        return records

    def remove_organization_membership(
        self,
        user_id: str,
        organization_id: str,
    ) -> OrganizationMembership | None:
        membership = self._organization_memberships.pop((user_id, organization_id), None)
        return None if membership is None else _copy_model(membership)

    def save_session(self, session: AuthSession) -> AuthSession:
        stored = AuthSession.model_validate(session.model_dump())
        self._sessions[stored.session_id] = stored
        return _copy_model(stored)

    def get_session(self, session_id: str) -> AuthSession | None:
        session = self._sessions.get(session_id)
        return None if session is None else _copy_model(session)

    def list_sessions_for_user(self, user_id: str, organization_id: str) -> list[AuthSession]:
        items = [
            session
            for session in self._sessions.values()
            if session.user_id == user_id and session.organization_id == organization_id
        ]
        return [
            _copy_model(item)
            for item in sorted(
                items,
                key=lambda item: (
                    item.last_seen_at or item.issued_at,
                    item.issued_at,
                    item.session_id,
                ),
                reverse=True,
            )
        ]

    def revoke_session(self, session_id: str, *, revoked_at: datetime | None = None) -> AuthSession | None:
        existing = self._sessions.get(session_id)
        if existing is None:
            return None
        updated = existing.model_copy(update={"revoked_at": revoked_at or utc_now()})
        self._sessions[session_id] = updated
        return _copy_model(updated)

    def save_refresh_token_family(self, family: RefreshTokenFamily) -> RefreshTokenFamily:
        stored = RefreshTokenFamily.model_validate(family.model_dump())
        self._refresh_families[stored.family_id] = stored
        return _copy_model(stored)

    def get_refresh_token_family(self, family_id: str) -> RefreshTokenFamily | None:
        family = self._refresh_families.get(family_id)
        return None if family is None else _copy_model(family)

    def list_refresh_token_families_for_session(self, session_id: str) -> list[RefreshTokenFamily]:
        items = [family for family in self._refresh_families.values() if family.session_id == session_id]
        return [_copy_model(item) for item in sorted(items, key=lambda item: item.issued_at, reverse=True)]

    def revoke_refresh_token_family(
        self,
        family_id: str,
        *,
        revoked_at: datetime | None = None,
        compromised_at: datetime | None = None,
    ) -> RefreshTokenFamily | None:
        existing = self._refresh_families.get(family_id)
        if existing is None:
            return None
        updated = existing.model_copy(
            update={
                "revoked_at": revoked_at or utc_now(),
                "compromised_at": compromised_at,
            }
        )
        self._refresh_families[family_id] = updated
        return _copy_model(updated)

    def save_external_identity(self, identity: ExternalIdentity) -> ExternalIdentity:
        stored = ExternalIdentity.model_validate(identity.model_dump())
        key = (stored.provider_type, stored.provider_key, stored.subject)
        self._external_identities[key] = stored
        return _copy_model(stored)

    def get_external_identity(
        self,
        provider_type: str,
        provider_key: str,
        subject: str,
    ) -> ExternalIdentity | None:
        identity = self._external_identities.get((provider_type, provider_key, subject))
        return None if identity is None else _copy_model(identity)

    def list_external_identities_for_user(self, user_id: str) -> list[ExternalIdentity]:
        items = [item for item in self._external_identities.values() if item.user_id == user_id]
        return [_copy_model(item) for item in sorted(items, key=lambda item: item.external_identity_id)]

    def save_enterprise_sso_configuration(
        self,
        configuration: EnterpriseSSOConfiguration,
    ) -> EnterpriseSSOConfiguration:
        stored = EnterpriseSSOConfiguration.model_validate(configuration.model_dump())
        existing_id = self._enterprise_sso_configurations_by_org.get(stored.organization_id)
        if existing_id is not None and existing_id != stored.sso_configuration_id:
            self._enterprise_sso_configurations.pop(existing_id, None)
        self._enterprise_sso_configurations[stored.sso_configuration_id] = stored
        self._enterprise_sso_configurations_by_org[stored.organization_id] = stored.sso_configuration_id
        return _copy_model(stored)

    def get_enterprise_sso_configuration(
        self,
        sso_configuration_id: str,
    ) -> EnterpriseSSOConfiguration | None:
        configuration = self._enterprise_sso_configurations.get(sso_configuration_id)
        return None if configuration is None else _copy_model(configuration)

    def get_enterprise_sso_configuration_for_organization(
        self,
        organization_id: str,
    ) -> EnterpriseSSOConfiguration | None:
        configuration_id = self._enterprise_sso_configurations_by_org.get(organization_id)
        if configuration_id is None:
            return None
        return self.get_enterprise_sso_configuration(configuration_id)

    def list_enterprise_sso_configurations(self) -> list[EnterpriseSSOConfiguration]:
        items = list(self._enterprise_sso_configurations.values())
        return [_copy_model(item) for item in sorted(items, key=lambda item: item.sso_configuration_id)]

    def save_organization_invitation(self, invitation: OrganizationInvitation) -> OrganizationInvitation:
        stored = OrganizationInvitation.model_validate(invitation.model_dump())
        existing = self._organization_invitations.get(stored.invitation_id)
        if existing is not None and existing.token_hash != stored.token_hash:
            self._organization_invitations_by_token_hash.pop(existing.token_hash, None)
        self._organization_invitations[stored.invitation_id] = stored
        self._organization_invitations_by_token_hash[stored.token_hash] = stored.invitation_id
        return _copy_model(stored)

    def get_organization_invitation(self, invitation_id: str) -> OrganizationInvitation | None:
        invitation = self._organization_invitations.get(invitation_id)
        return None if invitation is None else _copy_model(invitation)

    def get_organization_invitation_by_token_hash(self, token_hash: str) -> OrganizationInvitation | None:
        invitation_id = self._organization_invitations_by_token_hash.get(token_hash)
        if invitation_id is None:
            return None
        return self.get_organization_invitation(invitation_id)

    def list_organization_invitations(self, organization_id: str) -> list[OrganizationInvitation]:
        items = [
            invitation
            for invitation in self._organization_invitations.values()
            if invitation.organization_id == organization_id
        ]
        return [
            _copy_model(item)
            for item in sorted(
                items,
                key=lambda item: (item.created_at, item.invitation_id),
                reverse=True,
            )
        ]

    def get_active_organization_invitation_by_email(
        self,
        organization_id: str,
        email: str,
    ) -> OrganizationInvitation | None:
        normalized_email = normalize_email(email)
        active = [
            invitation
            for invitation in self._organization_invitations.values()
            if invitation.organization_id == organization_id
            and invitation.email == normalized_email
            and invitation.accepted_at is None
            and invitation.revoked_at is None
            and invitation.expires_at > utc_now()
        ]
        if not active:
            return None
        latest = max(active, key=lambda item: (item.created_at, item.invitation_id))
        return _copy_model(latest)

    def save_auth_challenge(self, challenge: AuthChallenge) -> AuthChallenge:
        stored = AuthChallenge.model_validate(challenge.model_dump())
        existing = self._auth_challenges.get(stored.challenge_id)
        if existing is not None:
            self._auth_challenges_by_token_hash.pop(existing.token_hash, None)
        self._auth_challenges[stored.challenge_id] = stored
        self._auth_challenges_by_token_hash[stored.token_hash] = stored.challenge_id
        return _copy_model(stored)

    def get_auth_challenge(self, challenge_id: str) -> AuthChallenge | None:
        challenge = self._auth_challenges.get(challenge_id)
        return None if challenge is None else _copy_model(challenge)

    def get_auth_challenge_by_token_hash(self, token_hash: str) -> AuthChallenge | None:
        challenge_id = self._auth_challenges_by_token_hash.get(token_hash)
        if challenge_id is None:
            return None
        return self.get_auth_challenge(challenge_id)
