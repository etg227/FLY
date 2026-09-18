"""FLY launcher.

v0.8+: application self-updates are release-based and fail closed:
- discover only the latest GitHub Release;
- download FLY-update.zip + FLY-update.zip.sha256 from that release;
- verify SHA-256 before replacing any code;
- never update code from the mutable main branch or third-party mirrors.

User data (private/, core/, runtime/) is never touched.
"""
from __future__ import annotations
import hashlib, io, json, os, queue, re, shutil, subprocess, sys, tempfile, threading, urllib.request, webbrowser, zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk

OWNER = "etg227"
REPO = "FLY"
PROTECTED = {"private", "core", "runtime", ".git"}
TIMEOUT = 20
RELEASE_API = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
UPDATE_ASSET = "FLY-update.zip"
CHECKSUM_ASSET = "FLY-update.zip.sha256"

PYTHON_VERSION = "3.12.10"
PYTHON_URLS = [
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe",
]

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def fetch(url: str, ui=None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"{REPO}-launcher"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        buf, done = io.BytesIO(), 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            buf.write(chunk); done += len(chunk)
            if ui and total:
                ui.progress(done / total)
        return buf.getvalue()

def local_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return "0"

def _version_tuple(v):
    nums = re.findall(r"\d+", str(v))
    return tuple(int(x) for x in nums[:4]) or (0,)

def latest_release(ui=None):
    data = json.loads(fetch(RELEASE_API, ui).decode("utf-8", errors="replace"))
    tag = str(data.get("tag_name","")).lstrip("vV")
    assets = {a.get("name"):a.get("browser_download_url") for a in data.get("assets",[]) if a.get("name")}
    return tag, assets

def _parse_sha256(blob: bytes) -> str:
    text = blob.decode("utf-8-sig", errors="replace").strip()
    m = re.search(r"\b([a-fA-F0-9]{64})\b", text)
    if not m:
        raise RuntimeError("Release 校验文件里没有 SHA-256 值。")
    return m.group(1).lower()

def _safe_extract_update(data: bytes, expected_sha: str, root: Path, ui):
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != expected_sha.lower():
        raise RuntimeError(f"更新包校验不一致，拒绝更新（{actual[:12]} != {expected_sha[:12]}）。")
    with tempfile.TemporaryDirectory(prefix="fly-update-") as td:
        tdir = Path(td)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            bad = [n for n in zf.namelist() if Path(n).is_absolute() or ".." in Path(n).parts]
            if bad:
                raise RuntimeError("更新包内发现不安全路径，已拒绝。")
            zf.extractall(tdir)
        entries = [p for p in tdir.iterdir()]
        top = entries[0] if len(entries)==1 and entries[0].is_dir() else tdir
        if not (top / "main.py").exists() or not (top / "VERSION").exists():
            raise RuntimeError("更新包缺少必要的程序文件。")
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
    ui.log("更新包 SHA-256 校验通过。")

OBSOLETE = ["START_FLY.bat","LAUNCHER.bat","INSTALL_CORE.bat","start_fly.py",
            "FLY.exe","launcher.spec","FLY.spec","scripts/INSTALL_CORE.ps1"]

def apply_release_update(root: Path, assets, ui):
    zip_url = assets.get(UPDATE_ASSET)
    sum_url = assets.get(CHECKSUM_ASSET)
    if not zip_url or not sum_url:
        raise RuntimeError(
            f"最新 Release 缺少可验证的更新文件对（{UPDATE_ASSET} + {CHECKSUM_ASSET}），保持当前版本不变。"
        )
    ui.log("正在从 GitHub Release 下载更新包...")
    expected = _parse_sha256(fetch(sum_url, ui))
    data = fetch(zip_url, ui)
    _safe_extract_update(data, expected, root, ui)
    for name in OBSOLETE:
        try:
            p=root/name
            if p.is_file(): p.unlink()
        except OSError:
            pass

def find_python():
    py=shutil.which("py")
    if py: return [py,"-3"]
    p=shutil.which("python")
    if p: return [p]
    local=os.environ.get("LOCALAPPDATA","")
    if local:
        base=Path(local)/"Programs"/"Python"
        launch=base/"Launcher"/"py.exe"
        if launch.exists(): return [str(launch),"-3"]
        for d in sorted(base.glob("Python3*"), reverse=True):
            exe=d/"python.exe"
            if exe.exists(): return [str(exe)]
    return None

def windowless(python_cmd):
    exe=Path(python_cmd[0])
    alt={"py.exe":"pyw.exe","python.exe":"pythonw.exe"}.get(exe.name.lower())
    if alt:
        cand=exe.with_name(alt)
        if cand.exists(): return [str(cand)]+python_cmd[1:], True
    return list(python_cmd), False

def install_python(ui):
    ui.status(f"正在从 python.org 下载 Python {PYTHON_VERSION}（约 26MB）...")
    tmp=Path(tempfile.mkdtemp(prefix="fly-python-"))
    exe=tmp/f"python-{PYTHON_VERSION}-amd64.exe"
    try:
        data=fetch(PYTHON_URLS[0],ui)
    except Exception as e:
        ui.log(f"Python 下载失败：{e}"); return None
    exe.write_bytes(data)
    ui.status("正在静默安装 Python（约 1-2 分钟，请勿关闭窗口）...")
    ui.progress(None)
    r=subprocess.run([str(exe),"/quiet","InstallAllUsers=0","PrependPath=1",
                      "Include_launcher=1","InstallLauncherAllUsers=0",
                      "Include_tcltk=1","Include_test=0","Include_doc=0",
                      "Include_dev=0","Include_idle=0","Include_pip=0",
                      "AssociateFiles=0","Shortcuts=0"],
                     timeout=900,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if r.returncode != 0:
        ui.log(f"Python 安装程序返回错误码 {r.returncode}。"); return None
    return find_python()

CORE_API="https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
CORE_PATTERNS=(r"^mihomo-windows-amd64-v1-v[0-9].*\.zip$",r"^mihomo-windows-amd64.*\.zip$")

def ensure_core(root: Path, ui):
    core=root/"core"/"mihomo.exe"
    if core.exists(): return
    ui.status("正在从 MetaCubeX 官方 Release 下载加速内核（约 21MB）...")
    ui.progress(None)
    data=json.loads(fetch(CORE_API,ui).decode("utf-8",errors="replace"))
    asset=None
    for pat in CORE_PATTERNS:
        asset=next((a for a in data.get("assets",[]) if re.match(pat,a.get("name",""))),None)
        if asset: break
    if not asset: raise RuntimeError("未找到兼容的 Mihomo Windows 安装包。")
    blob=fetch(asset["browser_download_url"],ui)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name=next((n for n in zf.namelist() if re.search(r"mihomo.*\.exe$",n)),None)
        if not name: raise RuntimeError("Mihomo 安装包里没有可执行文件。")
        core.parent.mkdir(parents=True,exist_ok=True)
        core.write_bytes(zf.read(name))
    ui.log("加速内核安装完成。")

def run_flow(root: Path, ui):
    ui.status("正在检查 GitHub Release 更新...")
    cur=local_version(root)
    try:
        remote,assets=latest_release(ui)
        if remote and _version_tuple(remote) > _version_tuple(cur):
            ui.status(f"发现已验证的新版本 {remote}，正在更新...")
            apply_release_update(root,assets,ui)
            ui.log(f"已更新：{cur} → {remote}")
        elif remote:
            ui.log(f"已是最新版本（{cur}）。")
    except Exception as e:
        ui.log(f"更新已安全跳过：{e}")

    if not (root/"main.py").exists():
        raise RuntimeError("缺少程序文件 main.py，首次运行需要能访问 GitHub Release，请检查网络后重试。")
    ui.status("检查运行环境...")
    ui.progress(None)
    py=find_python()
    if not py: py=install_python(ui)
    if not py:
        try: webbrowser.open("https://www.python.org/downloads/")
        except Exception: pass
        raise RuntimeError('自动安装 Python 失败。已打开官网下载页，请手动安装（勾选 "Add python.exe to PATH"）后重新运行本程序。')
    try:
        ensure_core(root,ui)
    except Exception as e:
        ui.log(f"内核下载未完成（{e}），主程序会自动重试。")
    return py

def launch(root: Path, python_cmd):
    cmd,ok=windowless(python_cmd)
    flags=0 if ok else getattr(subprocess,"CREATE_NO_WINDOW",0)
    subprocess.Popen(cmd+[str(root/"main.py")],cwd=str(root),creationflags=flags)

class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FLY 启动器"); self.geometry("470x320"); self.resizable(False,False)
        self.q=queue.Queue()
        f=ttk.Frame(self,padding=16); f.pack(fill="both",expand=True)
        ttk.Label(f,text="FLY",font=("Segoe UI",16,"bold")).pack(anchor="w")
        self.status_lbl=ttk.Label(f,text="准备中..."); self.status_lbl.pack(anchor="w",pady=(6,4))
        self.bar=ttk.Progressbar(f,mode="indeterminate"); self.bar.pack(fill="x",pady=(0,8)); self.bar.start(12)
        self.logbox=tk.Text(f,height=9,font=("Segoe UI",9),state="disabled",relief="flat",background=self.cget("background"))
        self.logbox.pack(fill="both",expand=True)
        self.exit_btn=ttk.Button(f,text="退出",command=self.destroy)
        threading.Thread(target=self.worker,daemon=True).start()
        self.after(100,self.poll)

    def status(self,s): self.q.put(("status",s))
    def progress(self,frac): self.q.put(("progress",frac))
    def log(self,s): self.q.put(("log",s))

    def worker(self):
        root=app_dir()
        try:
            py=run_flow(root,self)
            self.status("启动 FLY...")
            launch(root,py)
            self.q.put(("done",None))
        except Exception as e:
            self.q.put(("error",str(e)))

    def poll(self):
        try:
            while True:
                kind,val=self.q.get_nowait()
                if kind=="status": self.status_lbl.configure(text=val)
                elif kind=="progress":
                    if val is None:
                        self.bar.configure(mode="indeterminate"); self.bar.start(12)
                    else:
                        self.bar.stop(); self.bar.configure(mode="determinate",value=val*100)
                elif kind=="log": self._append(val)
                elif kind=="done":
                    self.bar.stop(); self.bar.configure(mode="determinate",value=100)
                    self.status_lbl.configure(text="启动完成 ✓")
                    self.after(1200,self.destroy); return
                elif kind=="error":
                    self.bar.stop(); self.status_lbl.configure(text="出错了")
                    self._append("[错误] "+val)
                    self.exit_btn.pack(anchor="e",pady=(8,0))
        except queue.Empty: pass
        self.after(100,self.poll)

    def _append(self,s):
        self.logbox.configure(state="normal"); self.logbox.insert("end",s+"\n"); self.logbox.see("end"); self.logbox.configure(state="disabled")

if __name__=="__main__":
    LauncherApp().mainloop()
