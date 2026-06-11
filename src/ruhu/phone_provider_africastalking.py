from __future__ import annotations

import httpx
from dataclasses import dataclass, field
from typing import Literal

from .phone_number_registry import PhoneBindingHealthStatus, PhoneBindingVerificationStatus
from .phone_numbers import normalize_e164_number


# ── Provider errors ───────────────────────────────────────────────────────────

class AfricasTalkingProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AfricasTalkingCredentialError(AfricasTalkingProviderError):
    """Raised when credentials are invalid or the API rejects them."""


class AfricasTalkingReachabilityError(AfricasTalkingProviderError):
    """Raised when a callback URL fails reachability checks."""


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class AfricasTalkingCredentialValidationResult:
    valid: bool
    username: str
    account_type: str | None = None
    balance: str | None = None
    error: str | None = None

    def model_dump(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "username": self.username,
            "account_type": self.account_type,
            "balance": self.balance,
            "error": self.error,
        }


CallbackReachabilityStatus = Literal["reachable", "unreachable", "timeout", "error"]


@dataclass(slots=True, frozen=True)
class AfricasTalkingCallbackReachabilityResult:
    url: str
    status: CallbackReachabilityStatus
    http_status_code: int | None = None
    error: str | None = None

    @property
    def reachable(self) -> bool:
        return self.status == "reachable"

    def model_dump(self) -> dict[str, object]:
        return {
            "url": self.url,
            "status": self.status,
            "http_status_code": self.http_status_code,
            "error": self.error,
            "reachable": self.reachable,
        }


# ── SMS send result types ─────────────────────────────────────────────────────

# Africa's Talking SMS recipient status codes (from official docs).
# 100 Processed, 101 Sent, 102 Queued — these are success / in-flight states.
# Anything else is a per-recipient rejection that a sender can act on.
_AT_SUCCESS_STATUS_CODES = {100, 101, 102}


SmsRecipientStatus = Literal["sent", "queued", "processed", "rejected", "error"]


@dataclass(slots=True, frozen=True)
class AfricasTalkingSmsRecipient:
    phone_number: str
    status: SmsRecipientStatus
    status_code: int | None = None
    message_id: str | None = None
    cost: str | None = None
    error: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status in {"sent", "queued", "processed"}

    def model_dump(self) -> dict[str, object]:
        return {
            "phone_number": self.phone_number,
            "status": self.status,
            "status_code": self.status_code,
            "message_id": self.message_id,
            "cost": self.cost,
            "error": self.error,
            "accepted": self.accepted,
        }


@dataclass(slots=True, frozen=True)
class AfricasTalkingSmsResult:
    """Outcome of a ``send_sms`` call.

    ``accepted`` reflects whether the API call itself completed and at
    least one recipient was accepted by AT. Per-recipient status lives on
    each ``AfricasTalkingSmsRecipient`` so the caller can fan-out
    follow-up actions (retry, mark blacklisted, etc.) on a per-phone basis.
    """

    accepted: bool
    recipients: list[AfricasTalkingSmsRecipient] = field(default_factory=list)
    summary: str | None = None
    error: str | None = None

    def model_dump(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "recipients": [r.model_dump() for r in self.recipients],
            "summary": self.summary,
            "error": self.error,
        }


# ── Provider class ────────────────────────────────────────────────────────────

