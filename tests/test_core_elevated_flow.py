"""提权路径下 CoreManager 的行为：日志走 API 流、退出被感知、stop 能终止。"""
import subprocess, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock

import backend.core_manager as cm
from backend.config import Paths
from backend.core_manager import CoreError, CoreManager
from backend.elevated_launch import ElevationCancelled


class FakeElevated:
    stdout = None
    def __init__(self):
        self.alive = True
        self.returncode = None
        self.terminated = False
        self.closed = False
        self._exited = threading.Event()
    def poll(self):
        return self.returncode if not self.alive else None
    def wait(self, timeout=None):
        if timeout is None:
            self._exited.wait()
        elif not self._exited.wait(timeout):
            raise subprocess.TimeoutExpired("mihomo(elevated)", timeout)
        return self.returncode
    def terminate(self):
        self.terminated = True
        self.exit(1)
    kill = terminate
    def close(self): self.closed = True
    def exit(self, code):
        self.alive = False; self.returncode = code; self._exited.set()


def _mk(paths_dir):
    paths = Paths(Path(paths_dir))
    logs = []
    exits = []
    lines = []
    mgr = CoreManager(paths, logs.append, on_exit=exits.append, on_line=lines.append)
    return mgr, logs, exits, lines


class ElevatedFlowTests(unittest.TestCase):
    def _start(self, mgr, proc, stream_rec):
        cfg = Path(tempfile.mkdtemp()) / "config.yaml"; cfg.write_text("x", encoding="utf-8")
        def fake_stream(port, secret, on_line, log, alive, level="warning"):
            stop = threading.Event()
            stream_rec.append({"port": port, "secret": secret, "on_line": on_line,
                               "alive": alive, "stop": stop})
            return stop, threading.Thread(target=lambda: None)
        with mock.patch.object(cm, "launch_elevated", return_value=proc) as le, \
             mock.patch.object(cm, "start_stream", side_effect=fake_stream), \
             mock.patch.object(cm, "build_runtime_config",
                               return_value=(cfg.parent, cfg, [])), \
             mock.patch.object(cm, "redact_runtime_config"), \
             mock.patch.object(CoreManager, "validate"), \
             mock.patch.object(CoreManager, "cleanup_orphans"), \
             mock.patch.object(CoreManager, "_preflight_ports"), \
             mock.patch.object(CoreManager, "_assign_job"), \
             mock.patch.object(CoreManager, "_controller_ready", return_value=True):
            mgr.start(["dmm"], elevate=True)
        return le

    def test_elevated_start_uses_api_log_stream_with_secret(self):
        mgr, logs, exits, lines = _mk(tempfile.mkdtemp())
        proc, rec = FakeElevated(), []
        le = self._start(mgr, proc, rec)
        self.assertTrue(le.called)
        self.assertEqual(len(rec), 1)
        self.assertTrue(rec[0]["secret"])
        rec[0]["on_line"]("dial FLY-JP error: x")      # 流内容进入 on_line 钩子
        self.assertEqual(lines, ["dial FLY-JP error: x"])
        mgr.stop()

    def test_unexpected_exit_is_detected_via_handle_wait(self):
        mgr, logs, exits, lines = _mk(tempfile.mkdtemp())
        proc, rec = FakeElevated(), []
        self._start(mgr, proc, rec)
        proc.exit(9)
        for _ in range(100):
            if exits: break
            time.sleep(0.02)
        self.assertEqual(exits, [9], "提权内核死掉必须触发 on_exit")
        self.assertTrue(rec[0]["stop"].is_set(), "内核退出后必须叫停日志流")

    def test_stop_terminates_and_closes_handle(self):
        mgr, logs, exits, lines = _mk(tempfile.mkdtemp())
        proc, rec = FakeElevated(), []
        self._start(mgr, proc, rec)
        mgr.stop()
        self.assertTrue(proc.terminated)
        self.assertTrue(proc.closed, "句柄必须关闭")
        self.assertTrue(rec[0]["stop"].is_set())
        self.assertEqual(exits, [], "主动停止不算意外退出")

    def test_uac_cancel_becomes_friendly_core_error(self):
        mgr, logs, exits, lines = _mk(tempfile.mkdtemp())
        cfg = Path(tempfile.mkdtemp()) / "c.yaml"; cfg.write_text("x", encoding="utf-8")
        with mock.patch.object(cm, "launch_elevated",
                               side_effect=ElevationCancelled("已取消")), \
             mock.patch.object(cm, "build_runtime_config",
                               return_value=(cfg.parent, cfg, [])), \
             mock.patch.object(CoreManager, "validate"), \
             mock.patch.object(CoreManager, "cleanup_orphans"), \
             mock.patch.object(CoreManager, "_preflight_ports"):
            with self.assertRaises(CoreError) as ctx:
                mgr.start(["dmm"], elevate=True)
        self.assertIn("取消授权", str(ctx.exception))
        self.assertIsNone(mgr.process)

    def test_non_elevated_path_still_uses_stdout_pipe(self):
        mgr, logs, exits, lines = _mk(tempfile.mkdtemp())
        class BlockingStdout:
            """真实内核运行期间管道保持打开——替身也得如此，
            否则读完即触发 _notify_exit，被误判为内核退出。"""
            def __init__(self): self.released = threading.Event()
            def __iter__(self): return self
            def __next__(self):
                self.released.wait(timeout=5)
                raise StopIteration
        class FakePopen:
            def __init__(self): self.stdout = BlockingStdout()
            def poll(self): return None
            def wait(self, timeout=None): return 0
            def terminate(self): pass
            kill = terminate
        cfg = Path(tempfile.mkdtemp()) / "c.yaml"; cfg.write_text("x", encoding="utf-8")
        streams = []
        with mock.patch.object(cm.subprocess, "Popen", return_value=FakePopen()), \
             mock.patch.object(cm, "start_stream", side_effect=lambda *a, **k: streams.append(1)), \
             mock.patch.object(cm, "build_runtime_config", return_value=(cfg.parent, cfg, [])), \
             mock.patch.object(cm, "redact_runtime_config"), \
             mock.patch.object(CoreManager, "validate"), \
             mock.patch.object(CoreManager, "cleanup_orphans"), \
             mock.patch.object(CoreManager, "_preflight_ports"), \
             mock.patch.object(CoreManager, "_assign_job"), \
             mock.patch.object(CoreManager, "_controller_ready", return_value=True):
            mgr.start(["dmm"], elevate=False)
        self.assertEqual(streams, [], "普通路径不该启用 API 日志流")
        mgr.stop()
        mgr.process = None


if __name__ == "__main__":
    unittest.main()
