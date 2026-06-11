"""Tests for DNS-rebinding-safe remote fetching in ``atlas_provisioning``.

``_fetch_remote_text`` must connect to the IP address that was actually
validated (pinned), never letting httpx re-resolve the hostname at
connect time. The original hostname must be preserved as the ``Host``
header and as the TLS SNI / certificate-verification name
(``sni_hostname`` extension). Each redirect hop re-validates and re-pins.

All DNS resolution and HTTP transport is mocked — no real network.
"""
from __future__ import annotations

import socket
from typing import Callable

import httpx
import pytest

from ruhu.atlas_provisioning import (
    _fetch_remote_text,
    _pinned_request_for_url,
    _validate_remote_fetch_url,
    is_safe_provisioning_base_url,
)


PUBLIC_IP = "93.184.216.34"
PUBLIC_IP_2 = "8.8.8.8"
PRIVATE_IP = "10.0.0.5"


def _addrinfo(ip: str, port: int = 443) -> list:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def _patch_dns(monkeypatch: pytest.MonkeyPatch, resolver: Callable[[str], list]) -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return resolver(host)

    monkeypatch.setattr("ruhu.atlas_provisioning.socket.getaddrinfo", fake_getaddrinfo)


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Route the httpx.Client used by ``_fetch_remote_text`` through a
    MockTransport while preserving its kwargs (timeout, redirect policy)."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("ruhu.atlas_provisioning.httpx.Client", client_factory)


# ── _pinned_request_for_url unit behavior ───────────────────────────


def test_pinning_targets_validated_ip_with_original_host_and_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dns(monkeypatch, lambda host: _addrinfo(PUBLIC_IP))
    request_url, headers, extensions = _pinned_request_for_url(
        "https://docs.example.com/openapi.json"
    )
    assert request_url == f"https://{PUBLIC_IP}/openapi.json"
    assert headers == {"Host": "docs.example.com"}
    assert extensions == {"sni_hostname": "docs.example.com"}


def test_pinning_preserves_explicit_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dns(monkeypatch, lambda host: _addrinfo(PUBLIC_IP, 8443))
    request_url, headers, extensions = _pinned_request_for_url(
        "https://docs.example.com:8443/spec"
    )
    assert request_url == f"https://{PUBLIC_IP}:8443/spec"
    assert headers == {"Host": "docs.example.com:8443"}
    assert extensions == {"sni_hostname": "docs.example.com"}


def test_pinning_skips_sni_extension_for_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dns(monkeypatch, lambda host: _addrinfo(PUBLIC_IP, 80))
    request_url, headers, extensions = _pinned_request_for_url("http://docs.example.com/spec")
    assert request_url == f"http://{PUBLIC_IP}/spec"
    assert headers == {"Host": "docs.example.com"}
    assert extensions == {}


def test_pinning_passes_literal_ip_urls_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_dns(host):  # literal IPs must never be resolved
        raise AssertionError("getaddrinfo must not be called for a literal IP URL")

    _patch_dns(monkeypatch, no_dns)
    url = f"https://{PUBLIC_IP}/openapi.json"
    assert _pinned_request_for_url(url) == (url, {}, {})


def test_pinning_brackets_ipv6_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    ipv6 = "2606:2800:220:1:248:1893:25c8:1946"
    _patch_dns(
        monkeypatch,
        lambda host: [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ipv6, 443, 0, 0))],
    )
    request_url, headers, extensions = _pinned_request_for_url("https://docs.example.com/spec")
    assert request_url == f"https://[{ipv6}]/spec"
    assert headers == {"Host": "docs.example.com"}
    assert extensions == {"sni_hostname": "docs.example.com"}


def test_pinning_rejects_any_private_address_in_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dns(
        monkeypatch,
        lambda host: _addrinfo(PUBLIC_IP) + _addrinfo(PRIVATE_IP),
    )
    with pytest.raises(ValueError, match="non-public address"):
        _pinned_request_for_url("https://docs.example.com/spec")


