"""Subscription quota info: Clash-compatible providers return a
`subscription-userinfo` response header (upload/download/total/expire) when the
subscription URL is fetched with a Clash-like User-Agent."""
from __future__ import annotations
import hashlib, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

# 同一个订阅链接在几秒内会被请求好几次（流量面板、启动预检、内核拉 provider）。
# 不少机场对订阅接口限流，重复请求会被 403/429 挡掉，于是 FLY 把自己关在门外。
# 短时结果缓存让一次启动只真正打一次订阅接口。
_PROBE_TTL = 45.0
_probe_cache = {}
_probe_lock = threading.Lock()

def _describe_error(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} {e.reason}"
    if isinstance(e, urllib.error.URLError):
        return f"{type(e.reason).__name__ if e.reason else 'URLError'}: {e.reason}"
    return f"{type(e).__name__}: {e}"

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

def _probe(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": "clash.meta; mihomo (FLY)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        header = resp.headers.get("subscription-userinfo", "")
        resp.read(1024)
    return True, parse_userinfo(header), ""

def check_subscription(url: str, timeout=8, use_cache=True, retries=1):
    """(reachable, userinfo, reason) — 探测订阅链接，不保留响应体。

    reason 会带上真实原因（HTTP 403 / 超时 / DNS 失败…），否则日志里
    只剩一句“无法访问”，既没法排查，也分不清是限流还是真的断网。"""
    now = time.time()
    if use_cache:
        with _probe_lock:
            hit = _probe_cache.get(url)
            if hit and now - hit[0] < _PROBE_TTL:
                return hit[1]
    result = (False, None, "未知错误")
    for attempt in range(max(1, int(retries) + 1)):
        try:
            result = _probe(url, timeout)
            break
        except Exception as e:
            result = (False, None, _describe_error(e))
            if attempt < retries:
                time.sleep(1.5)      # 抖动/限流窗口，隔一下再试一次
    with _probe_lock:
        _probe_cache[url] = (time.time(), result)
    return result

def provider_cache_name(url: str) -> str:
    """Stable per-URL cache filename, so reordering subscriptions keeps caches."""
    return "sub_" + hashlib.md5(str(url).encode("utf-8")).hexdigest()[:10] + ".yaml"

def select_usable_subscriptions(urls, provider_dir, log=lambda m: None, timeout=8):
    """Keep subscriptions that respond now, or that at least have a local
    cache mihomo can start from; skip dead+uncached ones so one broken
    provider can never block startup."""
    urls = [str(u).strip() for u in urls if str(u).strip()]
    if not urls:
        return []
    results = {}
    with ThreadPoolExecutor(max_workers=min(4, len(urls))) as pool:
        futs = {pool.submit(check_subscription, u, timeout): u for u in urls}
        for f in futs:
            ok, _info, reason = f.result()
            results[futs[f]] = (ok, reason)
    usable, failures = [], []
    for i, u in enumerate(urls, 1):
        cached = (provider_dir / provider_cache_name(u)).exists()
        ok, reason = results.get(u, (False, "未检测"))
        if ok:
            usable.append(u)
        elif cached:
            log(f"[SUB] 订阅 {i} 暂时无法访问（{reason}），使用本地缓存启动。")
            usable.append(u)
        else:
            # 只报原因，不报链接——日志是要发给别人看的
            log(f"[SUB] 订阅 {i} 无法访问且没有缓存，本次跳过：{reason}")
            failures.append(f"订阅 {i}：{reason}")
    if not usable and failures:
        log("[SUB] 提示：机场常对订阅接口限流，若刚刚才拉取过，隔一分钟再试往往就能成功。")
    return usable

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
