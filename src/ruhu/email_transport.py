from __future__ import annotations

import hashlib
import logging
import re
import smtplib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Literal, Protocol
from uuid import uuid4

import httpx

from .runtime_config import RuntimeSettings

logger = logging.getLogger(__name__)

_TOKEN_QUERY_PATTERN = re.compile(r"(?P<key>(?:token|invite)=)(?P<value>[^&#\\s\"'<>]+)", re.IGNORECASE)
_SECRET_METADATA_MARKERS = ("token", "secret", "password")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def redact_email_secrets(value: str) -> str:
    return _TOKEN_QUERY_PATTERN.sub(lambda match: f"{match.group('key')}[redacted]", value)


def _redact_metadata(metadata: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = key.strip().lower()
        if any(marker in normalized_key for marker in _SECRET_METADATA_MARKERS):
            redacted[key] = "[redacted]"
        else:
            redacted[key] = value
    return redacted


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to_email: str
    subject: str
    html_content: str
    text_content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


EmailTransport = Literal["smtp", "dev_outbox", "resend"]


@dataclass(frozen=True, slots=True)
class EmailDeliveryResult:
    transport: EmailTransport
    message_id: str
    outbox_entry_id: str | None = None
    delivery_id: str | None = None
    status: Literal["sent", "queued", "failed"] = "sent"
    attempt_count: int = 1


@dataclass(frozen=True, slots=True)
class EmailDeliveryState:
    delivery_id: str
    transport: EmailTransport
    to_email: str
    subject: str
    status: Literal["sent", "queued", "failed"]
    attempt_count: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None = None
    last_error: str | None = None
    outbox_entry_id: str | None = None


@dataclass(frozen=True, slots=True)
class DevOutboxEntry:
    entry_id: str
    created_at: datetime
    to_email: str
    subject: str
    html_content: str
    text_content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class EmailTransportError(Exception):
    """Transient or unclassified transport failure. Eligible for retry."""


class EmailTransportPermanentError(EmailTransportError):
    """Non-retryable transport failure (validation, auth, suppressed recipient).

    Raised when the upstream provider returns a 4xx that will not succeed
    on retry — e.g. invalid recipient, bad API key, recipient on suppression
    list. RetryingEmailSender treats this as terminal and stops retrying.
    """


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> EmailDeliveryResult: ...


class SmtpEmailSender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_email: str,
        from_name: str,
        username: str | None = None,
        password: str | None = None,
        use_starttls: bool = True,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not host.strip():
            raise ValueError("SMTP host is required")
        if not from_email.strip():
            raise ValueError("SMTP from_email is required")
        self.host = host.strip()
        self.port = port
        self.from_email = from_email.strip()
        self.from_name = from_name.strip() or self.from_email
        self.username = None if username is None else username.strip() or None
        self.password = None if password is None else password.strip() or None
        self.use_starttls = use_starttls
        self.timeout_seconds = timeout_seconds

    def send(self, message: EmailMessage) -> EmailDeliveryResult:
        message_id = f"<{uuid4()}@ruhu.local>"
        email_message = MIMEMultipart("alternative")
        email_message["Subject"] = message.subject
        email_message["From"] = f"{self.from_name} <{self.from_email}>"
        email_message["To"] = message.to_email
        email_message["Message-ID"] = message_id

        if message.text_content:
            email_message.attach(MIMEText(message.text_content, "plain"))
        email_message.attach(MIMEText(message.html_content, "html"))

        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as server:
                if self.use_starttls:
                    server.starttls()
                if self.username is not None and self.password is not None:
                    server.login(self.username, self.password)
                server.send_message(email_message)
        except Exception as exc:  # pragma: no cover - network failure path
            raise EmailTransportError(f"SMTP email send failed: {exc}") from exc

        logger.info("smtp_email_sent to=%s subject=%s", message.to_email, message.subject)
        return EmailDeliveryResult(transport="smtp", message_id=message_id)


class ResendEmailSender:
    """Production transport using the Resend HTTP API (https://resend.com).

    Posts to ``POST https://api.resend.com/emails`` with bearer auth.
    Sends a stable Idempotency-Key derived from the message contents so that
    retries — whether ours or a network-induced replay — collapse to a single
    delivery on Resend's side.

    Errors are classified:
      * 4xx (validation, auth, suppression) → ``EmailTransportPermanentError``
      * 5xx, 429, network/timeout → ``EmailTransportError`` (retryable)
    """

    DEFAULT_BASE_URL = "https://api.resend.com"

    def __init__(
        self,
        *,
        api_key: str,
        from_email: str,
        from_name: str,
        timeout_seconds: float = 10.0,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Resend api_key is required")
        if not from_email.strip():
            raise ValueError("Resend from_email is required")
        self._api_key = api_key.strip()
        self.from_email = from_email.strip()
        self.from_name = from_name.strip() or self.from_email
        self.timeout_seconds = timeout_seconds
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._client = client

    def _idempotency_key(self, message: EmailMessage) -> str:
        digest = hashlib.sha256()
        digest.update(message.to_email.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(message.subject.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(message.html_content.encode("utf-8"))
        if message.text_content:
            digest.update(b"\x1f")
            digest.update(message.text_content.encode("utf-8"))
        return digest.hexdigest()

    def _post(self, payload: dict[str, object], *, idempotency_key: str) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Idempotency-Key": idempotency_key,
        }
        url = f"{self._base_url}/emails"
        if self._client is not None:
            return self._client.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.post(url, json=payload, headers=headers)

    def send(self, message: EmailMessage) -> EmailDeliveryResult:
        from_header = f"{self.from_name} <{self.from_email}>"
        payload: dict[str, object] = {
            "from": from_header,
            "to": [message.to_email],
            "subject": message.subject,
            "html": message.html_content,
        }
        if message.text_content:
            payload["text"] = message.text_content

        idempotency_key = self._idempotency_key(message)
        try:
            response = self._post(payload, idempotency_key=idempotency_key)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
            raise EmailTransportError(f"Resend network error: {exc}") from exc

        if response.status_code >= 500 or response.status_code == 429:
            raise EmailTransportError(
                f"Resend transient error: {response.status_code} {response.text[:200]}"
            )
        if response.status_code >= 400:
            raise EmailTransportPermanentError(
                f"Resend permanent error: {response.status_code} {response.text[:200]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise EmailTransportError(f"Resend response not JSON: {exc}") from exc
        message_id = str(body.get("id") or f"<{uuid4()}@resend.local>")

        logger.info(
            "resend_email_sent to=%s subject=%s message_id=%s",
            message.to_email,
            message.subject,
            message_id,
        )
        return EmailDeliveryResult(transport="resend", message_id=message_id)


class DevOutboxEmailSender:
    def __init__(self) -> None:
        self.entries: list[DevOutboxEntry] = []

    def send(self, message: EmailMessage) -> EmailDeliveryResult:
        entry = DevOutboxEntry(
            entry_id=str(uuid4()),
            created_at=_utc_now(),
            to_email=message.to_email,
            subject=message.subject,
            html_content=message.html_content,
            text_content=message.text_content,
            metadata=dict(message.metadata),
        )
        self.entries.append(entry)

        redacted_html = redact_email_secrets(message.html_content)
        redacted_text = None if message.text_content is None else redact_email_secrets(message.text_content)
        logger.info(
            "dev_email_outbox_entry to=%s subject=%s metadata=%s html=%s text=%s",
            message.to_email,
            message.subject,
            _redact_metadata(message.metadata),
            redacted_html,
            redacted_text,
        )
        return EmailDeliveryResult(
            transport="dev_outbox",
            message_id=entry.entry_id,
            outbox_entry_id=entry.entry_id,
            delivery_id=entry.entry_id,
        )


class RetryingEmailSender:
    def __init__(
        self,
        inner: EmailSender,
        *,
        retry_delays_seconds: tuple[float, ...] = (1.0, 5.0),
    ) -> None:
        self.inner = inner
        self.retry_delays_seconds = retry_delays_seconds
        self.transport = _transport_for_sender(inner)
        self._lock = threading.Lock()
        self._deliveries: dict[str, EmailDeliveryState] = {}

    @property
    def deliveries(self) -> dict[str, EmailDeliveryState]:
        with self._lock:
            return dict(self._deliveries)

    def get_delivery(self, delivery_id: str) -> EmailDeliveryState | None:
        with self._lock:
            return self._deliveries.get(delivery_id)

    def send(self, message: EmailMessage) -> EmailDeliveryResult:
        delivery_id = str(uuid4())
        created_at = _utc_now()
        max_attempts = 1 + len(self.retry_delays_seconds)
        try:
            result = self.inner.send(message)
        except EmailTransportError as exc:
            permanent = isinstance(exc, EmailTransportPermanentError)
            should_retry = bool(self.retry_delays_seconds) and not permanent
            initial_state = EmailDeliveryState(
                delivery_id=delivery_id,
                transport=self.transport,
                to_email=message.to_email,
                subject=message.subject,
                status="queued" if should_retry else "failed",
                attempt_count=1,
                max_attempts=max_attempts,
                created_at=created_at,
                updated_at=created_at,
                last_error=str(exc),
            )
            with self._lock:
                self._deliveries[delivery_id] = initial_state
            if should_retry:
                thread = threading.Thread(
                    target=self._retry_delivery,
                    args=(delivery_id, message, created_at),
                    daemon=True,
                )
                thread.start()
                logger.warning(
                    "email_delivery_queued delivery_id=%s to=%s subject=%s error=%s",
                    delivery_id,
                    message.to_email,
                    message.subject,
                    str(exc),
                )
                return EmailDeliveryResult(
                    transport=self.transport,
                    message_id=delivery_id,
                    delivery_id=delivery_id,
                    status="queued",
                    attempt_count=1,
                )
            logger.error(
                "email_delivery_failed delivery_id=%s to=%s subject=%s error=%s",
                delivery_id,
                message.to_email,
                message.subject,
                str(exc),
            )
            return EmailDeliveryResult(
                transport=self.transport,
                message_id=delivery_id,
                delivery_id=delivery_id,
                status="failed",
                attempt_count=1,
            )
        state = EmailDeliveryState(
            delivery_id=delivery_id,
            transport=result.transport,
            to_email=message.to_email,
            subject=message.subject,
            status="sent",
            attempt_count=1,
            max_attempts=max_attempts,
            created_at=created_at,
            updated_at=_utc_now(),
            sent_at=_utc_now(),
            outbox_entry_id=result.outbox_entry_id,
        )
        with self._lock:
            self._deliveries[delivery_id] = state
        return EmailDeliveryResult(
            transport=result.transport,
            message_id=result.message_id,
            outbox_entry_id=result.outbox_entry_id,
            delivery_id=delivery_id,
            status="sent",
            attempt_count=1,
        )

    def _retry_delivery(self, delivery_id: str, message: EmailMessage, created_at: datetime) -> None:
        max_attempts = 1 + len(self.retry_delays_seconds)
        attempt_count = 1
        for delay in self.retry_delays_seconds:
            time.sleep(delay)
            attempt_count += 1
            try:
                result = self.inner.send(message)
            except EmailTransportError as exc:
                permanent = isinstance(exc, EmailTransportPermanentError)
                if permanent:
                    status: Literal["queued", "failed"] = "failed"
                else:
                    status = "queued" if attempt_count < max_attempts else "failed"
                state = EmailDeliveryState(
                    delivery_id=delivery_id,
                    transport=self.transport,
                    to_email=message.to_email,
                    subject=message.subject,
                    status=status,
                    attempt_count=attempt_count,
                    max_attempts=max_attempts,
                    created_at=created_at,
                    updated_at=_utc_now(),
                    last_error=str(exc),
                )
                with self._lock:
                    self._deliveries[delivery_id] = state
                logger.warning(
                    "email_delivery_retry_failed delivery_id=%s attempt=%s/%s to=%s subject=%s permanent=%s error=%s",
                    delivery_id,
                    attempt_count,
                    max_attempts,
                    message.to_email,
                    message.subject,
                    permanent,
                    str(exc),
                )
                if permanent:
                    return
                continue

            state = EmailDeliveryState(
                delivery_id=delivery_id,
                transport=result.transport,
                to_email=message.to_email,
                subject=message.subject,
                status="sent",
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                created_at=created_at,
                updated_at=_utc_now(),
                sent_at=_utc_now(),
                outbox_entry_id=result.outbox_entry_id,
            )
            with self._lock:
                self._deliveries[delivery_id] = state
            logger.info(
                "email_delivery_retry_succeeded delivery_id=%s attempt=%s/%s to=%s subject=%s",
                delivery_id,
                attempt_count,
                max_attempts,
                message.to_email,
                message.subject,
            )
            return


def _transport_for_sender(sender: EmailSender) -> EmailTransport:
    if isinstance(sender, DevOutboxEmailSender):
        return "dev_outbox"
    if isinstance(sender, ResendEmailSender):
        return "resend"
    return "smtp"


def _build_resend_sender(settings: RuntimeSettings) -> ResendEmailSender:
    return ResendEmailSender(
        api_key=settings.resend_api_key or "",
        from_email=settings.resend_from_email or settings.smtp_from_email,
        from_name=settings.resend_from_name or settings.smtp_from_name,
        timeout_seconds=settings.resend_timeout_seconds,
    )


def _build_smtp_sender(settings: RuntimeSettings) -> SmtpEmailSender:
    if not settings.smtp_host:
        raise ValueError("SMTP host is required when email_provider=smtp")
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        from_email=settings.smtp_from_email,
        from_name=settings.smtp_from_name,
        use_starttls=settings.smtp_starttls,
    )


def build_email_sender(settings: RuntimeSettings) -> EmailSender:
    """Build the active email sender according to ``settings.email_provider``.

    Selection rules:
      * Explicit override: ``RUHU_EMAIL_PROVIDER=resend|smtp|dev`` wins.
      * Auto: Resend (if ``RUHU_RESEND_API_KEY`` set) →
        SMTP (if ``RUHU_SMTP_HOST`` set) → DevOutbox.

    Production senders are wrapped in ``RetryingEmailSender`` so transient
    failures get a background retry; permanent (4xx-class) failures fail
    fast and are not retried.
    """
    provider = (settings.email_provider or "auto").strip().lower()

    if provider == "dev":
        return DevOutboxEmailSender()
    if provider == "resend":
        return RetryingEmailSender(_build_resend_sender(settings))
    if provider == "smtp":
        return RetryingEmailSender(_build_smtp_sender(settings))
    if provider not in ("auto", ""):
        raise ValueError(
            f"Unknown email_provider {provider!r}; expected one of: auto, resend, smtp, dev"
        )

    # auto-select
    if settings.resend_api_key:
        return RetryingEmailSender(_build_resend_sender(settings))
    if settings.smtp_host:
        return RetryingEmailSender(_build_smtp_sender(settings))
    return DevOutboxEmailSender()
