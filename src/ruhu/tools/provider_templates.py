"""Provider templates for one-click integration setup.

Each template describes a known third-party provider and the starter tool
definitions that should be auto-created when the provider is connected.
Templates are NOT a separate entity in the database — they're a helper that
creates ``ToolConnection`` + ``ToolDefinitionRecord`` rows.

Usage::

    from ruhu.tools.provider_templates import PROVIDER_TEMPLATES, setup_provider

    template = PROVIDER_TEMPLATES["hubspot"]
    connection, tools = setup_provider(
        template,
        session_factory=sf,
        organization_id="org_123",
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from ruhu.db_models import APIConnectionRecord, ToolDefinitionRecord

from .oauth_providers import OAUTH_PROVIDERS, OAuthProviderConfig


_DEFAULT_TEMPLATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": True,
}
_DEFAULT_TEMPLATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}


@dataclass(frozen=True, slots=True)
class StarterTool:
    """A tool definition that is auto-created when a provider is connected."""

    ref: str
    function_name: str
    display_name: str
    description: str
    endpoint_path: str
    http_method: str = "POST"
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class ProviderTemplate:
    """Static template for a known integration provider."""

    slug: str
    display_name: str
    category: str  # "crm" | "calendar" | "ticketing" | "other"
    icon: str = ""
    auth_type: str = "oauth2"
    base_url: str = ""
    oauth_config: OAuthProviderConfig | None = None
    starter_tools: list[StarterTool] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


# ── Provider template registry ──────────────────────────────────────────────

PROVIDER_TEMPLATES: dict[str, ProviderTemplate] = {
    "hubspot": ProviderTemplate(
        slug="hubspot",
        display_name="HubSpot",
        category="crm",
        icon="🟠",
        auth_type="oauth2",
        base_url="https://api.hubapi.com",
        oauth_config=OAUTH_PROVIDERS.get("hubspot"),
        capabilities=[
            "Create and update CRM contacts",
            "Look up contacts by email",
            "List and manage deals",
        ],
        starter_tools=[
            StarterTool(
                ref="crm.create_contact",
                function_name="create_contact",
                display_name="Create Contact",
                description="Create a new contact in your CRM with email, name, and company details.",
                endpoint_path="/crm/v3/objects/contacts",
                http_method="POST",
                read_only=False,
            ),
            StarterTool(
                ref="crm.get_contact",
                function_name="get_contact",
                display_name="Get Contact",
                description="Fetch an existing HubSpot contact by contact ID for live verification or follow-up routing.",
                endpoint_path="/crm/v3/objects/contacts/{contact_id}",
                http_method="GET",
                read_only=True,
            ),
            StarterTool(
                ref="crm.list_deals",
                function_name="list_deals",
                display_name="List Deals",
                description="Retrieve a list of deals from your CRM pipeline.",
                endpoint_path="/crm/v3/objects/deals",
                http_method="GET",
                read_only=True,
            ),
        ],
    ),
    "google_calendar": ProviderTemplate(
        slug="google_calendar",
        display_name="Google Calendar",
        category="calendar",
        icon="📅",
        auth_type="oauth2",
        base_url="https://www.googleapis.com/calendar/v3",
        oauth_config=OAUTH_PROVIDERS.get("google_calendar"),
        capabilities=[
            "Create calendar events",
            "Check availability and free/busy status",
            "List upcoming events",
        ],
        starter_tools=[
            StarterTool(
                ref="calendar.create_event",
                function_name="create_event",
                display_name="Create Event",
                description="Book a new event on the connected Google Calendar.",
                endpoint_path="/calendars/primary/events",
                http_method="POST",
                read_only=False,
            ),
            StarterTool(
                ref="calendar.check_availability",
                function_name="check_availability",
                display_name="Check Availability",
                description="Check free/busy status on the connected Google Calendar.",
                endpoint_path="/freeBusy",
                http_method="POST",
                read_only=True,
            ),
            StarterTool(
                ref="calendar.get_event",
                function_name="get_event",
                display_name="Get Event",
                description="Fetch a single Google Calendar event by event ID for live status verification.",
                endpoint_path="/calendars/primary/events/{event_id}",
                http_method="GET",
                read_only=True,
            ),
            StarterTool(
                ref="calendar.cancel_event",
                function_name="cancel_event",
                display_name="Cancel Event",
                description="Cancel an event on the connected Google Calendar by event ID.",
                endpoint_path="/calendars/primary/events/{event_id}",
                http_method="DELETE",
                read_only=False,
            ),
            StarterTool(
                ref="calendar.update_event",
                function_name="update_event",
                display_name="Update Event",
                description="Update (e.g., reschedule) an existing Google Calendar event.",
                endpoint_path="/calendars/primary/events/{event_id}",
                http_method="PATCH",
                read_only=False,
            ),
        ],
    ),
    "microsoft_calendar": ProviderTemplate(
        slug="microsoft_calendar",
        display_name="Microsoft Calendar",
        category="calendar",
        icon="📅",
        auth_type="oauth2",
        base_url="https://graph.microsoft.com/v1.0",
        oauth_config=OAUTH_PROVIDERS.get("microsoft_calendar"),
        capabilities=[
            "Create calendar events on the user's Outlook/Microsoft 365 calendar",
            "Find available meeting times across attendees",
            "Cancel or update existing events",
        ],
        starter_tools=[
            # NOTE: tool refs use the generic `calendar.*` namespace so the
            # action_config dispatch (calendar(action="...")) works with
            # whichever calendar provider the customer connected. Only one
            # calendar provider may be connected per organization at a time.
            StarterTool(
                ref="calendar.create_event",
                function_name="create_event",
                display_name="Create Event",
                description="Create a calendar event on the connected Microsoft account.",
                endpoint_path="/me/events",
                http_method="POST",
                read_only=False,
            ),
            StarterTool(
                ref="calendar.check_availability",
                function_name="check_availability",
                display_name="Check Availability",
                description="Find available meeting times across the specified attendees.",
                endpoint_path="/me/findMeetingTimes",
                http_method="POST",
                read_only=True,
            ),
            StarterTool(
                ref="calendar.get_event",
                function_name="get_event",
                display_name="Get Event",
                description="Fetch a single Microsoft Calendar event by event ID for live status verification.",
                endpoint_path="/me/events/{event_id}",
                http_method="GET",
                read_only=True,
            ),
            StarterTool(
                ref="calendar.cancel_event",
                function_name="cancel_event",
                display_name="Cancel Event",
                description="Cancel a calendar event by its event ID.",
                endpoint_path="/me/events/{event_id}/cancel",
                http_method="POST",
                read_only=False,
            ),
            StarterTool(
                ref="calendar.update_event",
                function_name="update_event",
                display_name="Update Event",
                description="Update (e.g., reschedule) an existing Microsoft Calendar event.",
                endpoint_path="/me/events/{event_id}",
                http_method="PATCH",
                read_only=False,
            ),
        ],
    ),
    "zendesk": ProviderTemplate(
        slug="zendesk",
        display_name="Zendesk",
        category="ticketing",
        icon="🎫",
        # Zendesk OAuth is per-subdomain. The setup request must provide
        # `template_config={"subdomain": "<customer_subdomain>"}` so the
        # backend substitutes {subdomain} in the OAuth URLs and base_url.
        auth_type="oauth2",
        base_url="https://{subdomain}.zendesk.com/api/v2",
        oauth_config=OAUTH_PROVIDERS.get("zendesk"),
        capabilities=[
            "Create support tickets",
            "Look up existing tickets",
            "Update ticket status",
        ],
        starter_tools=[
            StarterTool(
                ref="ticketing.create_ticket",
                function_name="create_ticket",
                display_name="Create Ticket",
                description="Create a new support ticket in your Zendesk instance.",
                endpoint_path="/tickets.json",
                http_method="POST",
                read_only=False,
            ),
            StarterTool(
                ref="ticketing.get_ticket",
                function_name="get_ticket",
                display_name="Get Ticket",
                description="Retrieve an existing support ticket by ID from Zendesk.",
                endpoint_path="/tickets/{ticket_id}.json",
                http_method="GET",
                read_only=True,
            ),
        ],
    ),
    "salesforce": ProviderTemplate(
        slug="salesforce",
        display_name="Salesforce",
        category="crm",
        icon="☁️",
        auth_type="oauth2",
        base_url="https://{instance}.salesforce.com/services/data/v59.0",
        oauth_config=OAUTH_PROVIDERS.get("salesforce"),
        capabilities=[
            "Create and update CRM contacts",
            "Look up accounts and opportunities",
            "Log activities and tasks",
        ],
        starter_tools=[
            StarterTool(
                ref="crm.create_contact",
                function_name="create_contact",
                display_name="Create Contact",
                description="Create a new contact record in your Salesforce CRM.",
                endpoint_path="/sobjects/Contact",
                http_method="POST",
                read_only=False,
            ),
            StarterTool(
                ref="crm.get_contact",
                function_name="get_contact",
                display_name="Get Contact",
                description="Fetch an existing Salesforce contact by contact ID for live verification or follow-up routing.",
                endpoint_path="/sobjects/Contact/{contact_id}",
                http_method="GET",
                read_only=True,
            ),
        ],
    ),
    "custom_oauth": ProviderTemplate(
        slug="custom_oauth",
        display_name="Custom OAuth Provider",
        category="custom",
        icon="🔧",
        # Generic OAuth provider. No preset URLs — the caller must supply
        # `auth_url_override`, `token_url_override`, and `base_url` in the
        # setup request. No preset OAUTH_PROVIDERS entry — the OAuthFlowManager
        # falls back to the connection's override when the provider is unknown.
        auth_type="oauth2",
        base_url="",  # must be supplied at setup
        capabilities=[
            "Bring-your-own OAuth provider",
            "Configure authorization URL, token URL, and API base at setup time",
            "Add your own starter tools after the connection is active",
        ],
        starter_tools=[],  # no presets — user adds tools via the catalog
    ),
}


def _collect_placeholders(*urls: str | None) -> list[str]:
    """Extract unique {placeholder} names from the given URL templates."""
    import re
    found: set[str] = set()
    for url in urls:
        if not url:
            continue
        for match in re.finditer(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", url):
            found.add(match.group(1))
    return sorted(found)


def list_templates() -> list[dict[str, Any]]:
    """Return all provider templates as serializable dicts for the API.

    Includes ``required_config`` — a list of placeholder keys the frontend
    must collect from the user before calling ``setup_provider`` (e.g.,
    Zendesk needs ``"subdomain"``; custom_oauth needs URL fields).
    """
    result = []
    for slug, tmpl in PROVIDER_TEMPLATES.items():
        oauth = tmpl.oauth_config
        # Only collect placeholders from URLs that must be resolved BEFORE
        # the OAuth flow starts. Placeholders in base_url that are only
        # resolved from the token response (e.g., Salesforce's {instance})
        # are handled automatically in _persist_tokens — frontend doesn't
        # need to prompt for them.
        pre_flow_placeholders = _collect_placeholders(
            oauth.authorization_url if oauth else None,
            oauth.token_url if oauth else None,
        )
        # If the base_url has a placeholder that's ALSO in the auth/token
        # URL, the frontend needs to collect it. Otherwise it's auto-resolved.
        base_placeholders = _collect_placeholders(tmpl.base_url)
        placeholders = sorted(set(pre_flow_placeholders) | (set(base_placeholders) & set(pre_flow_placeholders)))
        result.append({
            "slug": slug,
            "display_name": tmpl.display_name,
            "category": tmpl.category,
            "icon": tmpl.icon,
            "auth_type": tmpl.auth_type,
            "base_url": tmpl.base_url,
            "capabilities": list(tmpl.capabilities),
            "starter_tools": [
                {
                    "ref": t.ref,
                    "function_name": t.function_name,
                    "display_name": t.display_name,
                    "description": t.description,
                    "read_only": t.read_only,
                }
                for t in tmpl.starter_tools
            ],
            "has_oauth": tmpl.oauth_config is not None,
            # Placeholders the frontend must collect (e.g., ["subdomain"]).
            "required_config": placeholders,
            # True when the template has no preset URLs — frontend shows
            # full URL form instead of a placeholder form.
            "requires_custom_urls": slug == "custom_oauth",
        })
    return result


def setup_provider(
    template: ProviderTemplate,
    *,
    session_factory: sessionmaker[Session],
    organization_id: str,
    display_name: str | None = None,
    base_url: str | None = None,
    credentials_enc: str | None = None,
    auth_url_override: str | None = None,
    token_url_override: str | None = None,
    template_config: dict[str, str] | None = None,
    oauth_client_id_override: str | None = None,
    oauth_client_secret_enc: str | None = None,
) -> tuple[APIConnectionRecord, list[ToolDefinitionRecord]]:
    """Create a connection and starter tools from a provider template.

    Returns the created connection and list of tool definitions.
    For OAuth providers, the connection starts in ``needs_auth`` status —
    the caller should then initiate the OAuth flow.

    ``auth_url_override`` and ``token_url_override`` let callers use
    per-tenant OAuth endpoints (e.g., Zendesk subdomains) or fully custom
    OAuth providers without per-provider backend code.

    ``template_config`` is a dict of placeholder substitutions (e.g.,
    ``{"subdomain": "acme"}``) applied to ``base_url``, ``auth_url_override``,
    and ``token_url_override`` so callers can provide a single value and
    the template handles the URL templating.
    """
    now = _utcnow()
    conn_id = _new_id("conn")
    effective_base_url = base_url or template.base_url
    effective_auth_url = auth_url_override
    effective_token_url = token_url_override

    # Apply template_config placeholder substitutions. E.g., for Zendesk
    # with template_config={"subdomain": "acme"}, "{subdomain}" in the
    # base_url becomes "acme".
    if template_config:
        for placeholder, value in template_config.items():
            token = "{" + placeholder + "}"
            if effective_base_url:
                effective_base_url = effective_base_url.replace(token, value)
            if effective_auth_url:
                effective_auth_url = effective_auth_url.replace(token, value)
            if effective_token_url:
                effective_token_url = effective_token_url.replace(token, value)
        # Also substitute on the oauth_config defaults if the template
        # points to a provider whose preset URLs contain placeholders
        # (e.g., Zendesk's provider-default auth URL has {subdomain}).
        if template.oauth_config is not None:
            if not effective_auth_url and "{" in template.oauth_config.authorization_url:
                effective_auth_url = template.oauth_config.authorization_url
                for placeholder, value in template_config.items():
                    effective_auth_url = effective_auth_url.replace("{" + placeholder + "}", value)
            if not effective_token_url and "{" in template.oauth_config.token_url:
                effective_token_url = template.oauth_config.token_url
                for placeholder, value in template_config.items():
                    effective_token_url = effective_token_url.replace("{" + placeholder + "}", value)

    # Validate: no unresolved placeholders should remain in base_url
    # or the URL overrides — that would cause runtime errors later.
    for label, value in (
        ("base_url", effective_base_url),
        ("auth_url_override", effective_auth_url),
        ("token_url_override", effective_token_url),
    ):
        if value and "{" in value and "}" in value:
            raise ValueError(
                f"{label} has unresolved placeholder: {value!r}. "
                f"Provide 'template_config' with the required substitutions."
            )

    connection = APIConnectionRecord(
        connection_id=conn_id,
        organization_id=organization_id,
        display_name=display_name or template.display_name,
        provider=template.slug,
        auth_type=template.auth_type,
        base_url=effective_base_url,
        auth_url_override=effective_auth_url,
        token_url_override=effective_token_url,
        oauth_client_id_override=oauth_client_id_override,
        oauth_client_secret_enc=oauth_client_secret_enc,
        credentials_enc=credentials_enc,
        status="needs_auth" if template.auth_type == "oauth2" else "active",
        metadata_json={
            "template_slug": template.slug,
            "category": template.category,
            "template_config": dict(template_config) if template_config else {},
        },
        created_at=now,
        updated_at=now,
    )

    tool_records: list[ToolDefinitionRecord] = []
    for starter in template.starter_tools:
        tool_records.append(
            ToolDefinitionRecord(
                tool_definition_id=_new_id("tool"),
                organization_id=organization_id,
                connection_id=conn_id,
                kind="integration",
                tool_ref=starter.ref,
                function_name=starter.function_name,
                display_name=starter.display_name,
                description=starter.description,
                endpoint_path=starter.endpoint_path,
                http_method=starter.http_method,
                read_only=starter.read_only,
                enabled=True,
                input_schema_json=dict(_DEFAULT_TEMPLATE_INPUT_SCHEMA),
                output_schema_json=dict(_DEFAULT_TEMPLATE_OUTPUT_SCHEMA),
                timeout_ms=5000,
                metadata_json={"template_slug": template.slug},
                created_at=now,
                updated_at=now,
            )
        )

    with session_factory.begin() as session:
        session.add(connection)
        for tool in tool_records:
            session.add(tool)

    return connection, tool_records
