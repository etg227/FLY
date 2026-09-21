"""Disposable native-Windows release-candidate robustness gate."""
from __future__ import annotations
import gc, os, shutil, socket, sys, tempfile, time, unittest, urllib.request, warnings
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
if os.name!="nt" or os.environ.get("FLY_WINDOWS_RC_LIVE")!="1":
    raise SystemExit("requires native Windows + FLY_WINDOWS_RC_LIVE=1")

from backend.config import (Paths, ensure_private_files, load_app_settings,
                            save_custom_profiles, update_app_settings)
from backend.core_installer import VALID, install_core, inspect_core
from backend.core_manager import CoreManager
from backend.elevated_launch import lock_launch_inputs
from backend.system_proxy import SystemProxy, _read_current, _write

def free_port():
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    s.bind(("127.0.0.1",0))
    p=s.getsockname()[1]
    s.close()
    return p

def unique_ports():
    a,b=free_port(),free_port()
    while a==b:
        b=free_port()
    return a,b

class WindowsLaunchLockLive(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix="fly-lock-live-"))
        self.exe=self.root/"core"/"mihomo.exe"
        self.cfg=self.root/"runtime"/"mihomo"/"config.yaml"
        self.exe.parent.mkdir(parents=True)
        self.cfg.parent.mkdir(parents=True)
        self.exe.write_bytes(b"MZ"+b"x"*4096)
        self.cfg.write_text("mode: rule\n",encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root,ignore_errors=True)

    def test_real_windows_handles_block_file_replace_and_parent_rename(self):
        repl_exe=self.root/"replacement.exe"; repl_exe.write_bytes(b"MZ"+b"y"*4096)
        repl_cfg=self.root/"replacement.yaml"; repl_cfg.write_text("evil: true\n",encoding="utf-8")
        guard=lock_launch_inputs([self.exe,self.cfg])
        try:
            with self.assertRaises(OSError):
                os.replace(repl_exe,self.exe)
            with self.assertRaises(OSError):
                os.replace(repl_cfg,self.cfg)
            with self.assertRaises(OSError):
                os.rename(self.exe.parent,self.root/"core-renamed")
            with self.assertRaises(OSError):
                os.rename(self.cfg.parent,self.root/"mihomo-renamed")
            self.assertTrue(self.exe.exists())
            self.assertTrue(self.cfg.exists())
        finally:
            guard.close()
        repl_exe.write_bytes(b"MZ"+b"z"*4096)
        os.replace(repl_exe,self.exe)

class WindowsProxyLive(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix="fly-proxy-live-"))
        self.backup=self.root/"proxy-backup.json"
        self.original=_read_current()
        self.logs=[]

    def tearDown(self):
        try: _write(self.original)
        except Exception: pass
        try: self.backup.unlink(missing_ok=True)
        except Exception: pass
        shutil.rmtree(self.root,ignore_errors=True)

    def test_real_hkcu_proxy_enable_restore_and_user_ownership(self):
        p=SystemProxy(self.backup,self.logs.append)
        port=free_port()
        p.enable(port)
        now=_read_current()
        self.assertEqual(now["ProxyEnable"],1)
        self.assertEqual(now["ProxyServer"],f"127.0.0.1:{port}")
        p.restore()
        self.assertEqual(_read_current(),self.original)
        p.enable(free_port())
        user={"ProxyEnable":1,"ProxyServer":"127.0.0.1:29999","ProxyOverride":"<local>"}
        _write(user)
        p.restore()
        self.assertEqual(_read_current(),user)

class RealCoreBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix="fly-core-live-"))
        cls.paths=Paths(cls.root)
        cls.logs=[]
        (cls.paths.app/"rules").mkdir(parents=True,exist_ok=True)
        ensure_private_files(cls.paths)
        install_core(cls.paths,cls.logs.append)
        if inspect_core(cls.paths).state != VALID:
            raise AssertionError("real pinned core did not verify")
        cls.paths.nodes_yaml.write_text(
            "proxies:\n"
            "  - name: 'Japan RC Dummy'\n"
            "    type: ss\n"
            "    server: 127.0.0.1\n"
            "    port: 9\n"
            "    cipher: aes-128-gcm\n"
            "    password: test\n",
            encoding="utf-8",
        )
        save_custom_profiles(cls.paths,[
            {"id":"domain-live","name":"Domain Live","domains":["example.invalid"]},
            {"id":"tun-live","name":"TUN Live","launch_mode":"tun",
             "processes":["definitely-not-running-fly-test.exe"],
             "domains":["example.invalid"]},
        ])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root,ignore_errors=True)

    def configure(self):
        mixed,ctrl=unique_ports()
        update_app_settings(self.paths,{
            "mixed_port":mixed,"controller_port":ctrl,"services_enabled":False,
        })
        return mixed,ctrl

class CorePipeLifecycleLive(RealCoreBase):
    def test_repeated_normal_start_stop_and_crash_have_no_unclosed_pipe_warning(self):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always",ResourceWarning)
            for _ in range(5):
                _,ctrl=self.configure()
                mgr=CoreManager(self.paths,self.logs.append)
                mgr.start(["domain-live"],elevate=False)
                self.assertTrue(mgr.is_running())
                secret=load_app_settings(self.paths)["api_secret"]
                self.assertTrue(mgr._controller_ready(ctrl,secret))
                mgr.stop()
                self.assertFalse(mgr.is_running())
            _,ctrl=self.configure()
            exited=[]
            mgr=CoreManager(self.paths,self.logs.append,on_exit=lambda c:exited.append(c))
            mgr.start(["domain-live"],elevate=False)
            proc=mgr.process
            proc.kill()
            deadline=time.time()+8
            while time.time()<deadline and mgr.process is not None:
                time.sleep(.05)
            self.assertIsNone(mgr.process)
            self.assertTrue(exited)
            del proc, mgr
            gc.collect()
        leaks=[str(w.message) for w in seen
               if "unclosed file" in str(w.message).lower()
               or "textiowrapper" in str(w.message).lower()]
        self.assertEqual(leaks,[],leaks)

class ElevatedTunLive(RealCoreBase):
    def test_real_locked_elevated_tun_auto_route_connectivity_and_cleanup(self):
        _,ctrl=self.configure()
        mgr=CoreManager(self.paths,self.logs.append)
        try:
            mgr.start(["tun-live"],elevate=True)
            self.assertTrue(mgr.is_running())
            cfg=(self.paths.runtime/"mihomo"/"config.yaml").read_text(encoding="utf-8")
            self.assertIn("tun:\n  enable: true",cfg)
            self.assertIn("auto-route: true",cfg)
            secret=load_app_settings(self.paths)["api_secret"]
            self.assertTrue(mgr._controller_ready(ctrl,secret))
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req=urllib.request.Request(
                "https://www.gstatic.com/generate_204",
                headers={"User-Agent":"FLY-0.9.0-RC-Windows-Test"})
            with opener.open(req,timeout=12) as resp:
                self.assertIn(resp.status,(200,204))
        finally:
            mgr.stop()
        time.sleep(.4)
        self.assertFalse(mgr.is_running())
        s=socket.socket()
        try:
            s.bind(("127.0.0.1",ctrl))
        finally:
            s.close()

if __name__=="__main__":
    unittest.main(verbosity=2)
