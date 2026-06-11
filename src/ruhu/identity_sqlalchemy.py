from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    AuthChallengeRecord,
    AuthSessionRecord,
    EnterpriseSSOConfigurationRecord,
    ExternalIdentityRecord,
    OrganizationInvitationRecord,
    IdentityOrganizationMembershipRecord,
    IdentityOrganizationRecord,
    IdentityUserRecord,
    RefreshTokenFamilyRecord,
)
from .email_normalization import normalize_email
from .identity import (
    AuthChallenge,
    AuthSession,
    EnterpriseSSOConfiguration,
    ExternalIdentity,
    Organization,
    OrganizationInvitation,
    OrganizationMemberRecord,
    OrganizationMembership,
    RefreshTokenFamily,
    User,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class SQLAlchemyIdentityStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_user(self, user: User) -> User:
        stored = User.model_validate(user.model_dump())
        with self._session_factory.begin() as session:
            record = session.get(IdentityUserRecord, stored.user_id)
            if record is None:
                record = IdentityUserRecord(user_id=stored.user_id)
                session.add(record)
            self._apply_user(record, stored)
        return self.get_user(stored.user_id) or stored

    def get_user(self, user_id: str) -> User | None:
        with self._session_factory() as session:
            record = session.get(IdentityUserRecord, user_id)
            return None if record is None else self._to_user(record)

    def get_user_by_email(self, email: str) -> User | None:
        normalized_email = normalize_email(email)
        with self._session_factory() as session:
            record = session.scalar(select(IdentityUserRecord).where(IdentityUserRecord.email == normalized_email))
            return None if record is None else self._to_user(record)

    def list_users(self) -> list[User]:
        with self._session_factory() as session:
            records = session.scalars(
                select(IdentityUserRecord).order_by(IdentityUserRecord.email.asc(), IdentityUserRecord.user_id.asc())
            ).all()
        return [self._to_user(record) for record in records]

    def save_organization(self, organization: Organization) -> Organization:
        stored = Organization.model_validate(organization.model_dump())
        with self._session_factory.begin() as session:
            record = session.get(IdentityOrganizationRecord, stored.organization_id)
            if record is None:
                record = IdentityOrganizationRecord(organization_id=stored.organization_id)
                session.add(record)
            self._apply_organization(record, stored)
        return self.get_organization(stored.organization_id) or stored

    def get_organization(self, organization_id: str) -> Organization | None:
        with self._session_factory() as session:
            record = session.get(IdentityOrganizationRecord, organization_id)
            return None if record is None else self._to_organization(record)

    def list_organizations(self) -> list[Organization]:
        with self._session_factory() as session:
            records = session.scalars(
                select(IdentityOrganizationRecord).order_by(
                    IdentityOrganizationRecord.name.asc(),
                    IdentityOrganizationRecord.organization_id.asc(),
                )
            ).all()
        return [self._to_organization(record) for record in records]

    def add_organization_membership(self, membership: OrganizationMembership) -> OrganizationMembership:
        stored = OrganizationMembership.model_validate(membership.model_dump())
        with self._session_factory.begin() as session:
            record = session.get(
                IdentityOrganizationMembershipRecord,
                {"user_id": stored.user_id, "organization_id": stored.organization_id},
            )
            if record is None:
                record = IdentityOrganizationMembershipRecord(
                    user_id=stored.user_id,
                    organization_id=stored.organization_id,
                )
                session.add(record)
            record.role = stored.role
            record.is_account_owner = stored.is_account_owner
            record.created_at = stored.created_at
        return self.get_organization_membership(stored.user_id, stored.organization_id) or stored

    def get_organization_membership(
        self,
        user_id: str,
        organization_id: str,
    ) -> OrganizationMembership | None:
        with self._session_factory() as session:
            record = session.get(
                IdentityOrganizationMembershipRecord,
                {"user_id": user_id, "organization_id": organization_id},
            )
            return None if record is None else self._to_membership(record)

    def list_organization_memberships_for_user(self, user_id: str) -> list[OrganizationMembership]:
        with self._session_factory() as session:
            records = session.scalars(
                select(IdentityOrganizationMembershipRecord)
                .where(IdentityOrganizationMembershipRecord.user_id == user_id)
                .order_by(IdentityOrganizationMembershipRecord.organization_id.asc())
            ).all()
        return [self._to_membership(record) for record in records]

    def list_organization_members(self, organization_id: str) -> list[OrganizationMemberRecord]:
        with self._session_factory() as session:
            rows = session.execute(
                select(IdentityOrganizationMembershipRecord, IdentityUserRecord)
                .join(
                    IdentityUserRecord,
                    IdentityUserRecord.user_id == IdentityOrganizationMembershipRecord.user_id,
                )
                .where(IdentityOrganizationMembershipRecord.organization_id == organization_id)
                .order_by(
                    IdentityOrganizationMembershipRecord.created_at.asc(),
                    IdentityOrganizationMembershipRecord.user_id.asc(),
                )
            ).all()
        return [
            OrganizationMemberRecord(
                user=self._to_user(user_record),
                membership=self._to_membership(membership_record),
            )
            for membership_record, user_record in rows
        ]

    def remove_organization_membership(
        self,
        user_id: str,
        organization_id: str,
    ) -> OrganizationMembership | None:
        existing = self.get_organization_membership(user_id, organization_id)
        if existing is None:
            return None
        with self._session_factory.begin() as session:
            record = session.get(
                IdentityOrganizationMembershipRecord,
                {"user_id": user_id, "organization_id": organization_id},
            )
            if record is not None:
                session.delete(record)
        return existing

    def save_session(self, session_value: AuthSession) -> AuthSession:
        stored = AuthSession.model_validate(session_value.model_dump())
        with self._session_factory.begin() as session:
            record = session.get(AuthSessionRecord, stored.session_id)
            if record is None:
                record = AuthSessionRecord(session_id=stored.session_id)
                session.add(record)
            record.user_id = stored.user_id
            record.organization_id = stored.organization_id
            record.issued_at = stored.issued_at
            record.expires_at = stored.expires_at
            record.last_seen_at = stored.last_seen_at
            record.created_ip = stored.created_ip
            record.last_seen_ip = stored.last_seen_ip
            record.user_agent = stored.user_agent
            record.revoked_at = stored.revoked_at
        return self.get_session(stored.session_id) or stored

    def get_session(self, session_id: str) -> AuthSession | None:
        with self._session_factory() as session:
            record = session.get(AuthSessionRecord, session_id)
            return None if record is None else self._to_auth_session(record)

    def list_sessions_for_user(self, user_id: str, organization_id: str) -> list[AuthSession]:
        with self._session_factory() as session:
            records = session.scalars(
                select(AuthSessionRecord)
                .where(
                    AuthSessionRecord.user_id == user_id,
                    AuthSessionRecord.organization_id == organization_id,
                )
                .order_by(
                    AuthSessionRecord.last_seen_at.desc(),
                    AuthSessionRecord.issued_at.desc(),
                    AuthSessionRecord.session_id.desc(),
                )
            ).all()
        return [self._to_auth_session(record) for record in records]

    def revoke_session(self, session_id: str, *, revoked_at: datetime | None = None) -> AuthSession | None:
        existing = self.get_session(session_id)
        if existing is None:
            return None
        updated = existing.model_copy(update={"revoked_at": revoked_at or _utc_now()})
        return self.save_session(updated)

    def save_refresh_token_family(self, family: RefreshTokenFamily) -> RefreshTokenFamily:
        stored = RefreshTokenFamily.model_validate(family.model_dump())
        with self._session_factory.begin() as session:
            record = session.get(RefreshTokenFamilyRecord, stored.family_id)
            if record is None:
                record = RefreshTokenFamilyRecord(family_id=stored.family_id)
                session.add(record)
            record.session_id = stored.session_id
            record.user_id = stored.user_id
            record.organization_id = stored.organization_id
            record.current_token_id = stored.current_token_id
            record.current_token_hash = stored.current_token_hash
            record.issued_at = stored.issued_at
            record.expires_at = stored.expires_at
            record.revoked_at = stored.revoked_at
            record.compromised_at = stored.compromised_at
        return self.get_refresh_token_family(stored.family_id) or stored

    def get_refresh_token_family(self, family_id: str) -> RefreshTokenFamily | None:
        with self._session_factory() as session:
            record = session.get(RefreshTokenFamilyRecord, family_id)
            return None if record is None else self._to_refresh_family(record)

    def list_refresh_token_families_for_session(self, session_id: str) -> list[RefreshTokenFamily]:
        with self._session_factory() as session:
            records = session.scalars(
                select(RefreshTokenFamilyRecord)
                .where(RefreshTokenFamilyRecord.session_id == session_id)
                .order_by(RefreshTokenFamilyRecord.issued_at.desc(), RefreshTokenFamilyRecord.family_id.desc())
            ).all()
        return [self._to_refresh_family(record) for record in records]

    def revoke_refresh_token_family(
        self,
        family_id: str,
        *,
        revoked_at: datetime | None = None,
        compromised_at: datetime | None = None,
    ) -> RefreshTokenFamily | None:
        existing = self.get_refresh_token_family(family_id)
        if existing is None:
            return None
        updated = existing.model_copy(
            update={
                "revoked_at": revoked_at or _utc_now(),
                "compromised_at": compromised_at,
            }
        )
        return self.save_refresh_token_family(updated)

    def save_external_identity(self, identity: ExternalIdentity) -> ExternalIdentity:
        stored = ExternalIdentity.model_validate(identity.model_dump())
        with self._session_factory.begin() as session:
            record = session.scalar(
                select(ExternalIdentityRecord).where(
                    ExternalIdentityRecord.provider_type == stored.provider_type,
                    ExternalIdentityRecord.provider_key == stored.provider_key,
                    ExternalIdentityRecord.subject == stored.subject,
                )
            )
            if record is None:
                record = ExternalIdentityRecord(external_identity_id=stored.external_identity_id)
                session.add(record)
            record.user_id = stored.user_id
            record.organization_id = stored.organization_id
            record.provider_type = stored.provider_type
            record.provider_key = stored.provider_key
            record.subject = stored.subject
            record.email = stored.email
            record.claims_json = dict(stored.claims)
            record.created_at = stored.created_at
            record.updated_at = stored.updated_at
        resolved = self.get_external_identity(stored.provider_type, stored.provider_key, stored.subject)
        return resolved or stored

    def get_external_identity(
        self,
        provider_type: str,
        provider_key: str,
        subject: str,
    ) -> ExternalIdentity | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(ExternalIdentityRecord).where(
                    ExternalIdentityRecord.provider_type == provider_type,
                    ExternalIdentityRecord.provider_key == provider_key,
                    ExternalIdentityRecord.subject == subject,
                )
            )
            return None if record is None else self._to_external_identity(record)

    def list_external_identities_for_user(self, user_id: str) -> list[ExternalIdentity]:
        with self._session_factory() as session:
            records = session.scalars(
                select(ExternalIdentityRecord)
                .where(ExternalIdentityRecord.user_id == user_id)
                .order_by(ExternalIdentityRecord.external_identity_id.asc())
            ).all()
        return [self._to_external_identity(record) for record in records]

    def save_enterprise_sso_configuration(
        self,
        configuration: EnterpriseSSOConfiguration,
    ) -> EnterpriseSSOConfiguration:
        stored = EnterpriseSSOConfiguration.model_validate(configuration.model_dump())
        with self._session_factory.begin() as session:
            record = session.get(EnterpriseSSOConfigurationRecord, stored.sso_configuration_id)
            if record is None:
                record = session.scalar(
                    select(EnterpriseSSOConfigurationRecord).where(
                        EnterpriseSSOConfigurationRecord.organization_id == stored.organization_id,
                    )
                )
            if record is None:
                record = EnterpriseSSOConfigurationRecord(sso_configuration_id=stored.sso_configuration_id)
                session.add(record)
            record.organization_id = stored.organization_id
            record.issuer_url = stored.issuer_url
            record.client_id = stored.client_id
            record.client_secret_ref = stored.client_secret_ref
            record.allowed_domains_json = list(stored.allowed_domains)
            record.scopes_json = list(stored.scopes)
            record.is_active = stored.is_active
            record.enforce_sso = stored.enforce_sso
            record.jit_provisioning_enabled = stored.jit_provisioning_enabled
            record.created_at = stored.created_at
            record.updated_at = stored.updated_at
        resolved = self.get_enterprise_sso_configuration(stored.sso_configuration_id)
        if resolved is not None:
            return resolved
        fallback = self.get_enterprise_sso_configuration_for_organization(stored.organization_id)
        return fallback or stored

    def get_enterprise_sso_configuration(
        self,
        sso_configuration_id: str,
    ) -> EnterpriseSSOConfiguration | None:
        with self._session_factory() as session:
            record = session.get(EnterpriseSSOConfigurationRecord, sso_configuration_id)
            return None if record is None else self._to_enterprise_sso_configuration(record)

    def get_enterprise_sso_configuration_for_organization(
        self,
        organization_id: str,
    ) -> EnterpriseSSOConfiguration | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(EnterpriseSSOConfigurationRecord).where(
                    EnterpriseSSOConfigurationRecord.organization_id == organization_id,
                )
            )
            return None if record is None else self._to_enterprise_sso_configuration(record)

    def list_enterprise_sso_configurations(self) -> list[EnterpriseSSOConfiguration]:
        with self._session_factory() as session:
            records = session.scalars(
                select(EnterpriseSSOConfigurationRecord).order_by(
                    EnterpriseSSOConfigurationRecord.organization_id.asc(),
                    EnterpriseSSOConfigurationRecord.sso_configuration_id.asc(),
                )
            ).all()
        return [self._to_enterprise_sso_configuration(record) for record in records]

    def save_organization_invitation(self, invitation: OrganizationInvitation) -> OrganizationInvitation:
        stored = OrganizationInvitation.model_validate(invitation.model_dump())
        with self._session_factory.begin() as session:
            record = session.get(OrganizationInvitationRecord, stored.invitation_id)
            if record is None:
                record = OrganizationInvitationRecord(invitation_id=stored.invitation_id)
                session.add(record)
            record.organization_id = stored.organization_id
            record.email = stored.email
            record.role = stored.role
            record.is_account_owner = stored.is_account_owner
            record.invited_by_user_id = stored.invited_by_user_id
            record.token_hash = stored.token_hash
            record.created_at = stored.created_at
            record.expires_at = stored.expires_at
            record.accepted_at = stored.accepted_at
            record.accepted_by_user_id = stored.accepted_by_user_id
            record.revoked_at = stored.revoked_at
            record.revoked_by_user_id = stored.revoked_by_user_id
        resolved = self.get_organization_invitation(stored.invitation_id)
        return resolved or stored

    def get_organization_invitation(self, invitation_id: str) -> OrganizationInvitation | None:
        with self._session_factory() as session:
            record = session.get(OrganizationInvitationRecord, invitation_id)
            return None if record is None else self._to_organization_invitation(record)

    def get_organization_invitation_by_token_hash(self, token_hash: str) -> OrganizationInvitation | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(OrganizationInvitationRecord).where(OrganizationInvitationRecord.token_hash == token_hash)
            )
            return None if record is None else self._to_organization_invitation(record)

    def list_organization_invitations(self, organization_id: str) -> list[OrganizationInvitation]:
        with self._session_factory() as session:
            records = session.scalars(
                select(OrganizationInvitationRecord)
                .where(OrganizationInvitationRecord.organization_id == organization_id)
                .order_by(
                    OrganizationInvitationRecord.created_at.desc(),
                    OrganizationInvitationRecord.invitation_id.desc(),
                )
            ).all()
        return [self._to_organization_invitation(record) for record in records]

    def get_active_organization_invitation_by_email(
        self,
        organization_id: str,
        email: str,
    ) -> OrganizationInvitation | None:
        normalized_email = normalize_email(email)
        now = _utc_now()
        with self._session_factory() as session:
            record = session.scalar(
                select(OrganizationInvitationRecord)
                .where(
                    OrganizationInvitationRecord.organization_id == organization_id,
                    OrganizationInvitationRecord.email == normalized_email,
                    OrganizationInvitationRecord.accepted_at.is_(None),
                    OrganizationInvitationRecord.revoked_at.is_(None),
                    OrganizationInvitationRecord.expires_at > now,
                )
                .order_by(
                    OrganizationInvitationRecord.created_at.desc(),
                    OrganizationInvitationRecord.invitation_id.desc(),
                )
            )
            return None if record is None else self._to_organization_invitation(record)

    def save_auth_challenge(self, challenge: AuthChallenge) -> AuthChallenge:
        stored = AuthChallenge.model_validate(challenge.model_dump())
        with self._session_factory.begin() as session:
            record = session.get(AuthChallengeRecord, stored.challenge_id)
            if record is None:
                record = AuthChallengeRecord(challenge_id=stored.challenge_id)
                session.add(record)
            record.kind = stored.kind
            record.email = stored.email
            record.user_id = stored.user_id
            record.organization_id = stored.organization_id
            record.invitation_id = stored.invitation_id
            record.token_hash = stored.token_hash
            record.created_at = stored.created_at
            record.expires_at = stored.expires_at
            record.consumed_at = stored.consumed_at
        resolved = self.get_auth_challenge(stored.challenge_id)
        return resolved or stored

    def get_auth_challenge(self, challenge_id: str) -> AuthChallenge | None:
        with self._session_factory() as session:
            record = session.get(AuthChallengeRecord, challenge_id)
            return None if record is None else self._to_auth_challenge(record)

    def get_auth_challenge_by_token_hash(self, token_hash: str) -> AuthChallenge | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(AuthChallengeRecord).where(AuthChallengeRecord.token_hash == token_hash)
            )
            return None if record is None else self._to_auth_challenge(record)

    @staticmethod
    def _apply_user(record: IdentityUserRecord, user: User) -> None:
        record.email = user.email
        record.display_name = user.display_name
        record.avatar_url = user.avatar_url
        record.timezone = user.timezone
        record.language = user.language
        record.preferences_json = dict(user.preferences)
        record.is_superuser = user.is_superuser
        record.is_active = user.is_active
        record.deleted_at = user.deleted_at
        record.last_login_at = user.last_login_at
        record.last_active_at = user.last_active_at
        record.created_at = user.created_at

    @staticmethod
    def _apply_organization(record: IdentityOrganizationRecord, organization: Organization) -> None:
        record.slug = organization.slug
        record.name = organization.name
        record.domain = organization.domain
        record.email = organization.email
        record.phone = organization.phone
        record.icon_url = organization.icon_url
        record.description = organization.description
        record.brand_color = organization.brand_color
        record.settings_json = dict(organization.settings)
        record.metadata_json = dict(organization.metadata)
        record.is_active = organization.is_active
        record.deleted_at = organization.deleted_at
        record.created_at = organization.created_at
        record.deletion_state = organization.deletion_state
        record.deletion_scheduled_for = organization.deletion_scheduled_for
        record.deletion_requested_at = organization.deletion_requested_at
        record.deletion_requested_by = organization.deletion_requested_by

    @staticmethod
    def _to_user(record: IdentityUserRecord) -> User:
        return User(
            user_id=record.user_id,
            email=record.email,
            display_name=record.display_name,
            avatar_url=record.avatar_url,
            timezone=record.timezone,
            language=record.language,
            preferences=dict(record.preferences_json or {}),
            is_superuser=record.is_superuser,
            is_active=record.is_active,
            deleted_at=_ensure_utc(record.deleted_at),
            last_login_at=_ensure_utc(record.last_login_at),
            last_active_at=_ensure_utc(record.last_active_at),
            created_at=_ensure_utc(record.created_at) or _utc_now(),
        )

    @staticmethod
    def _to_organization(record: IdentityOrganizationRecord) -> Organization:
        return Organization(
            organization_id=record.organization_id,
            slug=record.slug,
            name=record.name,
            domain=record.domain,
            email=record.email,
            phone=record.phone,
            icon_url=record.icon_url,
            description=record.description,
            brand_color=record.brand_color,
            settings=dict(record.settings_json or {}),
            metadata=dict(record.metadata_json or {}),
            is_active=record.is_active,
            deleted_at=_ensure_utc(record.deleted_at),
            created_at=_ensure_utc(record.created_at),
            deletion_state=getattr(record, "deletion_state", "active") or "active",
            deletion_scheduled_for=_ensure_utc(getattr(record, "deletion_scheduled_for", None)),
            deletion_requested_at=_ensure_utc(getattr(record, "deletion_requested_at", None)),
            deletion_requested_by=getattr(record, "deletion_requested_by", None),
        )

    @staticmethod
    def _to_membership(record: IdentityOrganizationMembershipRecord) -> OrganizationMembership:
        return OrganizationMembership(
            user_id=record.user_id,
            organization_id=record.organization_id,
            role=record.role,
            is_account_owner=record.is_account_owner,
            created_at=_ensure_utc(record.created_at),
        )

    @staticmethod
    def _to_auth_session(record: AuthSessionRecord) -> AuthSession:
        return AuthSession(
            session_id=record.session_id,
            user_id=record.user_id,
            organization_id=record.organization_id,
            issued_at=_ensure_utc(record.issued_at),
            expires_at=_ensure_utc(record.expires_at),
            last_seen_at=_ensure_utc(record.last_seen_at),
            created_ip=record.created_ip,
            last_seen_ip=record.last_seen_ip,
            user_agent=record.user_agent,
            revoked_at=_ensure_utc(record.revoked_at),
        )

    @staticmethod
    def _to_refresh_family(record: RefreshTokenFamilyRecord) -> RefreshTokenFamily:
        return RefreshTokenFamily(
            family_id=record.family_id,
            session_id=record.session_id,
            user_id=record.user_id,
            organization_id=record.organization_id,
            current_token_id=record.current_token_id,
            current_token_hash=record.current_token_hash,
            issued_at=_ensure_utc(record.issued_at),
            expires_at=_ensure_utc(record.expires_at),
            revoked_at=_ensure_utc(record.revoked_at),
            compromised_at=_ensure_utc(record.compromised_at),
        )

    @staticmethod
    def _to_external_identity(record: ExternalIdentityRecord) -> ExternalIdentity:
        return ExternalIdentity(
            external_identity_id=record.external_identity_id,
            user_id=record.user_id,
            organization_id=record.organization_id,
            provider_type=record.provider_type,
            provider_key=record.provider_key,
            subject=record.subject,
            email=record.email,
            claims=dict(record.claims_json or {}),
            created_at=_ensure_utc(record.created_at),
            updated_at=_ensure_utc(record.updated_at),
        )

    @staticmethod
    def _to_enterprise_sso_configuration(
        record: EnterpriseSSOConfigurationRecord,
    ) -> EnterpriseSSOConfiguration:
        return EnterpriseSSOConfiguration(
            sso_configuration_id=record.sso_configuration_id,
            organization_id=record.organization_id,
            issuer_url=record.issuer_url,
            client_id=record.client_id,
            client_secret_ref=record.client_secret_ref,
            allowed_domains=list(record.allowed_domains_json or []),
            scopes=list(record.scopes_json or []),
            is_active=record.is_active,
            enforce_sso=record.enforce_sso,
            jit_provisioning_enabled=record.jit_provisioning_enabled,
            created_at=_ensure_utc(record.created_at),
            updated_at=_ensure_utc(record.updated_at),
        )

    @staticmethod
    def _to_organization_invitation(record: OrganizationInvitationRecord) -> OrganizationInvitation:
        return OrganizationInvitation(
            invitation_id=record.invitation_id,
            organization_id=record.organization_id,
            email=record.email,
            role=record.role,
            is_account_owner=record.is_account_owner,
            invited_by_user_id=record.invited_by_user_id,
            token_hash=record.token_hash,
            created_at=_ensure_utc(record.created_at),
            expires_at=_ensure_utc(record.expires_at),
            accepted_at=_ensure_utc(record.accepted_at),
            accepted_by_user_id=record.accepted_by_user_id,
            revoked_at=_ensure_utc(record.revoked_at),
            revoked_by_user_id=record.revoked_by_user_id,
        )

    @staticmethod
    def _to_auth_challenge(record: AuthChallengeRecord) -> AuthChallenge:
        return AuthChallenge(
            challenge_id=record.challenge_id,
            kind=record.kind,
            email=record.email,
            user_id=record.user_id,
            organization_id=record.organization_id,
            invitation_id=record.invitation_id,
            token_hash=record.token_hash,
            created_at=_ensure_utc(record.created_at),
            expires_at=_ensure_utc(record.expires_at),
            consumed_at=_ensure_utc(record.consumed_at),
        )
