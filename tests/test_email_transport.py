from __future__ import annotations

from ruhu.email_transport import (
    DevOutboxEmailSender,
    EmailDeliveryResult,
    EmailMessage,
    EmailTransportError,
    RetryingEmailSender,
    SmtpEmailSender,
    build_email_sender,
    redact_email_secrets,
)
from ruhu.runtime_config import RuntimeSettings


def test_redact_email_secrets_masks_token_query_params() -> None:
    redacted = redact_email_secrets(
        "https://app.example.com/accept-invitation?token=abc123&next=/login invite=xyz789"
    )
    assert "abc123" not in redacted
    assert "xyz789" not in redacted
    assert "token=[redacted]" in redacted
    assert "invite=[redacted]" in redacted


def test_dev_outbox_sender_stores_message_and_returns_outbox_id() -> None:
    sender = DevOutboxEmailSender()
    delivery = sender.send(
        EmailMessage(
            to_email="user@example.com",
            subject="Sign in",
            html_content='<a href="https://app.example.com/auth/magic-link?token=abc123">Sign in</a>',
            text_content="https://app.example.com/auth/magic-link?token=abc123",
            metadata={"kind": "magic_link", "token": "abc123"},
        )
    )
    assert delivery.transport == "dev_outbox"
    assert delivery.outbox_entry_id is not None
    assert len(sender.entries) == 1
    assert "abc123" in sender.entries[0].html_content


def test_build_email_sender_defaults_to_dev_outbox_without_smtp_host() -> None:
    sender = build_email_sender(RuntimeSettings())
    assert isinstance(sender, DevOutboxEmailSender)


def test_build_email_sender_wraps_smtp_with_retry_sender() -> None:
    sender = build_email_sender(
        RuntimeSettings(
            smtp_host="smtp.example.com",
            smtp_user="smtp-user",
            smtp_password="smtp-password",
        )
    )
    assert isinstance(sender, RetryingEmailSender)


def test_smtp_email_sender_delivers_multipart_message(monkeypatch) -> None:
    events: list[object] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            events.append(("connect", host, port, timeout))

        def __enter__(self) -> "FakeSMTP":
            events.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            events.append("exit")

        def starttls(self) -> None:
            events.append("starttls")

        def login(self, username: str, password: str) -> None:
            events.append(("login", username, password))

        def send_message(self, message) -> None:
            events.append(("send", message["To"], message["Subject"], message["From"]))

    monkeypatch.setattr("ruhu.email_transport.smtplib.SMTP", FakeSMTP)

    sender = SmtpEmailSender(
        host="smtp.example.com",
        port=587,
        from_email="noreply@example.com",
        from_name="Ruhu",
        username="smtp-user",
        password="smtp-pass",
        use_starttls=True,
    )
    delivery = sender.send(
        EmailMessage(
            to_email="user@example.com",
            subject="Invitation",
            html_content="<p>Hello</p>",
            text_content="Hello",
        )
    )

    assert delivery.transport == "smtp"
    assert delivery.message_id.startswith("<")
    assert ("connect", "smtp.example.com", 587, 10.0) in events
    assert "starttls" in events
    assert ("login", "smtp-user", "smtp-pass") in events
    assert ("send", "user@example.com", "Invitation", "Ruhu <noreply@example.com>") in events


def test_retrying_email_sender_queues_then_marks_sent_after_retry(monkeypatch) -> None:
    attempts = {"count": 0}

    class FlakySmtpSender(SmtpEmailSender):
        def __init__(self) -> None:
            pass

        def send(self, message: EmailMessage):  # type: ignore[override]
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise EmailTransportError("temporary smtp failure")
            return EmailDeliveryResult(transport="smtp", message_id="smtp-message-2")

    monkeypatch.setattr("ruhu.email_transport.time.sleep", lambda _seconds: None)

    sender = RetryingEmailSender(FlakySmtpSender(), retry_delays_seconds=(0.0,))
    result = sender.send(
        EmailMessage(
            to_email="user@example.com",
            subject="Invitation",
            html_content="<p>Hello</p>",
            text_content="Hello",
        )
    )

    assert result.status == "queued"
    assert result.delivery_id is not None

    for _ in range(100):
        state = sender.get_delivery(result.delivery_id)
        if state is not None and state.status == "sent":
            break
    else:  # pragma: no cover - defensive timeout path
        raise AssertionError("delivery did not reach sent state")

    state = sender.get_delivery(result.delivery_id)
    assert state is not None
    assert state.status == "sent"
    assert state.attempt_count == 2
