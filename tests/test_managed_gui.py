"""Exercise the real Tk lifecycle without modifying the machine's proxy/core."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import Paths, save_json
from backend.managed_runtime import ManagedRuntime

try:
    import tkinter as tk
    import main as fly
except ImportError:
    tk, fly = None, None


@unittest.skipIf(tk is None, "Tkinter is not available")
class ManagedGuiTests(unittest.TestCase):
    def test_manager_stop_restores_proxy_before_core_shutdown_and_exits_tk(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = Paths(Path(__file__).resolve().parent.parent, Path(directory))
            managed = ManagedRuntime(paths, "a" * 32)
            try:
                root = tk.Tk()
            except tk.TclError as error:
                self.skipTest(f"No Tk display: {error}")
            root.withdraw()
            events = []
            with patch.object(fly, "PATHS", paths), \
                 patch.object(fly.FlyApp, "_setup_tray"), \
                 patch.object(fly.CoreManager, "cleanup_orphans"), \
                 patch.object(fly, "schedule_launcher_replace") as replace:
                app = fly.FlyApp(root, managed_runtime=managed)
                self.assertTrue(managed.ready_path.exists())
                replace.assert_not_called()
                app.sysproxy_active = True
                with patch.object(app.sysproxy, "restore", side_effect=lambda: events.append("proxy")), \
                     patch.object(app.core, "stop", side_effect=lambda: events.append("core")):
                    save_json(managed.command_path, {
                        "schema": 1, "session": managed.session, "action": "stop",
                    })
                    # Same callback used by the scheduled main-thread poll.
                    app._poll_managed_runtime()
                self.assertTrue(app._closing)
                self.assertEqual(events, ["proxy", "core"])
                self.assertFalse(app.sysproxy_active)
            managed.close()
            self.assertFalse(managed.ready_path.exists())


if __name__ == "__main__":
    unittest.main()
