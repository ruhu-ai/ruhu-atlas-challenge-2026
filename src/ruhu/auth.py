from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from jwt.exceptions import DecodeError

from .email_normalization import normalize_email
from .identity import (
    AuthChallenge,
    AuthSession,
    EnterpriseSSOConfiguration,
    ExternalIdentity,
    IdentityStore,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    RefreshTokenFamily,
    SessionAuditContext,
    User,
)
from .jwt_keys import JWTKeyManager


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuthError(Exception):
    pass


class AuthenticationError(AuthError):
    pass


class AuthorizationError(AuthError):
    pass


class ConflictError(AuthError):
    pass


class TokenExpiredError(AuthenticationError):
    pass


class AccessTokenClaims(BaseModel):
    iss: str
    sub: str
    sid: str
    org: str
    iat: int
    exp: int
    typ: Literal["access"] = "access"


class RefreshTokenClaims(BaseModel):
    iss: str
    sub: str
    sid: str
    org: str
    fid: str
    jti: str
    iat: int
    exp: int
    typ: Literal["refresh"] = "refresh"


class OAuthStateClaims(BaseModel):
    iss: str
    iat: int
    exp: int
    typ: Literal["oauth_state"] = "oauth_state"
    payload: dict[str, object]


class ExternalAuthProfile(BaseModel):
    subject: str
    email: str
    email_verified: bool | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    claims: dict[str, object] = Field(default_factory=dict)


class AuthenticatedPrincipal(BaseModel):
    user: User
    organization: Organization
    session: AuthSession
    organization_membership: OrganizationMembership

    @property
    def organization_role(self) -> str:
        return self.organization_membership.role

    @property
    def is_account_owner(self) -> bool:
        return self.organization_membership.is_account_owner

    @property
    def is_superuser(self) -> bool:
        return self.user.is_superuser

    @property
    def can_manage_organization(self) -> bool:
        return self.organization_membership.role == "admin" or self.organization_membership.is_account_owner


class IssuedSession(BaseModel):
    access_token: str
    session: AuthSession


class IssuedBrowserSession(BaseModel):
    access_token: str
    refresh_token: str
    session: AuthSession
    refresh_family: RefreshTokenFamily


class IssuedOrganizationInvitation(BaseModel):
    invitation: OrganizationInvitation
    invitation_token: str


class IssuedAuthChallenge(BaseModel):
    challenge: AuthChallenge
    token: str


class JWTCodec:
    def __init__(
        self,
        *,
        secret: str | None = None,
        issuer: str = "ruhu",
        key_manager: JWTKeyManager | None = None,
    ) -> None:
        self.issuer = issuer
        if key_manager is None:
            key_manager = JWTKeyManager.from_sources(hs256_secret=secret)
        self.key_manager = key_manager

    def encode(self, claims: BaseModel) -> str:
        payload = claims.model_dump(mode="json")
        key, algorithm, headers = self.key_manager.signing_params()
        if not headers:
            headers = {"typ": "JWT"}
        else:
            headers = {"typ": "JWT", **headers}
        return jwt.encode(
            payload,
            key,
            algorithm=algorithm,
            headers=headers,
        )

    def decode(self, token: str) -> AccessTokenClaims:
        payload = self._decode_payload(token)
        claims = AccessTokenClaims.model_validate(payload)
        if claims.typ != "access":
            raise AuthenticationError("unexpected access token type")
        return claims

    def decode_refresh_token(self, token: str) -> RefreshTokenClaims:
        payload = self._decode_payload(token)
        claims = RefreshTokenClaims.model_validate(payload)
        if claims.typ != "refresh":
            raise AuthenticationError("unexpected refresh token type")
        return claims

    def decode_oauth_state_token(self, token: str) -> OAuthStateClaims:
        payload = self._decode_payload(token)
        claims = OAuthStateClaims.model_validate(payload)
        if claims.typ != "oauth_state":
            raise AuthenticationError("unexpected oauth state token type")
        return claims

    def _decode_payload(self, token: str) -> dict[str, object]:
        try:
            header = jwt.get_unverified_header(token)
        except DecodeError as exc:
            raise AuthenticationError("malformed token") from exc
        if header.get("typ") != "JWT":
            raise AuthenticationError("unsupported token header")
        algorithm = header.get("alg")
        if algorithm not in {"HS256", "RS256"}:
            raise AuthenticationError("unsupported token header")
        candidates = self.key_manager.verification_candidates(
            algorithm=algorithm,
            kid=header.get("kid") if isinstance(header.get("kid"), str) else None,
        )
        if not candidates:
            raise AuthenticationError("no verification key available for token")

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                payload = jwt.decode(
                    token,
                    candidate,
                    algorithms=[algorithm],
                    issuer=self.issuer,
                    leeway=30,  # clock-skew tolerance, seconds
                    options={"verify_exp": True, "verify_aud": False},
                )
                if not isinstance(payload, dict):
                    raise AuthenticationError("invalid token payload")
                return payload
            except ExpiredSignatureError as exc:
                # Token structurally valid but past exp + leeway. No other key
                # candidate can change that — short-circuit with a typed error.
                raise TokenExpiredError("token has expired") from exc
            except InvalidTokenError as exc:
                last_error = exc
                continue
        raise AuthenticationError("invalid token signature") from last_error

    def public_jwks(self) -> dict[str, list[dict[str, object]]]:
        return self.key_manager.public_jwks()


