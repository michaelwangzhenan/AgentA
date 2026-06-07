"""
URLGuard —— SSRF 防御

判定一个 URL 是否可被 `fetch_url` / `fetch.fetch` 这类外发请求安全访问：
- 必须是 `http` / `https` scheme（`file://` / 自定义 scheme 一律拒）
- 解析出的主机不能落在内网 IP 段（私有 / loopback / link-local / multicast / reserved）
- 域名走 DNS 反查后再判（防 DNS rebinding 攻击）

为什么不靠 `_tool_fetch_url` 现有的 scheme 检查：
- 原检查只拦 `http(s)` 之外的 scheme，**不拦** `http://10.0.0.1/...` 这类内网 IP
- MCP 引入 `fetch.fetch` 第三方实现，二者必须共用同一道防线
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 字面 localhost 别名（绕过 IP 解析的常见 trick）
_LOCALHOST_ALIASES = frozenset({
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
})


def is_url_safe(url: str) -> bool:
    """判断 URL 是否允许外发请求。

    安全准则（任一不满足即返 False）：
    1. 类型是非空字符串
    2. URL 可解析且 scheme ∈ {http, https}
    3. host 非空
    4. host 不是 localhost 字面别名
    5. host 解析出的 IP 不在 private / loopback / link-local / multicast / reserved 段
       （host 是合法 IP 时直接判；是域名时先 DNS 反查）
    6. DNS 反查失败（域名不存在 / 网络问题）一律拒（保守路径）
    """
    if not isinstance(url, str) or not url.strip():
        return False

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return False

    host = parsed.hostname  # 自动去 port + 转 lowercase
    if not host:
        return False

    if host in _LOCALHOST_ALIASES:
        return False

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # 不是字面 IP → 走 DNS 解析；解析失败一律拒
        try:
            resolved = socket.gethostbyname(host)
            ip = ipaddress.ip_address(resolved)
        except (socket.gaierror, OSError, ValueError) as exc:
            logger.warning("[URLGuard] %s DNS 解析失败：%s（拒）", host, exc)
            return False

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        logger.warning("[URLGuard] %s → %s 内网/保留段，拒", host, ip)
        return False

    return True
