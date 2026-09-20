"""Pinned official Mihomo core integrity tests."""
import hashlib, io, json, tempfile, unittest, zipfile
from pathlib import Path

from backend.config import Paths
from backend.core_installer import (
    CoreVerifyError, expected_sha256, find_sha256, install_core, MIRRORS, CORE_VERSION
)

EXE=b"MZ fake mihomo binary"
def make_zip(payload=EXE,name="mihomo-windows-amd64.exe"):
    b=io.BytesIO()
    with zipfile.ZipFile(b,"w") as z:z.writestr(name,payload)
    return b.getvalue()
ZIP=make_zip(); ZIP_SHA=hashlib.sha256(ZIP).hexdigest()
ASSET_NAME="mihomo-windows-amd64-v1-v1.19.31.zip"

def release(digest=None,extra_assets=()):
    a={"name":ASSET_NAME,"browser_download_url":"https://github.test/core.zip"}
    if digest:a["digest"]=digest
    return {"assets":[a]+list(extra_assets)}

class FakeFetch:
    def __init__(self,rel,zip_bytes=ZIP,sums=None):
        self.rel,self.zip_bytes,self.sums=rel,zip_bytes,sums; self.calls=[]
    def __call__(self,url,log,timeout=30,progress_tag=None,sources=None):
        self.calls.append((url,tuple(sources) if sources is not None else None))
        if "api.github.com" in url:return json.dumps(self.rel).encode(),""
        if url.endswith("/sums"):return (self.sums or b""),""
        return self.zip_bytes,""

class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.paths=Paths(Path(tempfile.mkdtemp())); self.logs=[]
    def test_core_version_is_pinned(self):
        self.assertEqual(CORE_VERSION,"v1.19.31")
        self.assertEqual(MIRRORS,[""])
    def test_checksum_parser_binds_filename(self):
        text=f"{'a'*64}  other.zip\n{ZIP_SHA} *{ASSET_NAME}\n"
        self.assertEqual(find_sha256(text,ASSET_NAME),ZIP_SHA)
    def test_github_digest_installs_from_official_only(self):
        ff=FakeFetch(release(digest=f"sha256:{ZIP_SHA}"))
        install_core(self.paths,self.logs.append,fetch=ff)
        self.assertEqual(self.paths.core_exe.read_bytes(),EXE)
        zip_call=[c for c in ff.calls if c[0].endswith("core.zip")][0]
        self.assertEqual(zip_call[1],("",))
    def test_tampered_binary_rejected(self):
        ff=FakeFetch(release(digest=f"sha256:{ZIP_SHA}"),zip_bytes=make_zip(b"EVIL"))
        with self.assertRaises(CoreVerifyError):install_core(self.paths,self.logs.append,fetch=ff)
        self.assertFalse(self.paths.core_exe.exists())
    def test_missing_trusted_hash_is_fail_closed(self):
        ff=FakeFetch(release())
        with self.assertRaises(CoreVerifyError):install_core(self.paths,self.logs.append,fetch=ff)
        self.assertFalse(any(c[0].endswith("core.zip") for c in ff.calls))
    def test_checksum_asset_may_supply_hash(self):
        sums={"name":"mihomo.sha256","browser_download_url":"https://github.test/sums"}
        ff=FakeFetch(release(extra_assets=[sums]),sums=f"{ZIP_SHA}  {ASSET_NAME}\n".encode())
        install_core(self.paths,self.logs.append,fetch=ff)
        self.assertEqual(self.paths.core_exe.read_bytes(),EXE)
        self.assertEqual([c for c in ff.calls if c[0].endswith("/sums")][0][1],("",))

if __name__=="__main__":unittest.main()
