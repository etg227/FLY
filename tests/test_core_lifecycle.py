"""内核意外退出必须被察觉——否则系统代理会一直指向一个死端口。"""
import tempfile, unittest
from pathlib import Path

from backend.config import Paths
from backend.core_manager import CoreManager


class FakeProc:
    def __init__(self, lines=(), code=1):
        self.stdout = iter(lines)
        self._code = code

    def wait(self, timeout=None):
        return self._code

    def poll(self):
        return self._code


class CoreExitTests(unittest.TestCase):
    def setUp(self):
        self.paths = Paths(Path(tempfile.mkdtemp()))
        self.logs = []
        self.seen = []
        self.cm = CoreManager(self.paths, self.logs.append, on_exit=self.seen.append)

    def test_unexpected_exit_triggers_callback(self):
        proc = FakeProc(["line one", "line two"], code=2)
        self.cm.process = proc
        self.cm._stopping = False
        self.cm._read_output(proc)
        self.assertEqual(self.seen, [2])
        self.assertIsNone(self.cm.process)
        self.assertTrue(any("意外退出" in x for x in self.logs))

    def test_deliberate_stop_is_not_reported_as_crash(self):
        proc = FakeProc([], code=0)
        self.cm.process = proc
        self.cm._stopping = True          # stop() 会置位
        self.cm._read_output(proc)
        self.assertEqual(self.seen, [])

    def test_stale_reader_thread_is_ignored(self):
        old, new = FakeProc([], 1), FakeProc([], 0)
        self.cm.process = new             # 已经换了新进程
        self.cm._stopping = False
        self.cm._read_output(old)
        self.assertEqual(self.seen, [])
        self.assertIs(self.cm.process, new)

    def test_exit_handler_failure_does_not_escape(self):
        def boom(code): raise RuntimeError("handler broken")
        self.cm.on_exit = boom
        proc = FakeProc([], 1)
        self.cm.process = proc
        self.cm._stopping = False
        self.cm._read_output(proc)        # 不应抛出
        self.assertTrue(any("exit handler failed" in x for x in self.logs))


if __name__ == "__main__":
    unittest.main()
