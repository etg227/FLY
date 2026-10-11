"""UI memory settings, generated icon format and the Windows tray icon."""
import shutil, struct, sys, tempfile, threading, time, unittest
from pathlib import Path

from backend.config import Paths, ensure_private_files, load_app_settings, save_json, update_app_settings
from backend.app_icon import ico_bytes, render, write_icons
from backend import win_tray


class UiMemorySettingsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.p = Paths(Path(self.td))
        ensure_private_files(self.p)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_defaults(self):
        s = load_app_settings(self.p)
        self.assertEqual(s["selected_profiles"], [])
        self.assertFalse(s["selection_saved"])
        self.assertEqual((s["window_geometry"], s["last_page"]), ("", "home"))
        self.assertTrue(s["close_to_tray"])

    def test_round_trip(self):
        update_app_settings(self.p, {"selected_profiles": ["gbf", "dmm"], "selection_saved": True,
                                     "window_geometry": "1080x760+-1900+40", "last_page": "profiles",
                                     "close_to_tray": False})
        s = load_app_settings(self.p)
        self.assertEqual(s["selected_profiles"], ["gbf", "dmm"])
        self.assertTrue(s["selection_saved"])
        self.assertEqual(s["window_geometry"], "1080x760+-1900+40")
        self.assertEqual(s["last_page"], "profiles")
        self.assertFalse(s["close_to_tray"])

    def test_garbage_is_normalized(self):
        save_json(self.p.app_settings, {"selected_profiles": ["ok", "bad id,FLY-JP", 5, "ok"],
                                        "window_geometry": "100x100; rm -rf", "last_page": "../x"})
        s = load_app_settings(self.p)
        self.assertEqual(s["selected_profiles"], ["ok"])
        self.assertEqual(s["window_geometry"], "")
        self.assertEqual(s["last_page"], "home")


class IconTests(unittest.TestCase):
    def test_ico_structure(self):
        data = ico_bytes(sizes=(16, 32))
        reserved, kind, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual((reserved, kind, count), (0, 1, 2))
        for i, n in enumerate((16, 32)):
            w, h, _, _, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
            self.assertEqual((w, h, planes, bpp), (n, n, 1, 32))
            self.assertEqual(struct.unpack_from("<I", data, offset)[0], 40)
            self.assertLessEqual(offset + size, len(data))

    def test_corners_transparent_and_active_has_dot(self):
        idle, active = render(32), render(32, active=True)
        self.assertEqual(idle[0][3], 0)
        self.assertEqual(idle[16 * 32 + 16][3], 255)
        self.assertNotEqual(idle[25 * 32 + 25], active[25 * 32 + 25])

    def test_write_icons_is_cached(self):
        td = Path(tempfile.mkdtemp())
        try:
            a, b = write_icons(td)
            self.assertTrue(a.exists() and b.exists())
            m = a.stat().st_mtime_ns
            write_icons(td)
            self.assertEqual(a.stat().st_mtime_ns, m)
        finally:
            shutil.rmtree(td, ignore_errors=True)


@unittest.skipUnless(sys.platform == "win32", "Windows tray API")
class WindowsTrayTests(unittest.TestCase):
    def test_tray_lifecycle_and_show_signal(self):
        td = Path(tempfile.mkdtemp())
        try:
            idle, active = write_icons(td)
            shown = threading.Event()
            tray = win_tray.TrayIcon(idle, "FLY test", on_activate=shown.set,
                                     menu=lambda: [("显示", shown.set, True)])
            self.assertTrue(tray.start())
            self.assertIsNone(tray.error)
            # Shell_NotifyIcon may legitimately fail on a headless runner; the
            # window, message loop and API signatures must still work.
            tray.set_tooltip("FLY · 加速中")
            tray.set_icon(active)
            tray.balloon("FLY", "test", "info")
            self.assertTrue(win_tray.signal_existing_instance())
            self.assertTrue(shown.wait(3))
            tray.stop()
            self.assertFalse(tray._thread.is_alive())
            self.assertIsNone(tray.hwnd)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_no_instance_to_signal(self):
        self.assertFalse(win_tray.signal_existing_instance())


class NonWindowsTrayTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "non-Windows fallback")
    def test_unavailable_off_windows(self):
        self.assertFalse(win_tray.available())
        self.assertFalse(win_tray.signal_existing_instance())


if __name__ == "__main__":
    unittest.main()
