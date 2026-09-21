"""Build the standalone Windows FLY launcher.

The release launcher is a real bootstrap artifact: it embeds the application
sources/rules and the pinned trusted Mihomo archive.  A brand-new empty folder
can therefore start FLY without reaching GitHub or installing Python first.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_VERSION = "v1.19.31"
CORE_ASSET_NAME = "mihomo-windows-amd64-v1-v1.19.31.zip"
CORE_ZIP_SHA256 = "d89c9bd746e8aacff89b2edf674813e25e8bd2dc565f4e12dc3b4526dd2b3177"
CORE_URL = f"https://github.com/MetaCubeX/mihomo/releases/download/{CORE_VERSION}/{CORE_ASSET_NAME}"

FILES = (
    "main.py", "launcher.py", "README.md", "LICENSE", "VERSION",
    "START_FLY_DEBUG.bat", ".gitignore",
)
DIRS = ("backend", "rules", "scripts")


def _download_core(dst: Path) -> None:
    req = urllib.request.Request(CORE_URL, headers={"User-Agent": "FLY-release-builder"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=120) as resp:
        blob = resp.read()
    actual = hashlib.sha256(blob).hexdigest()
    if actual.lower() != CORE_ZIP_SHA256:
        raise RuntimeError(f"pinned Mihomo SHA-256 mismatch: {actual}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(blob)


def _prepare_payload(dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, dst / name)
    for name in DIRS:
        src = ROOT / name
        if src.exists():
            shutil.copytree(src, dst / name, dirs_exist_ok=True)

    for cache in dst.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for pyc in dst.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)

    manifest = """{
  "schema": 1,
  "managed_dirs": ["backend", "rules", "scripts"],
  "managed_root": ["main.py", "launcher.py", "README.md", "LICENSE", "START_FLY_DEBUG.bat", ".gitignore", "update-manifest.json", "launcher.exe.new"]
}
"""
    (dst / "update-manifest.json").write_text(manifest, encoding="utf-8")
    _download_core(dst / "core" / "mihomo-verified.zip")


def main() -> int:
    if os.name != "nt":
        raise SystemExit("standalone launcher release builds must run on Windows")
    with tempfile.TemporaryDirectory(prefix="fly-bootstrap-build-") as td:
        payload = Path(td) / "bootstrap"
        _prepare_payload(payload)
        add_data = f"{payload};bootstrap"
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile", "--noconsole",
            "--name", "launcher",
            "--paths", str(ROOT),
            "--hidden-import", "main",
            "--collect-submodules", "backend",
            "--add-data", add_data,
            str(ROOT / "launcher.py"),
        ]
        subprocess.check_call(cmd, cwd=str(ROOT))
    exe = ROOT / "dist" / "launcher.exe"
    if not exe.exists() or exe.stat().st_size < 20_000_000:
        raise RuntimeError("standalone launcher.exe missing or unexpectedly small")
    print(f"built {exe} ({exe.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
