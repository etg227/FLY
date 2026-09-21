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

# Frozen launcher keeps a copy of the pinned core trust constants.
# tests/test_core_integrity.py asserts exact equality with backend/core_installer.py.
CORE_VERSION = "v1.19.31"
CORE_ASSET_NAME = "mihomo-windows-amd64-v1-v1.19.31.zip"
CORE_ZIP_SHA256 = "d89c9bd746e8aacff89b2edf674813e25e8bd2dc565f4e12dc3b4526dd2b3177"
CORE_DOWNLOAD_URL = (
    f"https://github.com/MetaCubeX/mihomo/releases/download/{CORE_VERSION}/{CORE_ASSET_NAME}"
)
CORE_ARCHIVE_FILENAME = "mihomo-verified.zip"

OBSOLETE = [
    "START_FLY.bat","LAUNCHER.bat","INSTALL_CORE.bat","start_fly.py",
    "FLY.exe","launcher.spec","FLY.spec","scripts/INSTALL_CORE.ps1",
    "optional/wnacg.json","optional/gdmusic.json","optional/annas.json",
]

class _LauncherMutex:
    def __init__(self, name=r"Local\FLY-etg227-launcher"):
        self.name=name; self.handle=None; self.owned=False
    def acquire(self):
        if os.name!="nt":
            self.owned=True; return True
        k=ctypes.windll.kernel32
        k.CreateMutexW.restype=ctypes.c_void_p
        h=k.CreateMutexW(None,True,self.name)
        err=int(k.GetLastError())
        if not h:
            # API failure is not the same thing as "another launcher exists".
            # In particular an invalid named-object path used to land here and
            # was misleadingly reported as an already-running instance.
            raise OSError(err, ctypes.FormatError(err) or "CreateMutexW failed")
        if err==183:
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

