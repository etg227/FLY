"""Downloads the latest mihomo core into core/mihomo.exe, with GitHub mirror
fallback and progress reported through the app log — no external console."""
from __future__ import annotations
import io, json, re, urllib.request, zipfile

MIRRORS = ["", "https://ghproxy.net/", "https://gh-proxy.com/"]
API = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
ASSET_PATTERNS = (r"^mihomo-windows-amd64-v1-v[0-9].*\.zip$", r"^mihomo-windows-amd64.*\.zip$")

def _fetch(url, log, timeout=30, progress_tag=None):
    last = None
    for m in MIRRORS:
        try:
            req = urllib.request.Request(m + url, headers={
                "User-Agent": "FLY-Core-Installer", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                buf, done, last_pct = io.BytesIO(), 0, -10
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    buf.write(chunk)
                    done += len(chunk)
                    if progress_tag and total:
                        pct = done * 100 // total
                        if pct >= last_pct + 10:
                            last_pct = pct
                            log(f"{progress_tag} {pct}%  ({done // 1048576}MB / {total // 1048576}MB)")
                return buf.getvalue()
        except Exception as e:
            log(f"[CORE] {'直连' if not m else '镜像 ' + m.split('/')[2]} 失败：{e}")
            last = e
    raise last

def install_core(paths, log):
    log("[CORE] 获取 mihomo 最新版本信息...")
    data = json.loads(_fetch(API, log, timeout=20).decode("utf-8", errors="replace"))
    asset = None
    for pat in ASSET_PATTERNS:
        asset = next((a for a in data.get("assets", []) if re.match(pat, a.get("name", ""))), None)
        if asset:
            break
    if not asset:
        raise RuntimeError("未找到 Windows AMD64 版本的 mihomo。")
    log(f"[CORE] 下载 {asset['name']} ...")
    blob = _fetch(asset["browser_download_url"], log, timeout=60, progress_tag="[CORE]")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next((n for n in zf.namelist() if re.search(r"mihomo.*\.exe$", n)), None)
        if not name:
            raise RuntimeError("安装包里没有 mihomo.exe。")
        exe_bytes = zf.read(name)
    paths.core_exe.parent.mkdir(parents=True, exist_ok=True)
    paths.core_exe.write_bytes(exe_bytes)
    log(f"[CORE] 内核安装完成（{len(exe_bytes) // 1048576}MB）。")
