"""provider 索引：失败不负缓存、并发只取一次、过期能自愈。"""
import threading, unittest
from unittest import mock
from backend.mihomo_api import MihomoApi, MihomoApiError

NODE = "🇯🇵 日本W01 | IEPL"
OTHER = "🇯🇵 日本W02 | IEPL"


class FakeApi(MihomoApi):
    """按脚本回答的假内核：index_fails 控制 /providers/proxies 是否可用。"""

    def __init__(self, nodes=(NODE,), index_fails=False, delay_ms=42):
        super().__init__(port=19090, secret="")
        self.nodes = list(nodes)
        self.index_fails = index_fails
        self.delay_ms = delay_ms
        self.calls = []
        self._lock = threading.Lock()

    def _request(self, method, path, data=None, timeout=8):
        with self._lock:
            self.calls.append(path)
        if path == "/providers/proxies":
            if self.index_fails:
                raise MihomoApiError("HTTP 503: boom", status=503)
            return {"providers": {"USER1": {"proxies": [{"name": n} for n in self.nodes]}}}
        if path.startswith("/providers/proxies/USER1/") and "/healthcheck?" in path:
            return {"delay": self.delay_ms}
        if path.startswith("/proxies/") and "/delay?" in path:
            raise MihomoApiError('HTTP 404: {"message":"Resource not found"}', status=404)
        raise AssertionError(f"unexpected {method} {path}")

    def count(self, needle):
        return sum(1 for p in self.calls if needle in p)


class NegativeCacheTests(unittest.TestCase):
    def test_index_failure_is_not_cached_forever(self):
        api = FakeApi(index_fails=True)
        with self.assertRaises(MihomoApiError):
            api.delay(NODE, "https://x/", 5000)
        api.index_fails = False                      # 内核恢复
        api._provider_at = 0.0                       # 跳过退避窗口
        self.assertEqual(api.delay(NODE, "https://x/", 5000), 42)

    def test_backoff_limits_retries_while_still_broken(self):
        api = FakeApi(index_fails=True)
        for _ in range(3):
            with self.assertRaises(MihomoApiError):
                api.delay(NODE, "https://x/", 5000)
        # 索引端点挂着时，退避窗口内只该尝试一次，而不是每个探测都重拉
        self.assertEqual(sum(1 for c in api.calls if c == "/providers/proxies"), 1)


class ConcurrencyTests(unittest.TestCase):
    def test_parallel_probes_fetch_index_once(self):
        api = FakeApi(nodes=[NODE, OTHER])
        errors = []

        def probe():
            try:
                api.delay(NODE, "https://x/", 5000)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=probe) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
        self.assertEqual(api.count("/providers/proxies?") + 
                         sum(1 for p in api.calls if p == "/providers/proxies"), 1)


class StaleIndexTests(unittest.TestCase):
    def test_node_added_after_index_was_built_still_resolves(self):
        api = FakeApi(nodes=[NODE])
        self.assertEqual(api.delay(NODE, "https://x/", 5000), 42)   # 建立索引
        api.nodes.append(OTHER)                                     # 订阅刷新后新增节点
        # 旧索引里没有 OTHER —— 修复前会走 legacy 接口拿到 404 并判为不可用
        self.assertEqual(api.delay(OTHER, "https://x/", 5000), 42)

    def test_non_404_errors_are_not_swallowed(self):
        api = FakeApi(nodes=[NODE])

        def boom(method, path, data=None, timeout=8):
            if path == "/providers/proxies":
                return {"providers": {"USER1": {"proxies": [{"name": NODE}]}}}
            raise MihomoApiError("HTTP 503: node down", status=503)

        api._request = boom
        with self.assertRaises(MihomoApiError) as ctx:
            api.delay(NODE, "https://x/", 5000)
        self.assertEqual(ctx.exception.status, 503)   # 真故障必须上报，不能回退成“可用”


class CleanupRaceTests(unittest.TestCase):
    def test_cleanup_skips_while_our_core_is_running(self):
        import tempfile
        from pathlib import Path
        from backend.config import Paths
        from backend.core_manager import CoreManager

        paths = Paths(Path(tempfile.mkdtemp()))
        paths.core_exe.parent.mkdir(parents=True, exist_ok=True)
        paths.core_exe.write_bytes(b"MZ" + b"x" * (1024 * 1024 + 8))
        (paths.core_exe.parent / "mihomo-verified.zip").write_bytes(b"archive")
        logs = []
        cm = CoreManager(paths, logs.append)
        called = []
        cm._cleanup_orphans_locked = lambda: called.append(1)

        with mock.patch("backend.core_manager.os.name", "nt"):
            cm.process = object()       # 自己的内核在跑
            cm.cleanup_orphans()
            self.assertEqual(called, [])    # 不扫，否则会杀掉自己

            cm.process = None
            cm.cleanup_orphans()
            self.assertEqual(len(called), 1)


if __name__ == "__main__":
    unittest.main()
