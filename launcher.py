"""FLY bootstrap launcher.

Updates are release-based, verified, transactional and recoverable.  private/,
core/ and runtime/ are user/runtime state and are never replaced by an app
update.
"""
from __future__ import annotations
import ctypes, hashlib, io, json, os, queue, re, shutil, subprocess, sys, tempfile, threading, urllib.request, webbrowser, zipfile
from pathlib import Path, PurePosixPath
import tkinter as tk
from tkinter import ttk

OWNER = "etg227"
REPO = "FLY"
PROTECTED = {"private", "core", "runtime", ".git"}
MANAGED_DIRS = ("backend", "rules", "scripts")
MANAGED_ROOT = (
    "main.py", "launcher.py", "README.md", "LICENSE", "START_FLY_DEBUG.bat",
    ".gitignore", "update-manifest.json", "launcher.exe.new",
)
TIMEOUT = 20
RELEASE_API = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
UPDATE_ASSET = "FLY-update.zip"
CHECKSUM_ASSET = "FLY-update.zip.sha256"
TXN_NAME = "update-txn"

PYTHON_VERSION = "3.12.10"
PYTHON_URLS = [f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe"]

CORE_VERSION = "v1.19.31"
CORE_API = f"https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/{CORE_VERSION}"
CORE_PATTERNS = (r"^mihomo-windows-amd64-v1-v[0-9].*\.zip$", r"^mihomo-windows-amd64.*\.zip$")
CORE_SUM_HINTS = ("sha256","sha512","checksum","sums","digest")

OBSOLETE = [
    "START_FLY.bat","LAUNCHER.bat","INSTALL_CORE.bat","start_fly.py",
    "FLY.exe","launcher.spec","FLY.spec","scripts/INSTALL_CORE.ps1",
    "optional/wnacg.json","optional/gdmusic.json","optional/annas.json",
]

class _LauncherMutex:
    def __init__(self, name=r"Local\\FLY-etg227-launcher"):
        self.name=name; self.handle=None; self.owned=False
    def acquire(self):
        if os.name!="nt":
            self.owned=True; return True
        k=ctypes.windll.kernel32
        k.CreateMutexW.restype=ctypes.c_void_p
        h=k.CreateMutexW(None,True,self.name)
        if not h:return False
        if k.GetLastError()==183:
            k.CloseHandle(h); return False
        self.handle=h; self.owned=True; return True
    def close(self):
        if self.handle and os.name=="nt":
            try:
                if self.owned:ctypes.windll.kernel32.ReleaseMutex(self.handle)
            except Exception:pass
            try:ctypes.windll.kernel32.CloseHandle(self.handle)
            except Exception:pass
        self.handle=None; self.owned=False

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def _opener():
    # Bootstrap/control traffic must never inherit FLY's Windows system proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))

def fetch(url: str, ui=None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"{REPO}-launcher"})
    with _opener().open(req, timeout=TIMEOUT) as resp:
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
    assets = {a.get("name"):a.get("browser_download_url")
              for a in data.get("assets",[]) if a.get("name")}
    return tag, assets

def _parse_sha256(blob: bytes, filename=UPDATE_ASSET) -> str:
    text = blob.decode("utf-8-sig", errors="replace").strip()
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for line in lines:
        m = re.match(r"^([a-fA-F0-9]{64})[\s*]+(.+)$", line)
        if m and PurePosixPath(m.group(2).replace("\\","/").strip()).name == filename:
            return m.group(1).lower()
    if len(lines) == 1:
        m = re.fullmatch(r"(?:SHA256\s*\([^)]*\)\s*=\s*)?([a-fA-F0-9]{64})", lines[0], re.I)
        if m:
            return m.group(1).lower()
    raise RuntimeError(f"Release 校验文件里没有 {filename} 对应的 SHA-256。")

def _safe_member(name):
    raw = str(name).replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    p = PurePosixPath(raw)
    if any(part in ("", ".", "..") for part in p.parts):
        return None
    if p.parts and p.parts[0] in PROTECTED:
        return None
    return p

