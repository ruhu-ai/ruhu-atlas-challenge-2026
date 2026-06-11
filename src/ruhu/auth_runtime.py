from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from sqlalchemy.orm import Session, sessionmaker

from .api_auth import AuthContextResolver
from .auth import AuthService, JWTCodec
from .db import build_session_factory, resolve_database_url
from .identity import IdentityStore
from .identity_sqlalchemy import SQLAlchemyIdentityStore
from .jwt_keys import JWTKeyManager
from .secret_sources import SecretSourceConfigurationError, load_text_secret
from .tenant import TenantIdentityRepositoryFactory


@dataclass(slots=True, frozen=True)
class PersistentAuthRuntime:
    identity_store: IdentityStore
    auth_service: AuthService
    auth_resolver: AuthContextResolver
    tenant_repositories: TenantIdentityRepositoryFactory


def build_jwt_codec(
    *,
    secret: str | None = None,
    issuer: str = "ruhu",
    private_key_pem: str | None = None,
    private_key_path: str | Path | None = None,
    private_key_secret_version: str | None = None,
    active_kid: str | None = None,
    verification_jwks: str | None = None,
    verification_jwks_path: str | Path | None = None,
    verification_jwks_secret_version: str | None = None,
    require_asymmetric_tokens: bool = False,
) -> JWTCodec:
    resolved_private_key_pem, resolved_private_key_path = _resolve_text_secret_source(
        inline_value=private_key_pem,
        file_path=private_key_path,
        secret_version=private_key_secret_version,
        setting_name="auth JWT private key",
    )
    resolved_verification_jwks, resolved_verification_jwks_path = _resolve_text_secret_source(
        inline_value=verification_jwks,
        file_path=verification_jwks_path,
        secret_version=verification_jwks_secret_version,
        setting_name="auth JWT verification JWKS",
    )
    if require_asymmetric_tokens:
        if secret is not None:
            raise SecretSourceConfigurationError(
                "HS256 secret cannot be configured when asymmetric token signing is required"
            )
        if resolved_private_key_pem is None and resolved_private_key_path is None:
            raise SecretSourceConfigurationError(
                "RS256 signing material is required when asymmetric token signing is enabled"
            )
    key_manager = JWTKeyManager.from_sources(
        hs256_secret=None if require_asymmetric_tokens else secret,
        private_key_pem=resolved_private_key_pem,
        private_key_path=resolved_private_key_path,
        active_kid=active_kid,
        verification_jwks=resolved_verification_jwks,
        verification_jwks_path=resolved_verification_jwks_path,
    )
    return JWTCodec(
        issuer=issuer,
        key_manager=key_manager,
    )


def build_persistent_auth_runtime(
    *,
    database_url: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
    secret: str | None = None,
    issuer: str = "ruhu",
    private_key_pem: str | None = None,
    private_key_path: str | Path | None = None,
    private_key_secret_version: str | None = None,
    active_kid: str | None = None,
    verification_jwks: str | None = None,
    verification_jwks_path: str | Path | None = None,
    verification_jwks_secret_version: str | None = None,
    require_asymmetric_tokens: bool = False,
    open_signup_domains: list[str] | None = None,
) -> PersistentAuthRuntime:
    resolved_session_factory = (
        session_factory
        if session_factory is not None
        else build_session_factory(resolve_database_url(database_url=database_url))
    )
    identity_store = SQLAlchemyIdentityStore(resolved_session_factory)
    auth_service = AuthService(
        identity_store=identity_store,
        jwt_codec=build_jwt_codec(
            secret=secret,
            issuer=issuer,
            private_key_pem=private_key_pem,
            private_key_path=private_key_path,
            private_key_secret_version=private_key_secret_version,
            active_kid=active_kid,
            verification_jwks=verification_jwks,
            verification_jwks_path=verification_jwks_path,
            verification_jwks_secret_version=verification_jwks_secret_version,
            require_asymmetric_tokens=require_asymmetric_tokens,
        ),
        open_signup_domains=open_signup_domains,
    )
    return PersistentAuthRuntime(
        identity_store=identity_store,
        auth_service=auth_service,
        auth_resolver=AuthContextResolver(auth_service=auth_service),
        tenant_repositories=TenantIdentityRepositoryFactory(identity_store=identity_store),
    )


def build_sqlalchemy_auth_runtime(
    *,
    database_url: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
    secret: str | None = None,
    issuer: str = "ruhu",
    private_key_pem: str | None = None,
    private_key_path: str | Path | None = None,
    private_key_secret_version: str | None = None,
    active_kid: str | None = None,
    verification_jwks: str | None = None,
    verification_jwks_path: str | Path | None = None,
    verification_jwks_secret_version: str | None = None,
    require_asymmetric_tokens: bool = False,
) -> PersistentAuthRuntime:
    return build_persistent_auth_runtime(
        database_url=database_url,
        session_factory=session_factory,
        secret=secret,
        issuer=issuer,
        private_key_pem=private_key_pem,
        private_key_path=private_key_path,
        private_key_secret_version=private_key_secret_version,
        active_kid=active_kid,
        verification_jwks=verification_jwks,
        verification_jwks_path=verification_jwks_path,
        verification_jwks_secret_version=verification_jwks_secret_version,
        require_asymmetric_tokens=require_asymmetric_tokens,
    )


def _resolve_text_secret_source(
    *,
    inline_value: str | None,
    file_path: str | Path | None,
    secret_version: str | None,
    setting_name: str,
) -> tuple[str | None, str | Path | None]:
    if secret_version is not None and (inline_value is not None or file_path is not None):
        raise SecretSourceConfigurationError(
            f"configure {setting_name} from either inline/file input or Secret Manager, not both"
        )
    if secret_version is None:
        return inline_value, file_path
    return load_text_secret(secret_version), None
