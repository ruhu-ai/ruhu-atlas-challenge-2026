from __future__ import annotations

import base64
import hashlib
import hmac
import time

from ruhu.ticketing_providers import ProviderConnectionConfig, verify_ticketing_webhook_signature


def test_verify_zendesk_webhook_signature_accepts_valid_headers(monkeypatch) -> None:
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000)
    connection = ProviderConnectionConfig(
        connection_id="conn-zd",
        provider="zendesk",
        auth_type="api_token",
        credentials_ref=None,
        provider_config={
            "webhook_secret": "zendesk-secret",
            "webhook_tolerance_seconds": 300,
        },
    )
    body = b'{"ticket":{"id":"123"}}'
    timestamp = "1700000000"
    signature = base64.b64encode(
        hmac.new(
            b"zendesk-secret",
            timestamp.encode("utf-8") + body,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    assert verify_ticketing_webhook_signature(
        connection,
        body=body,
        headers={
            "x-zendesk-webhook-signature": signature,
            "x-zendesk-webhook-signature-timestamp": timestamp,
        },
    ) is True


def test_verify_jira_webhook_signature_accepts_valid_headers() -> None:
    connection = ProviderConnectionConfig(
        connection_id="conn-jira",
        provider="jira",
        auth_type="api_token",
        credentials_ref=None,
        provider_config={"webhook_secret": "jira-secret"},
    )
    body = b'{"issue":{"id":"SUP-1"}}'
    digest = hmac.new(b"jira-secret", body, hashlib.sha256).hexdigest()

    assert verify_ticketing_webhook_signature(
        connection,
        body=body,
        headers={"x-hub-signature": f"sha256={digest}"},
    ) is True


def test_verify_freshdesk_webhook_signature_returns_none_when_provider_has_no_native_signing() -> None:
    connection = ProviderConnectionConfig(
        connection_id="conn-fd",
        provider="freshdesk",
        auth_type="api_token",
        credentials_ref=None,
        provider_config={"webhook_secret": "unused"},
    )

    assert verify_ticketing_webhook_signature(
        connection,
        body=b"{}",
        headers={},
    ) is None
