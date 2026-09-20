"""Subscription reachability and quota parsing."""
from __future__ import annotations
import base64, hashlib, math, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

_PROBE_TTL = 45.0
_probe_cache = {}
_probe_inflight = {}
_probe_lock = threading.Lock()

def _direct_opener():
    # FLY's own control traffic must never inherit the Windows system proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))

def _describe_error(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} {e.reason}"
    if isinstance(e, urllib.error.URLError):
        return f"{type(e.reason).__name__ if e.reason else 'URLError'}: {e.reason}"
    return f"{type(e).__name__}: {e}"

def _finite_int(value):
    try:
        n = float(str(value).strip())
        if not math.isfinite(n):
            return None
        return int(n)
    except (TypeError, ValueError, OverflowError):
        return None

def parse_userinfo(header: str):
    out = {}
    for part in str(header or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        n = _finite_int(v)
        if n is not None:
            out[k.strip().lower()] = n
    return out if out else None

def _looks_like_subscription(blob: bytes, header: str, content_type=""):
    if header:
        return True
    text = blob.decode("utf-8-sig", errors="replace").strip()
    lower = text[:4096].lower()
    if not text:
        return False
    if "<html" in lower or "<!doctype html" in lower or "<head" in lower:
        return False
    if "proxies:" in lower or "proxy-providers:" in lower:
        return True
    schemes = ("ss://","ssr://","vmess://","vless://","trojan://",
               "hysteria://","hysteria2://","tuic://","wireguard://")
    if any(s in lower for s in schemes):
        return True
    compact = "".join(text.split())
    if len(compact) >= 24:
        try:
            decoded = base64.b64decode(compact + "=" * (-len(compact) % 4),
                                       validate=False).decode("utf-8", errors="ignore").lower()
            if any(s in decoded for s in schemes):
                return True
        except Exception:
            pass
    ctype = str(content_type or "").lower()
    # YAML/Clash providers sometimes omit obvious keys in the first chunk.
    return ("yaml" in ctype or "octet-stream" in ctype) and len(blob) > 32

def fetch_userinfo(url: str, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "clash.meta; mihomo (FLY)"})
    with _direct_opener().open(req, timeout=timeout) as resp:
        header = resp.headers.get("subscription-userinfo", "")
        resp.read(1024)
    return parse_userinfo(header)

def _probe(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": "clash.meta; mihomo (FLY)"})
    with _direct_opener().open(req, timeout=timeout) as resp:
        header = resp.headers.get("subscription-userinfo", "")
        ctype = resp.headers.get("Content-Type", "")
        body = resp.read(64 * 1024)
    if not _looks_like_subscription(body, header, ctype):
        return False, None, "HTTP 200，但响应内容不像 Clash/Mihomo 订阅"
    return True, parse_userinfo(header), ""

def check_subscription(url: str, timeout=8, use_cache=True, retries=1):
    """(reachable, userinfo, reason), with per-URL single-flight probing."""
    url = str(url).strip()
    leader = False
    event = None
    now = time.time()
    with _probe_lock:
        if use_cache:
            hit = _probe_cache.get(url)
            if hit and now - hit[0] < _PROBE_TTL:
                return hit[1]
        event = _probe_inflight.get(url)
        if event is None:
            event = threading.Event()
            _probe_inflight[url] = event
            leader = True

    if not leader:
        event.wait(timeout=max(5, timeout * (max(1, int(retries) + 1)) + 5))
        with _probe_lock:
            hit = _probe_cache.get(url)
            if hit:
                return hit[1]
        return False, None, "订阅探测超时"

    result = (False, None, "未知错误")
    try:
        for attempt in range(max(1, int(retries) + 1)):
            try:
                result = _probe(url, timeout)
                if result[0]:
                    break
            except Exception as e:
                result = (False, None, _describe_error(e))
            if attempt < retries:
                time.sleep(1.5)
        with _probe_lock:
            _probe_cache[url] = (time.time(), result)
        return result
    finally:
        with _probe_lock:
            done = _probe_inflight.pop(url, None)
            if done:
                done.set()

def provider_cache_name(url: str) -> str:
    return "sub_" + hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:16] + ".yaml"

def select_usable_subscriptions(urls, provider_dir, log=lambda m: None, timeout=8):
    clean, seen = [], set()
    for u in urls:
        s = str(u).strip()
        if s and s not in seen:
            seen.add(s); clean.append(s)
    if not clean:
        return []
    results = {}
    with ThreadPoolExecutor(max_workers=min(4, len(clean))) as pool:
        futs = {pool.submit(check_subscription, u, timeout): u for u in clean}
        for f, u in futs.items():
            try:
                ok, _info, reason = f.result()
            except Exception as e:
                ok, reason = False, _describe_error(e)
            results[u] = (ok, reason)

    usable, failures = [], []
    for i, u in enumerate(clean, 1):
        cached = (provider_dir / provider_cache_name(u)).exists()
        ok, reason = results.get(u, (False, "未检测"))
        if ok:
            usable.append(u)
        elif cached:
            log(f"[SUB] 订阅 {i} 暂时无法访问（{reason}），使用本地缓存启动。")
            usable.append(u)
        else:
            log(f"[SUB] 订阅 {i} 无法访问且没有缓存，本次跳过：{reason}")
            failures.append(f"订阅 {i}：{reason}")
    if not usable and failures:
        log("[SUB] 若机场刚被频繁拉取而限流，请稍后重试；FLY 已避免同一 URL 并发重复探测。")
    return usable

def fmt_bytes(n):
    try:
        n = float(max(0, n))
    except (TypeError, ValueError, OverflowError):
        n = 0.0
    if not math.isfinite(n):
        n = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.2f} {unit}"
        n /= 1024

def fmt_speed(n):
    return fmt_bytes(n) + "/s"

def _normalize_expire(value):
    n = _finite_int(value)
    if not n or n <= 0:
        return 0
    # 13-digit millisecond timestamps are common on some panels.
    while n > 100_000_000_000:
        n //= 1000
    return n

def describe_userinfo(info):
    if not isinstance(info, dict) or not info:
        return "订阅未提供流量信息"
    total = _finite_int(info.get("total")) or 0
    upload = _finite_int(info.get("upload")) or 0
    download = _finite_int(info.get("download")) or 0
    used = max(0, upload) + max(0, download)
    parts = []
    if total > 0:
        remaining = max(0, total - used)
        parts.append(f"剩余 {fmt_bytes(remaining)} / 共 {fmt_bytes(total)}")
        if remaining / total <= 0.1:
            parts[-1] += "（不足 10%！）"
    elif used:
        parts.append(f"已用 {fmt_bytes(used)}")

    expire = _normalize_expire(info.get("expire"))
    if expire:
        try:
            parts.append("到期 " + time.strftime("%Y-%m-%d", time.localtime(expire)))
        except (OverflowError, OSError, ValueError):
            parts.append("到期时间无效")
    return " · ".join(parts) if parts else "订阅未提供流量信息"
