"""OAuth 2.0 provider configurations for tool connections.

Each entry in OAUTH_PROVIDERS maps a provider slug (stored in
``APIConnectionRecord.provider``) to its OAuth endpoints, default scopes,
and token-endpoint authentication style.

Adding a new provider
---------------------
1. Instantiate an ``OAuthProviderConfig`` with the provider's auth/token URLs.
2. Add it to ``OAUTH_PROVIDERS`` under a stable slug.
3. Pass ``client_id`` / ``client_secret`` via ``RuntimeSettings`` and expose
   them through the ``get_client_credentials()`` helper below.

Supported token_auth styles
---------------------------
- ``"post"``  — client_id + client_secret sent in the POST body (default).
- ``"basic"`` — client_id + client_secret sent as HTTP Basic Auth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ruhu.runtime_config import RuntimeSettings


@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    """Static configuration for a single OAuth 2.0 provider."""

    authorization_url: str
    token_url: str
    default_scopes: list[str]
    # Authentication style used when calling the token endpoint.
    token_auth: Literal["post", "basic"] = "post"
    # Extra query params appended to the authorization URL.
    extra_auth_params: dict[str, str] = field(default_factory=dict)
    # Human-readable label used in UI messages and logs.
    display_name: str = ""
    # Default API base URL for tool definitions using this provider.
    api_base_url: str = ""
    # Whether this provider supports PKCE (RFC 7636 / OAuth 2.1 best
    # practice). Defaults to True because every OAuth 2.0 server in this
    # registry (HubSpot, Google, Microsoft, Salesforce, Zendesk) supports
    # it per their public documentation. Custom or legacy providers that
    # don't accept ``code_challenge`` should set this to False; the auth
    # URL omits the challenge and the token-exchange omits the verifier.
    pkce_supported: bool = True
    # RFC 7009 token revocation endpoint. ``None`` means the provider
    # does not expose a standard revoke endpoint; in that case the local
    # connection is still cleared but no provider-side notification is
    # made. Operators should document the manual revoke path (typically
    # the user's app-permissions page on the provider's website).
    revoke_url: str | None = None


# ── Provider registry ─────────────────────────────────────────────────────────

OAUTH_PROVIDERS: dict[str, OAuthProviderConfig] = {
    "hubspot": OAuthProviderConfig(
        display_name="HubSpot",
        authorization_url="https://app.hubspot.com/oauth/authorize",
        token_url="https://api.hubapi.com/oauth/v1/token",
        default_scopes=[
            "crm.objects.contacts.read",
            "crm.objects.contacts.write",
            "crm.objects.deals.read",
            "crm.objects.companies.read",
        ],
        token_auth="post",
        api_base_url="https://api.hubapi.com",
    ),
    "google_calendar": OAuthProviderConfig(
        display_name="Google Calendar",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        default_scopes=[
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events",
        ],
        token_auth="post",
        extra_auth_params={
            "access_type": "offline",
            "prompt": "consent",  # force refresh_token on every consent
        },
        api_base_url="https://www.googleapis.com/calendar/v3",
        revoke_url="https://oauth2.googleapis.com/revoke",
    ),
    "microsoft_calendar": OAuthProviderConfig(
        display_name="Microsoft Calendar",
        # Microsoft identity platform v2.0 endpoints. `common` allows both
        # work/school (AAD) and personal (MSA) accounts. Customers with a
        # specific tenant can override to https://login.microsoftonline.com/{tenant}/...
        authorization_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        default_scopes=[
            "https://graph.microsoft.com/Calendars.ReadWrite",
            "offline_access",  # required to get a refresh_token
        ],
        token_auth="post",
        # response_mode=query keeps the auth code in the URL (default for web apps)
        extra_auth_params={
            "response_mode": "query",
        },
        api_base_url="https://graph.microsoft.com/v1.0",
    ),
    "salesforce": OAuthProviderConfig(
        display_name="Salesforce",
        # Production login. For sandbox testing, customers should override
        # to https://test.salesforce.com/services/oauth2/authorize via env.
        authorization_url="https://login.salesforce.com/services/oauth2/authorize",
        token_url="https://login.salesforce.com/services/oauth2/token",
        default_scopes=[
            "api",
            "refresh_token",
            "offline_access",
        ],
        token_auth="post",
        # Salesforce returns the user's instance URL in the token response.
        # The HTTP executor reads `instance_url` from oauth_token_json when
        # the connection's base_url contains the {instance} placeholder.
        api_base_url="https://login.salesforce.com",
        revoke_url="https://login.salesforce.com/services/oauth2/revoke",
    ),
    "zendesk": OAuthProviderConfig(
        display_name="Zendesk",
        # Zendesk OAuth is per-subdomain. Customers must configure
        # `ZENDESK_SUBDOMAIN` (or override base_url at connection time)
        # so URLs become https://{subdomain}.zendesk.com/oauth/authorizations/new
        authorization_url="https://{subdomain}.zendesk.com/oauth/authorizations/new",
        token_url="https://{subdomain}.zendesk.com/oauth/tokens",
        default_scopes=["read", "write"],
        token_auth="post",
        api_base_url="https://{subdomain}.zendesk.com/api/v2",
    ),
}


def get_client_credentials(
    provider: str,
    settings: RuntimeSettings,
) -> tuple[str, str] | None:
    """Return (client_id, client_secret) for *provider* from settings, or None.

    Returns None when the provider is unknown or credentials are not
    configured, so callers can raise an appropriate HTTP 503.
    """
    if provider == "hubspot":
        cid = settings.hubspot_client_id
        secret = settings.hubspot_client_secret
    elif provider == "google_calendar":
        cid = settings.google_client_id
        secret = settings.google_client_secret
    elif provider == "microsoft_calendar":
        cid = getattr(settings, "microsoft_client_id", None)
        secret = getattr(settings, "microsoft_client_secret", None)
    elif provider == "salesforce":
        cid = getattr(settings, "salesforce_client_id", None)
        secret = getattr(settings, "salesforce_client_secret", None)
    elif provider == "zendesk":
        cid = getattr(settings, "zendesk_client_id", None)
        secret = getattr(settings, "zendesk_client_secret", None)
    else:
        return None

    if not cid or not secret:
        return None
    return cid, secret
