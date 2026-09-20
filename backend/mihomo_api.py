from __future__ import annotations
import json, queue, re, threading, time, urllib.parse, urllib.request, urllib.error

class MihomoApiError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

def _not_found(err):
    return getattr(err, "status", None) == 404

class MihomoApi:
    def __init__(self, port=19090, secret=""):
        self.base = f"http://127.0.0.1:{int(port)}"
        self.secret = str(secret or "")
        self._provider_by_node = None
        self._provider_ok = False
        self._provider_at = 0.0
        self._provider_lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

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
            with self._opener.open(req, timeout=timeout) as resp:
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
        return self._request("GET", "/connections", timeout=5)

    def select(self, node_name, group="FLY-JP"):
        self._request("PUT", f"/proxies/{urllib.parse.quote(group, safe='')}", {"name": node_name})

    def _refresh_provider_index(self):
        mapping = {}
        data = self._request("GET", "/providers/proxies", timeout=5)
        providers = data.get("providers", {}) if isinstance(data, dict) else {}
        if not isinstance(providers, dict):
            providers = {}
        for provider_name, provider in providers.items():
            proxies = provider.get("proxies", []) if isinstance(provider, dict) else []
            for proxy in proxies:
                name = str(proxy.get("name", "") if isinstance(proxy, dict) else proxy).strip()
                if name:
                    # Config gives each provider an additional-prefix, so names
                    # are unique across providers. Keep setdefault as a safe
                    # fallback for old cached configs.
                    mapping.setdefault(name, str(provider_name))
        self._provider_by_node = mapping
        return mapping

    def provider_for_node(self, node_name, refresh=False, _retry_after=2.0):
        with self._provider_lock:
            now = time.time()
            need = refresh or self._provider_by_node is None or not self._provider_ok
            blocked = (not self._provider_ok and self._provider_by_node is not None
                       and (now - self._provider_at) < _retry_after)
            if need and not blocked:
                self._provider_at = now
                try:
                    self._refresh_provider_index()
                    self._provider_ok = True
                except Exception:
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
                provider = self.provider_for_node(node_name, refresh=True)
                if provider:
                    try:
                        return probe_provider(provider)
                    except MihomoApiError as retry_error:
                        if not _not_found(retry_error):
                            raise

        try:
            return self._delay_value(self._request(
                "GET", f"/proxies/{name}/delay?{qs}", timeout=req_timeout))
        except MihomoApiError as e:
            if not _not_found(e):
                raise
            provider = self.provider_for_node(node_name, refresh=True)
            if not provider:
                raise
            return probe_provider(provider)

META_HINTS = ("traffic","expire","expiry","reset","remaining","剩余","流量","到期","套餐","官网","公告")
DEFAULT_JP = ("japan","jpn","日本","tokyo","osaka","東京","东京","大阪","🇯🇵")

def looks_like_japan(name, keywords=None):
    if not name:
        return False
    lower = name.lower()
    if any(x in lower for x in META_HINTS):
        return False
    words = keywords if isinstance(keywords, (list,tuple)) and keywords else DEFAULT_JP
    for word in words:
        w = str(word).strip()
        if len(w) < 2:
            continue
        wl = w.lower()
        if wl == "jp":
            if re.search(r"(^|[^a-z])jp([^a-z]|$)", lower):
                return True
        elif wl in lower:
            return True
    return False

