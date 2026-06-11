"""OAuth 2.0 flow management and background token refresh for tool connections.

Two components:
- ``OAuthFlowManager`` — stateless; builds authorization URLs and exchanges
  authorization codes for tokens.  State integrity is guaranteed by Fernet
  encryption (same key used for credential storage).
- ``OAuthTokenRefresher`` — asyncio background task that scans for connections
  whose access tokens expire within ``REFRESH_LEAD_SECONDS`` and refreshes
  them using the stored ``refresh_token``.

State encoding
--------------
The OAuth ``state`` parameter carries a Fernet-encrypted JSON payload so that
the callback endpoint can look up the originating connection and organisation
without a server-side session store.  Fernet tokens carry an embedded
timestamp, so replay attacks older than ``STATE_TTL_SECONDS`` are rejected.

Durability model
----------------
After a successful token exchange or refresh the new tokens are written to
``APIConnectionRecord.oauth_token_json`` and ``token_expires_at`` is set.
On refresh failure the connection ``status`` is set to ``"error"`` and
``error_message`` is recorded so operators can diagnose the problem.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
import httpx

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ruhu.db_models import APIConnectionRecord

from .cipher import CredentialCipher as BlobCipher, build_aad

from .oauth_providers import OAUTH_PROVIDERS, OAuthProviderConfig, get_client_credentials

log = logging.getLogger(__name__)


def _emit_oauth_audit(
    audit_router: Any | None,
    *,
    event_type: str,
    organization_id: str,
    connection_id: str,
    provider: str,
    outcome: str = "success",
    detail: dict[str, Any] | None = None,
) -> None:
    """Emit a structured audit event for an OAuth lifecycle transition.

    No-ops when ``audit_router`` is None (test/legacy wiring) — the
    business action still completes; only the audit record is skipped.
    Failures inside the audit pipeline are caught and logged so an audit
    bug never blocks the security-relevant action it's recording (e.g.,
    a revoke must clear local tokens even if the audit DB is down).
    """
    if audit_router is None:
        return
    try:
        from ruhu.audit.emitter import emit_audit_event

        emit_audit_event(
            audit_router,
            event_type=event_type,
            organization_id=organization_id,
            outcome=outcome,
            resource_type="oauth_connection",
            resource_id=connection_id,
            detail={"provider": provider, **(detail or {})},
        )
    except Exception as exc:
        log.warning(
            "oauth.audit_emit_failed event=%s connection=%s error=%s",
            event_type,
            connection_id,
            exc,
        )

# Fernet tokens are valid for this many seconds (10 minutes gives plenty of
# time to complete the browser consent flow).
_STATE_TTL_SECONDS = 600

# Refresh access tokens this many seconds before they expire.
_REFRESH_LEAD_SECONDS = 300  # 5 minutes

# Interval between refresh scans.
_REFRESH_POLL_SECONDS = 60

OAUTH_REFRESH_JOB_TYPE = "oauth_token_refresh.tick"

# HTTP timeout for token-endpoint calls.
_TOKEN_HTTP_TIMEOUT = 10.0

# ── Refresh-failure backoff curve ─────────────────────────────────────
#
# Exponential, deterministic. The window doubles per consecutive failure
# and is capped so a flapping provider doesn't get retried more than
# ~twice an hour. Success resets ``refresh_failure_count`` to 0 and the
# curve restarts.
#
# We keep the curve deterministic (no jitter) because the refresher
# already serializes attempts inside one tick (``for conn in
# connections: await self._refresh_one(conn)``); jitter would add
# implementation complexity without solving a real thundering-herd
# problem in this code path. If a single provider is shared by many
# connections, a future round of work can add per-provider stagger.
#
# ``invalid_grant`` failures don't traverse this curve — they jump
# straight to ``requires_reauth`` (excluded from the refresh scan), so
# transient errors are the only consumer of backoff.
_REFRESH_BACKOFF_BASE_SECONDS = 60
_REFRESH_BACKOFF_MAX_SECONDS = 1800  # 30 min — per-connection ceiling


def _refresh_backoff_seconds(failure_count: int) -> float:
    """Return the deterministic backoff window in seconds for
    *failure_count* consecutive failures.

    failure_count=0 → 0 (no backoff — fresh connections retry immediately).
    failure_count=1 → 60s
    failure_count=2 → 120s
    ...
    failure_count=N → min(60 * 2^(N-1), 1800)
    """
    if failure_count <= 0:
        return 0.0
    return float(
        min(
            _REFRESH_BACKOFF_BASE_SECONDS * (2 ** (failure_count - 1)),
            _REFRESH_BACKOFF_MAX_SECONDS,
        )
    )


# ── OAuthFlowManager ──────────────────────────────────────────────────────────


class OAuthFlowManager:
    """Builds authorization URLs and exchanges codes for tokens.

    Parameters
    ----------
    session_factory:
        SQLAlchemy session factory — used to read connections during
        code exchange and to write tokens after a successful exchange.
    cipher:
        ``CredentialCipher`` instance used to encrypt/sign the OAuth
        ``state`` parameter.  **Required** — the unsigned fallback has
        been removed because an attacker who discovers the callback URL
        could otherwise forge ``state`` to trick the server into
        persisting tokens on another tenant's connection.  Configure
        ``RUHU_TOOL_CREDENTIALS_ENCRYPTION_KEY`` before enabling OAuth.
    redirect_base_url:
        The base URL of the Ruhu backend (e.g. ``https://api.example.com``).
        The callback will be at ``{redirect_base_url}/api/tools/oauth/callback``.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        cipher: Any,  # Legacy CredentialCipher (Fernet-dict) — for OAuth state
        redirect_base_url: str,
        blob_cipher: BlobCipher | None = None,
        audit_router: Any | None = None,  # ``AuditEventRouter`` — avoid circular import
    ) -> None:
        """``blob_cipher`` is the phase-1 AEAD cipher used to populate
        ``oauth_token_ct`` on exchange_code.  Optional during rollout: when
        absent, only the legacy plaintext column is written and phase-2
        backfill encrypts later.

        ``audit_router`` is optional during construction because the router
        is itself constructed from ``app.state`` after the FastAPI app is
        built; use :py:meth:`set_audit_router` to inject it once available.
        Without a router, audit-emitting code paths still execute (no
        crash) but do not record events — this is the correct fallback so
        unit tests don't need to wire an audit pipeline.
        """
        if cipher is None:
            raise ValueError(
                "OAuthFlowManager requires a CredentialCipher. "
                "Set RUHU_TOOL_CREDENTIALS_ENCRYPTION_KEY and rebuild the app."
            )
        self._sf = session_factory
        self._cipher = cipher
        self._blob_cipher = blob_cipher
        self._redirect_base_url = redirect_base_url.rstrip("/")
        self._audit = audit_router

    def set_audit_router(self, audit_router: Any) -> None:
        """Inject the audit router after construction (mirrors the
        :py:class:`APIConnectionStore` pattern — wired from
        ``app.state.audit_router`` once the app finishes booting)."""
        self._audit = audit_router

    # ── Public API ────────────────────────────────────────────────────────────

    def build_authorization_url(
        self,
        *,
        connection_id: str,
        organization_id: str,
        provider: str,
        client_id: str,
        scopes: list[str] | None = None,
        auth_url_override: str | None = None,
        pkce_supported: bool | None = None,
    ) -> str:
        """Return the provider authorization URL the browser should navigate to.

        If ``auth_url_override`` is supplied (or stored on the connection),
        it is used as the authorization endpoint instead of the provider's
        default. This enables per-tenant URLs (Zendesk subdomains) and
        fully custom OAuth providers without per-provider backend code.

        PKCE (RFC 7636): when the provider supports it, a fresh
        ``code_verifier`` is generated, its ``code_challenge`` (S256) is
        appended to the authorization URL, and the verifier is sealed
        inside the encrypted ``state`` parameter so the callback can
        present it during token exchange. The ``pkce_supported`` argument
        defaults to the provider config; pass ``False`` explicitly for a
        custom OAuth server that doesn't accept the challenge.

        Raises ``KeyError`` if *provider* is not in ``OAUTH_PROVIDERS`` AND
        no ``auth_url_override`` is provided.
        """
        # Resolve URL: explicit override > connection-stored override > provider default
        if auth_url_override is None:
            auth_url_override = _load_connection_auth_url_override(
                self._sf, connection_id=connection_id, organization_id=organization_id
            )
        if provider in OAUTH_PROVIDERS:
            config = OAUTH_PROVIDERS[provider]
            authorization_url = auth_url_override or config.authorization_url
            scope_default = config.default_scopes
            extra_params = dict(config.extra_auth_params)
            provider_pkce = config.pkce_supported
        else:
            # Custom OAuth provider — no preset config, override is mandatory
            if not auth_url_override:
                raise KeyError(
                    f"unknown OAuth provider '{provider}' and no auth_url_override supplied"
                )
            authorization_url = auth_url_override
            scope_default = []
            extra_params = {}
            # Custom providers default to PKCE-on; operator can flip via
            # the ``pkce_supported`` arg if the legacy server rejects the
            # ``code_challenge`` parameter.
            provider_pkce = True

        use_pkce = provider_pkce if pkce_supported is None else bool(pkce_supported)
        code_verifier = _generate_pkce_verifier() if use_pkce else None

        # Resolve the final scope list and seal it inside the encrypted
        # state. The callback then knows what we ASKED for, so when the
        # token-exchange response comes back with a (possibly narrower)
        # GRANTED scope set, we can detect partial-consent up front
        # instead of waiting for tools to fail at runtime with cryptic
        # 403s. Some providers (Slack, GitHub) let users deselect
        # individual scopes on the consent screen; without this check
        # the platform never finds out until a tool is invoked.
        requested_scopes = list(scopes or scope_default)
        state_token = self._encode_state(
            connection_id=connection_id,
            organization_id=organization_id,
            provider=provider,
            code_verifier=code_verifier,
            requested_scopes=requested_scopes,
        )
        params: dict[str, str] = {
            "client_id": client_id,
            "redirect_uri": self._callback_url(),
            "response_type": "code",
            "scope": " ".join(requested_scopes),
            "state": state_token,
            **extra_params,
        }
        if code_verifier is not None:
            params["code_challenge"] = _pkce_challenge_from_verifier(code_verifier)
            params["code_challenge_method"] = "S256"
        return f"{authorization_url}?{urlencode(params)}"

    def decode_state(self, state_token: str) -> dict[str, Any]:
        """Decode and verify the ``state`` parameter from the provider callback.

        Returns the payload dict ``{connection_id, organization_id, provider, ...}``.
        Raises ``ValueError`` on tampered or expired tokens.

        TTL enforcement: Fernet checks the embedded timestamp against
        ``_STATE_TTL_SECONDS`` (10 minutes — long enough for an interactive
        consent flow, short enough to bound replay attacks). State that
        outlives this window is rejected with ``ValueError``.
        """
        try:
            payload = self._cipher.decrypt(state_token, ttl=_STATE_TTL_SECONDS)
        except Exception as exc:
            raise ValueError(f"invalid or expired OAuth state token: {exc}") from exc
        return payload  # type: ignore[return-value]

    async def exchange_code(
        self,
        *,
        connection_id: str,
        organization_id: str,
        provider: str,
        code: str,
        client_id: str,
        client_secret: str,
        code_verifier: str | None = None,
        requested_scopes: list[str] | None = None,
    ) -> None:
        """Exchange *code* for tokens and persist them on the connection record.

        Reads the per-connection ``token_url_override`` (if set) and uses it
        instead of the provider default. Enables per-tenant OAuth URLs.

        Pass ``code_verifier`` when the authorization request used PKCE; it
        is sent verbatim to the token endpoint per RFC 7636.

        ``requested_scopes`` is the list we asked for in the authorization
        URL. When supplied, ``_persist_tokens`` records it alongside the
        granted scope (RFC 6749 §5.1 ``scope`` field) so the platform can
        detect partial-consent ("user deselected scope X on the consent
        screen") and surface a Reconnect prompt before tools fail at
        runtime. Typically passed through from the encrypted state.

        Updates ``oauth_token_json``, ``token_expires_at``, and sets
        ``status = "active"``.  Marks ``status = "error"`` on failure.
        """
        config = OAUTH_PROVIDERS.get(provider)
        token_url_override = _load_connection_token_url_override(
            self._sf, connection_id=connection_id, organization_id=organization_id
        )
        extra_params: dict[str, str] = {
            "code": code,
            "redirect_uri": self._callback_url(),
        }
        if code_verifier:
            extra_params["code_verifier"] = code_verifier
        try:
            token_data = await _fetch_token(
                config=config,
                grant_type="authorization_code",
                client_id=client_id,
                client_secret=client_secret,
                extra_params=extra_params,
                token_url_override=token_url_override,
            )
        except Exception as exc:
            log.error(
                "oauth.exchange_code.failed connection_id=%s provider=%s error=%s",
                connection_id,
                provider,
                exc,
            )
            _mark_connection_error(
                self._sf,
                connection_id=connection_id,
                organization_id=organization_id,
                error=f"token exchange failed: {exc}",
            )
            raise

        _persist_tokens(
            self._sf,
            connection_id=connection_id,
            organization_id=organization_id,
            token_data=token_data,
            blob_cipher=self._blob_cipher,
            requested_scopes=requested_scopes,
        )
        log.info(
            "oauth.exchange_code.ok connection_id=%s provider=%s",
            connection_id,
            provider,
        )
        from ruhu.audit.events import AUTH_OAUTH_CONNECTION_AUTHORIZED

        _emit_oauth_audit(
            self._audit,
            event_type=AUTH_OAUTH_CONNECTION_AUTHORIZED,
            organization_id=organization_id,
            connection_id=connection_id,
            provider=provider,
        )

    async def revoke_connection(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        """Revoke the OAuth tokens for *connection_id* and clear local state.

        Two-step operation:

        1. **Provider notification** (best-effort) — POSTs the access token
           (and refresh token, if present) to the provider's RFC 7009
           revoke endpoint, when ``OAuthProviderConfig.revoke_url`` is
           configured. Failures here are logged but do NOT block step 2:
           an attacker who can steal a token cannot also stop us from
           clearing it locally, and providers regularly invalidate tokens
           outside our control (admin revoke, password reset, etc.).
        2. **Local cleanup** — clears ``oauth_token_json`` /
           ``oauth_token_ct``, sets ``status = "revoked"``,
           ``token_expires_at = None``. The connection record itself is
           kept so audit history, tool definitions, and agent bindings
           survive reconnect.

        Returns ``{"provider_revoke_attempted": bool, "provider_revoke_ok": bool}``
        so callers can surface whether the provider was notified.
        """
        provider_revoke_attempted = False
        provider_revoke_ok = False
        token_data: dict[str, Any] = {}

        with self._sf() as session:
            record = session.scalar(
                select(APIConnectionRecord).where(
                    APIConnectionRecord.connection_id == connection_id,
                    APIConnectionRecord.organization_id == organization_id,
                )
            )
            if record is None:
                raise ValueError(f"connection {connection_id!r} not found")
            provider = record.provider
            token_data = dict(record.oauth_token_json or {})

        config = OAUTH_PROVIDERS.get(provider)
        revoke_url = config.revoke_url if config else None
        if revoke_url and token_data:
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            for token in (access_token, refresh_token):
                if not isinstance(token, str) or not token:
                    continue
                provider_revoke_attempted = True
                try:
                    await _post_revoke_request(revoke_url=revoke_url, token=token)
                    provider_revoke_ok = True
                except Exception as exc:
                    log.warning(
                        "oauth.revoke_connection: provider revoke failed for %s: %s",
                        connection_id,
                        exc,
                    )

        _mark_connection_revoked(
            self._sf,
            connection_id=connection_id,
            organization_id=organization_id,
        )
        log.info(
            "oauth.revoke_connection.ok connection_id=%s provider=%s provider_revoke_ok=%s",
            connection_id,
            provider,
            provider_revoke_ok,
        )
        from ruhu.audit.events import AUTH_OAUTH_CONNECTION_REVOKED

        _emit_oauth_audit(
            self._audit,
            event_type=AUTH_OAUTH_CONNECTION_REVOKED,
            organization_id=organization_id,
            connection_id=connection_id,
            provider=provider,
            detail={
                "provider_revoke_attempted": provider_revoke_attempted,
                "provider_revoke_ok": provider_revoke_ok,
            },
        )
        return {
            "provider_revoke_attempted": provider_revoke_attempted,
            "provider_revoke_ok": provider_revoke_ok,
        }

    def force_refresh_sync(
        self,
        *,
        connection_id: str,
        organization_id: str,
        get_credentials: Any | None = None,
    ) -> dict[str, Any] | None:
        """Synchronously force a refresh of the connection's OAuth tokens.

        Used by the HTTP executor's 401-retry path: when an outbound tool
        call returns 401, the executor (running in a worker thread)
        calls this to rotate the token, then retries once with the new
        bearer.

        Returns the new token payload on success, or None when the
        refresh failed (provider rejected, credentials missing, etc.).
        Caller is expected to surface the failure as a tool error rather
        than retry — the user must reconnect.

        ``get_credentials`` is the same callable the background refresher
        uses (``(provider) -> (client_id, client_secret) | None``). When
        not supplied, only per-connection credential overrides are used.

        Implementation note: builds a one-off ``httpx.Client`` (sync) so
        this method works from any thread without an event loop. Uses
        ``_persist_tokens`` for the write, so all the same audit /
        backoff-reset semantics apply.
        """
        with self._sf() as session:
            record = session.scalar(
                select(APIConnectionRecord).where(
                    APIConnectionRecord.connection_id == connection_id,
                    APIConnectionRecord.organization_id == organization_id,
                )
            )
            if record is None:
                log.warning(
                    "oauth.force_refresh_sync: connection %s not found", connection_id
                )
                return None
            provider = record.provider
            token_data = dict(record.oauth_token_json or {})
            refresh_token = token_data.get("refresh_token")
            token_url_override = record.token_url_override
            client_id_override = record.oauth_client_id_override
            client_secret_enc = record.oauth_client_secret_enc

        if not refresh_token:
            log.warning(
                "oauth.force_refresh_sync: connection %s has no refresh_token",
                connection_id,
            )
            return None

        # Resolve client credentials. Per-connection override > caller-supplied resolver.
        creds: tuple[str, str] | None = None
        if client_id_override and client_secret_enc and self._cipher is not None:
            try:
                payload = self._cipher.decrypt(client_secret_enc)
                secret = payload.get("client_secret") if isinstance(payload, dict) else None
                if secret:
                    creds = (client_id_override, str(secret))
            except Exception:
                log.warning(
                    "oauth.force_refresh_sync: per-connection secret decrypt failed for %s",
                    connection_id,
                )
        if creds is None and get_credentials is not None:
            creds = get_credentials(provider)
        if creds is None:
            log.warning(
                "oauth.force_refresh_sync: no credentials available for provider %s",
                provider,
            )
            return None

        config = OAUTH_PROVIDERS.get(provider)
        if config is None and not token_url_override:
            log.warning(
                "oauth.force_refresh_sync: unknown provider %s and no token_url_override",
                provider,
            )
            return None

        client_id, client_secret = creds
        try:
            new_token_data = _fetch_token_sync(
                config=config,
                grant_type="refresh_token",
                client_id=client_id,
                client_secret=client_secret,
                extra_params={"refresh_token": refresh_token},
                token_url_override=token_url_override,
            )
        except Exception as exc:
            log.error(
                "oauth.force_refresh_sync: refresh failed for %s: %s",
                connection_id,
                exc,
            )
            # Don't mark the connection requires_reauth here — the
            # background refresher will catch and classify on its next
            # tick. Force-refresh is a best-effort hot-path operation;
            # we don't want to flip status mid-tool-call on a transient
            # failure.
            return None

        # Preserve existing refresh_token if provider didn't reissue one.
        if not new_token_data.get("refresh_token"):
            new_token_data["refresh_token"] = refresh_token

        _persist_tokens(
            self._sf,
            connection_id=connection_id,
            organization_id=organization_id,
            token_data=new_token_data,
            blob_cipher=self._blob_cipher,
        )
        log.info(
            "oauth.force_refresh_sync.ok connection_id=%s provider=%s",
            connection_id,
            provider,
        )
        return new_token_data

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _callback_url(self) -> str:
        # Redirect goes to the frontend OAuth callback page, which sends the
        # code back to the parent window via postMessage.  The parent then calls
        # POST /api/tools/oauth/exchange to complete the token exchange.
        return f"{self._redirect_base_url}/integrations/oauth/callback"

    def _encode_state(
        self,
        *,
        connection_id: str,
        organization_id: str,
        provider: str,
        code_verifier: str | None = None,
        requested_scopes: list[str] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "connection_id": connection_id,
            "organization_id": organization_id,
            "provider": provider,
        }
        if code_verifier is not None:
            # Sealed inside the Fernet-encrypted state so it never appears
            # in the browser URL or browser history; only the server holds
            # the key to recover it on callback.
            payload["code_verifier"] = code_verifier
        if requested_scopes:
            # Carried back to the callback so we can compare what we
            # asked for against what the provider granted (RFC 6749 §3.3
            # / §5.1). The browser doesn't see this — the URL still
            # carries the same scopes in the ``scope`` param, but the
            # state payload is the canonical record because providers
            # may normalise / collapse scopes in the URL parsing.
            payload["requested_scopes"] = list(requested_scopes)
        # Cipher is required (enforced in __init__); no unsigned fallback.
        return self._cipher.encrypt(payload)


# ── OAuthTokenRefresher ───────────────────────────────────────────────────────


class OAuthTokenRefresher:
    """Refreshes expiring OAuth access tokens.

    Scans the ``api_connections`` table for ``oauth2`` connections whose
    ``token_expires_at`` falls within ``refresh_lead_seconds`` of now.
    Runs as the ``oauth_token_refresh.tick`` recurring job on the unified
    jobs runtime (registered in ``ruhu.worker``).

    Parameters
    ----------
    session_factory:
        SQLAlchemy session factory scoped to the runtime DB.
    get_credentials:
        Callable ``(provider: str) -> (client_id, client_secret) | None``.
        Return None to skip providers whose credentials are not configured.
    refresh_lead_seconds:
        Refresh tokens this many seconds before they expire (default 300 s).
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        get_credentials: Any,  # Callable[[str], tuple[str, str] | None]
        cipher: Any = None,  # Legacy CredentialCipher (Fernet-dict) — per-connection secrets
        blob_cipher: BlobCipher | None = None,
        refresh_lead_seconds: float = _REFRESH_LEAD_SECONDS,
        audit_router: Any | None = None,  # ``AuditEventRouter`` — avoid circular import
    ) -> None:
        """``blob_cipher`` is the phase-1 AEAD cipher used to dual-write
        ``oauth_token_ct`` after every refresh.  Optional during rollout.
        ``audit_router`` records the security-relevant transition when a
        refresh fails with ``invalid_grant`` (provider-revoked refresh
        token) — successful refreshes are intentionally NOT audited to
        avoid drowning the log in routine machine activity."""
        self._sf = session_factory
        self._get_credentials = get_credentials
        self._cipher = cipher
        self._blob_cipher = blob_cipher
        self._lead = refresh_lead_seconds
        self._audit = audit_router

    def set_audit_router(self, audit_router: Any) -> None:
        self._audit = audit_router

    async def refresh_expiring_once(self) -> None:
        cutoff = datetime.now(timezone.utc) + timedelta(seconds=self._lead)
        with self._sf() as session:
            # ``requires_reauth`` is excluded: those connections need
            # interactive user action (reconsent) and re-trying the refresh
            # endpoint will keep failing until the user reconnects.
            connections = session.scalars(
                select(APIConnectionRecord).where(
                    APIConnectionRecord.auth_type == "oauth2",
                    APIConnectionRecord.status.in_(["active", "error"]),
                    APIConnectionRecord.token_expires_at.isnot(None),
                    APIConnectionRecord.token_expires_at <= cutoff,
                )
            ).all()

        if not connections:
            return

        log.info("oauth_token_refresher: refreshing %d connections", len(connections))
        for conn in connections:
            await self._refresh_one(conn)

    async def _refresh_one(self, conn: APIConnectionRecord) -> None:
        provider = conn.provider
        # Backoff guard: skip if a prior failure's cooldown has not yet
        # elapsed. Preserves the connection's failure_count untouched
        # (no attempt = no new datapoint) so the curve advances only on
        # actual attempts.
        if _is_within_refresh_backoff_window(conn, now=datetime.now(timezone.utc)):
            log.debug(
                "oauth_token_refresher: skipping %s — within backoff window (failures=%d)",
                conn.connection_id,
                conn.refresh_failure_count,
            )
            return

        # Per-connection credentials take precedence over platform defaults.
        # Required for providers (Zendesk, custom OAuth) where each customer
        # registers their own OAuth client.
        creds: tuple[str, str] | None = None
        if conn.oauth_client_id_override and conn.oauth_client_secret_enc and self._cipher is not None:
            try:
                payload = self._cipher.decrypt(conn.oauth_client_secret_enc)
                secret = payload.get("client_secret") if isinstance(payload, dict) else None
                if secret:
                    creds = (conn.oauth_client_id_override, str(secret))
            except Exception:
                log.warning(
                    "oauth_token_refresher: failed to decrypt per-connection secret for %s",
                    conn.connection_id,
                )

        if creds is None:
            creds = self._get_credentials(provider)

        if creds is None:
            log.debug(
                "oauth_token_refresher: skipping %s — no credentials for provider %s",
                conn.connection_id,
                provider,
            )
            return

        client_id, client_secret = creds
        config = OAUTH_PROVIDERS.get(provider)
        # Allow refresh for providers without a preset config if the
        # connection has a token_url_override set.
        if config is None and not conn.token_url_override:
            log.warning(
                "oauth_token_refresher: unknown provider %s and no token_url_override for %s",
                provider,
                conn.connection_id,
            )
            return

        token_data = dict(conn.oauth_token_json or {})
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            log.warning(
                "oauth_token_refresher: connection %s has no refresh_token — marking error",
                conn.connection_id,
            )
            _mark_connection_error(
                self._sf,
                connection_id=conn.connection_id,
                organization_id=conn.organization_id,
                error="no refresh_token in oauth_token_json",
            )
            return

        try:
            new_token_data = await _fetch_token(
                config=config,
                grant_type="refresh_token",
                client_id=client_id,
                client_secret=client_secret,
                extra_params={"refresh_token": refresh_token},
                token_url_override=conn.token_url_override,
            )
        except Exception as exc:
            log.error(
                "oauth_token_refresher: refresh failed for %s: %s",
                conn.connection_id,
                exc,
            )
            # RFC 6749 §5.2: ``invalid_grant`` from the token endpoint means
            # the refresh token has been revoked, expired, or is otherwise
            # unusable — the user must reconsent. Surface that as a
            # distinct status so the UI can show a "Reconnect" affordance
            # and the refresher stops hammering the dead connection.
            if _is_invalid_grant_error(exc):
                _mark_connection_requires_reauth(
                    self._sf,
                    connection_id=conn.connection_id,
                    organization_id=conn.organization_id,
                    error=f"refresh token rejected by provider: {exc}",
                )
                from ruhu.audit.events import (
                    AUTH_OAUTH_CONNECTION_REQUIRES_REAUTH,
                )

                _emit_oauth_audit(
                    self._audit,
                    event_type=AUTH_OAUTH_CONNECTION_REQUIRES_REAUTH,
                    organization_id=conn.organization_id,
                    connection_id=conn.connection_id,
                    provider=provider,
                    outcome="failure",
                    detail={"error_kind": "invalid_grant"},
                )
            else:
                _record_refresh_failure(
                    self._sf,
                    connection_id=conn.connection_id,
                    organization_id=conn.organization_id,
                    error=f"token refresh failed: {exc}",
                )
            return

        # Some providers (HubSpot) issue a new refresh token on refresh.
        # Google reuses the existing one — preserve it if the response omits it.
        if not new_token_data.get("refresh_token"):
            new_token_data["refresh_token"] = refresh_token

        _persist_tokens(
            self._sf,
            connection_id=conn.connection_id,
            organization_id=conn.organization_id,
            token_data=new_token_data,
            blob_cipher=self._blob_cipher,
        )
        log.info(
            "oauth_token_refresher: refreshed %s (%s)",
            conn.connection_id,
            provider,
        )


# ── Shared helpers ────────────────────────────────────────────────────────────


async def _fetch_token(
    *,
    config: OAuthProviderConfig | None,
    grant_type: str,
    client_id: str,
    client_secret: str,
    extra_params: dict[str, str],
    token_url_override: str | None = None,
) -> dict[str, Any]:
    """POST to the provider token endpoint and return the parsed JSON response.

    If ``token_url_override`` is supplied, it is used instead of
    ``config.token_url``. ``config`` may be None when the provider is fully
    custom (no preset OAuthProviderConfig) — in that case override and
    ``token_url_override`` is required.

    Raises ``httpx.HTTPStatusError`` or ``ValueError`` on failure.
    """
    token_url = token_url_override or (config.token_url if config else None)
    if not token_url:
        raise ValueError("token_url_override or config.token_url required")
    token_auth = config.token_auth if config else "post"

    body: dict[str, str] = {
        "grant_type": grant_type,
        **extra_params,
    }
    headers = {"Accept": "application/json"}
    auth: httpx.BasicAuth | None = None

    if token_auth == "basic":
        auth = httpx.BasicAuth(client_id, client_secret)
    else:
        body["client_id"] = client_id
        body["client_secret"] = client_secret

    async with httpx.AsyncClient(timeout=_TOKEN_HTTP_TIMEOUT) as client:
        response = await client.post(
            token_url,
            data=body,
            headers=headers,
            auth=auth,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise ValueError(
                f"token endpoint returned {exc.response.status_code}: {detail}"
            ) from exc
        data: dict[str, Any] = response.json()

    if "access_token" not in data:
        raise ValueError(f"token response missing access_token: {data!r}")
    return data


def _fetch_token_sync(
    *,
    config: OAuthProviderConfig | None,
    grant_type: str,
    client_id: str,
    client_secret: str,
    extra_params: dict[str, str],
    token_url_override: str | None = None,
) -> dict[str, Any]:
    """Synchronous twin of :py:func:`_fetch_token`.

    Used by :py:meth:`OAuthFlowManager.force_refresh_sync` from worker
    threads (HTTP executor's 401-retry path) where running an asyncio
    event loop is awkward and creating one per refresh is wasteful.
    Same body shape, same error semantics — just ``httpx.Client``
    instead of ``httpx.AsyncClient``.
    """
    token_url = token_url_override or (config.token_url if config else None)
    if not token_url:
        raise ValueError("token_url_override or config.token_url required")
    token_auth = config.token_auth if config else "post"

    body: dict[str, str] = {"grant_type": grant_type, **extra_params}
    headers = {"Accept": "application/json"}
    auth: httpx.BasicAuth | None = None

    if token_auth == "basic":
        auth = httpx.BasicAuth(client_id, client_secret)
    else:
        body["client_id"] = client_id
        body["client_secret"] = client_secret

    with httpx.Client(timeout=_TOKEN_HTTP_TIMEOUT) as client:
        response = client.post(token_url, data=body, headers=headers, auth=auth)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise ValueError(
                f"token endpoint returned {exc.response.status_code}: {detail}"
            ) from exc
        data: dict[str, Any] = response.json()

    if "access_token" not in data:
        raise ValueError(f"token response missing access_token: {data!r}")
    return data


def _load_connection_auth_url_override(
    session_factory: sessionmaker[Session],
    *,
    connection_id: str,
    organization_id: str,
) -> str | None:
    """Return the connection's auth_url_override if set, else None."""
    with session_factory() as session:
        value = session.scalar(
            select(APIConnectionRecord.auth_url_override).where(
                APIConnectionRecord.connection_id == connection_id,
                APIConnectionRecord.organization_id == organization_id,
            )
        )
        return value if value else None


def _load_connection_token_url_override(
    session_factory: sessionmaker[Session],
    *,
    connection_id: str,
    organization_id: str,
) -> str | None:
    """Return the connection's token_url_override if set, else None."""
    with session_factory() as session:
        value = session.scalar(
            select(APIConnectionRecord.token_url_override).where(
                APIConnectionRecord.connection_id == connection_id,
                APIConnectionRecord.organization_id == organization_id,
            )
        )
        return value if value else None


def _persist_tokens(
    session_factory: sessionmaker[Session],
    *,
    connection_id: str,
    organization_id: str,
    token_data: dict[str, Any],
    blob_cipher: BlobCipher | None = None,
    requested_scopes: list[str] | None = None,
) -> None:
    """Write new token data to the DB and update token_expires_at.

    For providers that return an instance/subdomain URL in the token
    response (e.g., Salesforce returns ``instance_url``), substitute
    matching placeholders in the connection's base_url so subsequent
    HTTP calls can resolve the correct host.

    When ``blob_cipher`` is provided, ``oauth_token_ct`` is also written
    (phase-1 dual-write — keeps the encrypted column in sync with refreshes
    and initial exchanges).  Without a cipher only the legacy plaintext
    column is written, and the phase-2 backfill script picks up the
    difference.

    When ``requested_scopes`` is supplied, the token JSON gets two extra
    keys: ``_requested_scopes`` (list[str]) and ``_scope_status``
    (one of ``"complete"`` / ``"partial"`` / ``"unknown"``). On refresh
    paths where ``requested_scopes`` is None the existing values from
    the record are preserved — the platform never forgets what was
    originally consented to even if a refresh response narrows scope
    or omits it entirely.
    """
    now = datetime.now(timezone.utc)
    expires_in = token_data.get("expires_in")
    token_expires_at: datetime | None = None
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        token_expires_at = now + timedelta(seconds=int(expires_in))

    # Resolve effective requested_scopes BEFORE encryption: the refresh
    # path (which doesn't carry a fresh requested_scopes) must inherit
    # the value persisted by the original exchange so a single refresh
    # never causes the platform to forget what the user consented to.
    effective_requested: list[str] | None
    if requested_scopes is not None:
        effective_requested = list(requested_scopes)
    else:
        with session_factory() as session:
            prior_payload = session.scalar(
                select(APIConnectionRecord.oauth_token_json).where(
                    APIConnectionRecord.connection_id == connection_id,
                    APIConnectionRecord.organization_id == organization_id,
                )
            )
        prior_requested = (
            prior_payload.get("_requested_scopes")
            if isinstance(prior_payload, dict)
            else None
        )
        effective_requested = (
            [str(s) for s in prior_requested]
            if isinstance(prior_requested, list)
            else None
        )

    augmented_token = dict(token_data)
    if effective_requested is not None:
        granted_scopes = _parse_granted_scopes(token_data)
        scope_status, missing = compute_scope_status(
            requested=effective_requested, granted=granted_scopes
        )
        augmented_token["_requested_scopes"] = list(effective_requested)
        augmented_token["_scope_status"] = scope_status
        if scope_status == "partial":
            log.warning(
                "oauth.partial_scope connection_id=%s missing=%s",
                connection_id,
                sorted(missing),
            )

    # Encrypt outside the transaction so crypto errors don't abort the write
    # half-way through; if blob_cipher is None this is a no-op.
    encrypted_token: bytes | None = None
    if blob_cipher is not None:
        plaintext = json.dumps(
            augmented_token, separators=(",", ":"), sort_keys=True
        ).encode()
        encrypted_token = blob_cipher.encrypt(
            plaintext,
            aad=build_aad(
                organization_id=organization_id, connection_id=connection_id
            ),
        )

    with session_factory.begin() as session:
        record = session.scalar(
            select(APIConnectionRecord).where(
                APIConnectionRecord.connection_id == connection_id,
                APIConnectionRecord.organization_id == organization_id,
            )
        )
        if record is None:
            log.error(
                "oauth._persist_tokens: connection %s not found", connection_id
            )
            return
        record.oauth_token_json = dict(augmented_token)
        if encrypted_token is not None:
            record.oauth_token_ct = encrypted_token
        record.token_expires_at = token_expires_at
        record.status = "active"
        record.error_message = None
        record.updated_at = now
        # Successful write of fresh tokens means the connection is healthy:
        # reset the backoff curve so the next failure (if any) starts at
        # the bottom rather than carrying scars from a prior outage.
        record.refresh_failure_count = 0
        record.last_refresh_attempt_at = now

        # Provider-specific URL substitution. Salesforce returns
        # `instance_url` (e.g., https://acme.my.salesforce.com) which
        # must replace the {instance} placeholder in base_url.
        instance_url = token_data.get("instance_url")
        if isinstance(instance_url, str) and instance_url and record.base_url:
            if "{instance}" in record.base_url:
                # base_url like "https://{instance}.salesforce.com/services/data/v59.0"
                # becomes "https://acme.my.salesforce.com/services/data/v59.0"
                # Strip scheme from instance_url to get just the host.
                host = instance_url.replace("https://", "").replace("http://", "").rstrip("/")
                # Replace the full "https://{instance}.salesforce.com" pattern with instance_url
                # to handle the full host correctly.
                record.base_url = record.base_url.replace(
                    "https://{instance}.salesforce.com",
                    instance_url.rstrip("/"),
                ).replace("{instance}", host.split(".")[0])


def _mark_connection_error(
    session_factory: sessionmaker[Session],
    *,
    connection_id: str,
    organization_id: str,
    error: str,
) -> None:
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        record = session.scalar(
            select(APIConnectionRecord).where(
                APIConnectionRecord.connection_id == connection_id,
                APIConnectionRecord.organization_id == organization_id,
            )
        )
        if record is None:
            return
        record.status = "error"
        record.error_message = error[:1024]
        record.updated_at = now


def _record_refresh_failure(
    session_factory: sessionmaker[Session],
    *,
    connection_id: str,
    organization_id: str,
    error: str,
) -> None:
    """Mark *connection_id* as failed to refresh and advance the backoff curve.

    Sets ``status="error"``, increments ``refresh_failure_count``, and
    stamps ``last_refresh_attempt_at`` — the (count, last_attempt) pair
    is what :py:func:`_is_within_refresh_backoff_window` consults to
    decide whether the next scan should retry or skip.

    Distinct from :py:func:`_mark_connection_error` (which is used for
    initial-exchange failures, where the backoff machinery is
    irrelevant — there's no refresh_token yet).
    """
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        record = session.scalar(
            select(APIConnectionRecord).where(
                APIConnectionRecord.connection_id == connection_id,
                APIConnectionRecord.organization_id == organization_id,
            )
        )
        if record is None:
            return
        record.status = "error"
        record.error_message = error[:1024]
        record.refresh_failure_count = (record.refresh_failure_count or 0) + 1
        record.last_refresh_attempt_at = now
        record.updated_at = now


def _is_within_refresh_backoff_window(
    conn: APIConnectionRecord, *, now: datetime
) -> bool:
    """Return True when *conn* is still in cooldown from a prior failure.

    A connection with ``refresh_failure_count == 0`` (no prior failures
    or recently reset by a successful refresh) is never in backoff.
    Otherwise the deterministic curve from :py:func:`_refresh_backoff_seconds`
    is added to ``last_refresh_attempt_at`` to compute the earliest
    eligible-retry time; ``now`` before that = skip.
    """
    if not conn.refresh_failure_count or conn.last_refresh_attempt_at is None:
        return False
    cooldown = _refresh_backoff_seconds(conn.refresh_failure_count)
    if cooldown <= 0:
        return False
    eligible_at = conn.last_refresh_attempt_at + timedelta(seconds=cooldown)
    return now < eligible_at


def _mark_connection_requires_reauth(
    session_factory: sessionmaker[Session],
    *,
    connection_id: str,
    organization_id: str,
    error: str,
) -> None:
    """Mark a connection as needing user reconsent.

    Used when the provider rejects the refresh token (``invalid_grant``).
    The refresh-loop excludes this status from its scan, so the connection
    sits idle until the user re-runs the OAuth start flow on the same
    ``connection_id``, which overwrites tokens via ``_persist_tokens`` and
    flips the status back to ``"active"``.
    """
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        record = session.scalar(
            select(APIConnectionRecord).where(
                APIConnectionRecord.connection_id == connection_id,
                APIConnectionRecord.organization_id == organization_id,
            )
        )
        if record is None:
            return
        record.status = "requires_reauth"
        record.error_message = error[:1024]
        record.updated_at = now


def _mark_connection_revoked(
    session_factory: sessionmaker[Session],
    *,
    connection_id: str,
    organization_id: str,
) -> None:
    """Clear OAuth tokens locally and mark the connection ``"revoked"``.

    The record itself is preserved so tool definitions, agent bindings
    and audit trails survive a future reconnect on the same connection_id.
    Both the legacy plaintext column and the AEAD ciphertext column are
    cleared to ensure no stale token material survives.
    """
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        record = session.scalar(
            select(APIConnectionRecord).where(
                APIConnectionRecord.connection_id == connection_id,
                APIConnectionRecord.organization_id == organization_id,
            )
        )
        if record is None:
            return
        record.status = "revoked"
        record.error_message = None
        record.oauth_token_json = None
        record.oauth_token_ct = None
        record.token_expires_at = None
        record.updated_at = now


async def _post_revoke_request(*, revoke_url: str, token: str) -> None:
    """POST a single token to the provider's RFC 7009 revoke endpoint.

    The spec requires servers to respond 200 for both successful revoke
    and unrecognised tokens (so an attacker can't probe for valid
    tokens). Any other status raises so the caller can log it.
    """
    async with httpx.AsyncClient(timeout=_TOKEN_HTTP_TIMEOUT) as client:
        response = await client.post(
            revoke_url,
            data={"token": token},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()


def _parse_granted_scopes(token_data: dict[str, Any]) -> set[str]:
    """Extract granted scopes from a token-endpoint response.

    Per RFC 6749 §3.3 / §5.1, ``scope`` is a space-delimited string in
    the token response. Some providers return it as a list directly
    (Microsoft Graph in some flows); we normalise both shapes to a set.
    Returns the empty set when ``scope`` is absent — callers should
    treat that as "unknown grant" rather than "nothing granted".
    """
    raw = token_data.get("scope")
    if isinstance(raw, str):
        return {part for part in raw.split() if part}
    if isinstance(raw, list):
        return {str(part) for part in raw if part}
    return set()


def compute_scope_status(
    *, requested: list[str], granted: set[str]
) -> tuple[str, set[str]]:
    """Classify the relationship between requested and granted scopes.

    Returns a ``(status, missing)`` pair where ``status`` is one of:

    * ``"complete"`` — every requested scope is in the granted set.
    * ``"partial"``  — some requested scopes are missing (user
      deselected on the consent screen, or provider narrowed).
    * ``"unknown"``  — the provider didn't return a ``scope`` field, so
      we have no signal to compare. Don't flip to ``"partial"`` on
      empty ``granted`` because some providers (Salesforce, when scope
      is in instance-defaults) intentionally omit the field.

    ``missing`` is always a set of strings — empty when status is
    ``"complete"`` or ``"unknown"``.
    """
    requested_set = {s for s in requested if s}
    if not requested_set:
        return "complete", set()
    if not granted:
        return "unknown", set()
    missing = requested_set - granted
    if missing:
        return "partial", missing
    return "complete", set()


def _is_invalid_grant_error(exc: Exception) -> bool:
    """Detect RFC 6749 §5.2 ``invalid_grant`` in a token-endpoint failure.

    ``_fetch_token`` raises ``ValueError`` with the response body text
    appended (``"token endpoint returned 400: <body>"``). Providers return
    the canonical ``error`` field in JSON or form-encoded format; both
    contain the literal string ``invalid_grant``, so a substring match is
    correct and avoids fragile JSON parsing of partial bodies.

    Edge case: the substring also appears in error_description text like
    ``"the grant is invalid"`` — that text is benign here because the
    provider only emits it alongside the canonical code.
    """
    return "invalid_grant" in str(exc)


# ── PKCE (RFC 7636) helpers ──────────────────────────────────────────


# RFC 7636 §4.1: code_verifier is 43–128 chars from the unreserved set
# [A-Z][a-z][0-9]-._~. We use ``secrets.token_urlsafe(64)`` which gives
# 86 url-safe-base64 chars from 64 random bytes — comfortably inside the
# allowed range and well above the recommended 256-bit entropy bar.
_PKCE_VERIFIER_BYTES = 64


def _generate_pkce_verifier() -> str:
    """Generate a fresh ``code_verifier`` per RFC 7636.

    ``token_urlsafe(64)`` returns ``[A-Za-z0-9_-]+`` which is a strict
    subset of the unreserved-character set the RFC permits. No ``=``
    padding is emitted.
    """
    return secrets.token_urlsafe(_PKCE_VERIFIER_BYTES)


def _pkce_challenge_from_verifier(verifier: str) -> str:
    """Compute the S256 ``code_challenge`` for a given verifier.

    ``base64url(sha256(verifier))`` per RFC 7636 §4.2, with the trailing
    ``=`` padding stripped (``base64.urlsafe_b64encode`` always pads).
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
