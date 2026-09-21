"""launcher.exe 的延后替换：不能借道可写临时目录，因为 FLY 可能是提权运行的。"""
import subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

import backend.self_update as self_update
from backend.self_update import _REPLACER, schedule_launcher_replace


class Recorder:
    def __init__(self): self.calls = []
    def __call__(self, argv, **kw): self.calls.append((argv, kw)); return None


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "launcher.exe.new").write_bytes(b"new launcher")
        (self.root / "launcher.exe").write_bytes(b"old launcher")

    def test_noop_when_nothing_is_pending(self):
        (self.root / "launcher.exe.new").unlink()
        spawn = Recorder()
        with mock.patch.object(self_update, "_IS_WINDOWS", True):
            self.assertFalse(schedule_launcher_replace(self.root, spawn=spawn))
        self.assertEqual(spawn.calls, [])

    def test_does_not_write_any_script_file(self):
        before = set(Path(tempfile.gettempdir()).glob("fly-launcher-update-*"))
        spawn = Recorder()
        with mock.patch.object(self_update, "_IS_WINDOWS", True):
            self.assertTrue(schedule_launcher_replace(self.root, spawn=spawn))
        after = set(Path(tempfile.gettempdir()).glob("fly-launcher-update-*"))
        self.assertEqual(before, after, "不得在临时目录留下可被掉包的脚本")
        self.assertEqual(list(self.root.glob("*.cmd")), [])

    def test_runs_the_current_interpreter_not_a_shell_script(self):
        spawn = Recorder()
        with mock.patch.object(self_update, "_IS_WINDOWS", True), \
             mock.patch.object(self_update.sys, "frozen", False, create=True):
            schedule_launcher_replace(self.root, spawn=spawn)
        argv = spawn.calls[0][0]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1], "-c")
        self.assertNotIn("cmd.exe", " ".join(argv).lower())
        for part in argv:
            self.assertFalse(str(part).lower().endswith(".cmd"))
        # 源码是 argv 里的字面量，不是磁盘上的路径
        self.assertIn("os.replace", argv[2])
        self.assertEqual(argv[3:], [str(self.root / "launcher.exe.new"),
                                    str(self.root / "launcher.exe")])

    def test_frozen_mode_uses_powershell_with_paths_in_environment(self):
        spawn = Recorder()
        with mock.patch.object(self_update, "_IS_WINDOWS", True), \
             mock.patch.object(self_update.sys, "frozen", True, create=True):
            self.assertTrue(schedule_launcher_replace(self.root, spawn=spawn))
        argv, kw = spawn.calls[0]
        self.assertEqual(argv[0].lower(), "powershell.exe")
        self.assertIn("-Command", argv)
        self.assertNotIn(str(self.root), " ".join(argv),
                         "user-controlled paths must not be interpolated into PowerShell code")
        self.assertEqual(kw["env"]["FLY_REPLACE_SRC"],
                         str(self.root / "launcher.exe.new"))
        self.assertEqual(kw["env"]["FLY_REPLACE_DST"],
                         str(self.root / "launcher.exe"))
        self.assertEqual(list(self.root.glob("*.cmd")), [])

    def test_replacer_source_actually_replaces(self):
        src, dst = self.root / "launcher.exe.new", self.root / "launcher.exe"
        r = subprocess.run([sys.executable, "-c", _REPLACER, str(src), str(dst)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(dst.read_bytes(), b"new launcher")
        self.assertFalse(src.exists())

    def test_spawn_failure_is_reported_not_raised(self):
        def boom(*a, **k): raise OSError("nope")
        logs = []
        with mock.patch.object(self_update, "_IS_WINDOWS", True):
            self.assertFalse(schedule_launcher_replace(self.root, logs.append, spawn=boom))
        self.assertTrue(any("失败" in x for x in logs))


if __name__ == "__main__":
    unittest.main()
