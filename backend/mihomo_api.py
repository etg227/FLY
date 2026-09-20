from __future__ import annotations
import json, re, threading, time, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

class MihomoApiError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status          # HTTP 状态码，没有就是 None

def _not_found(err):
    """只认状态码——响应体里恰好出现 'HTTP 404' 字样不该被当成 404。"""
    return getattr(err, "status", None) == 404

class MihomoApi:
    def __init__(self, port=19090, secret=""):
        self.base = f"http://127.0.0.1:{int(port)}"
        self.secret = str(secret or "")
        self._provider_by_node = None
        self._provider_ok = False        # 索引是否成功取到过
        self._provider_at = 0.0          # 上次尝试时间，失败后用于退避
        self._provider_lock = threading.Lock()

    def _request(self, method, path, data=None, timeout=8):
        body = None
        headers = {}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return None if not raw else json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise MihomoApiError(f"HTTP {e.code}: {detail or e.reason}", status=e.code) from e
        except Exception as e:
            raise MihomoApiError(str(e)) from e

    def get_group(self, group="FLY-JP"):
        return self._request("GET", f"/proxies/{urllib.parse.quote(group, safe='')}")

    def connections(self):
        """Global stats incl. uploadTotal/downloadTotal since core start."""
        return self._request("GET", "/connections", timeout=5)

    def select(self, node_name, group="FLY-JP"):
        self._request("PUT", f"/proxies/{urllib.parse.quote(group, safe='')}", {"name": node_name})

    def _refresh_provider_index(self):
        """Map provider-backed proxy names to their provider.

        Mihomo keeps proxy-provider nodes under /providers/proxies/... and some
        builds do not expose them through /proxies/{name}/delay. Cache the
        provider membership so parallel latency tests do not refetch it for
        every node.
        """
        mapping = {}
        data = self._request("GET", "/providers/proxies", timeout=5)
        providers = data.get("providers", {}) if isinstance(data, dict) else {}
        if not isinstance(providers, dict):
            providers = {}
        for provider_name, provider in providers.items():
            proxies = provider.get("proxies", []) if isinstance(provider, dict) else []
            for proxy in proxies:
                if isinstance(proxy, dict):
                    name = str(proxy.get("name", "")).strip()
                else:
                    name = str(proxy).strip()
                if name:
                    mapping.setdefault(name, str(provider_name))
        self._provider_by_node = mapping
        return mapping

    def provider_for_node(self, node_name, refresh=False, _retry_after=2.0):
        """返回节点所属的 provider 名；未知返回 None。

        失败不做负缓存：取索引偶发失败一次就永久退回 legacy 接口的话，
        整轮选线会全灭，而且日志里看不出任何退化。同时用锁+退避窗口
        兜住两件事——并发测速只打一次 /providers/proxies；索引端点真的
        挂了时，也不会被每个探测各重试一遍。"""
        with self._provider_lock:
            now = time.time()
            need = refresh or self._provider_by_node is None or not self._provider_ok
            # 刚失败过就先别重试；索引取到过之后，refresh 仍然可以强制刷新
            blocked = (not self._provider_ok and self._provider_by_node is not None
                       and (now - self._provider_at) < _retry_after)
            if need and not blocked:
                self._provider_at = now
                try:
                    self._refresh_provider_index()
                    self._provider_ok = True
                except Exception:
                    # provider 发现只是兼容层，取不到就先走 legacy 接口，
                    # 但保持 _provider_ok=False，退避窗口过后还会再试。
                    self._provider_ok = False
                    if self._provider_by_node is None:
                        self._provider_by_node = {}
            return self._provider_by_node.get(node_name)

    @staticmethod
    def _delay_value(data):
        delay = data.get("delay") if isinstance(data, dict) else None
        if isinstance(delay, int) and delay > 0:
            return delay
        raise MihomoApiError("No usable latency result")

    def delay(self, node_name, test_url, timeout_ms=5000):
        """Measure one node using the endpoint appropriate for its source.

        Provider-backed nodes use:
          /providers/proxies/{provider}/{proxy}/healthcheck

        Standalone/global proxies keep using:
          /proxies/{proxy}/delay
        """
        name = urllib.parse.quote(node_name, safe="")
        qs = urllib.parse.urlencode({"url": test_url, "timeout": int(timeout_ms)})
        req_timeout = (int(timeout_ms) / 1000) + 3

        def probe_provider(provider):
            p = urllib.parse.quote(provider, safe="")
            return self._delay_value(self._request(
                "GET", f"/providers/proxies/{p}/{name}/healthcheck?{qs}", timeout=req_timeout))

        provider = self.provider_for_node(node_name)
        if provider:
            try:
                return probe_provider(provider)
            except MihomoApiError as e:
                if not _not_found(e):
                    raise
                # 订阅刷新后节点可能换了 provider——刷新索引再试一次
                provider = self.provider_for_node(node_name, refresh=True)
                if provider:
                    try:
                        return probe_provider(provider)
                    except MihomoApiError as retry_error:
                        if not _not_found(retry_error):
                            raise

        # 独立 proxies 与老版本内核的兼容路径
        try:
            return self._delay_value(self._request(
                "GET", f"/proxies/{name}/delay?{qs}", timeout=req_timeout))
        except MihomoApiError as e:
            if not _not_found(e):
                raise
            # legacy 也 404：订阅节点根本不在 tunnel.Proxies() 里。
            # 索引可能没取到或已过期，强制刷新后最后再试一次 provider 接口。
            provider = self.provider_for_node(node_name, refresh=True)
            if not provider:
                raise
            return probe_provider(provider)

