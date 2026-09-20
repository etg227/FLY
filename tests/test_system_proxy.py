"""System proxy ownership tests."""
import json, tempfile, unittest
from pathlib import Path
from unittest import mock
import backend.system_proxy as sp

class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.path=Path(tempfile.mkdtemp())/"backup.json"; self.logs=[]
    def test_restore_does_not_clobber_user_changed_proxy(self):
        backup={"version":2,"fly_proxy_server":"127.0.0.1:17890",
                "original":{"ProxyEnable":0,"ProxyServer":"","ProxyOverride":""}}
        self.path.write_text(json.dumps(backup),encoding="utf-8")
        p=sp.SystemProxy(self.path,self.logs.append)
        with mock.patch.object(sp,"_IS_WINDOWS",True),\
             mock.patch.object(sp,"_read_current",return_value={"ProxyEnable":1,"ProxyServer":"127.0.0.1:9999","ProxyOverride":""}),\
             mock.patch.object(sp,"_write") as write:
            p.restore()
        write.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_orphan_restore_only_when_current_proxy_is_flys(self):
        backup={"version":2,"fly_proxy_server":"127.0.0.1:17890",
                "original":{"ProxyEnable":1,"ProxyServer":"corp:8080","ProxyOverride":"<local>"}}
        self.path.write_text(json.dumps(backup),encoding="utf-8")
        p=sp.SystemProxy(self.path,self.logs.append)
        with mock.patch.object(sp,"_IS_WINDOWS",True),\
             mock.patch.object(sp,"_read_current",return_value={"ProxyEnable":1,"ProxyServer":"127.0.0.1:17890","ProxyOverride":""}),\
             mock.patch.object(sp,"_write") as write:
            p.restore_orphan()
        write.assert_called_once_with(backup["original"])

    def test_enable_failure_rolls_back_before_returning(self):
        original={"ProxyEnable":1,"ProxyServer":"corp:8080","ProxyOverride":"<local>"}
        p=sp.SystemProxy(self.path,self.logs.append)
        calls=[]
        def fake_write(values):
            calls.append(dict(values))
            if len(calls)==1:
                raise OSError("registry write failed")
        with mock.patch.object(sp,"_IS_WINDOWS",True),\
             mock.patch.object(sp,"_read_current",return_value=original),\
             mock.patch.object(sp,"_write",side_effect=fake_write):
            with self.assertRaises(OSError):
                p.enable(17890)
        self.assertEqual(calls[1],original)
        self.assertFalse(self.path.exists(), "successful rollback should clear the ownership backup")

    def test_failed_enable_keeps_backup_when_rollback_also_fails(self):
        original={"ProxyEnable":0,"ProxyServer":"","ProxyOverride":""}
        p=sp.SystemProxy(self.path,self.logs.append)
        with mock.patch.object(sp,"_IS_WINDOWS",True),\
             mock.patch.object(sp,"_read_current",return_value=original),\
             mock.patch.object(sp,"_write",side_effect=[OSError("enable"),OSError("rollback")]):
            with self.assertRaises(OSError):
                p.enable(17890)
        self.assertTrue(self.path.exists(), "backup must remain for next-start orphan recovery")

if __name__=="__main__":unittest.main()
