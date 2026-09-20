"""Install and verify the pinned Mihomo core without executing untrusted code.

FLY pins one exact official GitHub asset.  The archive SHA-256 is embedded in
the application and the verified archive is retained next to mihomo.exe.  At
runtime we can therefore compare the executable with the copy inside that
trusted archive before executing it, instead of using `mihomo -v` as a
security check.
"""
from __future__ import annotations
import hashlib, io, os, tempfile, time, urllib.request, zipfile
from dataclasses import dataclass
from pathlib import Path

CORE_VERSION = "v1.19.31"
CORE_ASSET_NAME = "mihomo-windows-amd64-v1-v1.19.31.zip"
CORE_ZIP_SHA256 = "d89c9bd746e8aacff89b2edf674813e25e8bd2dc565f4e12dc3b4526dd2b3177"
CORE_DOWNLOAD_URL = (
    f"https://github.com/MetaCubeX/mihomo/releases/download/{CORE_VERSION}/{CORE_ASSET_NAME}"
)
CORE_ARCHIVE_FILENAME = "mihomo-verified.zip"
MIRRORS = [""]  # compatibility constant: trusted core downloads are official-only

VALID = "valid"
MISSING = "missing"
REPAIRABLE = "repairable"
INVALID = "invalid"
TRANSIENT = "transient"

class CoreVerifyError(RuntimeError):
    """The downloaded or local core failed trusted integrity verification."""

@dataclass(frozen=True)
class CoreInspection:
    state: str
    detail: str = ""

def core_archive_path(paths) -> Path:
    return paths.core_exe.parent / CORE_ARCHIVE_FILENAME

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _verified_archive_bytes(blob: bytes) -> bytes:
    actual = _sha256_bytes(blob)
    if actual.lower() != CORE_ZIP_SHA256.lower():
        raise CoreVerifyError(
            f"Mihomo 归档 SHA-256 不匹配（期望 {CORE_ZIP_SHA256[:12]}...，"
            f"实际 {actual[:12]}...）。")
    return blob

def _extract_exe(blob: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = [n for n in zf.namelist()
                     if Path(n.replace("\\", "/")).name.lower().startswith("mihomo")
                     and n.lower().endswith(".exe")]
            if len(names) != 1:
                raise CoreVerifyError("可信归档里没有唯一的 mihomo.exe。")
            data = zf.read(names[0])
    except zipfile.BadZipFile as e:
        raise CoreVerifyError("Mihomo 归档不是有效 ZIP。") from e
    if len(data) < 1024 * 1024 or data[:2] != b"MZ":
        raise CoreVerifyError("Mihomo 可执行文件结构异常。")
    return data

def _write_atomic(target: Path, data: bytes):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        delay = 0.02
        last = None
        for i in range(8):
            try:
                os.replace(tmp, target)
                last = None
                break
            except PermissionError as e:
                last = e
                if i == 7:
                    break
                time.sleep(delay)
                delay = min(0.25, delay * 2)
        if last is not None:
            raise last
    except PermissionError as e:
        Path(tmp).unlink(missing_ok=True)
        raise RuntimeError(f"{target.name} 正在被占用，无法替换；请先停止加速。") from e
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

def _get(url, timeout=60, log=None, progress_tag=None):
    req = urllib.request.Request(url, headers={"User-Agent": "FLY-Core-Installer"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
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

def _fetch(url, log, timeout=60, progress_tag=None, sources=None):
    # Kept compatible with existing test injection points; sources other than
    # official direct are intentionally rejected.
    if sources not in (None, [""], ("",)):
        raise CoreVerifyError("Mihomo 内核只允许从官方 GitHub 直连下载。")
    return _get(url, timeout=timeout, log=log, progress_tag=progress_tag), ""

def inspect_core(paths) -> CoreInspection:
    """Verify the installed exe against the retained, pinned official archive.

    No executable is launched here.  Permission/sharing failures are transient;
    mismatched bytes are invalid; a trusted archive with a missing/corrupt exe
    is repairable without network access.
    """
    exe = Path(paths.core_exe)
    archive = core_archive_path(paths)
    if not exe.exists() and not archive.exists():
        return CoreInspection(MISSING, "未安装 Mihomo 内核。")
    if not archive.exists():
        return CoreInspection(INVALID, "缺少可信 Mihomo 归档，无法证明现有 exe 的来源。")
    try:
        blob = archive.read_bytes()
        _verified_archive_bytes(blob)
        trusted_exe = _extract_exe(blob)
        trusted_hash = _sha256_bytes(trusted_exe)
    except (PermissionError, OSError) as e:
        return CoreInspection(TRANSIENT, f"暂时无法读取可信归档：{e}")
    except CoreVerifyError as e:
        return CoreInspection(INVALID, str(e))

    if not exe.exists():
        return CoreInspection(REPAIRABLE, "exe 缺失，但可信归档完整，可离线修复。")
    try:
        st = exe.stat()
        if st.st_size != len(trusted_exe):
            return CoreInspection(REPAIRABLE, "exe 大小与可信归档不一致，可离线修复。")
        if _sha256_file(exe) != trusted_hash:
            return CoreInspection(REPAIRABLE, "exe 内容与可信归档不一致，可离线修复。")
        with exe.open("rb") as fh:
            if fh.read(2) != b"MZ":
                return CoreInspection(REPAIRABLE, "exe 头部异常，可离线修复。")
    except (PermissionError, OSError) as e:
        return CoreInspection(TRANSIENT, f"暂时无法读取 mihomo.exe：{e}")
    return CoreInspection(VALID, f"{CORE_VERSION} 完整性验证通过。")

def repair_from_archive(paths, log=lambda m: None) -> bool:
    """Repair mihomo.exe from the pinned local archive without network access."""
    archive = core_archive_path(paths)
    if not archive.exists():
        return False
    try:
        blob = _verified_archive_bytes(archive.read_bytes())
        exe_bytes = _extract_exe(blob)
        _write_atomic(paths.core_exe, exe_bytes)
        log("[CORE] 已从本地可信归档修复 mihomo.exe。")
        return True
    except CoreVerifyError:
        return False

def install_core(paths, log, fetch=None):
    """Install the exact pinned official Mihomo asset and retain its archive."""
    fetch = fetch or _fetch

    # Prefer the already verified local archive. This also repairs an exe that
    # was deleted/quarantined by antivirus without requiring Internet access.
    if repair_from_archive(paths, log):
        return

    log(f"[CORE] 下载固定内核 {CORE_VERSION} / {CORE_ASSET_NAME}（官方 GitHub）...")
    try:
        blob, _ = fetch(CORE_DOWNLOAD_URL, log, timeout=90,
                        progress_tag="[CORE]", sources=[""])
    except Exception as e:
        raise RuntimeError(
            "无法下载固定 Mihomo 内核。可以稍后重试，或手动把精确归档 "
            f"{CORE_ASSET_NAME} 保存为 {core_archive_path(paths)}。\n"
            f"官方地址：{CORE_DOWNLOAD_URL}\n原因：{e}") from e

    _verified_archive_bytes(blob)
    exe_bytes = _extract_exe(blob)

    # Write the trust anchor archive first, then the executable. A crash between
    # the two leaves a repairable state that inspect_core() can recover.
    _write_atomic(core_archive_path(paths), blob)
    _write_atomic(paths.core_exe, exe_bytes)
    log(f"[CORE] {CORE_VERSION} 安装完成；归档与 exe 均已通过固定 SHA-256 校验。")
