"""API 日志流：提权内核唯一的日志通道，故障切换信号依赖它。"""
import json, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.core_log_stream import CoreLogStream, start_stream

SECRET = "s3cret"


class StreamHandler(BaseHTTPRequestHandler):
    lines = []
    require_auth = True
    seen_headers = []

    def log_message(self, *a): pass

    def do_GET(self):
        type(self).seen_headers.append(dict(self.headers))
        if self.require_auth and self.headers.get("Authorization") != f"Bearer {SECRET}":
            self.send_response(401); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            for item in type(self).lines:
                self.wfile.write((json.dumps(item) if isinstance(item, dict) else item).encode() + b"\n")
                self.wfile.flush()
                time.sleep(0.01)
        except BrokenPipeError:
            pass


class LogStreamTests(unittest.TestCase):
    def setUp(self):
        StreamHandler.lines = []
        StreamHandler.seen_headers = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), StreamHandler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.payloads, self.logs = [], []

    def tearDown(self):
        self.srv.shutdown()

    def _run(self, alive=lambda: True, wait=0.6):
        stop, t = start_stream(self.port, SECRET, self.payloads.append,
                               self.logs.append, alive)
        time.sleep(wait)
        stop.set()
        t.join(timeout=3)
        self.assertFalse(t.is_alive())

    def test_payloads_reach_on_line_and_log(self):
        dial = '[TCP] dial FLY-JP (match DomainSuffix/google.com) error: dial tcp 1.2.3.4:443: i/o timeout'
        StreamHandler.lines = [{"type": "warning", "payload": dial}]
        self._run()
        self.assertIn(dial, self.payloads)                     # 喂给 DialFailureTracker 的原文
        self.assertTrue(any(dial in x for x in self.logs))     # 用户可见日志

    def test_auth_header_is_sent(self):
        StreamHandler.lines = [{"type": "info", "payload": "x"}]
        self._run()
        self.assertTrue(all(h.get("Authorization") == f"Bearer {SECRET}"
                            for h in StreamHandler.seen_headers))

    def test_malformed_lines_do_not_kill_the_stream(self):
        StreamHandler.lines = ["{broken json", {"type": "warning", "payload": "after-broken"}]
        self._run()
        self.assertIn("after-broken", self.payloads)

    def test_reconnects_after_server_closes_stream(self):
        StreamHandler.lines = [{"type": "warning", "payload": "hello"}]
        self._run(wait=1.2)
        # 服务器每次发完即断流；能收到多次说明重连生效
        self.assertGreaterEqual(self.payloads.count("hello"), 2)

    def test_stops_when_core_process_dies(self):
        StreamHandler.lines = [{"type": "info", "payload": "x"}]
        alive_flag = {"v": True}
        stop, t = start_stream(self.port, SECRET, self.payloads.append,
                               self.logs.append, lambda: alive_flag["v"])
        time.sleep(0.3)
        alive_flag["v"] = False
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "内核退出后流线程必须自行结束")

    def test_hook_exception_does_not_kill_stream(self):
        StreamHandler.lines = [{"type": "warning", "payload": "boom"},
                               {"type": "warning", "payload": "still-alive"}]
        def bad_hook(payload):
            if payload == "boom": raise RuntimeError("hook broken")
            self.payloads.append(payload)
        stop, t = start_stream(self.port, SECRET, bad_hook, self.logs.append, lambda: True)
        time.sleep(0.6); stop.set(); t.join(timeout=3)
        self.assertIn("still-alive", self.payloads)


class UnreachableApiTests(unittest.TestCase):
    def test_unreachable_api_warns_once_and_keeps_retrying(self):
        logs = []
        stop = threading.Event()
        stream = CoreLogStream(1, "", None, logs.append, stop, lambda: True,
                               reconnect_delay=0.05)
        t = threading.Thread(target=stream.run, daemon=True)
        t.start(); time.sleep(0.8); stop.set(); t.join(timeout=3)
        warns = [x for x in logs if "日志流暂不可用" in x]
        self.assertEqual(len(warns), 1, "重试期间不该刷屏")


if __name__ == "__main__":
    unittest.main()