META_HINTS = ("traffic","expire","expiry","reset","remaining","剩余","流量","到期","套餐","官网","公告")
DEFAULT_JP = ("japan","jpn","日本","tokyo","osaka","東京","东京","大阪","🇯🇵")

def looks_like_japan(name, keywords=None):
    if not name: return False
    lower = name.lower()
    if any(x in lower for x in META_HINTS): return False
    for word in (keywords or DEFAULT_JP):
        w = str(word).strip()
        if not w: continue
        wl = w.lower()
        if wl == "jp":
            if re.search(r"(^|[^a-z])jp([^a-z]|$)", lower):
                return True
        elif wl in lower:
            return True
    return False

class JapanNodeSelector:
    def __init__(self, api, log, keywords=None, test_urls=None,
                 test_url="https://www.gstatic.com/generate_204", timeout_ms=5000,
                 light_url="https://www.gstatic.com/generate_204"):
        self.api, self.log = api, log
        self.keywords = keywords
        urls = test_urls or [test_url]
        self.test_urls = list(dict.fromkeys(str(x).strip() for x in urls if str(x).strip()))
        if not self.test_urls:
            self.test_urls = ["https://www.gstatic.com/generate_204"]
        self.test_url = self.test_urls[0]
        # Lightweight endpoint for liveness/sticky checks: heavy service pages
        # inflate delay numbers and would churn the sticky threshold.
        self.light_url = str(light_url or "https://www.gstatic.com/generate_204").strip()
        self.timeout_ms = int(timeout_ms)

    def candidates(self, group="FLY-JP"):
        data = self.api.get_group(group)
        nodes = data.get("all", []) if isinstance(data, dict) else []
        out, seen = [], set()
        for n in nodes:
            if isinstance(n, str) and n not in seen and looks_like_japan(n, self.keywords):
                out.append(n); seen.add(n)
        return out

    def wait_for_candidates(self, group="FLY-JP", wait_s=45):
        deadline = time.time() + wait_s
        waiting_logged = False
        while True:
            try:
                nodes = self.candidates(group)
            except MihomoApiError as e:
                nodes = []
                last_err = str(e)
            else:
                last_err = None
            if nodes:
                return nodes
            if time.time() >= deadline:
                if last_err:
                    raise MihomoApiError(f"Provider not ready: {last_err}")
                raise MihomoApiError("No Japan node found (Japan / JPN / JP / 日本 / Tokyo / Osaka).")
            if not waiting_logged:
                self.log("[JP] Waiting for node provider to load...")
                waiting_logged = True
            time.sleep(2)

    def measure(self, node):
        delays, errors = [], []
        for url in self.test_urls:
            try:
                delays.append(self.api.delay(node, url, self.timeout_ms))
            except Exception as e:
                errors.append(f"{url}: {e}")
        if not delays:
            raise MihomoApiError("; ".join(errors) or "All latency targets failed")
        return max(delays)

    def measure_light(self, node):
        """Cheap liveness probe against the generic endpoint only."""
        return self.api.delay(node, self.light_url, self.timeout_ms)

    def _measure(self, node):
        try:
            return node, self.measure(node), None
        except Exception as e:
            return node, None, str(e)

    def auto_select(self, group="FLY-JP", wait_s=45, preferred=None, sticky_max_delay_ms=1000):
        nodes = self.wait_for_candidates(group, wait_s)
        if preferred and preferred in nodes:
            try:
                d = self.measure_light(preferred)
                if d <= int(sticky_max_delay_ms):
                    self.api.select(preferred, group)
                    now = self.api.get_group(group).get("now")
                    if now == preferred:
                        self.log(f"[JP STICKY] {preferred} ({d} ms) — keeping the same exit IP.")
                        return preferred, d
                else:
                    self.log(f"[JP] Last node degraded ({d} ms > {sticky_max_delay_ms} ms); re-selecting.")
            except Exception as e:
                self.log(f"[JP] Last node unavailable ({e}); re-selecting.")

        self.log(f"[JP] Found {len(nodes)} Japan candidate(s); testing {len(self.test_urls)} target(s).")
        results = []
        with ThreadPoolExecutor(max_workers=min(6, len(nodes))) as pool:
            futs = [pool.submit(self._measure, n) for n in nodes]
            for f in as_completed(futs):
                n, d, err = f.result()
                if d is not None:
                    self.log(f"[JP TEST] {n}: {d} ms")
                else:
                    self.log(f"[JP TEST] {n}: unavailable ({err})")
                results.append((n, d))

        healthy = sorted([(n,d) for n,d in results if d is not None], key=lambda x:x[1])
        if healthy:
            chosen, delay = healthy[0]
        else:
            chosen, delay = nodes[0], None
            self.log("[JP] All delay tests failed; falling back to first Japan-labelled node.")

        self.api.select(chosen, group)
        now = self.api.get_group(group).get("now")
        if now != chosen:
            raise MihomoApiError(f"Switch failed: expected {chosen!r}, got {now!r}")
        self.log(f"[JP SELECT] {chosen}" + (f" ({delay} ms)" if delay is not None else ""))
        return chosen, delay

    def cycle_next(self, group="FLY-JP"):
        data = self.api.get_group(group)
        current = data.get("now")
        nodes = self.candidates(group)
        if not nodes:
            raise MihomoApiError("No Japan nodes available.")
        idx = (nodes.index(current)+1) % len(nodes) if current in nodes else 0
        chosen = nodes[idx]
        self.api.select(chosen, group)
        try:
            delay = self.measure(chosen)
        except Exception:
            delay = None
        self.log(f"[JP SWITCH] {current or '(none)'} -> {chosen}" + (f" ({delay} ms)" if delay else ""))
        return chosen, delay
