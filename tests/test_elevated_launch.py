"""只提权 mihomo 本体的启动器：Popen 风格语义必须精确，这是停止内核的唯一通道。"""
import subprocess, unittest

from backend.elevated_launch import (ERROR_CANCELLED, STILL_ACTIVE, WAIT_OBJECT_0,
                                     WAIT_TIMEOUT, ElevatedProcess, ElevationCancelled,
                                     ElevationError, launch_elevated)


class FakeWin:
    def __init__(self, running=True, code=0, launch_err=0, terminate_ok=True):
        self.running, self.code = running, code
        self.launch_err, self.terminate_ok = launch_err, terminate_ok
        self.calls = []

    def shell_execute_elevated(self, exe, params, cwd):
        self.calls.append(("launch", exe, params, cwd))
        if self.launch_err:
            return None, self.launch_err
        return 1234, 0

    def wait(self, handle, ms):
        self.calls.append(("wait", ms))
        return WAIT_TIMEOUT if self.running else WAIT_OBJECT_0

    def exit_code(self, handle):
        return True, self.code

    def terminate(self, handle, code=1):
        self.calls.append(("terminate", code))
        if self.terminate_ok:
            self.running = False
            self.code = code
            return True
        return False

    def pid(self, handle): return 4321
    def close(self, handle): self.calls.append(("close",))


class LaunchTests(unittest.TestCase):
    def test_arguments_are_quoted_windows_style(self):
        win = FakeWin()
        launch_elevated(r"C:\FLY\core\mihomo.exe", ["-d", r"C:\My Dir\mihomo", "-f", "cfg.yaml"],
                        cwd=r"C:\FLY", win=win)
        _, exe, params, cwd = win.calls[0]
        self.assertIn('"C:\\My Dir\\mihomo"', params)     # 含空格必须带引号
        self.assertEqual(cwd, r"C:\FLY")

    def test_uac_cancel_maps_to_dedicated_error(self):
        with self.assertRaises(ElevationCancelled):
            launch_elevated("x.exe", [], None, win=FakeWin(launch_err=ERROR_CANCELLED))

    def test_other_failures_raise_elevation_error_with_code(self):
        with self.assertRaises(ElevationError) as ctx:
            launch_elevated("x.exe", [], None, win=FakeWin(launch_err=5))
        self.assertIn("5", str(ctx.exception))

    def test_non_windows_without_backend_is_refused(self):
        import backend.elevated_launch as el
        if el.os.name != "nt":
            with self.assertRaises(ElevationError):
                launch_elevated("x.exe", [], None)


class ProcessSemanticsTests(unittest.TestCase):
    def test_poll_running_then_exit(self):
        win = FakeWin(running=True)
        p = launch_elevated("m.exe", [], None, win=win)
        self.assertIsNone(p.poll())
        win.running, win.code = False, 7
        self.assertEqual(p.poll(), 7)
        self.assertEqual(p.returncode, 7)

    def test_exit_code_259_is_not_mistaken_for_running(self):
        # 真实退出码恰好是 STILL_ACTIVE(259) 时，信号态判断保证不会误判
        win = FakeWin(running=False, code=STILL_ACTIVE)
        p = launch_elevated("m.exe", [], None, win=win)
        self.assertEqual(p.poll(), STILL_ACTIVE)

    def test_wait_timeout_raises_like_popen(self):
        p = launch_elevated("m.exe", [], None, win=FakeWin(running=True))
        with self.assertRaises(subprocess.TimeoutExpired):
            p.wait(timeout=0.01)

    def test_terminate_then_wait(self):
        win = FakeWin(running=True)
        p = launch_elevated("m.exe", [], None, win=win)
        p.terminate()
        self.assertEqual(p.wait(timeout=1), 1)

    def test_terminate_failure_is_an_error_not_silence(self):
        p = launch_elevated("m.exe", [], None, win=FakeWin(running=True, terminate_ok=False))
        with self.assertRaises(OSError):
            p.terminate()

    def test_close_is_idempotent_and_poll_after_close_keeps_code(self):
        win = FakeWin(running=False, code=0)
        p = launch_elevated("m.exe", [], None, win=win)
        self.assertEqual(p.poll(), 0)
        p.close(); p.close()
        self.assertEqual(p.poll(), 0)
        self.assertEqual([c for c in win.calls if c == ("close",)], [("close",)])


if __name__ == "__main__":
    unittest.main()
