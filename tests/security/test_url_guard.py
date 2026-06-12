"""
test_url_guard —— Phase 3.3 SSRF 防御行为单测

覆盖维度（与验收 ⑥ 对应）：
1. 非法输入（None / 非字符串 / 空 / 全空白）→ False
2. 非 http(s) scheme（file:// / ftp:// / ssh:// / 自定义）→ False
3. 内网 IP 字面值（10/8 / 172.16/12 / 192.168/16 / 127/8 / ::1 / fe80::）→ False
4. localhost 字面别名（含 ip6-localhost）→ False
5. 域名 DNS 解析后落内网 → False（防 DNS rebinding）
6. 域名 DNS 解析失败 → False（保守路径）
7. 公网 IP / 公网域名 → True
8. multicast / reserved / unspecified IP → False

DNS 行为统一 mock，UT 不真发网络请求。
"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from src.agent.core.url_guard import is_url_safe


class TestInvalidInputs:
    def test_none_returns_false(self) -> None:
        assert is_url_safe(None) is False  # type: ignore[arg-type]

    def test_non_string_returns_false(self) -> None:
        assert is_url_safe(123) is False  # type: ignore[arg-type]

    def test_empty_string_returns_false(self) -> None:
        assert is_url_safe("") is False

    def test_whitespace_only_returns_false(self) -> None:
        assert is_url_safe("   ") is False


class TestSchemeWhitelist:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "ssh://example.com",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "//example.com/x",  # protocol-relative，无 scheme
        "no-scheme.example.com",
    ])
    def test_non_http_scheme_rejected(self, url: str) -> None:
        assert is_url_safe(url) is False


class TestPrivateIPLiterals:
    """字面 IP 直接判，无需 DNS。"""

    @pytest.mark.parametrize("url", [
        "http://10.0.0.1/",
        "http://10.255.255.255/api",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
        "http://192.168.1.1/",
        "http://192.168.0.0:8080/",
        "http://127.0.0.1/",
        "http://127.0.0.1:9000/",
        "https://[::1]/",
        "https://[fe80::1]/",  # link-local
        "http://0.0.0.0/",  # unspecified
        "http://224.0.0.1/",  # multicast
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata (link-local)
    ])
    def test_private_ip_rejected(self, url: str) -> None:
        assert is_url_safe(url) is False


class TestLocalhostAliases:
    @pytest.mark.parametrize("url", [
        "http://localhost/",
        "https://localhost:3000/",
        "http://localhost.localdomain/",
        "http://ip6-localhost/",
        "http://ip6-loopback/",
    ])
    def test_localhost_alias_rejected(self, url: str) -> None:
        assert is_url_safe(url) is False


class TestDomainResolution:
    """域名走 DNS 反查后再判。"""

    def test_public_domain_resolves_to_public_ip_allowed(self) -> None:
        with patch("socket.gethostbyname", return_value="93.184.216.34"):  # example.com 历史 IP
            assert is_url_safe("https://example.com/path") is True

    def test_domain_resolves_to_private_ip_rejected(self) -> None:
        """DNS rebinding：域名解析到内网 IP 也拒。"""
        with patch("socket.gethostbyname", return_value="10.0.0.5"):
            assert is_url_safe("https://evil.example.com/") is False

    def test_domain_resolves_to_loopback_rejected(self) -> None:
        with patch("socket.gethostbyname", return_value="127.0.0.1"):
            assert is_url_safe("https://attacker.example.com/") is False

    def test_dns_failure_rejected(self) -> None:
        """解析失败一律拒（保守）。"""
        with patch("socket.gethostbyname", side_effect=socket.gaierror("DNS NXDOMAIN")):
            assert is_url_safe("https://does-not-exist.example.invalid/") is False


class TestPublicIPAllowed:
    @pytest.mark.parametrize("url", [
        "https://8.8.8.8/",
        "https://1.1.1.1/dns-query",
        "http://93.184.216.34/",
    ])
    def test_public_ip_allowed(self, url: str) -> None:
        assert is_url_safe(url) is True


class TestEdgeCases:
    def test_url_with_port_normal(self) -> None:
        with patch("socket.gethostbyname", return_value="93.184.216.34"):
            assert is_url_safe("https://example.com:8443/x") is True

    def test_url_with_userinfo_normal(self) -> None:
        with patch("socket.gethostbyname", return_value="93.184.216.34"):
            assert is_url_safe("https://user:pass@example.com/x") is True

    def test_url_with_trailing_whitespace(self) -> None:
        with patch("socket.gethostbyname", return_value="93.184.216.34"):
            assert is_url_safe("  https://example.com/  ") is True
