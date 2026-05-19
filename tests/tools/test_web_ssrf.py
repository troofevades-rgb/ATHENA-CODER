"""SSRF defense tests for athena.tools.web.

These tests do not require network access — DNS resolution is
monkeypatched via ``athena.safety.url_safety.socket.getaddrinfo``.

The project's approval callback signature is
``(tool_name: str, args: dict) -> "allow"|"deny"``; tests use that
shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from athena.safety.approval_callback import (
    reset_approval_callback,
    set_approval_callback,
)
from athena.safety.url_safety import (
    URLSecurityDenied,
    allow_external_urls,
    validate_url,
)


def _allow_cb(*_args: Any, **_kwargs: Any) -> str:
    return "allow"


def _deny_cb(*_args: Any, **_kwargs: Any) -> str:
    return "deny"


def _stub_dns(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, list[str]]) -> None:
    """Make ``socket.getaddrinfo`` return fixed IPs for known hosts."""

    def fake_getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> list:
        if host not in mapping:
            import socket as _socket

            raise _socket.gaierror(f"unknown host: {host}")
        return [(None, None, None, None, (ip, port or 0)) for ip in mapping[host]]

    monkeypatch.setattr(
        "athena.safety.url_safety.socket.getaddrinfo",
        fake_getaddrinfo,
    )


# ---------------------------------------------------------------------------
# Scheme checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com:70/",
        "jar:file:///some.jar!/inner",
        "dict://localhost:11211/stats",
    ],
)
def test_non_http_schemes_blocked(url: str) -> None:
    with pytest.raises(URLSecurityDenied, match="scheme"):
        validate_url(url)


def test_http_scheme_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    result = validate_url("http://example.com/")
    assert result.scheme == "http"


def test_https_scheme_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    result = validate_url("https://example.com/")
    assert result.scheme == "https"


# ---------------------------------------------------------------------------
# IPv4 block list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",
        "169.254.1.1",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
    ],
)
def test_ipv4_block_list(ip: str) -> None:
    token = set_approval_callback(_deny_cb)
    try:
        with pytest.raises(URLSecurityDenied):
            validate_url(f"http://{ip}/")
    finally:
        reset_approval_callback(token)


# ---------------------------------------------------------------------------
# IPv6 block list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "::1",
        "fe80::1",
        "fc00::1",
        "fd00::1",
        "fd00:ec2::254",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_ipv6_block_list(ip: str) -> None:
    token = set_approval_callback(_deny_cb)
    try:
        with pytest.raises(URLSecurityDenied):
            validate_url(f"http://[{ip}]/")
    finally:
        reset_approval_callback(token)


# ---------------------------------------------------------------------------
# Hostname resolution
# ---------------------------------------------------------------------------


def test_hostname_resolving_to_blocked_ip_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dns(monkeypatch, {"evil.example.com": ["127.0.0.1"]})
    token = set_approval_callback(_deny_cb)
    try:
        with pytest.raises(URLSecurityDenied):
            validate_url("http://evil.example.com/")
    finally:
        reset_approval_callback(token)


def test_hostname_resolving_to_public_ip_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    result = validate_url("http://example.com/")
    assert result.resolved_ip == "93.184.216.34"


def test_hostname_resolving_to_mixed_ips_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ANY resolved IP is blocked, the URL is denied (conservative)."""
    _stub_dns(monkeypatch, {"mixed.example.com": ["93.184.216.34", "127.0.0.1"]})
    token = set_approval_callback(_deny_cb)
    try:
        with pytest.raises(URLSecurityDenied):
            validate_url("http://mixed.example.com/")
    finally:
        reset_approval_callback(token)


# ---------------------------------------------------------------------------
# DNS failure
# ---------------------------------------------------------------------------


def test_dns_failure_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {})
    with pytest.raises(URLSecurityDenied, match="DNS resolution failed"):
        validate_url("http://nonexistent.example/")


# ---------------------------------------------------------------------------
# allow_external_urls
# ---------------------------------------------------------------------------


def test_allow_external_urls_bypasses_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {"local.test": ["127.0.0.1"]})
    token = set_approval_callback(_deny_cb)
    try:
        with allow_external_urls():
            result = validate_url("http://local.test/")
            assert result.resolved_ip == "127.0.0.1"
    finally:
        reset_approval_callback(token)


def test_allow_external_urls_does_not_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dns(monkeypatch, {"local.test": ["127.0.0.1"]})
    token = set_approval_callback(_deny_cb)
    try:
        with allow_external_urls():
            validate_url("http://local.test/")
        with pytest.raises(URLSecurityDenied):
            validate_url("http://local.test/")
    finally:
        reset_approval_callback(token)


# ---------------------------------------------------------------------------
# Approval-callback override
# ---------------------------------------------------------------------------


def test_approval_callback_can_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {"local.test": ["127.0.0.1"]})
    token = set_approval_callback(_allow_cb)
    try:
        result = validate_url("http://local.test/")
        assert result.resolved_ip == "127.0.0.1"
    finally:
        reset_approval_callback(token)


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def test_approved_block_logs_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {"local.test": ["127.0.0.1"]})
    audit_calls: list[dict[str, Any]] = []

    def fake_append(*, kind: str, payload: dict[str, Any]) -> None:
        audit_calls.append({"kind": kind, "payload": payload})

    monkeypatch.setattr("athena.safety.url_safety.audit_append", fake_append)

    token = set_approval_callback(_allow_cb)
    try:
        validate_url("http://local.test/")
    finally:
        reset_approval_callback(token)

    assert any(c["kind"] == "url_security_approval" for c in audit_calls)


def test_allow_external_logs_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {"local.test": ["127.0.0.1"]})
    audit_calls: list[dict[str, Any]] = []

    def fake_append(*, kind: str, payload: dict[str, Any]) -> None:
        audit_calls.append({"kind": kind, "payload": payload})

    monkeypatch.setattr("athena.safety.url_safety.audit_append", fake_append)

    with allow_external_urls():
        validate_url("http://local.test/")

    assert any(c["kind"] == "url_security_allow_external_bypass" for c in audit_calls)


# ---------------------------------------------------------------------------
# Integration with web.py
# ---------------------------------------------------------------------------


def test_web_fetch_blocks_cloud_metadata() -> None:
    from athena.tools import web

    token = set_approval_callback(_deny_cb)
    try:
        with pytest.raises(URLSecurityDenied):
            web.WebFetch("http://169.254.169.254/latest/meta-data/")
    finally:
        reset_approval_callback(token)


def test_web_fetch_blocks_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    from athena.tools import web

    _stub_dns(monkeypatch, {"localhost": ["127.0.0.1"]})
    token = set_approval_callback(_deny_cb)
    try:
        with pytest.raises(URLSecurityDenied):
            web.WebFetch("http://localhost:8080/admin")
    finally:
        reset_approval_callback(token)


def test_web_fetch_blocks_rfc1918() -> None:
    from athena.tools import web

    token = set_approval_callback(_deny_cb)
    try:
        with pytest.raises(URLSecurityDenied):
            web.WebFetch("http://192.168.1.1/admin")
    finally:
        reset_approval_callback(token)
