"""SSRF protection for outbound HTTP tool requests.

Three-layer defence:

1. **Scheme allowlist** — only ``http`` and ``https`` are permitted.
2. **Hostname blocklist** — rejects ``localhost``, link-local hostnames, and
   cloud metadata endpoints *before* DNS resolution, preventing DNS-rebind
   attacks that resolve a public hostname to a private IP.
3. **IP blocklist** — resolves the hostname via ``socket.getaddrinfo`` and
   rejects any address in a private, loopback, link-local, reserved, or
   cloud-metadata range.  IPv4-mapped IPv6 addresses (``::ffff:10.0.0.1``)
   are unwrapped before checking.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# ── Blocked networks ────────────────────────────────────────────────────────────

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # RFC 1918 private
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    # Loopback
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv6Network("::1/128"),
    # Link-local
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv6Network("fe80::/10"),
    # "This" network
    ipaddress.IPv4Network("0.0.0.0/8"),
    # Reserved / future-use
    ipaddress.IPv4Network("240.0.0.0/4"),
    # Shared address space (carrier-grade NAT, cloud internals)
    ipaddress.IPv4Network("100.64.0.0/10"),
    # IPv6 unique-local
    ipaddress.IPv6Network("fc00::/7"),
    # Cloud metadata endpoint (single address)
    ipaddress.IPv4Network("169.254.169.254/32"),
]

_BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.internal",
})

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


class SSRFBlockedError(ValueError):
    """Raised when a URL targets a blocked destination."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"SSRF blocked: {reason}")
        self.url = url
        self.reason = reason


def validate_url(url: str) -> str:
    """Validate *url* against SSRF rules and return the cleaned URL.

    Raises ``SSRFBlockedError`` if the URL targets a blocked scheme, hostname,
    or IP address.  The returned URL is the original input (no rewriting).
    """
    parsed = urlparse(url)

    # Layer 1: Scheme
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SSRFBlockedError(url, f"scheme '{scheme}' is not allowed")

    # Layer 2: Hostname blocklist (pre-DNS)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise SSRFBlockedError(url, "missing hostname")
    if hostname in _BLOCKED_HOSTNAMES:
        raise SSRFBlockedError(url, f"hostname '{hostname}' is blocked")

    # Layer 3: DNS resolution + IP blocklist
    try:
        addrinfo = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise SSRFBlockedError(url, f"DNS resolution failed for '{hostname}'")

    for family, _, _, _, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        addr = ipaddress.ip_address(ip_str)

        # Unwrap IPv4-mapped IPv6 (::ffff:10.0.0.1 → 10.0.0.1)
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped

        for network in _BLOCKED_NETWORKS:
            if addr in network:
                raise SSRFBlockedError(
                    url,
                    f"resolved IP {ip_str} falls in blocked network {network}",
                )

    return url
