from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import Paths, ensure_private_files, list_routing_profiles
from backend.managed_runtime import ManagedRuntime, resolve_paths


class ManagedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.source.mkdir()
        self.data = Path(self.temp.name) / "data"
        self.paths = Paths(self.source, self.data)

    def test_legacy_paths_remain_portable(self):
        paths = Paths(self.source)
        self.assertEqual(paths.private, self.source / "private")
        self.assertEqual(paths.core_exe, self.source / "core" / "mihomo.exe")

    def test_source_resources_and_user_data_are_independent(self):
        (self.source / "rules").mkdir()
        (self.source / "rules" / "sample.json").write_text(json.dumps({
            "id": "sample", "name": "Sample", "domains": ["example.org"],
        }))
        ensure_private_files(self.paths)
        self.assertTrue(self.paths.app_settings.exists())
        self.assertEqual(self.paths.core_exe, self.data / "core" / "mihomo.exe")
        self.assertEqual(self.paths.runtime, self.data / "runtime")
        self.assertFalse((self.source / "private").exists())
        self.assertEqual(list_routing_profiles(self.paths)[0]["id"], "sample")

    def test_explicit_data_dir_wins_over_environment(self):
        with patch.dict(os.environ, {"FLY_DATA_DIR": str(self.data)}):
            self.assertEqual(resolve_paths(self.source).data, self.data.resolve())
            chosen = self.data / "explicit"
            self.assertEqual(resolve_paths(self.source, str(chosen)).data, chosen.resolve())
            with self.assertRaises(ValueError):
                resolve_paths(self.source, " ")

    def test_stop_only_applies_to_the_current_session_and_is_consumed(self):
        runtime = ManagedRuntime(self.paths, "a" * 32)
        runtime.mark_ready()
        ready = json.loads(runtime.ready_path.read_text())
        self.assertEqual(ready["pid"], os.getpid())
        for session, action, expected in [
            ("b" * 32, "stop", None),
            (runtime.session, "unknown", None),
            (runtime.session, "show", "show"),
            (runtime.session, "stop", "stop"),
        ]:
            runtime.command_path.write_text(json.dumps({
                "schema": 1, "session": session, "action": action,
            }))
            self.assertEqual(runtime.poll(), expected)
            self.assertIsNone(runtime.poll())
        runtime.close()
        self.assertFalse(runtime.ready_path.exists())

    def test_session_cannot_escape_runtime_directory(self):
        for session in ("../private", "A" * 32, "a" * 31, "a" * 33):
            with self.assertRaises(ValueError):
                ManagedRuntime(self.paths, session)


if __name__ == "__main__":
    unittest.main()