class JapanNodeSelector:
    def __init__(self, api, log, keywords=None, test_urls=None, required_urls=None,
                 test_url="https://www.gstatic.com/generate_204", timeout_ms=5000,
                 light_url="https://www.gstatic.com/generate_204",
                 cancel_event=None, total_timeout_s=35):
        self.api, self.log = api, log
        self.keywords = keywords
        source = required_urls if required_urls is not None else test_urls
        self.required_urls = list(dict.fromkeys(
            str(x).strip() for x in (source or []) if str(x).strip()))
        self.light_url = str(light_url or test_url or "https://www.gstatic.com/generate_204").strip()
        self.test_urls = self.required_urls or [self.light_url]
        self.test_url = self.test_urls[0]
        self.timeout_ms = max(1000, min(30000, int(timeout_ms)))
        self.cancel_event = cancel_event
        self.total_timeout_s = max(5, int(total_timeout_s))

    def _cancelled(self):
        return bool(self.cancel_event and self.cancel_event.is_set())

    def _check_cancelled(self):
        if self._cancelled():
            raise MihomoApiError("Node selection cancelled")

    def candidates(self, group="FLY-JP"):
        self._check_cancelled()
        data = self.api.get_group(group)
        nodes = data.get("all", []) if isinstance(data, dict) else []
        out, seen = [], set()
        for n in nodes:
            if isinstance(n, str) and n not in seen and looks_like_japan(n, self.keywords):
                out.append(n); seen.add(n)
        return out

    def wait_for_candidates(self, group="FLY-JP", wait_s=20):
        deadline = time.time() + max(1, wait_s)
        waiting_logged = False
        last_err = None
        while True:
            self._check_cancelled()
            try:
                nodes = self.candidates(group)
            except MihomoApiError as e:
                nodes = []; last_err = str(e)
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
            if self.cancel_event:
                if self.cancel_event.wait(0.5):
                    self._check_cancelled()
            else:
                time.sleep(0.5)

    def measure(self, node):
        """A node is healthy only if every selected service target succeeds.

        The generic gstatic endpoint is used only when no profile supplied a
        service-specific latency target.
        """
        self._check_cancelled()
        urls = self.required_urls or [self.light_url]
        delays = []
        for url in urls:
            self._check_cancelled()
            try:
                delays.append(self.api.delay(node, url, self.timeout_ms))
            except Exception as e:
                raise MihomoApiError(f"{url}: {e}") from e
        if not delays:
            raise MihomoApiError("No latency target")
        return max(delays)

    def measure_light(self, node):
        # Keep watchdog/sticky service-aware. A target service being blocked is
        # a real failure even when Google's 204 endpoint still answers.
        return self.measure(node)

    def _measure(self, node):
        try:
            return node, self.measure(node), None
        except Exception as e:
            return node, None, str(e)

    def _commit_selection(self, chosen, group):
        self._check_cancelled()
        self.api.select(chosen, group)
        now = self.api.get_group(group).get("now")
        if now != chosen:
            raise MihomoApiError(f"Switch failed: expected {chosen!r}, got {now!r}")

    def auto_select(self, group="FLY-JP", wait_s=20, preferred=None,
                    sticky_max_delay_ms=1000, exclude=None):
        excluded = set(exclude or ())
        nodes = [n for n in self.wait_for_candidates(group, wait_s) if n not in excluded]
        if not nodes:
            raise MihomoApiError("No eligible Japan nodes remain after excluding failed nodes.")

        if preferred and preferred in nodes:
            try:
                d = self.measure(preferred)
                if d <= int(sticky_max_delay_ms):
                    self._commit_selection(preferred, group)
                    self.log(f"[JP STICKY] {preferred} ({d} ms) — verified against selected service.")
                    return preferred, d
                self.log(f"[JP] Last node degraded ({d} ms > {sticky_max_delay_ms} ms); re-selecting.")
            except Exception as e:
                self.log(f"[JP] Last node unavailable ({e}); re-selecting.")

        self.log(f"[JP] Found {len(nodes)} Japan candidate(s); testing {len(self.test_urls)} required target(s).")
        results = []
        jobs = queue.Queue()
        output = queue.Queue()
        for n in nodes:
            jobs.put(n)

        def worker():
            while not self._cancelled():
                try:
                    n = jobs.get_nowait()
                except queue.Empty:
                    return
                output.put(self._measure(n))

        # Daemon workers are deliberate: urllib/socket calls cannot be forcibly
        # cancelled. Stop/close must not be held hostage by a stuck probe.
        workers = [threading.Thread(target=worker, daemon=True)
                   for _ in range(min(6, len(nodes)))]
        for t in workers:
            t.start()

        completed = 0
        deadline = time.time() + self.total_timeout_s
        while completed < len(nodes) and time.time() < deadline:
            self._check_cancelled()
            try:
                n, d, err = output.get(timeout=min(0.25, max(0.01, deadline-time.time())))
            except queue.Empty:
                continue
            completed += 1
            if d is not None:
                self.log(f"[JP TEST {completed}/{len(nodes)}] {n}: {d} ms")
            else:
                self.log(f"[JP TEST {completed}/{len(nodes)}] {n}: unavailable ({err})")
            results.append((n, d))
        if completed < len(nodes):
            self.log(f"[JP] 测速总时限 {self.total_timeout_s}s 已到；剩余探测线程将作为 daemon 自行结束。")

        self._check_cancelled()
        healthy = sorted([(n,d) for n,d in results if d is not None], key=lambda x:x[1])
        if not healthy:
            raise MihomoApiError("没有任何通过所选服务健康检查的日本节点；保持加速未启动。")

        chosen, delay = healthy[0]
        self._commit_selection(chosen, group)
        self.log(f"[JP SELECT] {chosen} ({delay} ms)")
        return chosen, delay

    def cycle_next(self, group="FLY-JP"):
        data = self.api.get_group(group)
        current = data.get("now")
        nodes = self.candidates(group)
        if not nodes:
            raise MihomoApiError("No Japan nodes available.")
        start = (nodes.index(current) + 1) % len(nodes) if current in nodes else 0
        ordered = nodes[start:] + nodes[:start]
        errors = []
        for chosen in ordered:
            if chosen == current:
                continue
            try:
                delay = self.measure(chosen)  # validate before changing traffic
                self._commit_selection(chosen, group)
                self.log(f"[JP SWITCH] {current or '(none)'} -> {chosen} ({delay} ms)")
                return chosen, delay
            except Exception as e:
                errors.append(f"{chosen}: {e}")
                self.log(f"[JP SWITCH] 跳过不可用节点 {chosen}: {e}")
        raise MihomoApiError(
            "没有找到可切换的健康日本节点；当前节点保持不变。" +
            (" " + "; ".join(errors[:3]) if errors else ""))
