"""Transactional launcher/update regression tests."""
import hashlib, io, json, shutil, tempfile, unittest, zipfile
from pathlib import Path
from unittest import mock

# launcher 在 import 阶段就要 tkinter；没有 tk 的环境（部分 Linux）应当跳过，
# 而不是让整个测试套件在收集阶段就失败。CI 用 windows-latest，原生自带。
# 只吞 ImportError —— launcher.py 真有语法错误时照样会炸出来。
try:
    import launcher
except ImportError as exc:
    launcher, HAVE_TK, SKIP_REASON = None, False, f"launcher 需要 tkinter（{exc}）"
else:
    HAVE_TK, SKIP_REASON = True, ""


class UI:
    def __init__(self):self.lines=[]
    def log(self,x):self.lines.append(x)
    def progress(self,x):pass
    def status(self,x):pass

@unittest.skipUnless(HAVE_TK, SKIP_REASON)
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.td=Path(tempfile.mkdtemp()); (self.td/"runtime").mkdir()
        self.ui=UI()
    def tearDown(self):shutil.rmtree(self.td,ignore_errors=True)

    def test_checksum_selects_matching_filename_not_first_hash(self):
        right="b"*64
        blob=(f"{'a'*64}  other.zip\n{right}  FLY-update.zip\n").encode()
        self.assertEqual(launcher._parse_sha256(blob,"FLY-update.zip"),right)

    def test_backslash_traversal_is_rejected_cross_platform(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w") as z:z.writestr("..\\evil.txt","x")
        data=buf.getvalue()
        with self.assertRaises(RuntimeError):
            launcher._extract_verified(data,hashlib.sha256(data).hexdigest(),self.td/"stage")
        self.assertFalse((self.td.parent/"evil.txt").exists())

    def _payload(self,version="0.8.11"):
        top=self.td/"payload"; (top/"backend").mkdir(parents=True)
        (top/"rules").mkdir(); (top/"scripts").mkdir()
        (top/"main.py").write_text("new main",encoding="utf-8")
        (top/"launcher.py").write_text("new launcher",encoding="utf-8")
        (top/"VERSION").write_text(version,encoding="utf-8")
        (top/"backend"/"new.py").write_text("new",encoding="utf-8")
        manifest={"schema":1,"managed_dirs":["backend","rules","scripts"],
                  "managed_root":["main.py","launcher.py","update-manifest.json","launcher.exe.new"]}
        (top/"update-manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
        (top/"launcher.exe.new").write_bytes(b"new-exe")
        return top

    def test_transaction_removes_upstream_deleted_module_and_writes_version_last(self):
        (self.td/"backend").mkdir()
        (self.td/"backend"/"removed.py").write_text("old",encoding="utf-8")
        (self.td/"main.py").write_text("old main",encoding="utf-8")
        (self.td/"VERSION").write_text("0.8.10",encoding="utf-8")
        launcher._transactional_install(self._payload(),self.td,self.ui)
        self.assertFalse((self.td/"backend"/"removed.py").exists())
        self.assertTrue((self.td/"backend"/"new.py").exists())
        self.assertEqual((self.td/"VERSION").read_text(),"0.8.11")
        self.assertTrue((self.td/"launcher.exe.new").exists())

    def test_transaction_rolls_back_if_commit_fails(self):
        (self.td/"backend").mkdir()
        (self.td/"backend"/"old.py").write_text("old",encoding="utf-8")
        (self.td/"main.py").write_text("old main",encoding="utf-8")
        (self.td/"VERSION").write_text("0.8.10",encoding="utf-8")
        real=launcher._atomic_file_copy
        def fail_version(src,dst):
            if dst.name=="VERSION":raise OSError("disk full")
            return real(src,dst)
        with mock.patch.object(launcher,"_atomic_file_copy",side_effect=fail_version):
            with self.assertRaises(OSError):launcher._transactional_install(self._payload(),self.td,self.ui)
        self.assertEqual((self.td/"main.py").read_text(),"old main")
        self.assertTrue((self.td/"backend"/"old.py").exists())
        self.assertEqual((self.td/"VERSION").read_text(),"0.8.10")

    def test_recovery_rolls_back_interrupted_transaction(self):
        (self.td/"backend").mkdir()
        (self.td/"backend"/"new.py").write_text("bad partial",encoding="utf-8")
        txn=self.td/"runtime"/launcher.TXN_NAME; backup=txn/"backup"/"backend"
        backup.mkdir(parents=True)
        (backup/"old.py").write_text("old",encoding="utf-8")
        (txn/"backup"/"VERSION").write_text("0.8.10",encoding="utf-8")
        manifest={"managed_dirs":["backend"],"managed_root":[]}
        (txn/"journal.json").write_text(json.dumps({"state":"committing","manifest":manifest}),encoding="utf-8")
        self.assertTrue(launcher.recover_interrupted_update(self.td,self.ui))
        self.assertTrue((self.td/"backend"/"old.py").exists())
        self.assertFalse((self.td/"backend"/"new.py").exists())

if __name__=="__main__":unittest.main()
