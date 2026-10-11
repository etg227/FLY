"""onefile 自我拉起：子进程必须是独立实例，不能复用并锁住父进程的 _MEI。"""
import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

try:
    import launcher
except ImportError as exc:
    launcher, HAVE_TK, SKIP_REASON = None, False, f"launcher 需要 tkinter（{exc}）"
else:
    HAVE_TK, SKIP_REASON = True, ""

MEI = os.path.join(tempfile.gettempdir(), "_MEI00001c3c2")
PARENT_ENV = {
    "PATH": "C:/Windows",
    "_PYI_ARCHIVE_FILE": "C:/FLY/launcher.exe",          # 复用父进程环境的关键标记
    "_PYI_APPLICATION_HOME_DIR": MEI,
    "_PYI_PARENT_PROCESS_LEVEL": "1",
    "_MEIPASS2": MEI,                                    # PyInstaller 5.x 的标记
    "TCL_LIBRARY": os.path.join(MEI, "_tcl_data"),
    "TK_LIBRARY": os.path.join(MEI, "_tk_data"),
    "USER_SETTING": "keep-me",
}


def _frozen():
    return mock.patch.multiple(sys, frozen=True, _MEIPASS=MEI, create=True)


@unittest.skipUnless(HAVE_TK, SKIP_REASON)
class ChildEnvTests(unittest.TestCase):
    def _env(self, base):
        with _frozen(), mock.patch.dict(os.environ, base, clear=True):
            return launcher._frozen_child_env()

    def test_reuse_markers_and_mei_tcltk_are_stripped(self):
        env = self._env(PARENT_ENV)
        for k in ("_PYI_ARCHIVE_FILE", "_PYI_APPLICATION_HOME_DIR",
                  "_PYI_PARENT_PROCESS_LEVEL", "_MEIPASS2", "TCL_LIBRARY", "TK_LIBRARY"):
            self.assertNotIn(k, env, k)
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(env["USER_SETTING"], "keep-me")

    def test_user_tcl_library_outside_mei_is_kept(self):
        own = os.path.join(tempfile.gettempdir(), "my-tcl")
        self.assertEqual(self._env({**PARENT_ENV, "TCL_LIBRARY": own})["TCL_LIBRARY"], own)

    def test_prefix_collision_is_not_treated_as_inside_mei(self):
        sibling = MEI + "9" + os.sep + "_tcl_data"          # _MEI00001c3c29 ≠ _MEI00001c3c2
        self.assertEqual(self._env({**PARENT_ENV, "TCL_LIBRARY": sibling})["TCL_LIBRARY"], sibling)

    def test_source_mode_leaves_environment_alone(self):
        with mock.patch.object(sys, "frozen", False, create=True), \
             mock.patch.dict(os.environ, PARENT_ENV, clear=True):
            self.assertEqual(launcher._frozen_child_env(), PARENT_ENV)


@unittest.skipUnless(HAVE_TK, SKIP_REASON)
class LauncherSpawnTests(unittest.TestCase):
    def test_run_main_child_is_forced_to_be_an_independent_instance(self):
        with _frozen(), mock.patch.dict(os.environ, PARENT_ENV, clear=True), \
             mock.patch.object(launcher.subprocess, "Popen") as popen:
            launcher.launch(Path(tempfile.mkdtemp()), ["launcher.exe", "--run-main", "C:/FLY"])
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env.get("PYINSTALLER_RESET_ENVIRONMENT"), "1")
        self.assertNotIn("_PYI_ARCHIVE_FILE", env, "留着它子进程就会复用父进程的 _MEI")
        self.assertNotIn("TCL_LIBRARY", env)
        self.assertEqual(env["USER_SETTING"], "keep-me")

    def test_source_mode_launch_does_not_inject_pyinstaller_vars(self):
        with mock.patch.object(sys, "frozen", False, create=True), \
             mock.patch.object(launcher.subprocess, "Popen") as popen:
            launcher.launch(Path(tempfile.mkdtemp()), ["python", "main.py"])
        self.assertNotIn("PYINSTALLER_RESET_ENVIRONMENT", popen.call_args.kwargs["env"])

    def test_dll_reset_is_noop_outside_frozen_windows(self):
        with launcher._DllSearchReset() as r:
            self.assertIsNone(r.prev)


if __name__ == "__main__":
    unittest.main()
