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
        with mock.patch.object(sp.os,"name","nt"),\
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
        with mock.patch.object(sp.os,"name","nt"),\
             mock.patch.object(sp,"_read_current",return_value={"ProxyEnable":1,"ProxyServer":"127.0.0.1:17890","ProxyOverride":""}),\
             mock.patch.object(sp,"_write") as write:
            p.restore_orphan()
        write.assert_called_once_with(backup["original"])

if __name__=="__main__":unittest.main()
