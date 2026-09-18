"""FLY launcher: checks GitHub for a newer version, updates the code in place,
then starts the app. User data (private/, core/, runtime/) is never touched,
and any update failure falls back to launching the currently installed version.

Build launcher.exe with scripts/BUILD_LAUNCHER.ps1, or just run LAUNCHER.bat.
"""
from __future__ import annotations
import io, shutil, subprocess, sys, tempfile, time, urllib.request, webbrowser, zipfile
from pathlib import Path

OWNER = "etg227"
REPO = "FLY"
BRANCH = "main"
PROTECTED = {"private", "core", "runtime", ".git"}
TIMEOUT = 15
# Mainland networks often cannot reach GitHub directly; fall back to mirrors.
MIRRORS = ["", "https://ghproxy.net/", "https://gh-proxy.com/"]

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def fetch(url: str) -> bytes:
    last = None
    for m in MIRRORS:
        try:
            req = urllib.request.Request(m + url, headers={"User-Agent": f"{REPO}-launcher"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except Exception as e:
            print(f"  {'直连' if not m else '镜像 ' + m} 失败：{e}")
            last = e
    raise last

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

def find_python():
    py = shutil.which("py")
    if py:
        return [py, "-3"]
    p = shutil.which("python")
    if p:
        return [p]
    return None

def pause(msg="按回车键退出..."):
    try:
        input(msg)
    except EOFError:
        pass

def launch(root: Path) -> bool:
    if not find_python():
        print()
        print("未检测到 Python！FLY 需要 Python 3.11 或更新版本才能运行。")
        print('请安装 Python（安装第一步务必勾选 "Add python.exe to PATH"），装好后重新运行本程序。')
        print("下载地址: https://www.python.org/downloads/")
        try:
            webbrowser.open("https://www.python.org/downloads/")
        except Exception:
            pass
        pause()
        return False
    if not (root / "main.py").exists():
        print("未找到程序文件（main.py）。首次运行需要联网从 GitHub 拉取，请检查网络后重试。")
        pause()
        return False
    print("启动 FLY...")
    bat = root / "START_FLY.bat"
    subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(root),
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return True

def main():
    root = app_dir()
    print(f"FLY launcher — 当前版本 {local_version(root)}")
    try:
        remote = remote_version()
        cur = local_version(root)
        if remote and remote != cur:
            print(f"发现新版本 {remote}（当前 {cur}），开始更新...")
            apply_update(root)
        else:
            print("已是最新版本。")
    except Exception as e:
        print(f"检查更新失败：{e}")
        if not (root / "main.py").exists():
            print("首次运行需要能访问 GitHub（或镜像站）下载程序文件，请换个网络环境再试。")
            pause()
            return
        print("跳过更新，直接启动当前版本。")
    if launch(root):
        time.sleep(2)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[launcher] 未处理的错误：{e}")
        pause()
