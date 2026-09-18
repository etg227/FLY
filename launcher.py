"""FLY launcher: checks GitHub for a newer version, updates the code in place,
then starts the app. User data (private/, core/, runtime/) is never touched,
and any update failure falls back to launching the currently installed version.

Build launcher.exe with scripts/BUILD_LAUNCHER.ps1, or just run LAUNCHER.bat.
"""
from __future__ import annotations
import io, os, shutil, subprocess, sys, tempfile, time, urllib.request, webbrowser, zipfile
from pathlib import Path

OWNER = "etg227"
REPO = "FLY"
BRANCH = "main"
PROTECTED = {"private", "core", "runtime", ".git"}
TIMEOUT = 15
# Mainland networks often cannot reach GitHub directly; fall back to mirrors.
MIRRORS = ["", "https://ghproxy.net/", "https://gh-proxy.com/"]

# Auto-installed when no Python is found. Domestic mirrors first.
PYTHON_VERSION = "3.12.10"
PYTHON_URLS = [
    f"https://registry.npmmirror.com/-/binary/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe",
    f"https://mirrors.huaweicloud.com/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe",
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe",
]

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
    # PATH of this process is stale right after a fresh install — probe the
    # standard per-user install locations directly.
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        base = Path(local) / "Programs" / "Python"
        launcher = base / "Launcher" / "py.exe"
        if launcher.exists():
            return [str(launcher), "-3"]
        for d in sorted(base.glob("Python3*"), reverse=True):
            exe = d / "python.exe"
            if exe.exists():
                return [str(exe)]
    return None

def download_file(url: str, dst: Path):
    req = urllib.request.Request(url, headers={"User-Agent": f"{REPO}-launcher"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(dst, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done, last_pct = 0, -10
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                if pct >= last_pct + 10:
                    last_pct = pct
                    print(f"  下载中 {pct}%  ({done // 1048576}MB / {total // 1048576}MB)")

def install_python():
    print()
    print(f"未检测到 Python，开始自动下载 Python {PYTHON_VERSION}（约 27MB，安装到当前用户，无需管理员权限）...")
    tmp = Path(tempfile.mkdtemp(prefix="fly-python-"))
    exe = tmp / f"python-{PYTHON_VERSION}-amd64.exe"
    ok = False
    for url in PYTHON_URLS:
        try:
            print(f"  从 {url.split('/')[2]} 下载...")
            download_file(url, exe)
            ok = True
            break
        except Exception as e:
            print(f"  失败：{e}")
    if not ok:
        return None
    print("正在静默安装 Python（约 1-2 分钟，请勿关闭窗口）...")
    r = subprocess.run([str(exe), "/quiet", "InstallAllUsers=0", "PrependPath=1",
                        "Include_launcher=1", "InstallLauncherAllUsers=0",
                        "Include_tcltk=1", "Include_test=0", "Include_doc=0",
                        "Include_dev=0", "Include_idle=0", "Include_pip=0",
                        "AssociateFiles=0", "Shortcuts=0"], timeout=900)
    if r.returncode != 0:
        print(f"安装程序返回错误码 {r.returncode}。")
        return None
    py = find_python()
    if py:
        print("Python 安装完成。")
    return py

def ensure_python():
    py = find_python()
    if py:
        return py
    try:
        py = install_python()
    except Exception as e:
        print(f"自动安装 Python 出错：{e}")
        py = None
    if py:
        return py
    print()
    print('自动安装失败。请手动安装 Python 3.11+（安装时勾选 "Add python.exe to PATH"）后重新运行本程序。')
    print("下载地址: https://www.python.org/downloads/")
    try:
        webbrowser.open("https://www.python.org/downloads/")
    except Exception:
        pass
    return None

def pause(msg="按回车键退出..."):
    try:
        input(msg)
    except EOFError:
        pass

def launch(root: Path) -> bool:
    python = ensure_python()
    if not python:
        pause()
        return False
    main_py = root / "main.py"
    if not main_py.exists():
        print("未找到程序文件（main.py）。首次运行需要联网从 GitHub 拉取，请检查网络后重试。")
        pause()
        return False
    print("启动 FLY...")
    # `|| pause` keeps the console open if the app fails to start, so errors
    # are visible instead of a flash-and-close window.
    inner = subprocess.list2cmdline(python + [str(main_py)]) + " || pause"
    subprocess.Popen(f'cmd /c "{inner}"', cwd=str(root),
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
