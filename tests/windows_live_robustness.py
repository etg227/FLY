"""Destructive Windows-only robustness tests for ephemeral CI VMs.

These tests intentionally touch HKCU Internet Settings, Windows file sharing,
ShellExecuteExW("runas"), and a real pinned Mihomo process. They are gated by
FLY_WINDOWS_LIVE=1 and are intended for disposable Windows runners only.
"""
from __future__ import annotations
import ctypes
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

if os.name != "nt" or os.environ.get("FLY_WINDOWS_LIVE") != "1":
    raise SystemExit("windows_live_robustness.py requires Windows + FLY_WINDOWS_LIVE=1")

from backend.config import (
    Paths, ensure_private_files, save_custom_profiles, save_json,
    load_app_settings, update_app_settings,
)
from backend.core_installer import (
    VALID, REPAIRABLE, install_core, inspect_core, core_archive_path,
    CORE_VERSION,
)
from backend.core_manager import CoreManager
from backend.elevated_launch import launch_elevated
from backend.system_proxy import SystemProxy, _read_current, _write


def free_port():
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    s.bind(("127.0.0.1",0))
    p=s.getsockname()[1]
    s.close()
    return p


class LockedFile:
    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self,path):
        self.path=str(path)
        self.handle=None

    def __enter__(self):
        k=ctypes.windll.kernel32
        k.CreateFileW.restype=ctypes.c_void_p
        # share mode = 0 denies read/write/delete to force a real Windows
        # sharing violation instead of relying on Python's normal open().
        h=k.CreateFileW(self.path,self.GENERIC_READ,0,None,
                        self.OPEN_EXISTING,self.FILE_ATTRIBUTE_NORMAL,None)
        if not h or h == self.INVALID_HANDLE_VALUE:
            raise ctypes.WinError()
        self.handle=h
        return self

    def close(self):
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle=None

    def __exit__(self,*exc):
        self.close()


class Test01PinnedCoreLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix="fly-live-core-"))
        cls.paths=Paths(cls.root)
        cls.logs=[]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root,ignore_errors=True)

    def test_01_download_verify_and_execute_real_core(self):
        install_core(self.paths,self.logs.append)
        result=inspect_core(self.paths)
        self.assertEqual(result.state,VALID,result.detail)
        self.assertTrue(core_archive_path(self.paths).exists())
        p=subprocess.run([str(self.paths.core_exe),"-v"],
                         capture_output=True,text=True,timeout=15,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn(CORE_VERSION.lstrip("v"),(p.stdout+p.stderr))

    def test_02_tamper_then_repair_offline_from_verified_archive(self):
        install_core(self.paths,self.logs.append)
        data=bytearray(self.paths.core_exe.read_bytes())
        data[len(data)//2] ^= 0x01
        self.paths.core_exe.write_bytes(data)
        self.assertEqual(inspect_core(self.paths).state,REPAIRABLE)
        def no_network(*a,**k):
            raise AssertionError("trusted archive repair must not use network")
        install_core(self.paths,self.logs.append,fetch=no_network)
        self.assertEqual(inspect_core(self.paths).state,VALID)


class Test02WindowsSharingLive(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix="fly-live-lock-"))
        self.paths=Paths(self.root)
        ensure_private_files(self.paths)

    def tearDown(self):
        shutil.rmtree(self.root,ignore_errors=True)

    def test_01_atomic_json_survives_transient_exclusive_lock(self):
        save_json(self.paths.app_settings,{"mixed_port":17890})
        lock=LockedFile(self.paths.app_settings).__enter__()
        try:
            t=threading.Timer(0.12,lock.close)
            t.start()
            save_json(self.paths.app_settings,{"mixed_port":18888})
            t.join(timeout=2)
        finally:
            lock.close()
        self.assertEqual(load_app_settings(self.paths)["mixed_port"],18888)

    def test_02_concurrent_settings_updates_under_windows_filesystem(self):
        errors=[]
        def worker(i):
            try:
                for n in range(20):
                    def mutate(s,i=i,n=n):
                        exes=dict(s.get("game_exes",{}))
                        exes[f"g{i}"]=fr"C:\Games\g{i}-{n}.exe"
                        s["game_exes"]=exes
                    update_app_settings(self.paths,mutator=mutate)
            except Exception as e:
                errors.append(repr(e))
        ts=[threading.Thread(target=worker,args=(i,)) for i in range(16)]
        [t.start() for t in ts]
        [t.join(timeout=30) for t in ts]
        self.assertFalse(any(t.is_alive() for t in ts),"settings writer deadlock")
        self.assertEqual(errors,[])
        exes=load_app_settings(self.paths)["game_exes"]
        self.assertEqual(set(exes),{f"g{i}" for i in range(16)})


class Test03SystemProxyRegistryLive(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix="fly-live-proxy-"))
        self.backup=self.root/"proxy-backup.json"
        self.logs=[]
        self.original=_read_current()

    def tearDown(self):
        # Hard cleanup even when an assertion fails: leave the runner exactly
        # as we found it before any later network-dependent test.
        try: _write(self.original)
        except Exception: pass
        try: self.backup.unlink(missing_ok=True)
        except Exception: pass
        shutil.rmtree(self.root,ignore_errors=True)

    def test_01_real_enable_and_restore_registry(self):
        p=SystemProxy(self.backup,self.logs.append)
        port=free_port()
        p.enable(port)
        now=_read_current()
        self.assertEqual(now["ProxyEnable"],1)
        self.assertEqual(now["ProxyServer"],f"127.0.0.1:{port}")
        p.restore()
        self.assertEqual(_read_current(),self.original)
        self.assertFalse(self.backup.exists())

    def test_02_user_change_is_not_clobbered(self):
        p=SystemProxy(self.backup,self.logs.append)
        p.enable(free_port())
        user_state={
            "ProxyEnable":1,
            "ProxyServer":"127.0.0.1:29999",
            "ProxyOverride":"<local>",
        }
        _write(user_state)
        p.restore()
        self.assertEqual(_read_current(),user_state)

    def test_03_orphan_backup_recovers_previous_state(self):
        p=SystemProxy(self.backup,self.logs.append)
        p.enable(free_port())
        # Simulate GUI death: no restore(), new instance on next launch.
        SystemProxy(self.backup,self.logs.append).restore_orphan()
        self.assertEqual(_read_current(),self.original)
        self.assertFalse(self.backup.exists())


