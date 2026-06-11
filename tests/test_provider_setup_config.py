"""Tests for per-tenant OAuth URL overrides and template_config handling.

Covers:
- Template list exposes `required_config` for templates with pre-flow placeholders
  (e.g., Zendesk `{subdomain}` in auth URL) but NOT for placeholders that are
  only resolved post-OAuth (e.g., Salesforce `{instance}` in base_url)
- Custom OAuth template signals `requires_custom_urls`
- Unresolved placeholders in final URLs are rejected
"""
from ruhu.tools.provider_templates import (
    _collect_placeholders,
    list_templates,
    PROVIDER_TEMPLATES,
)


def test_list_templates_surfaces_required_config_for_zendesk() -> None:
    """Zendesk needs `subdomain` because its OAuth URLs contain `{subdomain}`."""
    by_slug = {t["slug"]: t for t in list_templates()}
    assert "zendesk" in by_slug
    assert by_slug["zendesk"]["required_config"] == ["subdomain"]
    assert by_slug["zendesk"]["auth_type"] == "oauth2"


def test_list_templates_does_not_require_config_for_salesforce_instance() -> None:
    """Salesforce's `{instance}` in base_url is resolved from the token response
    after OAuth, not from the user at setup. required_config should be empty."""
    by_slug = {t["slug"]: t for t in list_templates()}
    assert by_slug["salesforce"]["required_config"] == []


def test_list_templates_zero_config_for_standard_oauth_providers() -> None:
    """HubSpot, Google Calendar, Microsoft Calendar have fully-formed URLs
    — no config required from the user."""
    by_slug = {t["slug"]: t for t in list_templates()}
    for slug in ("hubspot", "google_calendar", "microsoft_calendar"):
        assert by_slug[slug]["required_config"] == [], f"{slug} should be zero-config"
        assert by_slug[slug]["requires_custom_urls"] is False


def test_custom_oauth_template_signals_custom_urls() -> None:
    """custom_oauth template asks the frontend for a full URL form."""
    by_slug = {t["slug"]: t for t in list_templates()}
    assert "custom_oauth" in by_slug
    assert by_slug["custom_oauth"]["requires_custom_urls"] is True
    assert by_slug["custom_oauth"]["has_oauth"] is False  # no preset OAUTH_PROVIDERS entry
    assert by_slug["custom_oauth"]["starter_tools"] == []  # user adds their own


def test_collect_placeholders_extracts_names() -> None:
    assert _collect_placeholders("https://{subdomain}.zendesk.com/api") == ["subdomain"]
    assert _collect_placeholders("https://{a}.foo.com/{b}") == ["a", "b"]
    assert _collect_placeholders("https://static.com/api") == []
    assert _collect_placeholders(None) == []
    # Multiple URLs, deduplicated
    assert _collect_placeholders("https://{s}.a.com", "https://{s}.b.com") == ["s"]


def test_setup_provider_substitutes_placeholders_with_template_config() -> None:
    """setup_provider() substitutes {placeholder} values from template_config
    into base_url and (if applicable) the preset auth/token URLs."""
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from ruhu.db_models import APIConnectionRecord, ToolDefinitionRecord
    from ruhu.tools.provider_templates import setup_provider

    # In-memory SQLite: create only the tables we need (others have ARRAY columns)
    engine = create_engine("sqlite:///:memory:")
    APIConnectionRecord.__table__.create(engine)
    ToolDefinitionRecord.__table__.create(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)

    _ = datetime  # silence unused import warning

    template = PROVIDER_TEMPLATES["zendesk"]
    connection, tools = setup_provider(
        template,
        session_factory=sf,
        organization_id="org_test",
        template_config={"subdomain": "acme"},
    )

    # base_url has the subdomain substituted
    assert connection.base_url == "https://acme.zendesk.com/api/v2"
    # auth URL override was populated from the template preset + substitution
    assert connection.auth_url_override is not None
    assert "acme.zendesk.com" in connection.auth_url_override
    assert "{subdomain}" not in connection.auth_url_override
    # token URL similarly
    assert connection.token_url_override is not None
    assert "acme.zendesk.com" in connection.token_url_override
    # metadata records the template_config for later reference
    assert connection.metadata_json.get("template_config") == {"subdomain": "acme"}
    assert tools
    for tool in tools:
        assert tool.input_schema_json["type"] == "object"
        assert tool.input_schema_json["additionalProperties"] is True
        assert tool.output_schema_json["type"] == "object"
        assert tool.output_schema_json["additionalProperties"] is True


def test_setup_provider_rejects_unresolved_placeholders() -> None:
    """When template_config doesn't fill all placeholders, setup_provider raises."""
    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from ruhu.db_models import APIConnectionRecord, ToolDefinitionRecord
    from ruhu.tools.provider_templates import setup_provider

    engine = create_engine("sqlite:///:memory:")
    APIConnectionRecord.__table__.create(engine)
    ToolDefinitionRecord.__table__.create(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)

    template = PROVIDER_TEMPLATES["zendesk"]
    with pytest.raises(ValueError, match="unresolved placeholder"):
        setup_provider(
            template,
            session_factory=sf,
            organization_id="org_test",
            # Missing subdomain — {subdomain} should remain unresolved
            template_config={},
        )


def test_custom_oauth_setup_accepts_user_supplied_urls() -> None:
    """The custom_oauth template accepts fully user-supplied URLs and creates
    a connection with those overrides stored."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from ruhu.db_models import APIConnectionRecord, ToolDefinitionRecord
    from ruhu.tools.provider_templates import setup_provider

    engine = create_engine("sqlite:///:memory:")
    APIConnectionRecord.__table__.create(engine)
    ToolDefinitionRecord.__table__.create(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)

    template = PROVIDER_TEMPLATES["custom_oauth"]
    connection, tools = setup_provider(
        template,
        session_factory=sf,
        organization_id="org_test",
        display_name="My Custom IdP",
        base_url="https://api.custom-idp.example/v1",
        auth_url_override="https://custom-idp.example/oauth/authorize",
        token_url_override="https://custom-idp.example/oauth/token",
    )

    assert connection.base_url == "https://api.custom-idp.example/v1"
    assert connection.auth_url_override == "https://custom-idp.example/oauth/authorize"
    assert connection.token_url_override == "https://custom-idp.example/oauth/token"
    assert connection.display_name == "My Custom IdP"
    # No starter tools for custom_oauth
    assert tools == []
