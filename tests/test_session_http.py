from __future__ import annotations

from starlette.requests import Request

from ruhu.session_http import build_session_audit_context, request_uses_secure_cookies


def _request(*, scheme: str = "http", client: tuple[str, int] | None = ("127.0.0.1", 1234), headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": scheme,
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in (headers or {}).items()
            ],
            "client": client,
            "server": ("testserver", 80),
        }
    )


def test_request_uses_secure_cookies_only_from_trusted_scheme() -> None:
    insecure = _request(scheme="http", headers={"X-Forwarded-Proto": "https"})
    secure = _request(scheme="https")

    assert request_uses_secure_cookies(insecure) is False
    assert request_uses_secure_cookies(secure) is True


def test_session_audit_context_ignores_untrusted_forwarded_for_header() -> None:
    request = _request(
        headers={
            "X-Forwarded-For": "203.0.113.10",
            "User-Agent": "Widget Browser",
        }
    )

    audit = build_session_audit_context(request)

    assert audit.ip == "127.0.0.1"
    assert audit.user_agent == "Widget Browser"