def _embedded_bootstrap_dir() -> Path | None:
    """Return the PyInstaller-bundled bootstrap payload, if present."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    payload = Path(base) / "bootstrap"
    if not (payload / "main.py").exists() or not (payload / "VERSION").exists():
        return None
    return payload

def _seed_embedded_core(root: Path, payload: Path, ui=None) -> bool:
    """Install the pinned trusted core archive from the frozen launcher."""
    bundled = payload / "core" / CORE_ARCHIVE_FILENAME
    if not bundled.exists():
        return False
    blob = bundled.read_bytes()
    # Validate the embedded archive with the same hard-coded trust anchor used
    # for network downloads. A corrupt/modified bundle fails closed.
    _trusted_core_payload(blob)
    archive = root / "core" / CORE_ARCHIVE_FILENAME
    try:
        if archive.exists() and hashlib.sha256(archive.read_bytes()).hexdigest().lower() == CORE_ZIP_SHA256:
            return False
    except OSError:
        pass
    _atomic_bytes(archive, blob)
    if ui:
        ui.log(f"已从 launcher.exe 内置可信包准备 Mihomo {CORE_VERSION}。")
    return True

def seed_embedded_bootstrap(root: Path, ui=None) -> bool:
    """Restore a runnable local app from the standalone launcher.

    This runs before any network access.  A release launcher downloaded into a
    brand-new empty directory can therefore start even if GitHub is blocked.
    """
    payload = _embedded_bootstrap_dir()
    if payload is None:
        return False

    seeded = False
    essential_missing = (
        not (root / "main.py").exists()
        or not (root / "VERSION").exists()
        or not (root / "backend" / "config.py").exists()
    )
    if essential_missing:
        if ui:
            ui.status("正在从启动器内置包准备 FLY...")
        _transactional_install(payload, root, ui or _NullUi())
        seeded = True
        if ui:
            ui.log(f"本地程序文件已从 launcher.exe 内置包恢复为 v{local_version(root)}。")

    if _seed_embedded_core(root, payload, ui):
        seeded = True
    return seeded

class _NullUi:
    def log(self, _msg): pass
    def status(self, _msg): pass
    def progress(self, _frac): pass

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
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise RuntimeError("更新清单 schema 无效，已拒绝更新。")
    dirs_value = data.get("managed_dirs", [])
    roots_value = data.get("managed_root", [])
    if not isinstance(dirs_value, list) or not isinstance(roots_value, list):
        raise RuntimeError("更新清单 managed_dirs/managed_root 必须是数组。")
    if not all(isinstance(x, str) for x in dirs_value + roots_value):
        raise RuntimeError("更新清单路径必须是字符串。")
    requested_dirs = set(dirs_value)
    requested_roots = set(roots_value)
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

def _replace_retry(src, dst, attempts=8):
    delay=0.02
    last=None
    for i in range(max(1,int(attempts))):
        try:
            os.replace(src,dst)
            return
        except PermissionError as e:
            last=e
            if i+1>=attempts: break
            time.sleep(delay)
            delay=min(0.25,delay*2)
    raise last or PermissionError(f"cannot replace {dst}")

def _atomic_file_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=f".{dst.name}.", suffix=".new")
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
        _replace_retry(tmp, dst)
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
    except BaseException as update_error:
        try:
            _restore_snapshot(root, backup, manifest)
        except BaseException as rollback_error:
            # Keep journal + backup intact. The frozen launcher runs recovery
            # before starting main.py on the next launch, which is safer than
            # deleting the only rollback copy after a transient AV/file-lock.
            if ui:
                ui.log(
                    f"即时回滚未完成（{rollback_error}）；已保留事务备份，"
                    "下次启动会自动继续恢复。"
                )
            raise RuntimeError(
                f"更新失败且即时回滚未完成；恢复数据已保留在 {txn}。"
            ) from update_error
        else:
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

def _atomic_bytes(target: Path, data: bytes):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=str(target.parent),prefix=f".{target.name}.",suffix=".part")
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

def _trusted_core_payload(blob: bytes):
    actual=hashlib.sha256(blob).hexdigest()
    if actual.lower()!=CORE_ZIP_SHA256.lower():
        raise RuntimeError(
            f"Mihomo 归档 SHA-256 不匹配（{actual[:12]} != {CORE_ZIP_SHA256[:12]}）。")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names=[n for n in zf.namelist()
                   if PurePosixPath(n.replace("\\","/")).name.lower().startswith("mihomo")
                   and n.lower().endswith(".exe")]
            if len(names)!=1:
                raise RuntimeError("可信 Mihomo 归档里没有唯一 exe。")
            payload=zf.read(names[0])
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Mihomo 归档损坏。") from exc
    if len(payload)<1024*1024 or payload[:2]!=b"MZ":
        raise RuntimeError("Mihomo exe 结构异常。")
    return payload

def _file_sha256(path: Path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()

def ensure_core(root: Path, ui):
    core=root/"core"/"mihomo.exe"
    archive=root/"core"/CORE_ARCHIVE_FILENAME

    if archive.exists():
        try:
            blob=archive.read_bytes()
            payload=_trusted_core_payload(blob)
            if core.exists() and core.stat().st_size==len(payload) and \
               _file_sha256(core)==hashlib.sha256(payload).hexdigest():
                return
            _atomic_bytes(core,payload)
            ui.log(f"Mihomo {CORE_VERSION} 已从本地可信归档修复。")
            return
        except (OSError,RuntimeError) as e:
            ui.log(f"本地 Mihomo 可信归档不可用，将重新下载：{e}")

    ui.status(f"正在下载固定 Mihomo {CORE_VERSION}...")
    ui.progress(None)
    blob=fetch(CORE_DOWNLOAD_URL,ui)
    payload=_trusted_core_payload(blob)
    _atomic_bytes(archive,blob)
    _atomic_bytes(core,payload)
    ui.log(f"Mihomo {CORE_VERSION} 固定归档与 exe 完整性校验通过。")

def run_flow(root: Path, ui):
    (root/"runtime").mkdir(parents=True, exist_ok=True)
    recover_interrupted_update(root, ui)

    # Seed before touching the network.  On a true first run the frozen
    # launcher already contains the same-version app + pinned core, so there is
    # nothing useful to fetch before we can start.
    seeded = seed_embedded_bootstrap(root, ui)

    if not seeded:
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
    else:
        ui.log("首次/修复启动使用 launcher.exe 内置版本；本次不依赖 GitHub，更新检查将在下次启动进行。")

    if not (root/"main.py").exists():
        raise RuntimeError(
            "缺少程序文件 main.py，且当前 launcher.exe 不含可用的离线启动包。"
            "请重新下载官方最新 launcher.exe。"
        )

    # Frozen releases already carry a CPython runtime through PyInstaller.
    # Re-exec the same signed/trusted launcher in a dedicated --run-main mode
    # instead of installing a second Python just to execute main.py.
    if getattr(sys, "frozen", False):
        try:
            ensure_core(root,ui)
        except Exception as e:
            ui.log(f"内核准备未完成（{e}）。")
            raise
        return [sys.executable, "--run-main", str(root)]

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
    return py + [str(root/"main.py")]

def launch(root: Path, command):
    subprocess.Popen(
        list(command), cwd=str(root),
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)
    )

def _run_embedded_main(root_arg: str) -> int:
    """Run extracted main.py with the PyInstaller-embedded CPython runtime."""
    import runpy
    root = Path(root_arg).resolve()
    main_py = root / "main.py"
    if not main_py.exists():
        raise RuntimeError(f"embedded main mode cannot find {main_py}")
    os.chdir(root)
    sys.path.insert(0, str(root))
    runpy.run_path(str(main_py), run_name="__main__")
    return 0

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
            command=run_flow(root,self)
            self.status("启动 FLY...")
            launch(root,command)
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
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-main":
        raise SystemExit(_run_embedded_main(sys.argv[2]))

    guard=_LauncherMutex()
    try:
        acquired=guard.acquire()
    except OSError as e:
        try:
            r=tk.Tk(); r.withdraw()
            from tkinter import messagebox
            messagebox.showerror(
                "FLY",
                f"FLY 启动器单实例锁初始化失败（Windows 错误 {e.errno}）。\n\n{e}"
            )
            r.destroy()
        except Exception:
            pass
    else:
        if not acquired:
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