def _extract_verified(data: bytes, expected_sha: str, stage: Path):
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != expected_sha.lower():
        raise RuntimeError(f"更新包校验不一致，拒绝更新（{actual[:12]} != {expected_sha[:12]}）。")
    stage.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            rel = _safe_member(info.filename)
            if rel is None:
                raise RuntimeError(f"更新包内发现不安全路径：{info.filename!r}")
            dst = stage.joinpath(*rel.parts)
            if info.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
    # tolerate a single wrapper directory produced by some archive tools
    entries = list(stage.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return stage

def _read_manifest(top: Path):
    manifest = top / "update-manifest.json"
    if not manifest.exists():
        # Backward-compatible fallback; directory replacement still removes
        # stale modules even if an older package lacks a manifest.
        return {"managed_dirs": list(MANAGED_DIRS), "managed_root": list(MANAGED_ROOT)}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except Exception as e:
        raise RuntimeError(f"更新清单损坏：{e}") from e
    requested_dirs = set(data.get("managed_dirs", []))
    requested_roots = set(data.get("managed_root", []))
    unknown_dirs = requested_dirs - set(MANAGED_DIRS)
    unknown_roots = requested_roots - set(MANAGED_ROOT)
    if unknown_dirs or unknown_roots:
        raise RuntimeError("更新清单包含未授权路径，已拒绝更新。")
    # The trusted managed set is fixed by the launcher, not by the incoming
    # package. If a future release removes a managed root file/directory, the
    # old copy must disappear instead of surviving forever.
    return {"managed_dirs": list(MANAGED_DIRS),
            "managed_root": list(MANAGED_ROOT)}

def _copy_path(src: Path, dst: Path):
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    elif src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

def _remove_path(path: Path):
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
    elif path.exists():
        path.unlink()

def _atomic_file_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=f".{dst.name}.", suffix=".new")
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        try: Path(tmp).unlink(missing_ok=True)
        except OSError: pass
        raise

def _snapshot(root: Path, backup: Path, manifest):
    backup.mkdir(parents=True, exist_ok=True)
    for name in manifest["managed_dirs"]:
        src = root / name
        if src.exists():
            _copy_path(src, backup / name)
    for name in list(manifest["managed_root"]) + ["VERSION"]:
        src = root / name
        if src.exists():
            _copy_path(src, backup / name)

def _restore_snapshot(root: Path, backup: Path, manifest):
    for name in manifest["managed_dirs"]:
        dst = root / name
        if dst.exists(): _remove_path(dst)
        src = backup / name
        if src.exists(): _copy_path(src, dst)
    for name in list(manifest["managed_root"]) + ["VERSION"]:
        dst = root / name
        if dst.exists(): _remove_path(dst)
        src = backup / name
        if src.exists(): _copy_path(src, dst)

def recover_interrupted_update(root: Path, ui=None):
    txn = root / "runtime" / TXN_NAME
    journal = txn / "journal.json"
    if not journal.exists():
        return False
    try:
        data = json.loads(journal.read_text(encoding="utf-8-sig"))
        manifest = data.get("manifest") or {"managed_dirs":list(MANAGED_DIRS),"managed_root":list(MANAGED_ROOT)}
        backup = txn / "backup"
        if data.get("state") == "committing" and backup.exists():
            if ui: ui.log("检测到上次更新中断，正在回滚到更新前版本...")
            _restore_snapshot(root, backup, manifest)
        shutil.rmtree(txn, ignore_errors=True)
        if ui: ui.log("更新恢复完成。")
        return True
    except Exception as e:
        if ui: ui.log(f"更新恢复失败：{e}")
        return False

