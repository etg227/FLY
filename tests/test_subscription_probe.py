"""订阅探测：真实原因要能看见，同一链接不该被短时间重复请求。"""
import time, unittest, urllib.error
from unittest import mock

import backend.subscription as sub

URL = "https://example.jp/sub?token=x"


def _http_error(code, reason="Forbidden"):
    return urllib.error.HTTPError(URL, code, reason, {}, None)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        sub._probe_cache.clear()

    def test_reports_real_http_error(self):
        with mock.patch.object(sub, "_probe", side_effect=_http_error(403)):
            ok, info, reason = sub.check_subscription(URL, retries=0)
        self.assertFalse(ok)
        self.assertIn("403", reason)          # 修复前这里只有“无法访问”

    def test_reports_timeout(self):
        err = urllib.error.URLError(TimeoutError("timed out"))
        with mock.patch.object(sub, "_probe", side_effect=err):
            ok, _i, reason = sub.check_subscription(URL, retries=0)
        self.assertIn("Timeout", reason)

    def test_result_is_cached_so_we_do_not_hammer_the_endpoint(self):
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return True, {"total": 1}, ""

        with mock.patch.object(sub, "_probe", side_effect=fake):
            for _ in range(5):
                self.assertTrue(sub.check_subscription(URL)[0])
        self.assertEqual(len(calls), 1)       # 面板 + 预检只打一次

    def test_cache_expires(self):
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return True, None, ""

        with mock.patch.object(sub, "_probe", side_effect=fake):
            sub.check_subscription(URL)
            sub._probe_cache[URL] = (time.time() - sub._PROBE_TTL - 1, (True, None, ""))
            sub.check_subscription(URL)
        self.assertEqual(len(calls), 2)

    def test_transient_failure_is_retried_once(self):
        seq = [_http_error(429, "Too Many Requests"), None]

        def fake(url, timeout):
            item = seq.pop(0)
            if item: raise item
            return True, {"total": 5}, ""

        with mock.patch.object(sub, "_probe", side_effect=fake), \
             mock.patch.object(sub.time, "sleep", lambda *_: None):
            ok, info, _r = sub.check_subscription(URL)
        self.assertTrue(ok)
        self.assertEqual(info, {"total": 5})


class SelectUsableTests(unittest.TestCase):
    def setUp(self):
        sub._probe_cache.clear()

    def test_failure_reason_reaches_the_log(self):
        import tempfile
        from pathlib import Path
        logs = []
        with mock.patch.object(sub, "_probe", side_effect=_http_error(403)), \
             mock.patch.object(sub.time, "sleep", lambda *_: None):
            usable = sub.select_usable_subscriptions([URL], Path(tempfile.mkdtemp()), logs.append)
        self.assertEqual(usable, [])
        self.assertTrue(any("403" in x for x in logs), logs)
        self.assertTrue(any("限流" in x for x in logs), logs)
        self.assertFalse(any("token=x" in x for x in logs), "日志不应带出订阅链接")


if __name__ == "__main__":
    unittest.main()