class AuthService:
    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        jwt_codec: JWTCodec,
        now_provider: Callable[[], datetime] = utc_now,
        open_signup_domains: list[str] | None = None,
    ) -> None:
        self.identity_store = identity_store
        self.jwt_codec = jwt_codec
        self.now_provider = now_provider
        self._open_signup_domains: frozenset[str] = frozenset(
            d.strip().lower() for d in (open_signup_domains or []) if d.strip()
        )

    def create_oauth_state(
        self,
        *,
        payload: dict[str, object],
        ttl: timedelta = timedelta(minutes=10),
    ) -> str:
        now = self.now_provider()
        claims = OAuthStateClaims(
            iss=self.jwt_codec.issuer,
            iat=int(now.timestamp()),
            exp=int((now + ttl).timestamp()),
            typ="oauth_state",
            payload=payload,
        )
        return self.jwt_codec.encode(claims)

    def decode_oauth_state(self, state_token: str) -> dict[str, object]:
        claims = self.jwt_codec.decode_oauth_state_token(state_token)
        now = self.now_provider()
        if claims.exp <= int(now.timestamp()):
            raise TokenExpiredError("oauth state has expired")
        return dict(claims.payload)

    def get_organization_invitation_by_token(self, invitation_token: str) -> OrganizationInvitation:
        invitation = self.identity_store.get_organization_invitation_by_token_hash(self._hash_token(invitation_token))
        now = self.now_provider()
        if invitation is None:
            raise AuthenticationError("invitation is invalid")
        if invitation.revoked_at is not None or invitation.accepted_at is not None:
            raise ConflictError("invitation is no longer active")
        if invitation.expires_at <= now:
            raise AuthenticationError("invitation has expired")
        organization = self.identity_store.get_organization(invitation.organization_id)
        if organization is None:
            raise AuthorizationError("unknown organization")
        self._require_active_organization(organization)
        return invitation

    def get_enterprise_sso_configuration_for_organization(
        self,
        organization_id: str,
    ) -> EnterpriseSSOConfiguration | None:
        configuration = self.identity_store.get_enterprise_sso_configuration_for_organization(organization_id)
        if configuration is None:
            return None
        organization = self.identity_store.get_organization(organization_id)
        if organization is None:
            raise AuthorizationError("unknown organization")
        self._require_active_organization(organization)
        return configuration

    def find_active_enterprise_sso_configuration_by_email(
        self,
        email: str,
    ) -> EnterpriseSSOConfiguration | None:
        normalized_email = normalize_email(email)
        if "@" not in normalized_email:
            raise AuthenticationError("invalid work email")
        domain = normalized_email.split("@", 1)[1]
        for configuration in self.identity_store.list_enterprise_sso_configurations():
            if not configuration.is_active:
                continue
            if domain in configuration.allowed_domains:
                organization = self.identity_store.get_organization(configuration.organization_id)
                if organization is None or not organization.is_active or organization.deleted_at is not None:
                    continue
                return configuration
        return None

    def save_enterprise_sso_configuration(
        self,
        *,
        organization_id: str,
        issuer_url: str,
        client_id: str,
        client_secret_ref: str,
        allowed_domains: list[str],
        scopes: list[str] | None = None,
        is_active: bool = True,
        enforce_sso: bool = False,
        jit_provisioning_enabled: bool = False,
    ) -> EnterpriseSSOConfiguration:
        organization = self.identity_store.get_organization(organization_id)
        if organization is None:
            raise AuthorizationError("unknown organization")
        self._require_active_organization(organization)
        existing = self.identity_store.get_enterprise_sso_configuration_for_organization(organization_id)
        now = self.now_provider()
        configuration = EnterpriseSSOConfiguration(
            sso_configuration_id=(
                existing.sso_configuration_id if existing is not None else str(uuid4())
            ),
            organization_id=organization_id,
            issuer_url=issuer_url,
            client_id=client_id,
            client_secret_ref=client_secret_ref,
            allowed_domains=allowed_domains,
            scopes=scopes or ["openid", "profile", "email"],
            is_active=is_active,
            enforce_sso=enforce_sso,
            jit_provisioning_enabled=jit_provisioning_enabled,
            created_at=now if existing is None else existing.created_at,
            updated_at=now,
        )
        return self.identity_store.save_enterprise_sso_configuration(configuration)

    def disable_enterprise_sso_configuration(
        self,
        *,
        organization_id: str,
    ) -> EnterpriseSSOConfiguration | None:
        existing = self.identity_store.get_enterprise_sso_configuration_for_organization(organization_id)
        if existing is None:
            return None
        return self.identity_store.save_enterprise_sso_configuration(
            existing.model_copy(
                update={
                    "is_active": False,
                    "enforce_sso": False,
                    "updated_at": self.now_provider(),
                }
            )
        )

    def issue_session(
        self,
        *,
        user_id: str,
        organization_id: str,
        ttl: timedelta = timedelta(hours=1),
        audit: SessionAuditContext | None = None,
    ) -> IssuedSession:
        user = self.identity_store.get_user(user_id)
        if user is None:
            raise AuthenticationError("unknown user")
        self._require_active_user(user)

        organization = self.identity_store.get_organization(organization_id)
        if organization is None:
            raise AuthorizationError("unknown organization")
        self._require_active_organization(organization)

        organization_membership = self.identity_store.get_organization_membership(user_id, organization_id)
        if organization_membership is None:
            raise AuthorizationError("user is not a member of the organization")

        issued_at = self.now_provider()
        audit_context = audit or SessionAuditContext()
        expires_at = issued_at + ttl
        session = AuthSession(
            session_id=str(uuid4()),
            user_id=user.user_id,
            organization_id=organization.organization_id,
            issued_at=issued_at,
            expires_at=expires_at,
            last_seen_at=audit_context.occurred_at or issued_at,
            created_ip=audit_context.ip,
            last_seen_ip=audit_context.ip,
            user_agent=audit_context.user_agent,
        )
        stored_session = self.identity_store.save_session(session)
        claims = AccessTokenClaims(
            iss=self.jwt_codec.issuer,
            sub=user.user_id,
            sid=stored_session.session_id,
            org=organization.organization_id,
            iat=int(stored_session.issued_at.timestamp()),
            exp=int(stored_session.expires_at.timestamp()),
            typ="access",
        )
        return IssuedSession(access_token=self.jwt_codec.encode(claims), session=stored_session)

    def issue_browser_session(
        self,
        *,
        user_id: str,
        organization_id: str | None = None,
        access_ttl: timedelta = timedelta(hours=1),
        refresh_ttl: timedelta = timedelta(days=14),
        audit: SessionAuditContext | None = None,
    ) -> IssuedBrowserSession:
        resolved_organization_id = self.resolve_login_organization(
            user_id=user_id,
            organization_id=organization_id,
        )
        issued_session = self.issue_session(
            user_id=user_id,
            organization_id=resolved_organization_id,
            ttl=access_ttl,
            audit=audit,
        )
        now = self.now_provider()
        refresh_expires_at = now + refresh_ttl
        family_id = str(uuid4())
        token_id = str(uuid4())
        refresh_claims = RefreshTokenClaims(
            iss=self.jwt_codec.issuer,
            sub=issued_session.session.user_id,
            sid=issued_session.session.session_id,
            org=issued_session.session.organization_id,
            fid=family_id,
            jti=token_id,
            iat=int(now.timestamp()),
            exp=int(refresh_expires_at.timestamp()),
            typ="refresh",
        )
        refresh_token = self.jwt_codec.encode(refresh_claims)
        family = RefreshTokenFamily(
            family_id=family_id,
            session_id=issued_session.session.session_id,
            user_id=issued_session.session.user_id,
            organization_id=issued_session.session.organization_id,
            current_token_id=token_id,
            current_token_hash=self._hash_token(refresh_token),
            issued_at=now,
            expires_at=refresh_expires_at,
        )
        stored_family = self.identity_store.save_refresh_token_family(family)
        self._mark_user_login(issued_session.session.user_id, when=now)
        return IssuedBrowserSession(
            access_token=issued_session.access_token,
            refresh_token=refresh_token,
            session=issued_session.session,
            refresh_family=stored_family,
        )

    def refresh_browser_session(
        self,
        refresh_token: str,
        *,
        access_ttl: timedelta = timedelta(hours=1),
        refresh_ttl: timedelta = timedelta(days=14),
        audit: SessionAuditContext | None = None,
    ) -> IssuedBrowserSession:
        claims = self.jwt_codec.decode_refresh_token(refresh_token)
        now = self.now_provider()
        if claims.exp <= int(now.timestamp()):
            raise TokenExpiredError("refresh token has expired")

        family = self.identity_store.get_refresh_token_family(claims.fid)
        if family is None:
            raise AuthenticationError("refresh session is unavailable")
        if family.revoked_at is not None:
            raise AuthenticationError("refresh session has been revoked")
        if family.expires_at <= now:
            raise TokenExpiredError("refresh session has expired")

        if family.current_token_id != claims.jti or family.current_token_hash != self._hash_token(refresh_token):
            self.identity_store.revoke_refresh_token_family(
                family.family_id,
                revoked_at=now,
                compromised_at=now,
            )
            self.identity_store.revoke_session(family.session_id, revoked_at=now)
            raise AuthenticationError("refresh token reuse detected")

        session = self.identity_store.get_session(family.session_id)
        if session is None or session.revoked_at is not None:
            raise AuthenticationError("session is unavailable")

        user = self.identity_store.get_user(session.user_id)
        if user is None:
            raise AuthenticationError("unknown user")
        self._require_active_user(user)

        organization = self.identity_store.get_organization(session.organization_id)
        if organization is None:
            raise AuthorizationError("unknown organization")
        self._require_active_organization(organization)
        revoked_after = self._organization_auth_revoked_after_epoch(organization.settings)
        if revoked_after is not None and claims.iat < revoked_after:
            self.identity_store.revoke_refresh_token_family(
                family.family_id,
                revoked_at=now,
                compromised_at=now,
            )
            self.identity_store.revoke_session(session.session_id, revoked_at=now)
            raise AuthenticationError("session expired. please sign in again")

        access_expires_at = now + access_ttl
        audit_context = audit or SessionAuditContext()
        refreshed_session = session.model_copy(
            update={
                "expires_at": access_expires_at,
                "last_seen_at": audit_context.occurred_at or now,
                "last_seen_ip": audit_context.ip if audit_context.ip is not None else session.last_seen_ip,
                "user_agent": (
                    audit_context.user_agent if audit_context.user_agent is not None else session.user_agent
                ),
            }
        )
        stored_session = self.identity_store.save_session(refreshed_session)

        access_claims = AccessTokenClaims(
            iss=self.jwt_codec.issuer,
            sub=stored_session.user_id,
            sid=stored_session.session_id,
            org=stored_session.organization_id,
            iat=int(now.timestamp()),
            exp=int(stored_session.expires_at.timestamp()),
            typ="access",
        )
        access_token = self.jwt_codec.encode(access_claims)

        next_refresh_expires_at = now + refresh_ttl
        next_token_id = str(uuid4())
        next_refresh_claims = RefreshTokenClaims(
            iss=self.jwt_codec.issuer,
            sub=stored_session.user_id,
            sid=stored_session.session_id,
            org=stored_session.organization_id,
            fid=family.family_id,
            jti=next_token_id,
            iat=int(now.timestamp()),
            exp=int(next_refresh_expires_at.timestamp()),
            typ="refresh",
        )
        next_refresh_token = self.jwt_codec.encode(next_refresh_claims)
        rotated_family = family.model_copy(
            update={
                "current_token_id": next_token_id,
                "current_token_hash": self._hash_token(next_refresh_token),
                "expires_at": next_refresh_expires_at,
                "compromised_at": None,
            }
        )
        stored_family = self.identity_store.save_refresh_token_family(rotated_family)
        return IssuedBrowserSession(
            access_token=access_token,
            refresh_token=next_refresh_token,
            session=stored_session,
            refresh_family=stored_family,
        )

    def revoke_browser_session(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        now = self.now_provider()
        session_id: str | None = None

        if refresh_token is not None:
            with_refresh = self.jwt_codec.decode_refresh_token(refresh_token)
            session_id = with_refresh.sid
            self.identity_store.revoke_refresh_token_family(with_refresh.fid, revoked_at=now)

        if access_token is not None:
            with_access = self.jwt_codec.decode(access_token)
            session_id = with_access.sid

        if session_id is not None:
            self._revoke_session_refresh_families(session_id, revoked_at=now)
            self.identity_store.revoke_session(session_id, revoked_at=now)

    def authenticate_access_token(self, token: str) -> AuthenticatedPrincipal:
        claims = self.jwt_codec.decode(token)
        now = self.now_provider()
        if claims.exp <= int(now.timestamp()):
            raise TokenExpiredError("access token has expired")

        session = self.identity_store.get_session(claims.sid)
        if session is None or session.revoked_at is not None:
            raise AuthenticationError("session is unavailable")
        if session.expires_at <= now:
            raise TokenExpiredError("session has expired")
        if session.user_id != claims.sub or session.organization_id != claims.org:
            raise AuthenticationError("session scope does not match token claims")

        user = self.identity_store.get_user(claims.sub)
        if user is None:
            raise AuthenticationError("unknown user")
        self._require_active_user(user)

        organization = self.identity_store.get_organization(claims.org)
        if organization is None:
            raise AuthorizationError("unknown organization")
        self._require_active_organization(organization)
        if self._is_token_stale_for_organization(claims, organization):
            raise AuthenticationError("session expired. please sign in again")

        organization_membership = self.identity_store.get_organization_membership(user.user_id, organization.organization_id)
        if organization_membership is None:
            raise AuthorizationError("user is not a member of the organization")

        return AuthenticatedPrincipal(
            user=user,
            organization=organization,
            session=session,
            organization_membership=organization_membership,
        )

    def record_session_activity(
        self,
        session_id: str,
        *,
        audit: SessionAuditContext | None = None,
        min_update_interval: timedelta = timedelta(minutes=5),
    ) -> AuthSession | None:
        session = self.identity_store.get_session(session_id)
        if session is None or session.revoked_at is not None:
            return session
        audit_context = audit or SessionAuditContext()
        seen_at = audit_context.occurred_at or self.now_provider()
        should_skip_time_update = (
            session.last_seen_at is not None
            and seen_at - session.last_seen_at < min_update_interval
            and audit_context.ip == session.last_seen_ip
            and (
                audit_context.user_agent is None
                or audit_context.user_agent == session.user_agent
            )
        )
        if should_skip_time_update:
            return session
        updated_session = session.model_copy(
            update={
                "last_seen_at": seen_at,
                "last_seen_ip": audit_context.ip if audit_context.ip is not None else session.last_seen_ip,
                "user_agent": (
                    audit_context.user_agent if audit_context.user_agent is not None else session.user_agent
                ),
            }
        )
        stored_session = self.identity_store.save_session(updated_session)
        self._mark_user_activity(stored_session.user_id, when=seen_at)
        return stored_session

    def list_user_sessions(
        self,
        *,
        user_id: str,
        organization_id: str,
    ) -> list[AuthSession]:
        now = self.now_provider()
        return [
            session
            for session in self.identity_store.list_sessions_for_user(user_id, organization_id)
            if session.revoked_at is None and session.expires_at > now
        ]

    def revoke_user_session(
        self,
        *,
        session_id: str,
        user_id: str,
        organization_id: str,
        revoked_at: datetime | None = None,
    ) -> AuthSession:
        session = self.identity_store.get_session(session_id)
        if session is None:
            raise AuthorizationError("unknown session")
        if session.user_id != user_id or session.organization_id != organization_id:
            raise AuthorizationError("unknown session")
        now = revoked_at or self.now_provider()
        self._revoke_session_refresh_families(session.session_id, revoked_at=now)
        revoked_session = self.identity_store.revoke_session(session.session_id, revoked_at=now)
        if revoked_session is None:
            raise AuthorizationError("unknown session")
        return revoked_session

    def revoke_user_sessions(
        self,
        *,
        user_id: str,
        organization_id: str,
        revoked_at: datetime | None = None,
    ) -> list[AuthSession]:
        now = revoked_at or self.now_provider()
        revoked_sessions: list[AuthSession] = []
        for session in self.list_user_sessions(user_id=user_id, organization_id=organization_id):
            self._revoke_session_refresh_families(session.session_id, revoked_at=now)
            revoked_session = self.identity_store.revoke_session(session.session_id, revoked_at=now)
            if revoked_session is not None:
                revoked_sessions.append(revoked_session)
        return revoked_sessions

    def resolve_login_organization(
        self,
        *,
        user_id: str,
        organization_id: str | None,
    ) -> str:
        memberships = self.identity_store.list_organization_memberships_for_user(user_id)
        if not memberships:
            raise AuthorizationError("user does not belong to any organization")
        if organization_id is None:
            if len(memberships) != 1:
                raise AuthorizationError("organization_id is required for multi-organization users")
            return memberships[0].organization_id

        membership = self.identity_store.get_organization_membership(user_id, organization_id)
        if membership is None:
            raise AuthorizationError("user is not a member of the organization")
        return membership.organization_id

    def link_external_identity(self, identity: ExternalIdentity) -> ExternalIdentity:
        user = self.identity_store.get_user(identity.user_id)
        if user is None:
            raise AuthenticationError("unknown user")
        organization_membership = self.identity_store.get_organization_membership(identity.user_id, identity.organization_id)
        if organization_membership is None:
            raise AuthorizationError("user is not a member of the organization")
        return self.identity_store.save_external_identity(identity)

    def revoke_organization_sessions(
        self,
        *,
        organization_id: str,
        revoked_at: datetime | None = None,
    ) -> Organization:
        organization = self.identity_store.get_organization(organization_id)
        if organization is None:
            raise AuthorizationError("unknown organization")
        self._require_active_organization(organization)
        cutoff = revoked_at or self.now_provider()
        settings = dict(organization.settings)
        settings["auth_revoked_after_epoch"] = int(cutoff.timestamp())
        settings["auth_revoked_after"] = cutoff.isoformat()
        return self.identity_store.save_organization(
            organization.model_copy(
                update={
                    "settings": settings,
                }
            )
        )

    def create_organization_invitation(
        self,
        *,
        organization_id: str,
        email: str,
        role: str,
        is_account_owner: bool,
        invited_by_user_id: str,
        ttl: timedelta = timedelta(days=7),
    ) -> IssuedOrganizationInvitation:
        organization = self.identity_store.get_organization(organization_id)
        if organization is None:
            raise AuthorizationError("unknown organization")
        self._require_active_organization(organization)
        inviter_membership = self.identity_store.get_organization_membership(invited_by_user_id, organization_id)
        if inviter_membership is None:
            raise AuthorizationError("inviter is not a member of the organization")
        normalized_email = normalize_email(email)
        existing_user = self.identity_store.get_user_by_email(normalized_email)
        if existing_user is not None:
            existing_membership = self.identity_store.get_organization_membership(existing_user.user_id, organization_id)
            if existing_membership is not None:
                raise ConflictError("user is already a member of the organization")
        existing_invitation = self.identity_store.get_active_organization_invitation_by_email(
            organization_id,
            normalized_email,
        )
        if existing_invitation is not None:
            raise ConflictError("an active invitation already exists for that email")

        now = self.now_provider()
        invitation_token = secrets.token_urlsafe(32)
        invitation = OrganizationInvitation(
            organization_id=organization_id,
            email=normalized_email,
            role=role,
            is_account_owner=is_account_owner,
            invited_by_user_id=invited_by_user_id,
            token_hash=self._hash_token(invitation_token),
            created_at=now,
            expires_at=now + ttl,
        )
        stored_invitation = self.identity_store.save_organization_invitation(invitation)
        return IssuedOrganizationInvitation(
            invitation=stored_invitation,
            invitation_token=invitation_token,
        )

    def list_organization_invitations(self, *, organization_id: str) -> list[OrganizationInvitation]:
        return self.identity_store.list_organization_invitations(organization_id)

    def revoke_organization_invitation(
        self,
        *,
        invitation_id: str,
        organization_id: str,
        revoked_by_user_id: str,
        revoked_at: datetime | None = None,
    ) -> OrganizationInvitation:
        invitation = self.identity_store.get_organization_invitation(invitation_id)
        if invitation is None or invitation.organization_id != organization_id:
            raise AuthorizationError("unknown invitation")
        now = revoked_at or self.now_provider()
        updated_invitation = invitation.model_copy(
            update={
                "revoked_at": now,
                "revoked_by_user_id": revoked_by_user_id,
            }
        )
        return self.identity_store.save_organization_invitation(updated_invitation)

    def request_magic_link(
        self,
        *,
        email: str,
        organization_id: str | None = None,
        invitation_token: str | None = None,
        ttl: timedelta = timedelta(minutes=15),
    ) -> IssuedAuthChallenge:
        normalized_email = normalize_email(email)
        user = self.identity_store.get_user_by_email(normalized_email)
        now = self.now_provider()
        token = secrets.token_urlsafe(32)

        if user is not None:
            self._require_active_user(user)
            resolved_organization_id = self.resolve_login_organization(
                user_id=user.user_id,
                organization_id=organization_id,
            )
            self._assert_non_sso_only_signin(
                organization_id=resolved_organization_id,
                mode="Magic link sign-in",
            )
            challenge = AuthChallenge(
                kind="magic_link_existing",
                email=normalized_email,
                user_id=user.user_id,
                organization_id=resolved_organization_id,
                token_hash=self._hash_token(token),
                created_at=now,
                expires_at=now + ttl,
            )
        else:
            email_domain = normalized_email.split("@", 1)[-1].lower()
            if email_domain in self._open_signup_domains:
                user = self._provision_user_with_organization(normalized_email)
                resolved_organization_id = self.resolve_login_organization(
                    user_id=user.user_id,
                    organization_id=organization_id,
                )
                challenge = AuthChallenge(
                    kind="magic_link_existing",
                    email=normalized_email,
                    user_id=user.user_id,
                    organization_id=resolved_organization_id,
                    token_hash=self._hash_token(token),
                    created_at=now,
                    expires_at=now + ttl,
                )
            else:
                if invitation_token is None or not invitation_token.strip():
                    raise AuthorizationError("sign-up is invite-only. Provide a valid invitation token.")
                invitation = self.get_organization_invitation_by_token(invitation_token)
                if invitation.email != normalized_email:
                    raise AuthorizationError("invitation email does not match request email")
                challenge = AuthChallenge(
                    kind="magic_link_invitation",
                    email=normalized_email,
                    organization_id=invitation.organization_id,
                    invitation_id=invitation.invitation_id,
                    token_hash=self._hash_token(token),
                    created_at=now,
                    expires_at=now + ttl,
                )

        stored_challenge = self.identity_store.save_auth_challenge(challenge)
        return IssuedAuthChallenge(challenge=stored_challenge, token=token)

    def verify_magic_link(
        self,
        *,
        token: str,
        audit: SessionAuditContext | None = None,
        access_ttl: timedelta = timedelta(hours=1),
        refresh_ttl: timedelta = timedelta(days=14),
    ) -> IssuedBrowserSession:
        challenge = self.identity_store.get_auth_challenge_by_token_hash(self._hash_token(token))
        now = self.now_provider()
        if challenge is None:
            raise AuthenticationError("invalid or expired sign-in link")
        if challenge.consumed_at is not None:
            raise ConflictError("sign-in link has already been used")
        if challenge.expires_at <= now:
            raise AuthenticationError("sign-in link has expired")

        self.identity_store.save_auth_challenge(
            challenge.model_copy(update={"consumed_at": now})
        )

        if challenge.kind == "magic_link_existing":
            if challenge.user_id is None or challenge.organization_id is None:
                raise AuthenticationError("magic link challenge is invalid")
            self._assert_non_sso_only_signin(
                organization_id=challenge.organization_id,
                mode="Magic link sign-in",
            )
            return self.issue_browser_session(
                user_id=challenge.user_id,
                organization_id=challenge.organization_id,
                access_ttl=access_ttl,
                refresh_ttl=refresh_ttl,
                audit=audit,
            )

        if challenge.kind == "magic_link_invitation":
            if challenge.invitation_id is None:
                raise AuthenticationError("magic link challenge is invalid")
            invitation = self.identity_store.get_organization_invitation(challenge.invitation_id)
            if invitation is None:
                raise AuthenticationError("invitation is invalid")
            user = self._complete_organization_invitation(
                invitation=invitation,
                display_name=self._default_display_name_for_email(invitation.email),
            )
            return self.issue_browser_session(
                user_id=user.user_id,
                organization_id=invitation.organization_id,
                access_ttl=access_ttl,
                refresh_ttl=refresh_ttl,
                audit=audit,
            )

        raise AuthenticationError("unsupported auth challenge type")

    def authenticate_external_identity(
        self,
        *,
        provider_type: str,
        provider_key: str,
        profile: ExternalAuthProfile,
        organization_id: str | None = None,
        invitation_token: str | None = None,
        jit_organization_id: str | None = None,
        jit_default_role: str = "analyst",
        audit: SessionAuditContext | None = None,
        access_ttl: timedelta = timedelta(hours=1),
        refresh_ttl: timedelta = timedelta(days=14),
    ) -> IssuedBrowserSession:
        normalized_email = normalize_email(profile.email)
        if profile.email_verified is False:
            raise AuthenticationError("email is not verified by identity provider")

        invitation: OrganizationInvitation | None = None
        if invitation_token is not None and invitation_token.strip():
            invitation = self.get_organization_invitation_by_token(invitation_token)
            if invitation.email != normalized_email:
                raise AuthorizationError("invitation email does not match identity email")

        existing_identity = self.identity_store.get_external_identity(
            provider_type,
            provider_key,
            profile.subject,
        )
        user = self.identity_store.get_user_by_email(normalized_email)
        if existing_identity is not None:
            linked_user = self.identity_store.get_user(existing_identity.user_id)
            if linked_user is None:
                raise AuthenticationError("linked account not found")
            user = linked_user

        if user is not None:
            self._require_active_user(user)

        resolved_organization_id: str | None = None
        if invitation is not None:
            user = self._complete_organization_invitation(
                invitation=invitation,
                existing_user=user,
                display_name=profile.display_name,
                avatar_url=profile.avatar_url,
            )
            resolved_organization_id = invitation.organization_id
        elif user is None and jit_organization_id is not None:
            organization = self.identity_store.get_organization(jit_organization_id)
            if organization is None:
                raise AuthorizationError("unknown organization")
            self._require_active_organization(organization)
            user = self.identity_store.save_user(
                User(
                    user_id=str(uuid4()),
                    email=normalized_email,
                    display_name=profile.display_name or self._default_display_name_for_email(normalized_email),
                    avatar_url=profile.avatar_url,
                )
            )
            self.identity_store.add_organization_membership(
                OrganizationMembership(
                    user_id=user.user_id,
                    organization_id=jit_organization_id,
                    role=jit_default_role,
                )
            )
            resolved_organization_id = jit_organization_id
        elif user is None:
            email_domain = normalized_email.split("@", 1)[-1].lower()
            if email_domain in self._open_signup_domains:
                user = self._provision_user_with_organization(
                    normalized_email,
                    display_name=profile.display_name,
                    avatar_url=profile.avatar_url,
                )
                resolved_organization_id = self.resolve_login_organization(
                    user_id=user.user_id,
                    organization_id=organization_id,
                )
            else:
                raise AuthorizationError("sign-up is invite-only. A valid invitation token is required.")

        assert user is not None

        if resolved_organization_id is None:
            if provider_type == "oidc" and jit_organization_id is not None:
                resolved_organization_id = jit_organization_id
            else:
                resolved_organization_id = self.resolve_login_organization(
                    user_id=user.user_id,
                    organization_id=organization_id,
                )

        if provider_type == "google":
            self._assert_non_sso_only_signin(
                organization_id=resolved_organization_id,
                mode="Google sign-in",
            )

        linked_identity = ExternalIdentity(
            external_identity_id=(
                existing_identity.external_identity_id if existing_identity is not None else str(uuid4())
            ),
            user_id=user.user_id,
            organization_id=resolved_organization_id,
            provider_type=provider_type,
            provider_key=provider_key,
            subject=profile.subject,
            email=normalized_email,
            claims=profile.claims,
            created_at=(
                existing_identity.created_at if existing_identity is not None else self.now_provider()
            ),
            updated_at=self.now_provider(),
        )
        self.link_external_identity(linked_identity)

        return self.issue_browser_session(
            user_id=user.user_id,
            organization_id=resolved_organization_id,
            access_ttl=access_ttl,
            refresh_ttl=refresh_ttl,
            audit=audit,
        )

    def update_user_profile(
        self,
        *,
        user_id: str,
        display_name: str | None = None,
        avatar_url: str | None = None,
        timezone_name: str | None = None,
        language: str | None = None,
        preferences: dict[str, object] | None = None,
    ) -> User:
        user = self.identity_store.get_user(user_id)
        if user is None:
            raise AuthenticationError("unknown user")
        self._require_active_user(user)
        updates: dict[str, object] = {}
        if display_name is not None:
            updates["display_name"] = display_name
        if avatar_url is not None:
            updates["avatar_url"] = avatar_url
        if timezone_name is not None:
            updates["timezone"] = timezone_name
        if language is not None:
            updates["language"] = language
        if preferences is not None:
            updates["preferences"] = dict(preferences)
        if not updates:
            return user
        return self.identity_store.save_user(user.model_copy(update=updates))

    def set_user_superuser(
        self,
        *,
        target_user_id: str,
        enabled: bool,
        actor_user_id: str | None = None,
    ) -> User:
        user = self.identity_store.get_user(target_user_id)
        if user is None:
            raise AuthorizationError("unknown user")
        if enabled:
            self._require_active_user(user)
        if not enabled:
            if actor_user_id is not None and actor_user_id == target_user_id:
                raise ConflictError("cannot revoke your own superuser access")
            active_superusers = [
                candidate
                for candidate in self.identity_store.list_users()
                if candidate.is_superuser and candidate.is_active and candidate.deleted_at is None
            ]
            if user.is_superuser and len(active_superusers) <= 1:
                raise ConflictError("cannot revoke the last active internal superuser")
        if user.is_superuser == enabled:
            return user
        return self.identity_store.save_user(user.model_copy(update={"is_superuser": enabled}))

    def accept_organization_invitation(
        self,
        *,
        invitation_token: str,
        display_name: str | None = None,
        timezone_name: str = "UTC",
        language: str = "en",
        audit: SessionAuditContext | None = None,
        access_ttl: timedelta = timedelta(hours=1),
        refresh_ttl: timedelta = timedelta(days=14),
    ) -> IssuedBrowserSession:
        invitation = self.get_organization_invitation_by_token(invitation_token)
        user = self._complete_organization_invitation(
            invitation=invitation,
            display_name=display_name,
            timezone_name=timezone_name,
            language=language,
        )
        return self.issue_browser_session(
            user_id=user.user_id,
            organization_id=invitation.organization_id,
            access_ttl=access_ttl,
            refresh_ttl=refresh_ttl,
            audit=audit,
        )

    def _complete_organization_invitation(
        self,
        *,
        invitation: OrganizationInvitation,
        existing_user: User | None = None,
        display_name: str | None = None,
        timezone_name: str = "UTC",
        language: str = "en",
        avatar_url: str | None = None,
    ) -> User:
        now = self.now_provider()
        if invitation.revoked_at is not None or invitation.accepted_at is not None:
            raise ConflictError("invitation is no longer active")
        if invitation.expires_at <= now:
            raise AuthenticationError("invitation has expired")

        organization = self.identity_store.get_organization(invitation.organization_id)
        if organization is None:
            raise AuthorizationError("unknown organization")
        self._require_active_organization(organization)

        user = existing_user or self.identity_store.get_user_by_email(invitation.email)
        if user is None:
            user = self.identity_store.save_user(
                User(
                    user_id=str(uuid4()),
                    email=invitation.email,
                    display_name=display_name or self._default_display_name_for_email(invitation.email),
                    avatar_url=avatar_url,
                    timezone=timezone_name,
                    language=language,
                )
            )
        else:
            self._require_active_user(user)
            updates: dict[str, object] = {}
            if display_name and not user.display_name:
                updates["display_name"] = display_name
            if avatar_url and not user.avatar_url:
                updates["avatar_url"] = avatar_url
            if updates:
                user = self.identity_store.save_user(user.model_copy(update=updates))

        existing_membership = self.identity_store.get_organization_membership(user.user_id, invitation.organization_id)
        if existing_membership is not None:
            raise ConflictError("user is already a member of the organization")

        self.identity_store.add_organization_membership(
            OrganizationMembership(
                user_id=user.user_id,
                organization_id=invitation.organization_id,
                role=invitation.role,
                is_account_owner=invitation.is_account_owner,
            )
        )
        self.identity_store.save_organization_invitation(
            invitation.model_copy(
                update={
                    "accepted_at": now,
                    "accepted_by_user_id": user.user_id,
                }
            )
        )
        return user

    def _assert_non_sso_only_signin(self, *, organization_id: str, mode: str) -> None:
        configuration = self.identity_store.get_enterprise_sso_configuration_for_organization(organization_id)
        if configuration is None:
            return
        if configuration.is_active and configuration.enforce_sso:
            raise AuthorizationError(
                f"{mode} is disabled for this organization. Use enterprise SSO."
            )

    def _provision_user_with_organization(
        self,
        email: str,
        display_name: str | None = None,
        avatar_url: str | None = None,
    ) -> User:
        """Auto-create a user for an open-signup domain email.

        If an organization already exists for the email domain, the new user is
        added as a member of that org instead of creating a duplicate.

        Re-entrant: if the user already exists (e.g. two concurrent requests),
        the existing user is returned without creating a duplicate.
        """
        # Check once more under the assumption that a concurrent request may
        # have just created this user between the caller's check and now.
        existing = self.identity_store.get_user_by_email(email)
        if existing is not None:
            return existing

        now = self.now_provider()
        resolved_display_name = display_name or self._default_display_name_for_email(email)
        domain = email.split("@", 1)[-1].lower()
        org_name = domain.split(".")[0].replace("-", " ").title()
        slug_prefix = domain.replace(".", "-")

        # Look for an existing active organization for this domain.
        existing_org: Organization | None = None
        for org in self.identity_store.list_organizations():
            if org.deleted_at is not None or not org.is_active:
                continue
            # Match by explicit domain field first, then by slug prefix convention.
            if org.domain is not None and org.domain.lower() == domain:
                existing_org = org
                break
            if org.slug.startswith(slug_prefix):
                existing_org = org
                break

        user_id = str(uuid4())
        if existing_org is not None:
            organization = existing_org
            is_account_owner = False
        else:
            organization = self.identity_store.save_organization(
                Organization(
                    organization_id=str(uuid4()),
                    name=org_name,
                    domain=domain,
                    slug=f"{slug_prefix}-{str(uuid4())[:8]}",
                    created_at=now,
                )
            )
            is_account_owner = True

        user = self.identity_store.save_user(
            User(
                user_id=user_id,
                email=email,
                display_name=resolved_display_name,
                avatar_url=avatar_url,
                created_at=now,
            )
        )
        self.identity_store.add_organization_membership(
            OrganizationMembership(
                user_id=user.user_id,
                organization_id=organization.organization_id,
                role="admin",
                is_account_owner=is_account_owner,
            )
        )
        return user

    @staticmethod
    def _default_display_name_for_email(email: str) -> str:
        local_part = email.split("@", 1)[0].strip()
        if not local_part:
            return "User"
        return local_part.replace(".", " ").replace("_", " ").title()

    @staticmethod
    def _require_active_user(user: User) -> None:
        if user.deleted_at is not None or not user.is_active:
            raise AuthenticationError("user not found or deactivated")

    @staticmethod
    def _require_active_organization(organization: Organization) -> None:
        if organization.deleted_at is not None or not organization.is_active:
            raise AuthenticationError("organization is not active")

    @staticmethod
    def _organization_auth_revoked_after_epoch(settings: object) -> int | None:
        if not isinstance(settings, dict):
            return None

        raw_epoch = settings.get("auth_revoked_after_epoch")
        if raw_epoch is not None:
            try:
                return int(raw_epoch)
            except (TypeError, ValueError):
                pass

        raw_iso = settings.get("auth_revoked_after")
        if isinstance(raw_iso, str) and raw_iso.strip():
            try:
                parsed = datetime.fromisoformat(raw_iso.strip())
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return int(parsed.timestamp())
            except ValueError:
                return None

        return None

    def _is_token_stale_for_organization(
        self,
        claims: AccessTokenClaims,
        organization: Organization,
    ) -> bool:
        revoked_after = self._organization_auth_revoked_after_epoch(organization.settings)
        if revoked_after is None:
            return False
        return claims.iat < revoked_after

    def _mark_user_login(self, user_id: str, *, when: datetime) -> None:
        user = self.identity_store.get_user(user_id)
        if user is None:
            return
        self.identity_store.save_user(
            user.model_copy(
                update={
                    "last_login_at": when,
                    "last_active_at": when,
                }
            )
        )

    def _mark_user_activity(self, user_id: str, *, when: datetime) -> None:
        user = self.identity_store.get_user(user_id)
        if user is None:
            return
        self.identity_store.save_user(
            user.model_copy(
                update={
                    "last_active_at": when,
                }
            )
        )

    def _revoke_session_refresh_families(self, session_id: str, *, revoked_at: datetime) -> None:
        for family in self.identity_store.list_refresh_token_families_for_session(session_id):
            self.identity_store.revoke_refresh_token_family(family.family_id, revoked_at=revoked_at)

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