class AfricasTalkingPhoneProvider:
    """Thin HTTP client for Africa's Talking operations that *can* be automated.

    Africa's Talking has no REST API for number listing or purchasing (unlike
    Telnyx), so this class covers only the two automation-friendly operations:

    1. ``validate_credentials`` — pings the AT User Data API to confirm that
       an (``username``, ``api_key``) pair is valid and the account is active.

    2. ``check_callback_reachability`` — sends a lightweight HTTP HEAD (then
       GET on 405) to a caller-supplied webhook URL to confirm it is publicly
       reachable before persisting it to the binding record.
    """

    _AT_PRODUCTION_BASE = "https://api.africastalking.com"
    _AT_SANDBOX_BASE = "https://api.sandbox.africastalking.com"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        username: str | None = None,
        sandbox: bool = False,
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._username = username
        base = self._AT_SANDBOX_BASE if sandbox else self._AT_PRODUCTION_BASE
        self._user_api_url = f"{base}/version1/user"
        self._messaging_url = f"{base}/version1/messaging"
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._http_client = http_client

    async def validate_credentials(
        self,
        *,
        username: str | None = None,
        api_key: str | None = None,
    ) -> AfricasTalkingCredentialValidationResult:
        """Call the AT User Data API to verify (username, api_key) credentials.

        Returns a result with ``valid=True`` on success, or ``valid=False``
        with an ``error`` message if the credentials are rejected or the call
        fails.  Never raises — callers can inspect ``.valid`` and act accordingly.

        When *username* or *api_key* are omitted, the constructor-provided
        defaults are used (from ``RuntimeSettings``).
        """
        effective_username = (username or self._username or "").strip()
        effective_api_key = (api_key or self._api_key or "").strip()
        if not effective_username:
            return AfricasTalkingCredentialValidationResult(
                valid=False,
                username=effective_username,
                error="username is required",
            )
        if not effective_api_key:
            return AfricasTalkingCredentialValidationResult(
                valid=False,
                username=effective_username,
                error="api_key is required",
            )
        try:
            response = await self._get(
                self._user_api_url,
                params={"username": effective_username},
                headers={"apiKey": effective_api_key, "Accept": "application/json"},
            )
            if response.status_code == 401 or response.status_code == 403:
                return AfricasTalkingCredentialValidationResult(
                    valid=False,
                    username=username,
                    error=f"credentials rejected by Africa's Talking (HTTP {response.status_code})",
                )
            if response.status_code != 200:
                return AfricasTalkingCredentialValidationResult(
                    valid=False,
                    username=username,
                    error=f"unexpected response from Africa's Talking (HTTP {response.status_code})",
                )
            body: dict = response.json() if response.content else {}
            user_data = body.get("UserData") or {}
            balance = str(user_data.get("balance") or "")
            account_type = str(user_data.get("type") or "")
            return AfricasTalkingCredentialValidationResult(
                valid=True,
                username=username,
                account_type=account_type or None,
                balance=balance or None,
            )
        except httpx.TimeoutException:
            return AfricasTalkingCredentialValidationResult(
                valid=False,
                username=username,
                error="connection to Africa's Talking API timed out",
            )
        except Exception as exc:
            return AfricasTalkingCredentialValidationResult(
                valid=False,
                username=username,
                error=f"credential validation failed: {exc}",
            )

    async def check_callback_reachability(
        self,
        url: str,
    ) -> AfricasTalkingCallbackReachabilityResult:
        """Send a lightweight probe request to ``url`` to verify it is publicly reachable.

        Tries HEAD first (low overhead); falls back to GET if the server returns
        405 Method Not Allowed.  A 2xx or 3xx response is considered reachable
        (the handler exists and is accepting traffic — it does not need to return
        200 to a probe that carries no valid payload).

        Returns a result with ``status='reachable'`` on success.  Never raises.
        """
        if not url or not url.strip():
            return AfricasTalkingCallbackReachabilityResult(
                url=url,
                status="error",
                error="url is required",
            )
        target_url = url.strip()
        try:
            response = await self._head(target_url)
            if response.status_code == 405:
                # Server disallows HEAD — try GET
                response = await self._get(target_url, headers={"Accept": "*/*"})
            reachable = response.status_code < 500
            return AfricasTalkingCallbackReachabilityResult(
                url=target_url,
                status="reachable" if reachable else "unreachable",
                http_status_code=response.status_code,
            )
        except httpx.TimeoutException:
            return AfricasTalkingCallbackReachabilityResult(
                url=target_url,
                status="timeout",
                error="request timed out",
            )
        except Exception as exc:
            return AfricasTalkingCallbackReachabilityResult(
                url=target_url,
                status="error",
                error=str(exc),
            )

    async def send_sms(
        self,
        *,
        to: str | list[str],
        message: str,
        sender_id: str | None = None,
        username: str | None = None,
        api_key: str | None = None,
    ) -> AfricasTalkingSmsResult:
        """Send an SMS via Africa's Talking ``/version1/messaging``.

        ``to`` accepts a single E.164 phone or a list of them. AT's API
        is comma-separated under the hood; we handle the join here so
        callers don't have to.

        Returns ``AfricasTalkingSmsResult`` with per-recipient status.
        Raises:
        * ``AfricasTalkingCredentialError`` on 401/403 (caller should
          stop sending and surface to operators).
        * ``AfricasTalkingReachabilityError`` on network/timeout
          failures (caller may retry).
        Per-recipient rejections (invalid phone, blacklisted, insufficient
        balance, etc.) do NOT raise — they appear as
        ``status="rejected"`` on the relevant ``recipients`` entries so
        the caller can act per-phone.
        """
        effective_username = (username or self._username or "").strip()
        effective_api_key = (api_key or self._api_key or "").strip()
        if not effective_username:
            return AfricasTalkingSmsResult(accepted=False, error="username is required")
        if not effective_api_key:
            return AfricasTalkingSmsResult(accepted=False, error="api_key is required")
        if not message or not message.strip():
            return AfricasTalkingSmsResult(accepted=False, error="message is required")

        recipients_input = [to] if isinstance(to, str) else list(to)
        recipients_input = [str(r).strip() for r in recipients_input if str(r).strip()]
        if not recipients_input:
            return AfricasTalkingSmsResult(accepted=False, error="at least one recipient is required")

        # Validate E.164 shape on every recipient before any network call.
        # An invalid number in the list would otherwise return a 4xx with a
        # mixed-bag response that's harder to reason about than a clean
        # client-side rejection.
        invalid: list[str] = []
        normalized: list[str] = []
        for raw in recipients_input:
            try:
                normalized.append(normalize_e164_number(raw))
            except Exception:
                invalid.append(raw)
        if invalid:
            return AfricasTalkingSmsResult(
                accepted=False,
                recipients=[
                    AfricasTalkingSmsRecipient(
                        phone_number=number,
                        status="rejected",
                        error="invalid E.164 phone number",
                    )
                    for number in invalid
                ],
                error=f"invalid recipient phone numbers: {', '.join(invalid)}",
            )

        body: dict[str, str] = {
            "username": effective_username,
            "to": ",".join(normalized),
            "message": message,
        }
        if sender_id and sender_id.strip():
            body["from"] = sender_id.strip()
        headers = {
            "apiKey": effective_api_key,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        try:
            response = await self._post_form(
                self._messaging_url, data=body, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise AfricasTalkingReachabilityError(
                f"Africa's Talking SMS request timed out: {exc}",
            ) from exc
        except httpx.NetworkError as exc:
            raise AfricasTalkingReachabilityError(
                f"Africa's Talking SMS network error: {exc}",
            ) from exc

        if response.status_code in (401, 403):
            raise AfricasTalkingCredentialError(
                f"Africa's Talking rejected SMS credentials (HTTP {response.status_code})",
                status_code=response.status_code,
            )

        if response.status_code >= 500:
            return AfricasTalkingSmsResult(
                accepted=False,
                error=f"Africa's Talking server error (HTTP {response.status_code})",
            )

        if response.status_code >= 400:
            return AfricasTalkingSmsResult(
                accepted=False,
                error=f"Africa's Talking rejected SMS request (HTTP {response.status_code}): {response.text[:200]}",
            )

        # AT returns 200 or 201 on accepted requests with a JSON body.
        try:
            payload = response.json()
        except ValueError as exc:
            return AfricasTalkingSmsResult(
                accepted=False,
                error=f"Africa's Talking returned non-JSON response: {exc}",
            )

        return _parse_at_sms_response(payload, expected_recipients=normalized)

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    async def _post_form(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        async with self._client() as client:
            return await client.post(url, data=data, headers=headers or {})

    async def _head(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        async with self._client() as client:
            return await client.head(url, headers=headers or {}, follow_redirects=True)

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        async with self._client() as client:
            return await client.get(
                url,
                params=params,
                headers=headers or {},
                follow_redirects=True,
            )

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client  # type: ignore[return-value]
        return httpx.AsyncClient(timeout=self._timeout_seconds)


def _optional_string(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None


def _object(value: object | None) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


@dataclass(slots=True, frozen=True)
class AfricasTalkingBindingSnapshot:
    provider_resource_id: str
    phone_number: str
    account_username: str | None = None
    voice_callback_url: str | None = None
    events_callback_url: str | None = None
    sip_trunk_target: str | None = None
    sip_auth_required: bool = True
    credentials_reference: str | None = None
    ip_whitelist_confirmed: bool = False
    sip_forwarding_confirmed: bool = False
    configuration_confirmed: bool = False
    last_verified_at: str | None = None
    notes: str | None = None
    manual_requirements: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    raw: dict[str, object] = field(default_factory=dict)


def build_africas_talking_snapshot(
    *,
    phone_number: str,
    provider_resource_id: str | None = None,
    account_username: str | None = None,
    voice_callback_url: str | None = None,
    events_callback_url: str | None = None,
    sip_trunk_target: str | None = None,
    sip_auth_required: bool = True,
    credentials_reference: str | None = None,
    ip_whitelist_confirmed: bool = False,
    sip_forwarding_confirmed: bool = False,
    configuration_confirmed: bool = False,
    last_verified_at: str | None = None,
    notes: str | None = None,
    raw: dict[str, object] | None = None,
) -> AfricasTalkingBindingSnapshot:
    normalized_phone_number = normalize_e164_number(phone_number)
    normalized_provider_resource_id = _optional_string(provider_resource_id) or normalized_phone_number
    normalized_account_username = _optional_string(account_username)
    normalized_voice_callback_url = _optional_string(voice_callback_url)
    normalized_events_callback_url = _optional_string(events_callback_url)
    normalized_sip_trunk_target = _optional_string(sip_trunk_target)
    normalized_credentials_reference = _optional_string(credentials_reference)
    normalized_last_verified_at = _optional_string(last_verified_at)
    normalized_notes = _optional_string(notes)
    manual_requirements = _build_manual_requirements(
        account_username=normalized_account_username,
        voice_callback_url=normalized_voice_callback_url,
        sip_trunk_target=normalized_sip_trunk_target,
        sip_auth_required=bool(sip_auth_required),
        credentials_reference=normalized_credentials_reference,
        ip_whitelist_confirmed=bool(ip_whitelist_confirmed),
        sip_forwarding_confirmed=bool(sip_forwarding_confirmed),
        configuration_confirmed=bool(configuration_confirmed),
    )
    recommended_actions = _build_recommended_actions(
        events_callback_url=normalized_events_callback_url,
        sip_trunk_target=normalized_sip_trunk_target,
        last_verified_at=normalized_last_verified_at,
    )
    return AfricasTalkingBindingSnapshot(
        provider_resource_id=normalized_provider_resource_id,
        phone_number=normalized_phone_number,
        account_username=normalized_account_username,
        voice_callback_url=normalized_voice_callback_url,
        events_callback_url=normalized_events_callback_url,
        sip_trunk_target=normalized_sip_trunk_target,
        sip_auth_required=bool(sip_auth_required),
        credentials_reference=normalized_credentials_reference,
        ip_whitelist_confirmed=bool(ip_whitelist_confirmed),
        sip_forwarding_confirmed=bool(sip_forwarding_confirmed),
        configuration_confirmed=bool(configuration_confirmed),
        last_verified_at=normalized_last_verified_at,
        notes=normalized_notes,
        manual_requirements=manual_requirements,
        recommended_actions=recommended_actions,
        raw={} if raw is None else dict(raw),
    )


def parse_africas_talking_snapshot(
    value: object | None,
    *,
    phone_number: str,
    provider_resource_id: str | None = None,
) -> AfricasTalkingBindingSnapshot:
    payload = _object(value)
    return build_africas_talking_snapshot(
        phone_number=phone_number,
        provider_resource_id=_optional_string(payload.get("provider_resource_id")) or provider_resource_id,
        account_username=_optional_string(payload.get("account_username")),
        voice_callback_url=_optional_string(payload.get("voice_callback_url")),
        events_callback_url=_optional_string(payload.get("events_callback_url")),
        sip_trunk_target=_optional_string(payload.get("sip_trunk_target")),
        sip_auth_required=bool(payload.get("sip_auth_required", True)),
        credentials_reference=_optional_string(payload.get("credentials_reference")),
        ip_whitelist_confirmed=bool(payload.get("ip_whitelist_confirmed", False)),
        sip_forwarding_confirmed=bool(payload.get("sip_forwarding_confirmed", False)),
        configuration_confirmed=bool(payload.get("configuration_confirmed", False)),
        last_verified_at=_optional_string(payload.get("last_verified_at")),
        notes=_optional_string(payload.get("notes")),
        raw=payload,
    )


def africas_talking_binding_projection(snapshot: AfricasTalkingBindingSnapshot) -> dict[str, object]:
    return {
        "provider_resource_id": snapshot.provider_resource_id,
        "phone_number": snapshot.phone_number,
        "account_username": snapshot.account_username,
        "voice_callback_url": snapshot.voice_callback_url,
        "events_callback_url": snapshot.events_callback_url,
        "sip_trunk_target": snapshot.sip_trunk_target,
        "sip_auth_required": snapshot.sip_auth_required,
        "credentials_reference": snapshot.credentials_reference,
        "ip_whitelist_confirmed": snapshot.ip_whitelist_confirmed,
        "sip_forwarding_confirmed": snapshot.sip_forwarding_confirmed,
        "configuration_confirmed": snapshot.configuration_confirmed,
        "last_verified_at": snapshot.last_verified_at,
        "notes": snapshot.notes,
        "manual_requirements": list(snapshot.manual_requirements),
        "recommended_actions": list(snapshot.recommended_actions),
    }


def derive_africas_talking_binding_state(
    snapshot: AfricasTalkingBindingSnapshot,
) -> tuple[PhoneBindingVerificationStatus, PhoneBindingHealthStatus, list[str]]:
    requirement_set = set(snapshot.manual_requirements)
    if requirement_set & {
        "set_account_username",
        "configure_voice_callback_url",
        "reconcile_callback_and_sip_trunk_target",
        "record_sip_credentials_reference",
    }:
        return "manual_required", "misconfigured", ["voice_inbound"]
    if requirement_set:
        return "manual_required", "degraded", ["voice_inbound"]
    return "verified", "healthy", ["voice_inbound"]


def _build_manual_requirements(
    *,
    account_username: str | None,
    voice_callback_url: str | None,
    sip_trunk_target: str | None,
    sip_auth_required: bool,
    credentials_reference: str | None,
    ip_whitelist_confirmed: bool,
    sip_forwarding_confirmed: bool,
    configuration_confirmed: bool,
) -> list[str]:
    requirements: list[str] = []
    if account_username is None:
        requirements.append("set_account_username")
    if voice_callback_url is None:
        requirements.append("configure_voice_callback_url")
    if sip_trunk_target is not None and voice_callback_url is not None and sip_trunk_target != voice_callback_url:
        requirements.append("reconcile_callback_and_sip_trunk_target")
    if sip_auth_required and credentials_reference is None:
        requirements.append("record_sip_credentials_reference")
    if not sip_forwarding_confirmed:
        requirements.append("confirm_sip_forwarding")
    if not ip_whitelist_confirmed:
        requirements.append("confirm_ip_whitelist")
    if not configuration_confirmed:
        requirements.append("confirm_provider_configuration")
    return requirements


def _build_recommended_actions(
    *,
    events_callback_url: str | None,
    sip_trunk_target: str | None,
    last_verified_at: str | None,
) -> list[str]:
    recommendations: list[str] = []
    if events_callback_url is None:
        recommendations.append("configure_events_callback_url")
    if sip_trunk_target is None:
        recommendations.append("record_sip_trunk_target")
    if last_verified_at is None:
        recommendations.append("record_last_verified_at")
    return recommendations


# ── SMS response parsing ─────────────────────────────────────────────


def _at_status_label(status_code: int | None, raw_status: str | None) -> SmsRecipientStatus:
    """Map an AT recipient ``statusCode`` into our normalized label.

    Per AT docs: 100 Processed, 101 Sent, 102 Queued; everything else
    indicates a per-recipient rejection. ``raw_status`` (a string like
    "Success" / "InvalidPhoneNumber") is used only as a fallback when the
    status code is missing.
    """
    if isinstance(status_code, int):
        if status_code == 101:
            return "sent"
        if status_code == 102:
            return "queued"
        if status_code == 100:
            return "processed"
        if status_code in _AT_SUCCESS_STATUS_CODES:
            return "sent"
        return "rejected"
    if isinstance(raw_status, str) and raw_status.strip().lower() == "success":
        return "sent"
    return "error"


def _parse_at_sms_response(
    payload: object,
    *,
    expected_recipients: list[str],
) -> AfricasTalkingSmsResult:
    """Parse the AT ``SMSMessageData`` envelope into our typed result.

    Defensive against shape drift: every field is optional in the
    response and we never trust types without checking. When the envelope
    is missing entirely we return ``accepted=False`` with the response
    serialized in ``error`` so operators can see what happened.
    """
    if not isinstance(payload, dict):
        return AfricasTalkingSmsResult(accepted=False, error="unexpected response shape")
    envelope = payload.get("SMSMessageData")
    if not isinstance(envelope, dict):
        return AfricasTalkingSmsResult(
            accepted=False,
            error=f"missing SMSMessageData in response: {payload!r}"[:500],
        )
    summary = envelope.get("Message")
    summary_str = summary.strip() if isinstance(summary, str) else None
    raw_recipients = envelope.get("Recipients") or []
    if not isinstance(raw_recipients, list):
        raw_recipients = []

    parsed: list[AfricasTalkingSmsRecipient] = []
    seen_numbers: set[str] = set()
    for entry in raw_recipients:
        if not isinstance(entry, dict):
            continue
        phone = str(entry.get("number") or "").strip()
        if not phone:
            continue
        seen_numbers.add(phone)
        status_code_raw = entry.get("statusCode")
        status_code = int(status_code_raw) if isinstance(status_code_raw, (int, float)) else None
        raw_status = entry.get("status") if isinstance(entry.get("status"), str) else None
        label = _at_status_label(status_code, raw_status)
        message_id = entry.get("messageId") if isinstance(entry.get("messageId"), str) else None
        cost = entry.get("cost") if isinstance(entry.get("cost"), str) else None
        error: str | None = None
        if label == "rejected":
            # Surface the AT-side reason (e.g. "InvalidPhoneNumber",
            # "InsufficientBalance") in the error field so callers can
            # branch on it without re-parsing the status code.
            error = raw_status or f"AT status_code {status_code}"
        parsed.append(
            AfricasTalkingSmsRecipient(
                phone_number=phone,
                status=label,
                status_code=status_code,
                message_id=message_id,
                cost=cost,
                error=error,
            )
        )

    # If AT silently dropped a recipient (no entry returned for a number we
    # asked it to send to), record it as an error so the caller knows the
    # outcome is uncertain rather than success.
    for expected in expected_recipients:
        if expected not in seen_numbers:
            parsed.append(
                AfricasTalkingSmsRecipient(
                    phone_number=expected,
                    status="error",
                    error="recipient missing from Africa's Talking response",
                )
            )

    accepted = any(r.accepted for r in parsed)
    return AfricasTalkingSmsResult(
        accepted=accepted,
        recipients=parsed,
        summary=summary_str,
    )
