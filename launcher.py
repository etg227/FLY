"""FLY launcher (GUI): checks GitHub for a newer version, updates the code in
place, auto-installs Python when missing, then starts the app windowless.
User data (private/, core/, runtime/) is never touched; every error stays
visible in the window instead of a flashing console.

Build launcher.exe with scripts/BUILD_LAUNCHER.ps1 (PyInstaller --noconsole).
"""
from __future__ import annotations
import io, json, os, queue, re, shutil, subprocess, sys, tempfile, threading, urllib.request, webbrowser, zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk

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

def fetch(url: str, ui=None) -> bytes:
    last = None
    for m in MIRRORS:
        try:
            req = urllib.request.Request(m + url, headers={"User-Agent": f"{REPO}-launcher"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                buf, done = io.BytesIO(), 0
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    buf.write(chunk)
                    done += len(chunk)
                    if ui and total:
                        ui.progress(done / total)
                return buf.getvalue()
        except Exception as e:
            if ui:
                ui.log(f"{'直连' if not m else '镜像 ' + m.split('/')[2]} 失败：{e}")
            last = e
    raise last

def local_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return "0"

def remote_version(ui=None) -> str:
    url = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}/VERSION"
    return fetch(url, ui).decode("utf-8-sig", errors="replace").strip()

# Files from older versions that updates no longer ship — removed so the
# user's folder stays clean (single-exe experience).
OBSOLETE = ["START_FLY.bat", "LAUNCHER.bat", "INSTALL_CORE.bat", "start_fly.py",
            "FLY.exe", "launcher.spec", "FLY.spec", "scripts/INSTALL_CORE.ps1"]

def apply_update(root: Path, ui):
    url = f"https://codeload.github.com/{OWNER}/{REPO}/zip/refs/heads/{BRANCH}"
    data = fetch(url, ui)
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
    for name in OBSOLETE:
        try:
            p = root / name
            if p.is_file():
                p.unlink()
        except OSError:
            pass

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

def windowless(python_cmd):
    """Swap py.exe/python.exe for their windowless siblings when available."""
    exe = Path(python_cmd[0])
    alt = {"py.exe": "pyw.exe", "python.exe": "pythonw.exe"}.get(exe.name.lower())
    if alt:
        cand = exe.with_name(alt)
        if cand.exists():
            return [str(cand)] + python_cmd[1:], True
    return list(python_cmd), False

def install_python(ui):
    ui.status(f"正在下载 Python {PYTHON_VERSION}（约 26MB）...")
    ui.log("未检测到 Python，自动下载安装（仅当前用户，无需管理员权限）。")
    tmp = Path(tempfile.mkdtemp(prefix="fly-python-"))
    exe = tmp / f"python-{PYTHON_VERSION}-amd64.exe"
    data = None
    for url in PYTHON_URLS:
        try:
            ui.log(f"从 {url.split('/')[2]} 下载...")
            data = fetch(url, ui)
            break
        except Exception as e:
            ui.log(f"失败：{e}")
    if data is None:
        return None
    exe.write_bytes(data)
    ui.status("正在安装 Python（约 1-2 分钟，请勿关闭窗口）...")
    ui.progress(None)  # indeterminate
    r = subprocess.run([str(exe), "/quiet", "InstallAllUsers=0", "PrependPath=1",
                        "Include_launcher=1", "InstallLauncherAllUsers=0",
                        "Include_tcltk=1", "Include_test=0", "Include_doc=0",
                        "Include_dev=0", "Include_idle=0", "Include_pip=0",
                        "AssociateFiles=0", "Shortcuts=0"],
                       timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        ui.log(f"安装程序返回错误码 {r.returncode}。")
        return None
    py = find_python()
    if py:
        ui.log("Python 安装完成。")
    return py

CORE_API = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
CORE_PATTERNS = (r"^mihomo-windows-amd64-v1-v[0-9].*\.zip$", r"^mihomo-windows-amd64.*\.zip$")

def ensure_core(root: Path, ui):
    """First run: fetch the mihomo core so the user never installs anything."""
    core = root / "core" / "mihomo.exe"
    if core.exists():
        return
    ui.status("下载加速内核（首次，约 21MB）...")
    ui.progress(None)
    data = json.loads(fetch(CORE_API, ui).decode("utf-8", errors="replace"))
    asset = None
    for pat in CORE_PATTERNS:
        asset = next((a for a in data.get("assets", []) if re.match(pat, a.get("name", ""))), None)
        if asset:
            break
    if not asset:
        raise RuntimeError("未找到 mihomo 内核安装包。")
    ui.log(f"下载 {asset['name']} ...")
    blob = fetch(asset["browser_download_url"], ui)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next((n for n in zf.namelist() if re.search(r"mihomo.*\.exe$", n)), None)
        if not name:
            raise RuntimeError("安装包里没有 mihomo.exe。")
        core.parent.mkdir(parents=True, exist_ok=True)
        core.write_bytes(zf.read(name))
    ui.log("加速内核安装完成。")

def run_flow(root: Path, ui):
    """Update + environment check. Returns the python command to launch with."""
    ui.status("检查更新...")
    cur = local_version(root)
    remote = None
    try:
        remote = remote_version(ui)
    except Exception as e:
        ui.log(f"检查更新失败：{e}")
    if remote and remote != cur:
        ui.status(f"发现新版本 {remote}，正在更新...")
        apply_update(root, ui)
        ui.log(f"已更新：{cur} → {remote}")
    elif remote:
        ui.log(f"已是最新版本（{remote}）。")
    if not (root / "main.py").exists():
        raise RuntimeError("未能获取程序文件。首次运行需要能访问 GitHub 或镜像站，请检查网络后重试。")

    ui.status("检查运行环境...")
    ui.progress(None)
    py = find_python()
    if not py:
        py = install_python(ui)
    if not py:
        try:
            webbrowser.open("https://www.python.org/downloads/")
        except Exception:
            pass
        raise RuntimeError('自动安装 Python 失败。已打开官网下载页，请手动安装'
                           '（勾选 "Add python.exe to PATH"）后重新运行本程序。')
    try:
        ensure_core(root, ui)
    except Exception as e:
        # Not fatal: the app retries the core download by itself on startup.
        ui.log(f"内核下载未完成（{e}），主程序会自动重试。")
    return py

def launch(root: Path, python_cmd):
    cmd, ok = windowless(python_cmd)
    flags = 0 if ok else getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(cmd + [str(root / "main.py")], cwd=str(root), creationflags=flags)

class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FLY 启动器")
        self.geometry("440x300")
        self.resizable(False, False)
        self.q = queue.Queue()

        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="FLY", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self.status_lbl = ttk.Label(f, text="准备中...", font=("Segoe UI", 10))
        self.status_lbl.pack(anchor="w", pady=(6, 4))
        self.bar = ttk.Progressbar(f, mode="indeterminate")
        self.bar.pack(fill="x", pady=(0, 8))
        self.bar.start(12)
        self.logbox = tk.Text(f, height=8, font=("Segoe UI", 9), state="disabled", relief="flat",
                              background=self.cget("background"))
        self.logbox.pack(fill="both", expand=True)
        self.exit_btn = ttk.Button(f, text="退出", command=self.destroy)

        threading.Thread(target=self.worker, daemon=True).start()
        self.after(100, self.poll)

    # ---- worker-side callbacks (thread-safe via queue) ----
    def status(self, s): self.q.put(("status", s))
    def progress(self, frac): self.q.put(("progress", frac))
    def log(self, s): self.q.put(("log", s))

    def worker(self):
        root = app_dir()
        try:
            py = run_flow(root, self)
            self.status("启动 FLY...")
            launch(root, py)
            self.q.put(("done", None))
        except Exception as e:
            self.q.put(("error", str(e)))

    # ---- UI-side ----
    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "status":
                    self.status_lbl.configure(text=val)
                elif kind == "progress":
                    if val is None:
                        self.bar.configure(mode="indeterminate"); self.bar.start(12)
                    else:
                        self.bar.stop(); self.bar.configure(mode="determinate", value=val * 100)
                elif kind == "log":
                    self._append(val)
                elif kind == "done":
                    self.bar.stop(); self.bar.configure(mode="determinate", value=100)
                    self.status_lbl.configure(text="启动完成 ✓")
                    self.after(1500, self.destroy)
                    return
                elif kind == "error":
                    self.bar.stop()
                    self.status_lbl.configure(text="出错了")
                    self._append("[错误] " + val)
                    self.exit_btn.pack(anchor="e", pady=(8, 0))
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def _append(self, s):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", s + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

if __name__ == "__main__":
    LauncherApp().mainloop()
