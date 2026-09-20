"""节点被墙时，内核的出站失败日志要能立刻触发重选，而不是干等三分钟。"""
import unittest
from backend.core_manager import CoreManager, DialFailureTracker

# 取自真实日志（v0.8.9，节点 IP 被墙后的表现）
FAIL = ('time="..." level=warning msg="[TCP] dial FLY-JP (match DomainSuffix/google.com) '
        '127.0.0.1:61795 --> www.google.com:443 error: jp3.miyazono-kaori.com:443 '
        'connect error: dial tcp 35.72.161.13:443: i/o timeout"')
OK_LINE = ('time="..." level=info msg="[TCP] 127.0.0.1:61826 --> '
           'static.cloudflareinsights.com:443 match Match using DIRECT"')
DIRECT_FAIL = ('time="..." level=warning msg="[TCP] dial DIRECT (match Match/) '
               '127.0.0.1:55049 --> www.google.com:443 error: dial tcp 31.13.92.37:443: i/o timeout"')


class FakeClock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.tk = DialFailureTracker(window=20.0, trigger=5, cooldown=60.0, clock=self.clock)

    def test_triggers_after_enough_failures(self):
        fired = [self.tk.feed(FAIL) for _ in range(5)]
        self.assertEqual(fired, [False, False, False, False, True])

    def test_healthy_lines_never_trigger(self):
        for _ in range(50):
            self.assertFalse(self.tk.feed(OK_LINE))

    def test_direct_failures_are_ignored(self):
        # 直连失败是用户自己的网络问题，跟日本节点无关，不该触发换节点
        for _ in range(50):
            self.assertFalse(self.tk.feed(DIRECT_FAIL))

    def test_failures_outside_the_window_do_not_accumulate(self):
        for _ in range(4):
            self.tk.feed(FAIL)
            self.clock.advance(6)        # 每 6 秒一次，窗口 20 秒内最多 4 条
        self.assertFalse(self.tk.feed(FAIL))

    def test_cooldown_prevents_flapping(self):
        for _ in range(5):
            self.tk.feed(FAIL)           # 第一次触发
        self.clock.advance(1)
        for _ in range(10):
            self.assertFalse(self.tk.feed(FAIL), "冷却期内不该反复触发")
        self.clock.advance(60)
        fired = [self.tk.feed(FAIL) for _ in range(5)]
        self.assertTrue(fired[-1])       # 冷却期过后可以再次触发

    def test_reset_clears_state(self):
        for _ in range(4): self.tk.feed(FAIL)
        self.tk.reset()
        self.assertFalse(self.tk.feed(FAIL))


class CoreLineHookTests(unittest.TestCase):
    def test_core_forwards_every_line_to_the_hook(self):
        import tempfile
        from pathlib import Path
        from backend.config import Paths

        seen = []
        cm = CoreManager(Paths(Path(tempfile.mkdtemp())), lambda m: None, on_line=seen.append)

        class FakeProc:
            def __init__(self): self.stdout = iter(["alpha", "", "beta"])
            def wait(self, timeout=None): return 0
            def poll(self): return 0

        proc = FakeProc()
        cm.process = proc
        cm._stopping = True              # 只验证转发，不触发退出回调
        cm._read_output(proc)
        self.assertEqual(seen, ["alpha", "beta"])

    def test_hook_failure_cannot_kill_the_reader(self):
        import tempfile
        from pathlib import Path
        from backend.config import Paths

        logs = []
        def boom(line): raise RuntimeError("hook broken")
        cm = CoreManager(Paths(Path(tempfile.mkdtemp())), logs.append, on_line=boom)

        class FakeProc:
            def __init__(self): self.stdout = iter(["a", "b"])
            def wait(self, timeout=None): return 0
            def poll(self): return 0

        proc = FakeProc()
        cm.process = proc
        cm._stopping = True
        cm._read_output(proc)            # 不应抛出
        self.assertEqual(len([x for x in logs if x.startswith("[CORE] ")]), 2)


if __name__ == "__main__":
    unittest.main()
