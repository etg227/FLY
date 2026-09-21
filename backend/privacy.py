from __future__ import annotations
import hashlib, ipaddress, re
from urllib.parse import urlsplit

_HOST_RE = re.compile(r"(?<![\w.-])((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?::(\d{1,5}))?")
_IPV4_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?")
_IPV6_BRACKET_RE = re.compile(r"\[([0-9A-Fa-f:]+)\](?::(\d{1,5}))?")
_IPV6_TOKEN_RE = re.compile(r"(?<![0-9A-Fa-f:])([0-9A-Fa-f:]{3,})(?![0-9A-Fa-f:])")
_URL_RE = re.compile(r"https?://[^\s\]\[()<>\"']+")
# api_secret 是 32 位 hex（secrets.token_hex(16)）。运行时配置已经抹掉它，
# 日志同样不该带出去。长 hex 串在日志里只可能是凭据或完整哈希，统一打码；
# 程序自己展示哈希时只取前 12 位，不受影响。
_HEX_SECRET_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,64}(?![0-9A-Fa-f])")

def _tag(kind, value):
    h = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"<{kind}:{h}>"

def _redact_url(match):
    raw = match.group(0)
    try:
        p = urlsplit(raw)
        suffix = ""
        if p.path and p.path not in ("", "/"):
            suffix = "/…"
        return _tag("url", p.hostname or raw) + suffix
    except Exception:
        return _tag("url", raw)

def _redact_ipv6_bracket(match):
    raw = match.group(1)
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return match.group(0)
    if addr.version != 6:
        return match.group(0)
    port = f":{match.group(2)}" if match.group(2) else ""
    return _tag("ip6", raw) + port

def _redact_ipv6_token(match):
    raw = match.group(1)
    if raw.count(":") < 2:
        return raw
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return raw
    return _tag("ip6", raw) if addr.version == 6 else raw

def redact_log_line(line):
    """Sanitize the persisted diagnostic log.

    The live UI may show full local diagnostics, but fly.log is intended to be
    shareable and therefore must not contain browsing targets, subscription
    hosts/tokens, or node server addresses.
    """
    text = str(line)
    text = _URL_RE.sub(_redact_url, text)
    text = _HEX_SECRET_RE.sub(lambda m: _tag("secret", m.group(0)), text)
    text = _IPV6_BRACKET_RE.sub(_redact_ipv6_bracket, text)
    text = _IPV6_TOKEN_RE.sub(_redact_ipv6_token, text)
    text = _IPV4_RE.sub(lambda m: _tag("ip", m.group(0)), text)
    text = _HOST_RE.sub(lambda m: _tag("host", m.group(1)) + (f":{m.group(2)}" if m.group(2) else ""), text)
    return text
