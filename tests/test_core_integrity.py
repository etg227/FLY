"""Pinned Mihomo archive trust-chain and verification robustness tests."""
import hashlib, io, json, tempfile, threading, time, unittest, zipfile
from pathlib import Path
from unittest import mock

import backend.core_installer as ci
import backend.core_manager as cm_mod
from backend.config import Paths
from backend.core_manager import CoreManager

FAKE_EXE = b"MZ" + b"x" * (1024 * 1024 + 4096)

def make_zip(payload=FAKE_EXE):
    b=io.BytesIO()
    with zipfile.ZipFile(b,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("mihomo-windows-amd64.exe",payload)
    return b.getvalue()

FAKE_ZIP=make_zip()
FAKE_SHA=hashlib.sha256(FAKE_ZIP).hexdigest()

class FakeFetch:
    def __init__(self,blob=FAKE_ZIP):
        self.blob=blob; self.calls=[]
    def __call__(self,url,log,timeout=60,progress_tag=None,sources=None):
        self.calls.append((url,sources))
        return self.blob,""

class TrustConstantTests(unittest.TestCase):
    def test_launcher_pins_same_core_constants(self):
        import re
        text=Path(__file__).resolve().parent.parent.joinpath("launcher.py").read_text(encoding="utf-8")
        for name,expected in [
            ("CORE_VERSION",ci.CORE_VERSION),
            ("CORE_ASSET_NAME",ci.CORE_ASSET_NAME),
            ("CORE_ZIP_SHA256",ci.CORE_ZIP_SHA256),
        ]:
            m=re.search(rf'^{name}\s*=\s*"([^"]+)"',text,re.M)
            self.assertIsNotNone(m,name)
            self.assertEqual(m.group(1),expected)

    def test_pinned_archive_is_exact_v1_asset(self):
        self.assertEqual(ci.CORE_VERSION,"v1.19.31")
        self.assertEqual(ci.CORE_ASSET_NAME,"mihomo-windows-amd64-v1-v1.19.31.zip")
        self.assertEqual(ci.CORE_ZIP_SHA256,
            "d89c9bd746e8aacff89b2edf674813e25e8bd2dc565f4e12dc3b4526dd2b3177")
        self.assertEqual(ci.MIRRORS,[""])

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp())
        self.paths=Paths(self.root)
        self.logs=[]
    def _archive(self):
        return ci.core_archive_path(self.paths)

    def test_official_download_installs_archive_and_exe(self):
        ff=FakeFetch()
        with mock.patch.object(ci,"CORE_ZIP_SHA256",FAKE_SHA):
            ci.install_core(self.paths,self.logs.append,fetch=ff)
        self.assertEqual(self._archive().read_bytes(),FAKE_ZIP)
        self.assertEqual(self.paths.core_exe.read_bytes(),FAKE_EXE)
        self.assertEqual(ff.calls[0][0],ci.CORE_DOWNLOAD_URL)
        self.assertEqual(ff.calls[0][1],[""])

    def test_tampered_download_is_rejected_without_touching_existing_exe(self):
        self.paths.core_exe.parent.mkdir(parents=True,exist_ok=True)
        self.paths.core_exe.write_bytes(b"MZ-old")
        ff=FakeFetch(make_zip(b"MZ"+b"evil"*300000))
        with mock.patch.object(ci,"CORE_ZIP_SHA256",FAKE_SHA):
            with self.assertRaises(ci.CoreVerifyError):
                ci.install_core(self.paths,self.logs.append,fetch=ff)
        self.assertEqual(self.paths.core_exe.read_bytes(),b"MZ-old")

    def test_archive_persist_failure_aborts_before_replacing_exe(self):
        self.paths.core_exe.parent.mkdir(parents=True, exist_ok=True)
        self.paths.core_exe.write_bytes(b"MZ-old")
        real_write = ci._write_atomic

        def fail_archive(target, data):
            if Path(target) == self._archive():
                raise PermissionError("archive locked")
            return real_write(target, data)

        with mock.patch.object(ci, "CORE_ZIP_SHA256", FAKE_SHA), \
             mock.patch.object(ci, "_write_atomic", side_effect=fail_archive):
            with self.assertRaises(RuntimeError):
                ci.install_core(self.paths, self.logs.append, fetch=FakeFetch())

        self.assertEqual(self.paths.core_exe.read_bytes(), b"MZ-old")
        self.assertFalse(any("安装完成" in x for x in self.logs))

    def test_valid_local_archive_repairs_exe_without_network(self):
        self._archive().parent.mkdir(parents=True,exist_ok=True)
        self._archive().write_bytes(FAKE_ZIP)
        self.paths.core_exe.write_bytes(b"MZ"+b"bad"*400000)
        ff=mock.Mock(side_effect=AssertionError("network must not be used"))
        with mock.patch.object(ci,"CORE_ZIP_SHA256",FAKE_SHA):
            ci.install_core(self.paths,self.logs.append,fetch=ff)
        self.assertEqual(self.paths.core_exe.read_bytes(),FAKE_EXE)
        ff.assert_not_called()

class InspectTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp()); self.paths=Paths(self.root)
        self.archive=ci.core_archive_path(self.paths)
        self.archive.parent.mkdir(parents=True,exist_ok=True)

    def test_valid_exe_matches_trusted_archive(self):
        self.archive.write_bytes(FAKE_ZIP); self.paths.core_exe.write_bytes(FAKE_EXE)
        with mock.patch.object(ci,"CORE_ZIP_SHA256",FAKE_SHA):
            self.assertEqual(ci.inspect_core(self.paths).state,ci.VALID)

    def test_missing_or_modified_exe_is_repairable(self):
        self.archive.write_bytes(FAKE_ZIP)
        with mock.patch.object(ci,"CORE_ZIP_SHA256",FAKE_SHA):
            self.assertEqual(ci.inspect_core(self.paths).state,ci.REPAIRABLE)
            self.paths.core_exe.write_bytes(b"MZ"+b"z"*len(FAKE_EXE))
            self.assertEqual(ci.inspect_core(self.paths).state,ci.REPAIRABLE)

    def test_archive_hash_mismatch_is_invalid(self):
        self.archive.write_bytes(FAKE_ZIP); self.paths.core_exe.write_bytes(FAKE_EXE)
        with mock.patch.object(ci,"CORE_ZIP_SHA256","0"*64):
            self.assertEqual(ci.inspect_core(self.paths).state,ci.INVALID)

class VerificationConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp()); self.paths=Paths(self.root)
        self.paths.core_exe.parent.mkdir(parents=True,exist_ok=True)
        self.paths.core_exe.write_bytes(FAKE_EXE)
        ci.core_archive_path(self.paths).write_bytes(FAKE_ZIP)
        self.manager=CoreManager(self.paths,lambda m:None)

    def test_parallel_verification_is_single_flight(self):
        calls=[]; gate=threading.Lock()
        def fake(paths):
            with gate:calls.append(1)
            time.sleep(.08)
            return ci.CoreInspection(ci.VALID,"ok")
        results=[]
        with mock.patch.object(cm_mod,"inspect_core",side_effect=fake):
            ts=[threading.Thread(target=lambda:results.append(self.manager.verify_binary_status().state))
                for _ in range(8)]
            [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(calls,[1])
        self.assertEqual(results,[ci.VALID]*8)

    def test_transient_verification_is_not_cached(self):
        seq=[ci.CoreInspection(ci.TRANSIENT,"locked"),ci.CoreInspection(ci.VALID,"ok")]
        with mock.patch.object(cm_mod,"inspect_core",side_effect=seq) as probe:
            self.assertEqual(self.manager.verify_binary_status().state,ci.TRANSIENT)
            self.assertEqual(self.manager.verify_binary_status().state,ci.VALID)
        self.assertEqual(probe.call_count,2)

if __name__=="__main__":
    unittest.main()


class ArchivePathAnomalyTests(unittest.TestCase):
    """归档路径异常（目录/被锁）曾让网络正常时也装不上内核。"""

    def test_archive_being_a_directory_falls_back_to_download(self):
        paths = Paths(Path(tempfile.mkdtemp()))
        paths.core_exe.parent.mkdir(parents=True, exist_ok=True)
        ci.core_archive_path(paths).mkdir()          # 目录占位
        logs = []
        with mock.patch.object(ci, "CORE_ZIP_SHA256", FAKE_SHA):
            ci.install_core(paths, logs.append, fetch=FakeFetch())
        self.assertTrue(paths.core_exe.exists())
        self.assertTrue(any("重新下载" in x for x in logs))


if __name__ == "__main__":
    unittest.main()
