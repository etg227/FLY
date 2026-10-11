"""launcher.exe 自我替换：v0.10 起 FLY 主程序就是 launcher.exe，运行期间文件一直被占用。"""
import os, shutil, subprocess, tempfile, time, unittest
from pathlib import Path
from unittest import mock

import backend.self_update as self_update
from backend.self_update import schedule_launcher_replace


class ReplaceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "launcher.exe.new").write_bytes(b"new launcher")
        (self.root / "launcher.exe").write_bytes(b"old launcher")
        self.win = mock.patch.object(self_update, "_IS_WINDOWS", True)
        self.win.start()

    def tearDown(self):
        self.win.stop()

    def test_noop_when_nothing_is_pending(self):
        (self.root / "launcher.exe.new").unlink()
        self.assertFalse(schedule_launcher_replace(self.root))
        self.assertEqual((self.root / "launcher.exe").read_bytes(), b"old launcher")

    def test_replaces_in_process_without_spawning_anything(self):
        with mock.patch("subprocess.Popen") as popen:
            self.assertTrue(schedule_launcher_replace(self.root))
        popen.assert_not_called()
        self.assertEqual((self.root / "launcher.exe").read_bytes(), b"new launcher")
        self.assertFalse((self.root / "launcher.exe.new").exists())
        self.assertEqual(list(self.root.glob("*.cmd")), [])

    def test_old_launcher_is_removed_when_not_in_use(self):
        schedule_launcher_replace(self.root)
        self.assertEqual(list(self.root.glob("launcher.exe.old*")), [])

    def test_leftover_old_launchers_are_cleaned_next_time(self):
        (self.root / "launcher.exe.new").unlink()
        (self.root / "launcher.exe.old.1.2").write_bytes(b"stale")
        schedule_launcher_replace(self.root)
        self.assertEqual(list(self.root.glob("launcher.exe.old*")), [])

    def test_failure_rolls_back_and_keeps_pending(self):
        real_replace = os.replace
        def flaky(src, dst):
            if str(src).endswith("launcher.exe.new"):
                raise PermissionError("blocked by AV")
            return real_replace(src, dst)
        logs = []
        with mock.patch.object(self_update.os, "replace", side_effect=flaky):
            self.assertFalse(schedule_launcher_replace(self.root, logs.append))
        self.assertEqual((self.root / "launcher.exe").read_bytes(), b"old launcher",
                         "失败后必须回滚，目录里不能没有启动器")
        self.assertTrue((self.root / "launcher.exe.new").exists(), "留到下次重试")
        self.assertTrue(any("替换失败" in x for x in logs))

    def test_first_install_without_existing_launcher(self):
        (self.root / "launcher.exe").unlink()
        self.assertTrue(schedule_launcher_replace(self.root))
        self.assertEqual((self.root / "launcher.exe").read_bytes(), b"new launcher")

    def test_non_windows_is_a_noop(self):
        with mock.patch.object(self_update, "_IS_WINDOWS", False):
            self.assertFalse(schedule_launcher_replace(self.root))
        self.assertTrue((self.root / "launcher.exe.new").exists())


@unittest.skipUnless(os.name == "nt", "需要真实 Windows 的 exe 文件锁语义")
class RunningExecutableTests(unittest.TestCase):
    """核心前提：Windows 不让覆盖运行中的 exe，但允许改名。在真机上验证。"""

    def test_running_launcher_is_replaced(self):
        root = Path(tempfile.mkdtemp())
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        exe = root / "launcher.exe"
        shutil.copy2(system32 / "PING.EXE", exe)          # 只依赖系统 DLL，拷走也能跑
        new_bytes = (system32 / "PING.EXE").read_bytes() + b"\0FLY-NEW"
        (root / "launcher.exe.new").write_bytes(new_bytes)
        proc = subprocess.Popen([str(exe), "-n", "30", "127.0.0.1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1.0)
            self.assertIsNone(proc.poll(), "替身进程应仍在运行")
            with self.assertRaises(PermissionError):
                os.remove(exe)                            # 证明文件确实被运行中的进程占用
            self.assertTrue(schedule_launcher_replace(root))
            self.assertEqual(exe.read_bytes(), new_bytes)
            self.assertIsNone(proc.poll(), "替换不应影响正在运行的旧进程")
            self.assertEqual(len(list(root.glob("launcher.exe.old*"))), 1, "运行中的旧文件留待下次清理")
        finally:
            proc.kill(); proc.wait(timeout=10)
        time.sleep(0.5)
        (root / "launcher.exe.new").write_bytes(b"x")       # 触发下一次调用
        os.remove(root / "launcher.exe.new")
        schedule_launcher_replace(root)
        self.assertEqual(list(root.glob("launcher.exe.old*")), [], "旧进程退出后应被清理")


if __name__ == "__main__":
    unittest.main()
