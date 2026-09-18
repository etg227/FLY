"""Subscription quota info: Clash-compatible providers return a
`subscription-userinfo` response header (upload/download/total/expire) when the
subscription URL is fetched with a Clash-like User-Agent."""
from __future__ import annotations
import time, urllib.request

def parse_userinfo(header: str):
    """'upload=123; download=456; total=789; expire=1735689600' -> dict."""
    out = {}
    for part in str(header or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip().lower()] = int(float(v.strip()))
            except ValueError:
                pass
    return out if out else None

def fetch_userinfo(url: str, timeout=15):
    """Returns the parsed userinfo dict, or None when the provider sends none."""
    req = urllib.request.Request(url, headers={"User-Agent": "clash.meta; mihomo (FLY)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        header = resp.headers.get("subscription-userinfo", "")
        # We only need the response headers; drain a little and close.
        resp.read(1024)
    return parse_userinfo(header)

def fmt_bytes(n):
    n = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.2f} {unit}"
        n /= 1024

def fmt_speed(n):
    return fmt_bytes(n) + "/s"

def describe_userinfo(info):
    """One display line: 剩余 / 总量 / 到期."""
    if not info:
        return "订阅未提供流量信息"
    total = info.get("total", 0)
    used = info.get("upload", 0) + info.get("download", 0)
    parts = []
    if total:
        remaining = max(0, total - used)
        parts.append(f"剩余 {fmt_bytes(remaining)} / 共 {fmt_bytes(total)}")
        if total > 0 and remaining / total <= 0.1:
            parts[-1] += "（不足 10%！）"
    elif used:
        parts.append(f"已用 {fmt_bytes(used)}")
    expire = info.get("expire", 0)
    if expire:
        parts.append("到期 " + time.strftime("%Y-%m-%d", time.localtime(expire)))
    return " · ".join(parts) if parts else "订阅未提供流量信息"