class Test04RealElevationLive(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix="fly-live-elev-"))

    def tearDown(self):
        shutil.rmtree(self.root,ignore_errors=True)

    def test_01_shell_execute_runas_real_process_and_handle(self):
        out=self.root/"elevated-ok.txt"
        code=(
            "from pathlib import Path; "
            f"Path(r'{out}').write_text('ok',encoding='utf-8')"
        )
        proc=launch_elevated(sys.executable,["-c",code],cwd=str(self.root))
        rc=proc.wait(timeout=20)
        proc.close()
        self.assertEqual(rc,0)
        self.assertEqual(out.read_text(encoding="utf-8"),"ok")

    def test_02_elevated_handle_can_terminate_child(self):
        proc=launch_elevated(
            sys.executable,
            ["-c","import time; time.sleep(30)"],
            cwd=str(self.root),
        )
        try:
            time.sleep(0.4)
            self.assertIsNone(proc.poll())
            proc.terminate()
            rc=proc.wait(timeout=10)
            self.assertIsInstance(rc,int)
        finally:
            proc.close()


class Test05RealMihomoLifecycleLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix="fly-live-manager-"))
        cls.paths=Paths(cls.root)
        (cls.paths.app/"rules").mkdir(parents=True,exist_ok=True)
        ensure_private_files(cls.paths)
        cls.logs=[]
        install_core(cls.paths,cls.logs.append)
        save_custom_profiles(cls.paths,[{
            "id":"live-site",
            "name":"Windows Live Site",
            "domains":["example.invalid"],
            "latency_test_urls":["https://www.gstatic.com/generate_204"],
        }])
        cls.paths.nodes_yaml.write_text(
            "proxies:\n"
            "  - name: 'Japan Live Dummy'\n"
            "    type: ss\n"
            "    server: 127.0.0.1\n"
            "    port: 9\n"
            "    cipher: aes-128-gcm\n"
            "    password: test\n",
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root,ignore_errors=True)

    def settings_ports(self):
        mixed,controller=free_port(),free_port()
        while controller==mixed:
            controller=free_port()
        update_app_settings(self.paths,{
            "mixed_port":mixed,
            "controller_port":controller,
            "services_enabled":False,
        })
        return mixed,controller

    def test_01_real_non_elevated_start_api_ready_and_clean_stop(self):
        _,controller=self.settings_ports()
        exits=[]
        mgr=CoreManager(self.paths,self.logs.append,on_exit=exits.append)
        try:
            mgr.start(["live-site"],elevate=False)
            self.assertTrue(mgr.is_running())
            secret=load_app_settings(self.paths)["api_secret"]
            self.assertTrue(mgr._controller_ready(controller,secret))
        finally:
            mgr.stop()
        time.sleep(0.2)
        self.assertFalse(mgr.is_running())
        self.assertEqual(exits,[])

    def test_02_real_unexpected_core_death_triggers_callback(self):
        self.settings_ports()
        event=threading.Event(); codes=[]
        def on_exit(code):
            codes.append(code); event.set()
        mgr=CoreManager(self.paths,self.logs.append,on_exit=on_exit)
        mgr.start(["live-site"],elevate=False)
        proc=mgr.process
        proc.kill()
        self.assertTrue(event.wait(10),"real core death callback did not fire")
        self.assertEqual(mgr.process,None)
        self.assertTrue(codes)

    def test_03_real_elevated_mihomo_start_and_stop(self):
        _,controller=self.settings_ports()
        exits=[]
        mgr=CoreManager(self.paths,self.logs.append,on_exit=exits.append)
        try:
            mgr.start(["live-site"],elevate=True)
            self.assertTrue(mgr.is_running())
            self.assertIsNone(mgr.process.stdout)
            secret=load_app_settings(self.paths)["api_secret"]
            self.assertTrue(mgr._controller_ready(controller,secret))
        finally:
            mgr.stop()
        self.assertFalse(mgr.is_running())
        self.assertEqual(exits,[])


if __name__=="__main__":
    unittest.main(verbosity=2)