def _transactional_install(top: Path, root: Path, ui):
    if not (top/"main.py").exists() or not (top/"VERSION").exists():
        raise RuntimeError("更新包缺少 main.py 或 VERSION。")
    manifest = _read_manifest(top)
    txn = root / "runtime" / TXN_NAME
    if txn.exists():
        shutil.rmtree(txn, ignore_errors=True)
    backup = txn / "backup"
    txn.mkdir(parents=True, exist_ok=True)
    _snapshot(root, backup, manifest)
    journal = txn / "journal.json"
    journal.write_text(json.dumps({"state":"committing","manifest":manifest},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        # Replace managed directories wholesale so upstream-deleted modules
        # cannot survive indefinitely on user machines.
        for name in manifest["managed_dirs"]:
            src, dst = top/name, root/name
            if dst.exists(): _remove_path(dst)
            if src.exists(): _copy_path(src, dst)
        for name in manifest["managed_root"]:
            src, dst = top/name, root/name
            if src.exists():
                _atomic_file_copy(src, dst)
            elif dst.exists():
                _remove_path(dst)
        # VERSION is the commit marker and is written last.
        _atomic_file_copy(top/"VERSION", root/"VERSION")
        journal.write_text(json.dumps({"state":"complete","manifest":manifest}),
                           encoding="utf-8")
        shutil.rmtree(txn, ignore_errors=True)
    except BaseException:
        try:
            _restore_snapshot(root, backup, manifest)
        finally:
            shutil.rmtree(txn, ignore_errors=True)
        raise
    for name in OBSOLETE:
        try:
            p = root / name
            if p.exists(): _remove_path(p)
        except OSError:
            pass
    ui.log("更新事务提交完成；旧模块已按清单清理。")

def apply_release_update(root: Path, assets, ui):
    zip_url = assets.get(UPDATE_ASSET)
    sum_url = assets.get(CHECKSUM_ASSET)
    if not zip_url or not sum_url:
        raise RuntimeError(f"最新 Release 缺少 {UPDATE_ASSET} + {CHECKSUM_ASSET}。")
    ui.log("正在从 GitHub Release 下载更新包...")
    expected = _parse_sha256(fetch(sum_url, ui), UPDATE_ASSET)
    data = fetch(zip_url, ui)
    with tempfile.TemporaryDirectory(dir=str(root/"runtime"), prefix="fly-stage-") as td:
        top = _extract_verified(data, expected, Path(td))
        _transactional_install(top, root, ui)

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
    ui.status(f"正在从 python.org 下载 Python {PYTHON_VERSION}...")
    tmp=Path(tempfile.mkdtemp(prefix="fly-python-"))
    exe=tmp/f"python-{PYTHON_VERSION}-amd64.exe"
    try:
        data=fetch(PYTHON_URLS[0],ui)
        exe.write_bytes(data)
        ui.status("正在静默安装 Python...")
        ui.progress(None)
        r=subprocess.run([str(exe),"/quiet","InstallAllUsers=0","PrependPath=1",
                          "Include_launcher=1","InstallLauncherAllUsers=0",
                          "Include_tcltk=1","Include_test=0","Include_doc=0",
                          "Include_dev=0","Include_idle=0","Include_pip=0",
                          "AssociateFiles=0","Shortcuts=0"],
                         timeout=900,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if r.returncode != 0:
            ui.log(f"Python 安装程序返回错误码 {r.returncode}。")
            return None
        return find_python()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def _sha_for(text,filename):
    lines=[x.strip() for x in str(text).splitlines() if x.strip()]
    for line in lines:
        m=re.match(r"^([0-9a-fA-F]{64})[\s*]+(.+)$",line)
        if m and PurePosixPath(m.group(2).replace("\\","/").strip()).name==filename:
            return m.group(1).lower()
    if len(lines)==1:
        m=re.search(r"\b([0-9a-fA-F]{64})\b",lines[0])
        if m: return m.group(1).lower()
    return None

def _core_expected_sha(data,asset):
    digest=str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        v=digest.split(":",1)[1].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}",v): return v
    for other in data.get("assets",[]):
        n=str(other.get("name",""))
        if n==asset.get("name") or not any(h in n.lower() for h in CORE_SUM_HINTS):
            continue
        try: blob=fetch(other["browser_download_url"])
        except Exception: continue
        sha=_sha_for(blob.decode("utf-8",errors="replace"),asset.get("name",""))
        if sha: return sha
    return None

def ensure_core(root: Path, ui):
    core=root/"core"/"mihomo.exe"
    if core.exists() and core.stat().st_size > 1024*1024:
        return
    ui.status(f"正在下载已验证兼容的 Mihomo {CORE_VERSION}...")
    ui.progress(None)
    data=json.loads(fetch(CORE_API,ui).decode("utf-8",errors="replace"))
    asset=None
    for pat in CORE_PATTERNS:
        asset=next((a for a in data.get("assets",[]) if re.match(pat,a.get("name",""))),None)
        if asset: break
    if not asset: raise RuntimeError("未找到兼容的 Mihomo Windows 安装包。")
    expected=_core_expected_sha(data,asset)
    if not expected:
        raise RuntimeError("官方 Mihomo Release 未提供可验证 SHA-256，已拒绝安装。")
    blob=fetch(asset["browser_download_url"],ui)
    actual=hashlib.sha256(blob).hexdigest()
    if actual!=expected:
        raise RuntimeError(f"内核完整性校验失败，已拒绝安装（{actual[:12]} != {expected[:12]}）。")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name=next((n for n in zf.namelist() if re.search(r"mihomo.*\.exe$",n)),None)
        if not name: raise RuntimeError("Mihomo 安装包里没有可执行文件。")
        payload=zf.read(name)
    core.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=str(core.parent),prefix=".mihomo-",suffix=".part")
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(payload); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,core)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise
    ui.log(f"Mihomo {CORE_VERSION} 完整性校验通过并安装完成。")

def run_flow(root: Path, ui):
    (root/"runtime").mkdir(parents=True, exist_ok=True)
    recover_interrupted_update(root, ui)
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
        raise RuntimeError("缺少程序文件 main.py，首次运行需要访问 GitHub Release。")
    ui.status("检查运行环境...")
    ui.progress(None)
    py=find_python()
    if not py: py=install_python(ui)
    if not py:
        try: webbrowser.open("https://www.python.org/downloads/")
        except Exception: pass
        raise RuntimeError('自动安装 Python 失败，请手动安装后重新运行。')
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
        self.logbox.configure(state="normal")
        self.logbox.insert("end",s+"\n"); self.logbox.see("end")
        self.logbox.configure(state="disabled")

if __name__=="__main__":
    guard=_LauncherMutex()
    if not guard.acquire():
        try:
            r=tk.Tk(); r.withdraw()
            from tkinter import messagebox
            messagebox.showinfo("FLY","FLY 启动器已经在运行，请使用现有窗口。")
            r.destroy()
        except Exception:
            pass
    else:
        try:
            LauncherApp().mainloop()
        finally:
            guard.close()
