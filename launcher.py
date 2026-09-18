"""FLY launcher: checks GitHub for a newer version, updates the code in place,
then starts the app. User data (private/, core/, runtime/) is never touched,
and any update failure falls back to launching the currently installed version.

Build launcher.exe with scripts/BUILD_LAUNCHER.ps1, or just run LAUNCHER.bat.
"""
from __future__ import annotations
import io, shutil, subprocess, sys, tempfile, urllib.request, zipfile
from pathlib import Path

OWNER = "etg227"
REPO = "FLY"
BRANCH = "main"
PROTECTED = {"private", "core", "runtime", ".git"}
TIMEOUT = 15

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"{REPO}-launcher"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()

def local_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0"

def remote_version() -> str:
    url = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}/VERSION"
    return fetch(url).decode("utf-8", errors="replace").strip()

def apply_update(root: Path):
    url = f"https://codeload.github.com/{OWNER}/{REPO}/zip/refs/heads/{BRANCH}"
    print("正在下载更新包...")
    data = fetch(url)
    with tempfile.TemporaryDirectory(prefix="fly-update-") as td:
        tdir = Path(td)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(tdir)
        top = next(p for p in tdir.iterdir() if p.is_dir())
        for src in top.rglob("*"):
            rel = src.relative_to(top)
            if rel.parts and rel.parts[0] in PROTECTED:
                continue
            dst = root / rel
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    print("更新完成。")

def launch(root: Path):
    bat = root / "START_FLY.bat"
    if bat.exists():
        subprocess.Popen(["cmd", "/c", "start", "", str(bat)], cwd=str(root))
    else:
        subprocess.Popen([sys.executable, str(root / "main.py")], cwd=str(root))

def main():
    root = app_dir()
    cur = local_version(root)
    print(f"FLY launcher — 当前版本 {cur}")
    try:
        remote = remote_version()
        if remote and remote != cur:
            print(f"发现新版本 {remote}（当前 {cur}），开始更新...")
            apply_update(root)
        else:
            print("已是最新版本。")
    except Exception as e:
        print(f"检查更新失败（{e}），跳过更新直接启动。")
    print("启动 FLY...")
    launch(root)

if __name__ == "__main__":
    main()
