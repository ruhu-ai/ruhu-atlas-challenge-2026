"""Tests for AfricasTalkingPhoneProvider.send_sms.

Covers:

* Single + multi-recipient happy paths
* Per-recipient status mapping (101 Sent / 102 Queued / others rejected)
* Mixed-result responses (some sent, some rejected)
* Input validation (missing creds, empty message, no recipients, invalid E.164)
* HTTP error handling: 401/403 → AfricasTalkingCredentialError;
  network/timeout → AfricasTalkingReachabilityError; 5xx → soft error;
  other 4xx → soft error
* Body-encoding contract (form-urlencoded, comma-separated ``to``,
  optional ``from`` sender_id, ``apiKey`` header, sandbox vs prod URL)
* Defensive parsing (drift in response shape, missing recipients in
  response, garbage payload)
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ruhu.phone_provider_africastalking import (
    AfricasTalkingCredentialError,
    AfricasTalkingPhoneProvider,
    AfricasTalkingReachabilityError,
    AfricasTalkingSmsRecipient,
    AfricasTalkingSmsResult,
    _at_status_label,
    _parse_at_sms_response,
)


def _mock_http_response(*, status_code: int, body: bytes = b"{}") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = body
    resp.text = body.decode("utf-8")
    resp.json.return_value = json.loads(body)
    return resp


def _mock_async_client(post_response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=post_response)
    return client


# ── Pure-function tests: status mapping + response parsing ───────────


def test_at_status_label_maps_known_success_codes() -> None:
    assert _at_status_label(101, "Success") == "sent"
    assert _at_status_label(102, "Queued") == "queued"
    assert _at_status_label(100, "Processed") == "processed"


def test_at_status_label_maps_unknown_code_to_rejected() -> None:
    # AT codes 4xx range are per-recipient rejections (InvalidPhone,
    # InsufficientBalance, etc.).
    assert _at_status_label(403, "InvalidPhoneNumber") == "rejected"
    assert _at_status_label(405, "InsufficientBalance") == "rejected"


def test_at_status_label_falls_back_to_raw_status_when_code_missing() -> None:
    assert _at_status_label(None, "Success") == "sent"
    assert _at_status_label(None, "weird") == "error"
    assert _at_status_label(None, None) == "error"


def test_parse_at_sms_response_extracts_recipients_from_envelope() -> None:
    payload = {
        "SMSMessageData": {
            "Message": "Sent to 1/1 Total Cost: KES 0.8000",
            "Recipients": [
                {
                    "number": "+254712345678",
                    "statusCode": 101,
                    "status": "Success",
                    "messageId": "ATXid_abc",
                    "cost": "KES 0.8000",
                }
            ],
        }
    }
    result = _parse_at_sms_response(payload, expected_recipients=["+254712345678"])

    assert result.accepted is True
    assert result.summary == "Sent to 1/1 Total Cost: KES 0.8000"
    assert len(result.recipients) == 1
    assert result.recipients[0].phone_number == "+254712345678"
    assert result.recipients[0].status == "sent"
    assert result.recipients[0].message_id == "ATXid_abc"
    assert result.recipients[0].cost == "KES 0.8000"
    assert result.recipients[0].error is None


def test_parse_at_sms_response_marks_rejected_recipients_with_at_reason() -> None:
    payload = {
        "SMSMessageData": {
            "Message": "Sent to 1/2",
            "Recipients": [
                {"number": "+254712345678", "statusCode": 101, "status": "Success"},
                {
                    "number": "+254799999999",
                    "statusCode": 403,
                    "status": "InvalidPhoneNumber",
                },
            ],
        }
    }
    result = _parse_at_sms_response(
        payload, expected_recipients=["+254712345678", "+254799999999"]
    )
    assert result.accepted is True  # at least one accepted
    rejected = [r for r in result.recipients if r.status == "rejected"]
    assert len(rejected) == 1
    assert rejected[0].error == "InvalidPhoneNumber"
    assert rejected[0].status_code == 403


def test_parse_at_sms_response_synthesizes_error_for_silently_dropped_recipient() -> None:
    """If we asked AT to send to two numbers but the response only mentions
    one, surface the missing one as an explicit error. Outcome is uncertain,
    not silently successful."""
    payload = {
        "SMSMessageData": {
            "Message": "Sent to 1/1",
            "Recipients": [
                {"number": "+254712345678", "statusCode": 101, "status": "Success"}
            ],
        }
    }
    result = _parse_at_sms_response(
        payload, expected_recipients=["+254712345678", "+254799999999"]
    )
    missing = [r for r in result.recipients if r.phone_number == "+254799999999"]
    assert len(missing) == 1
    assert missing[0].status == "error"
    assert "missing" in (missing[0].error or "")


def test_parse_at_sms_response_handles_non_dict_payload() -> None:
    result = _parse_at_sms_response("garbage", expected_recipients=[])
    assert result.accepted is False
    assert result.error == "unexpected response shape"


def test_parse_at_sms_response_handles_missing_envelope() -> None:
    result = _parse_at_sms_response({"some": "other shape"}, expected_recipients=[])
    assert result.accepted is False
    assert "missing SMSMessageData" in (result.error or "")


# ── send_sms input validation ────────────────────────────────────────


def test_send_sms_returns_error_when_username_missing() -> None:
    async def run() -> AfricasTalkingSmsResult:
        provider = AfricasTalkingPhoneProvider(api_key="k")  # no username
        return await provider.send_sms(to="+254712345678", message="hi")

    result = asyncio.run(run())
    assert result.accepted is False
    assert result.error == "username is required"


def test_send_sms_returns_error_when_api_key_missing() -> None:
    async def run() -> AfricasTalkingSmsResult:
        provider = AfricasTalkingPhoneProvider(username="ruhu")
        return await provider.send_sms(to="+254712345678", message="hi")

    result = asyncio.run(run())
    assert result.accepted is False
    assert result.error == "api_key is required"


def test_send_sms_rejects_empty_message() -> None:
    async def run() -> AfricasTalkingSmsResult:
        provider = AfricasTalkingPhoneProvider(api_key="k", username="u")
        return await provider.send_sms(to="+254712345678", message="   ")

    result = asyncio.run(run())
    assert result.accepted is False
    assert result.error == "message is required"


def test_send_sms_rejects_empty_recipient_list() -> None:
    async def run() -> AfricasTalkingSmsResult:
        provider = AfricasTalkingPhoneProvider(api_key="k", username="u")
        return await provider.send_sms(to=[], message="hi")

    result = asyncio.run(run())
    assert result.accepted is False
    assert "at least one recipient" in (result.error or "")


def test_send_sms_validates_e164_format_before_calling_at() -> None:
    """Invalid phone numbers should be caught client-side so we don't
    waste an AT call (and the call fee)."""
    async def run() -> AfricasTalkingSmsResult:
        client = _mock_async_client(_mock_http_response(status_code=200))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        result = await provider.send_sms(to="not-a-phone", message="hi")
        # Confirm we did NOT POST to AT.
        client.post.assert_not_called()
        return result

    result = asyncio.run(run())
    assert result.accepted is False
    assert result.recipients
    assert result.recipients[0].status == "rejected"
    assert "invalid E.164" in (result.recipients[0].error or "")


# ── send_sms happy paths ─────────────────────────────────────────────


def test_send_sms_single_recipient_returns_sent_status() -> None:
    body = b'{"SMSMessageData": {"Message": "Sent to 1/1", "Recipients": [{"number": "+254712345678", "statusCode": 101, "status": "Success", "messageId": "ATXid_1", "cost": "KES 0.80"}]}}'

    async def run() -> AfricasTalkingSmsResult:
        client = _mock_async_client(_mock_http_response(status_code=201, body=body))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        result = await provider.send_sms(to="+254712345678", message="hi")
        # Verify the wire call
        call_kwargs = client.post.call_args.kwargs
        assert call_kwargs["data"]["username"] == "u"
        assert call_kwargs["data"]["to"] == "+254712345678"
        assert call_kwargs["data"]["message"] == "hi"
        assert "from" not in call_kwargs["data"]  # no sender_id provided
        assert call_kwargs["headers"]["apiKey"] == "k"
        assert (
            call_kwargs["headers"]["Content-Type"]
            == "application/x-www-form-urlencoded"
        )
        return result

    result = asyncio.run(run())
    assert result.accepted is True
    assert result.recipients[0].status == "sent"
    assert result.recipients[0].message_id == "ATXid_1"
    assert result.recipients[0].cost == "KES 0.80"


def test_send_sms_multi_recipient_joins_with_comma_and_returns_per_recipient_status() -> None:
    body = (
        b'{"SMSMessageData": {"Message": "Sent to 2/2", "Recipients": ['
        b'{"number": "+254712345678", "statusCode": 101, "status": "Success", "messageId": "id1"},'
        b'{"number": "+254700000000", "statusCode": 102, "status": "Queued", "messageId": "id2"}'
        b']}}'
    )

    async def run() -> AfricasTalkingSmsResult:
        client = _mock_async_client(_mock_http_response(status_code=201, body=body))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        result = await provider.send_sms(
            to=["+254712345678", "+254700000000"], message="hi"
        )
        call_kwargs = client.post.call_args.kwargs
        assert call_kwargs["data"]["to"] == "+254712345678,+254700000000"
        return result

    result = asyncio.run(run())
    assert result.accepted is True
    statuses = {r.phone_number: r.status for r in result.recipients}
    assert statuses["+254712345678"] == "sent"
    assert statuses["+254700000000"] == "queued"


def test_send_sms_mixed_success_and_rejection() -> None:
    body = (
        b'{"SMSMessageData": {"Message": "Sent to 1/2", "Recipients": ['
        b'{"number": "+254712345678", "statusCode": 101, "status": "Success", "messageId": "id1"},'
        b'{"number": "+254700000000", "statusCode": 405, "status": "InsufficientBalance"}'
        b']}}'
    )

    async def run() -> AfricasTalkingSmsResult:
        client = _mock_async_client(_mock_http_response(status_code=201, body=body))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        return await provider.send_sms(
            to=["+254712345678", "+254700000000"], message="hi"
        )

    result = asyncio.run(run())
    assert result.accepted is True  # one accepted is enough for accepted=True
    rejected = [r for r in result.recipients if r.status == "rejected"]
    assert len(rejected) == 1
    assert rejected[0].error == "InsufficientBalance"


def test_send_sms_includes_sender_id_when_provided() -> None:
    body = b'{"SMSMessageData": {"Message": "ok", "Recipients": [{"number": "+254712345678", "statusCode": 101, "status": "Success"}]}}'

    async def run() -> None:
        client = _mock_async_client(_mock_http_response(status_code=201, body=body))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        await provider.send_sms(
            to="+254712345678", message="hi", sender_id="RUHU"
        )
        call_kwargs = client.post.call_args.kwargs
        assert call_kwargs["data"]["from"] == "RUHU"

    asyncio.run(run())


# ── send_sms HTTP error handling ─────────────────────────────────────


def test_send_sms_raises_credential_error_on_401() -> None:
    async def run() -> None:
        client = _mock_async_client(_mock_http_response(status_code=401))
        provider = AfricasTalkingPhoneProvider(
            api_key="bad", username="u", http_client=client
        )
        with pytest.raises(AfricasTalkingCredentialError):
            await provider.send_sms(to="+254712345678", message="hi")

    asyncio.run(run())


def test_send_sms_raises_credential_error_on_403() -> None:
    async def run() -> None:
        client = _mock_async_client(_mock_http_response(status_code=403))
        provider = AfricasTalkingPhoneProvider(
            api_key="bad", username="u", http_client=client
        )
        with pytest.raises(AfricasTalkingCredentialError):
            await provider.send_sms(to="+254712345678", message="hi")

    asyncio.run(run())


def test_send_sms_returns_soft_error_on_5xx() -> None:
    async def run() -> AfricasTalkingSmsResult:
        client = _mock_async_client(_mock_http_response(status_code=503))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        return await provider.send_sms(to="+254712345678", message="hi")

    result = asyncio.run(run())
    assert result.accepted is False
    assert "server error" in (result.error or "")


def test_send_sms_returns_soft_error_on_other_4xx() -> None:
    async def run() -> AfricasTalkingSmsResult:
        client = _mock_async_client(_mock_http_response(status_code=400))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        return await provider.send_sms(to="+254712345678", message="hi")

    result = asyncio.run(run())
    assert result.accepted is False
    assert "rejected SMS request" in (result.error or "")


def test_send_sms_raises_reachability_error_on_timeout() -> None:
    async def run() -> None:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        with pytest.raises(AfricasTalkingReachabilityError):
            await provider.send_sms(to="+254712345678", message="hi")

    asyncio.run(run())


def test_send_sms_raises_reachability_error_on_network_failure() -> None:
    async def run() -> None:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused", request=MagicMock())
        )
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        with pytest.raises(AfricasTalkingReachabilityError):
            await provider.send_sms(to="+254712345678", message="hi")

    asyncio.run(run())


# ── send_sms environment routing ─────────────────────────────────────


def test_send_sms_uses_sandbox_url_when_sandbox_true() -> None:
    body = b'{"SMSMessageData": {"Message": "ok", "Recipients": [{"number": "+254712345678", "statusCode": 101, "status": "Success"}]}}'

    async def run() -> None:
        client = _mock_async_client(_mock_http_response(status_code=201, body=body))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", sandbox=True, http_client=client
        )
        await provider.send_sms(to="+254712345678", message="hi")
        url = client.post.call_args.args[0]
        assert "sandbox.africastalking.com" in url

    asyncio.run(run())


def test_send_sms_uses_production_url_by_default() -> None:
    body = b'{"SMSMessageData": {"Message": "ok", "Recipients": [{"number": "+254712345678", "statusCode": 101, "status": "Success"}]}}'

    async def run() -> None:
        client = _mock_async_client(_mock_http_response(status_code=201, body=body))
        provider = AfricasTalkingPhoneProvider(
            api_key="k", username="u", http_client=client
        )
        await provider.send_sms(to="+254712345678", message="hi")
        url = client.post.call_args.args[0]
        assert "api.africastalking.com" in url
        assert "sandbox" not in url

    asyncio.run(run())


def test_send_sms_uses_overridden_credentials_when_provided() -> None:
    """Constructor-provided credentials are defaults; per-call overrides win."""
    body = b'{"SMSMessageData": {"Message": "ok", "Recipients": [{"number": "+254712345678", "statusCode": 101, "status": "Success"}]}}'

    async def run() -> None:
        client = _mock_async_client(_mock_http_response(status_code=201, body=body))
        provider = AfricasTalkingPhoneProvider(
            api_key="default", username="default-user", http_client=client
        )
        await provider.send_sms(
            to="+254712345678",
            message="hi",
            username="override-user",
            api_key="override-key",
        )
        call_kwargs = client.post.call_args.kwargs
        assert call_kwargs["data"]["username"] == "override-user"
        assert call_kwargs["headers"]["apiKey"] == "override-key"

    asyncio.run(run())
