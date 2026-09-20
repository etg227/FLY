"""Downloads the latest mihomo core into core/mihomo.exe.

安全前提：这个 exe 会被直接执行，TUN 配置下还是以管理员身份跑的。
所以校验链必须扎根在官方源上——

  1. release 元数据只认 api.github.com 直连；镜像给的元数据里的 digest
     无法自证，等于没有校验值。
  2. 拿到官方校验基准（asset digest 或同一 release 里的校验文件）后，
     zip 本体可以走任意镜像，下载完按 sha256 比对，不一致就换下一个源。
  3. 拿不到任何校验基准时，只接受官方直连下载，绝不接受镜像的二进制。
"""
from __future__ import annotations
import hashlib, io, json, os, re, tempfile, urllib.request, zipfile
from pathlib import Path

MIRRORS = ["", "https://ghproxy.net/", "https://gh-proxy.com/"]
API = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
ASSET_PATTERNS = (r"^mihomo-windows-amd64-v1-v[0-9].*\.zip$", r"^mihomo-windows-amd64.*\.zip$")
CHECKSUM_HINTS = ("sha256", "sha512", "checksum", "sums", "digest")

class CoreVerifyError(RuntimeError):
    """下载到的内核无法通过完整性校验。"""

def _source_name(prefix):
    return "官方直连" if not prefix else "镜像 " + prefix.split("/")[2]

def _get(url, prefix="", timeout=30, log=None, progress_tag=None):
    req = urllib.request.Request(prefix + url, headers={
        "User-Agent": "FLY-Core-Installer", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        buf, done, last_pct = io.BytesIO(), 0, -10
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            buf.write(chunk); done += len(chunk)
            if log and progress_tag and total:
                pct = done * 100 // total
                if pct >= last_pct + 10:
                    last_pct = pct
                    log(f"{progress_tag} {pct}%  ({done // 1048576}MB / {total // 1048576}MB)")
        return buf.getvalue()

def _fetch(url, log, timeout=30, progress_tag=None, sources=None):
    """按顺序尝试各个源；返回 (内容, 使用的源前缀)。"""
    last = None
    for prefix in (MIRRORS if sources is None else sources):
        try:
            return _get(url, prefix, timeout, log, progress_tag), prefix
        except Exception as e:
            log(f"[CORE] {_source_name(prefix)} 失败：{e}")
            last = e
    raise last or RuntimeError("no source available")

def find_sha256(text, filename):
    """从校验文件里找出某个文件名对应的 sha256。"""
    lines = [x.strip() for x in str(text).splitlines() if x.strip()]
    for line in lines:
        m = re.match(r"^([0-9a-fA-F]{64})[\s*]+(.+)$", line)
        if m and Path(m.group(2).strip()).name == filename:
            return m.group(1).lower()
    if len(lines) == 1:                      # 单文件校验，只有一个裸 hash
        m = re.search(r"\b([0-9a-fA-F]{64})\b", lines[0])
        if m:
            return m.group(1).lower()
    return None

def expected_sha256(release, asset, log, fetch=None):
    """返回 (sha256, 来源说明)；拿不到就是 (None, "")。只走官方直连。"""
    fetch = fetch or (lambda url: _fetch(url, log, timeout=20, sources=[""])[0])
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        value = digest.split(":", 1)[1].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value, "GitHub asset digest"
    name = asset.get("name", "")
    for other in release.get("assets", []):
        other_name = str(other.get("name", ""))
        if other_name == name or not any(h in other_name.lower() for h in CHECKSUM_HINTS):
            continue
        try:
            blob = fetch(other["browser_download_url"])
        except Exception as e:
            log(f"[CORE] 校验文件 {other_name} 获取失败：{e}")
            continue
        sha = find_sha256(blob.decode("utf-8", errors="replace"), name)
        if sha:
            return sha, f"校验文件 {other_name}"
    return None, ""

def _write_atomic(target: Path, data: bytes):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".mihomo-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except PermissionError as e:
        Path(tmp).unlink(missing_ok=True)
        raise RuntimeError("内核正在运行，无法替换，请先停止加速后再试。") from e
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

def install_core(paths, log, fetch=None):
    fetch = fetch or _fetch
    log("[CORE] 获取 mihomo 最新版本信息（仅官方源）...")
    try:
        meta, _prefix = fetch(API, log, timeout=20, sources=[""])
    except Exception as e:
        # 镜像给的元数据里 digest 是它自己写的，无法自证，所以宁可不装
        raise RuntimeError(
            "无法从 GitHub 官方接口获取版本信息；出于安全考虑不会改用镜像下载内核。"
            "可以稍后重试，或手动下载 mihomo-windows-amd64 的 exe 放到 core\\mihomo.exe。"
            f"（原因：{e}）") from e
    release = json.loads(meta.decode("utf-8", errors="replace"))

    asset = None
    for pat in ASSET_PATTERNS:
        asset = next((a for a in release.get("assets", []) if re.match(pat, a.get("name", ""))), None)
        if asset:
            break
    if not asset:
        raise RuntimeError("未找到 Windows AMD64 版本的 mihomo。")

    sha, origin = expected_sha256(release, asset, log,
                                  fetch=lambda url: fetch(url, log, timeout=20, sources=[""])[0])
    if sha:
        log(f"[CORE] 校验基准：{origin}（sha256 {sha[:12]}...），下载可走镜像。")
        sources = MIRRORS
    else:
        log("[CORE] 上游未提供可验证的校验值——本次只接受官方直连下载，不使用镜像。")
        sources = [""]

    log(f"[CORE] 下载 {asset['name']} ...")
    blob, used = fetch(asset["browser_download_url"], log, timeout=60,
                       progress_tag="[CORE]", sources=sources)
    actual = hashlib.sha256(blob).hexdigest()
    if sha and actual != sha:
        raise CoreVerifyError(
            f"内核校验失败，已拒绝安装（来源：{_source_name(used)}；"
            f"期望 {sha[:12]}... 实际 {actual[:12]}...）。")
    log(f"[CORE] 完整性校验通过（{_source_name(used)}，sha256 {actual[:12]}...）。"
        if sha else f"[CORE] 官方直连下载完成（sha256 {actual[:12]}...）。")

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next((n for n in zf.namelist() if re.search(r"mihomo.*\.exe$", n)), None)
        if not name:
            raise RuntimeError("安装包里没有 mihomo.exe。")
        exe_bytes = zf.read(name)
    _write_atomic(paths.core_exe, exe_bytes)
    log(f"[CORE] 内核安装完成（{len(exe_bytes) // 1048576}MB）。")
