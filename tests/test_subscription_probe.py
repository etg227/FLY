"""Subscription robustness tests."""
import threading, time, unittest, urllib.error
from unittest import mock
import backend.subscription as sub

URL="https://example.jp/sub?token=x"
def _http_error(code,reason="Forbidden"):
    return urllib.error.HTTPError(URL,code,reason,{},None)

class ProbeTests(unittest.TestCase):
    def setUp(self):
        sub._probe_cache.clear(); sub._probe_inflight.clear()
    def test_reports_real_http_error(self):
        with mock.patch.object(sub,"_probe",side_effect=_http_error(403)):
            ok,_,reason=sub.check_subscription(URL,retries=0)
        self.assertFalse(ok); self.assertIn("403",reason)
    def test_result_cache(self):
        calls=[]
        def fake(url,timeout):calls.append(url); return True,{"total":1},""
        with mock.patch.object(sub,"_probe",side_effect=fake):
            for _ in range(5):self.assertTrue(sub.check_subscription(URL)[0])
        self.assertEqual(len(calls),1)
    def test_single_flight_on_concurrent_miss(self):
        calls=[]; lock=threading.Lock()
        def fake(url,timeout):
            with lock:calls.append(url)
            time.sleep(.08)
            return True,{"total":1},""
        with mock.patch.object(sub,"_probe",side_effect=fake):
            results=[]
            ts=[threading.Thread(target=lambda:results.append(sub.check_subscription(URL,retries=0))) for _ in range(6)]
            [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(len(calls),1)
        self.assertEqual(len(results),6)
        self.assertTrue(all(x[0] for x in results))
    def test_transient_failure_retries(self):
        seq=[_http_error(429,"Too Many Requests"),None]
        def fake(url,timeout):
            x=seq.pop(0)
            if x:raise x
            return True,{"total":5},""
        with mock.patch.object(sub,"_probe",side_effect=fake),mock.patch.object(sub.time,"sleep",lambda *_:None):
            ok,info,_=sub.check_subscription(URL)
        self.assertTrue(ok); self.assertEqual(info,{"total":5})
    def test_html_200_is_not_subscription(self):
        self.assertFalse(sub._looks_like_subscription(b"<html><body>login</body></html>","", "text/html"))
        self.assertTrue(sub._looks_like_subscription(b"proxies:\n  - name: JP\n","", "text/yaml"))

class UserInfoTests(unittest.TestCase):
    def test_non_finite_values_are_ignored(self):
        info=sub.parse_userinfo("total=inf; upload=1e400; download=123")
        self.assertEqual(info,{"download":123})
    def test_millisecond_expire_is_normalized(self):
        text=sub.describe_userinfo({"expire":1735689600000})
        self.assertIn("2025",text)
    def test_absurd_expire_never_raises(self):
        text=sub.describe_userinfo({"expire":10**100})
        self.assertIsInstance(text,str)

class SelectUsableTests(unittest.TestCase):
    def setUp(self):
        sub._probe_cache.clear(); sub._probe_inflight.clear()
    def test_logs_reason_without_url_secret(self):
        import tempfile
        from pathlib import Path
        logs=[]
        with mock.patch.object(sub,"_probe",side_effect=_http_error(403)),mock.patch.object(sub.time,"sleep",lambda *_:None):
            usable=sub.select_usable_subscriptions([URL],Path(tempfile.mkdtemp()),logs.append)
        self.assertEqual(usable,[])
        self.assertTrue(any("403" in x for x in logs))
        self.assertFalse(any("token=x" in x for x in logs))

    def test_corrupt_or_empty_cache_is_not_accepted(self):
        import tempfile
        from pathlib import Path
        provider=Path(tempfile.mkdtemp())
        cache=provider/sub.provider_cache_name(URL)
        for payload in (b"", b"<html>login</html>", b"not a subscription"):
            cache.write_bytes(payload)
            with mock.patch.object(sub,"_probe",side_effect=_http_error(503)),\
                 mock.patch.object(sub.time,"sleep",lambda *_:None):
                usable=sub.select_usable_subscriptions([URL],provider,lambda m:None)
            self.assertEqual(usable,[])

    def test_valid_yaml_cache_allows_offline_start(self):
        import tempfile
        from pathlib import Path
        provider=Path(tempfile.mkdtemp())
        cache=provider/sub.provider_cache_name(URL)
        cache.write_text("proxies:\n  - name: JP\n    type: ss\n",encoding="utf-8")
        with mock.patch.object(sub,"_probe",side_effect=_http_error(503)),\
             mock.patch.object(sub.time,"sleep",lambda *_:None):
            usable=sub.select_usable_subscriptions([URL],provider,lambda m:None)
        self.assertEqual(usable,[URL])

if __name__=="__main__":unittest.main()
