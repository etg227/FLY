from __future__ import annotations
import os
import unittest
import uuid

from launcher import _LauncherMutex


class LauncherMutexTests(unittest.TestCase):
    def test_default_name_uses_single_local_namespace_separator(self):
        m=_LauncherMutex()
        self.assertEqual(m.name, r"Local\FLY-etg227-launcher")
        self.assertNotIn(r"Local\\", m.name)

    @unittest.skipUnless(os.name=="nt", "native Windows mutex semantics")
    def test_native_windows_single_instance_and_release(self):
        name=rf"Local\FLY-etg227-launcher-test-{uuid.uuid4().hex}"
        first=_LauncherMutex(name)
        second=_LauncherMutex(name)
        third=_LauncherMutex(name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.close()
            self.assertTrue(third.acquire())
        finally:
            first.close(); second.close(); third.close()

    @unittest.skipUnless(os.name=="nt", "native Windows mutex error semantics")
    def test_invalid_windows_mutex_name_is_not_reported_as_existing_instance(self):
        # Backslashes are not allowed inside the object-name portion after
        # the Local\ namespace prefix. This reproduces the v0.9.0 typo.
        bad=_LauncherMutex(r"Local\FLY\invalid")
        with self.assertRaises(OSError):
            bad.acquire()


if __name__=="__main__":
    unittest.main(verbosity=2)
