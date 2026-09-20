"""Windows-only live TUN smoke test for disposable GitHub runners."""
from __future__ import annotations
import os, shutil, socket, sys, tempfile, time, unittest, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
if os.name!="nt" or os.environ.get("FLY_WINDOWS_TUN_LIVE")!="1":
    raise SystemExit("requires Windows + FLY_WINDOWS_TUN_LIVE=1")

from backend.config import Paths, ensure_private_files, save_custom_profiles, update_app_settings, load_app_settings
from backend.core_installer import install_core
from backend.core_manager import CoreManager

def free_port():
    s=socket.socket(); s.bind(("127.0.0.1",0)); p=s.getsockname()[1]; s.close(); return p

class TunLive(unittest.TestCase):
    def test_real_elevated_tun_auto_route_and_cleanup(self):
        root=Path(tempfile.mkdtemp(prefix="fly-tun-live-"))
        paths=Paths(root)
        logs=[]
        try:
            (paths.app/"rules").mkdir(parents=True,exist_ok=True)
            ensure_private_files(paths)
            install_core(paths,logs.append)
            save_custom_profiles(paths,[{
                "id":"tun-live",
                "name":"TUN Live",
                "launch_mode":"tun",
                "processes":["definitely-not-running-fly-test.exe"],
                "domains":["example.invalid"],
            }])
            paths.nodes_yaml.write_text(
                "proxies:\n"
                "  - name: 'Japan TUN Dummy'\n"
                "    type: ss\n"
                "    server: 127.0.0.1\n"
                "    port: 9\n"
                "    cipher: aes-128-gcm\n"
                "    password: test\n",
                encoding="utf-8",
            )
            mixed,ctrl=free_port(),free_port()
            while ctrl==mixed: ctrl=free_port()
            update_app_settings(paths,{
                "mixed_port":mixed,
                "controller_port":ctrl,
                "services_enabled":False,
            })

            mgr=CoreManager(paths,logs.append)
            try:
                mgr.start(["tun-live"],elevate=True)
                self.assertTrue(mgr.is_running())
                cfg=(paths.runtime/"mihomo"/"config.yaml").read_text(encoding="utf-8")
                self.assertIn("tun:\n  enable: true",cfg)
                self.assertIn("auto-route: true",cfg)
                secret=load_app_settings(paths)["api_secret"]
                self.assertTrue(mgr._controller_ready(ctrl,secret))

                # Real outbound connectivity while TUN auto-route is active.
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                req=urllib.request.Request(
                    "https://www.gstatic.com/generate_204",
                    headers={"User-Agent":"FLY-Windows-TUN-Test"},
                )
                with opener.open(req,timeout=12) as resp:
                    self.assertIn(resp.status,(200,204))
            finally:
                mgr.stop()

            time.sleep(.5)
            self.assertFalse(mgr.is_running())
            # Port must be free again after stop; catches orphaned core/listeners.
            s=socket.socket()
            try:
                s.bind(("127.0.0.1",ctrl))
            finally:
                s.close()
        finally:
            shutil.rmtree(root,ignore_errors=True)

if __name__=="__main__":
    unittest.main(verbosity=2)
