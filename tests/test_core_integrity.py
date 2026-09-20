"""内核下载的完整性校验：校验链必须扎根官方源，镜像内容一律要比对哈希。"""
import hashlib, io, json, tempfile, unittest, zipfile
from pathlib import Path

from backend.config import Paths
from backend.core_installer import (CoreVerifyError, expected_sha256, find_sha256,
                                    install_core, MIRRORS)

EXE = b"MZ fake mihomo binary"


def make_zip(payload=EXE, name="mihomo-windows-amd64.exe"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, payload)
    return buf.getvalue()


ZIP = make_zip()
ZIP_SHA = hashlib.sha256(ZIP).hexdigest()
ASSET_NAME = "mihomo-windows-amd64-v1-v1.19.0.zip"


def release(digest=None, extra_assets=()):
    asset = {"name": ASSET_NAME, "browser_download_url": "https://github.test/core.zip"}
    if digest:
        asset["digest"] = digest
    return {"assets": [asset] + list(extra_assets)}


class ShaParsingTests(unittest.TestCase):
    def test_picks_the_line_for_our_file(self):
        text = (f"{'a'*64}  other.zip\n{ZIP_SHA} *{ASSET_NAME}\n{'b'*64}  third.zip\n")
        self.assertEqual(find_sha256(text, ASSET_NAME), ZIP_SHA)

    def test_single_bare_hash_file(self):
        self.assertEqual(find_sha256(f"  {ZIP_SHA}  \n", ASSET_NAME), ZIP_SHA)

    def test_unknown_file_returns_none(self):
        self.assertIsNone(find_sha256(f"{'a'*64}  other.zip\n{'b'*64}  third.zip", ASSET_NAME))


class ExpectedShaTests(unittest.TestCase):
    def test_github_digest_wins(self):
        rel = release(digest=f"sha256:{ZIP_SHA}")
        sha, origin = expected_sha256(rel, rel["assets"][0], print, fetch=None)
        self.assertEqual(sha, ZIP_SHA)
        self.assertIn("digest", origin)

    def test_malformed_digest_is_ignored(self):
        rel = release(digest="sha256:not-a-hash")
        sha, _ = expected_sha256(rel, rel["assets"][0], print, fetch=lambda url: b"")
        self.assertIsNone(sha)

    def test_falls_back_to_checksum_asset(self):
        sums = {"name": "checksums.txt", "browser_download_url": "https://github.test/sums"}
        rel = release(extra_assets=[sums])
        sha, origin = expected_sha256(
            rel, rel["assets"][0], print,
            fetch=lambda url: f"{ZIP_SHA}  {ASSET_NAME}\n".encode())
        self.assertEqual(sha, ZIP_SHA)
        self.assertIn("checksums.txt", origin)


class FakeFetch:
    def __init__(self, rel, zip_bytes=ZIP, sums=None):
        self.rel, self.zip_bytes, self.sums = rel, zip_bytes, sums
        self.calls = []

    def __call__(self, url, log, timeout=30, progress_tag=None, sources=None):
        self.calls.append((url, tuple(sources) if sources is not None else None))
        if "api.github.com" in url:
            return json.dumps(self.rel).encode(), ""
        if url.endswith("/sums"):
            return (self.sums or b""), ""
        prefix = (sources or MIRRORS)[0]
        return self.zip_bytes, prefix


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.paths = Paths(Path(tempfile.mkdtemp()))
        self.logs = []

    def _leftovers(self):
        d = self.paths.core_exe.parent
        return [p.name for p in d.glob(".mihomo-*")] if d.exists() else []

    def test_matching_hash_installs(self):
        fetch = FakeFetch(release(digest=f"sha256:{ZIP_SHA}"))
        install_core(self.paths, self.logs.append, fetch=fetch)
        self.assertEqual(self.paths.core_exe.read_bytes(), EXE)
        self.assertEqual(self._leftovers(), [])
        self.assertTrue(any("完整性校验通过" in x for x in self.logs))

    def test_tampered_download_is_rejected(self):
        fetch = FakeFetch(release(digest=f"sha256:{ZIP_SHA}"), zip_bytes=make_zip(b"EVIL"))
        with self.assertRaises(CoreVerifyError):
            install_core(self.paths, self.logs.append, fetch=fetch)
        self.assertFalse(self.paths.core_exe.exists(), "校验失败绝不能落盘")
        self.assertEqual(self._leftovers(), [])

    def test_without_a_trusted_hash_mirrors_are_refused(self):
        fetch = FakeFetch(release())          # 既无 digest 也无校验文件
        install_core(self.paths, self.logs.append, fetch=fetch)
        zip_call = [c for c in fetch.calls if c[0].endswith("core.zip")][0]
        self.assertEqual(zip_call[1], ("",), "没有校验基准时只能走官方直连")
        self.assertTrue(any("只接受官方直连" in x for x in self.logs))

    def test_release_metadata_is_never_taken_from_a_mirror(self):
        fetch = FakeFetch(release(digest=f"sha256:{ZIP_SHA}"))
        install_core(self.paths, self.logs.append, fetch=fetch)
        api_call = [c for c in fetch.calls if "api.github.com" in c[0]][0]
        self.assertEqual(api_call[1], ("",))

    def test_verified_hash_allows_mirror_sources(self):
        fetch = FakeFetch(release(digest=f"sha256:{ZIP_SHA}"))
        install_core(self.paths, self.logs.append, fetch=fetch)
        zip_call = [c for c in fetch.calls if c[0].endswith("core.zip")][0]
        self.assertEqual(zip_call[1], tuple(MIRRORS))

    def test_checksum_file_is_fetched_from_official_source_only(self):
        sums = {"name": "mihomo.sha256", "browser_download_url": "https://github.test/sums"}
        fetch = FakeFetch(release(extra_assets=[sums]),
                          sums=f"{ZIP_SHA}  {ASSET_NAME}\n".encode())
        install_core(self.paths, self.logs.append, fetch=fetch)
        sums_call = [c for c in fetch.calls if c[0].endswith("/sums")][0]
        self.assertEqual(sums_call[1], ("",))
        self.assertEqual(self.paths.core_exe.read_bytes(), EXE)


if __name__ == "__main__":
    unittest.main()