def test_pinning_fails_closed_when_resolution_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation resolved this name a moment ago; a sudden failure here is
    the rebinding signal itself, so pinning must fail closed rather than let
    httpx re-resolve the name at connect time."""

    def failing(host):
        raise OSError("temporary failure in name resolution")

    _patch_dns(monkeypatch, failing)
    with pytest.raises(ValueError, match="could not be pinned"):
        _pinned_request_for_url("https://docs.example.com/spec")


def test_pinning_fails_closed_on_empty_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dns(monkeypatch, lambda host: [])
    with pytest.raises(ValueError, match="did not resolve to a pinnable address"):
        _pinned_request_for_url("https://docs.example.com/spec")


# ── _validate_remote_fetch_url literal-IPv6 handling ─────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://[::1]/spec",  # IPv6 loopback
        "http://[::ffff:10.0.0.5]/spec",  # IPv4-mapped private
        "http://[fe80::1]/spec",  # link-local
        "http://[fd00::1]/spec",  # unique-local
    ],
)
def test_validate_rejects_private_ipv6_literals(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    def no_dns(host, *args, **kwargs):  # literal IPs must never be resolved
        raise AssertionError("getaddrinfo must not be called for a literal IP URL")

    monkeypatch.setattr("ruhu.atlas_provisioning.socket.getaddrinfo", no_dns)
    with pytest.raises(ValueError, match="non-public address"):
        _validate_remote_fetch_url(url)


def test_validate_allows_public_ipv6_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_dns(host, *args, **kwargs):
        raise AssertionError("getaddrinfo must not be called for a literal IP URL")

    monkeypatch.setattr("ruhu.atlas_provisioning.socket.getaddrinfo", no_dns)
    url = "https://[2606:2800:220:1:248:1893:25c8:1946]/spec"
    assert _validate_remote_fetch_url(url) == url


# ── is_safe_provisioning_base_url (no DNS) ───────────────────────────


@pytest.mark.parametrize(
    "candidate",
    [
        "https://api.example.com",
        "https://api.example.com/v1",
        "http://api.example.com:8080/v2",
        "https://{region}.api.example.com/v1",  # templated host, judged on suffix
        "",  # absent base_url is acceptable
        None,
    ],
)
def test_base_url_safe_values(candidate) -> None:
    assert is_safe_provisioning_base_url(candidate) is True


@pytest.mark.parametrize(
    "candidate",
    [
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://127.0.0.1/v1",  # loopback
        "http://10.0.0.5/api",  # RFC1918
        "http://[::1]/v1",  # IPv6 loopback
        "http://localhost/v1",
        "http://api.internal/v1",  # blocked suffix
        "ftp://api.example.com/v1",  # non-http scheme
        "https://user:pass@api.example.com/v1",  # embedded credentials
        "not a url at all",
    ],
)
def test_base_url_unsafe_values(candidate) -> None:
    assert is_safe_provisioning_base_url(candidate) is False


# ── _fetch_remote_text end-to-end (mocked transport) ─────────────────


def test_fetch_connects_to_pinned_ip_with_host_header_and_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dns(monkeypatch, lambda host: _addrinfo(PUBLIC_IP))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, text='{"openapi": "3.0.0"}', headers={"content-type": "application/json"}
        )

    _patch_transport(monkeypatch, handler)
    body, content_type = _fetch_remote_text("https://docs.example.com/openapi.json")

    assert body == '{"openapi": "3.0.0"}'
    assert content_type == "application/json"
    assert len(seen) == 1
    request = seen[0]
    assert request.url.host == PUBLIC_IP  # connected to the validated IP
    assert request.headers["host"] == "docs.example.com"
    assert request.extensions.get("sni_hostname") == "docs.example.com"


def test_fetch_blocks_rebinding_between_validation_and_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate a rebinding DNS server: first resolution (validation) is
    public, second resolution flips to a private address. The connection
    must NOT proceed to the private address."""
    answers = iter([_addrinfo(PUBLIC_IP), _addrinfo(PRIVATE_IP)])
    _patch_dns(monkeypatch, lambda host: next(answers))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no connection may be made when rebinding is detected")

    _patch_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="non-public address"):
        _fetch_remote_text("https://docs.example.com/openapi.json")


def test_fetch_pins_each_redirect_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    ips = {"docs.example.com": PUBLIC_IP, "spec.example.org": PUBLIC_IP_2}
    _patch_dns(monkeypatch, lambda host: _addrinfo(ips[host]))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers["host"] == "docs.example.com":
            return httpx.Response(
                302, headers={"location": "https://spec.example.org/openapi.json"}
            )
        return httpx.Response(200, text="ok-body", headers={"content-type": "text/plain"})

    _patch_transport(monkeypatch, handler)
    body, _ = _fetch_remote_text("https://docs.example.com/start")

    assert body == "ok-body"
    assert [str(request.url.host) for request in seen] == [PUBLIC_IP, PUBLIC_IP_2]
    assert [request.headers["host"] for request in seen] == [
        "docs.example.com",
        "spec.example.org",
    ]
    assert [request.extensions.get("sni_hostname") for request in seen] == [
        "docs.example.com",
        "spec.example.org",
    ]


def test_fetch_rejects_redirect_to_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    ips = {"docs.example.com": PUBLIC_IP, "internal.example.net": PRIVATE_IP}
    _patch_dns(monkeypatch, lambda host: _addrinfo(ips[host]))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["host"] == "docs.example.com"
        return httpx.Response(
            302, headers={"location": "https://internal.example.net/steal"}
        )

    _patch_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="non-public address"):
        _fetch_remote_text("https://docs.example.com/start")
