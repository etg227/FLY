from __future__ import annotations
import ctypes, json, os, uuid
from pathlib import Path
from .config import save_json

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_PRIVATE_172 = ";".join(f"172.{i}.*" for i in range(16, 32))
PROXY_OVERRIDE = (
    f"localhost;127.*;10.*;{_PRIVATE_172};192.168.*;169.254.*;"
    "*.local;*.lan;<local>"
)

def _read_current():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
        def val(name, default):
            try: return winreg.QueryValueEx(k, name)[0]
            except OSError: return default
        return {
            "ProxyEnable": int(val("ProxyEnable", 0)),
            "ProxyServer": str(val("ProxyServer", "")),
            "ProxyOverride": str(val("ProxyOverride", "")),
        }

def _write(values):
    import winreg
    desired = int(values.get("ProxyEnable", 0))
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
        # Fail-safe ordering: disable first, write complete parameters, then
        # enable last. A mid-write failure leaves DIRECT rather than a half-
        # configured enabled proxy.
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, str(values.get("ProxyServer", "")))
        winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, str(values.get("ProxyOverride", "")))
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, desired)
    _refresh()

def _refresh():
    wininet = ctypes.windll.Wininet
    wininet.InternetSetOptionW(0, 39, 0, 0)
    wininet.InternetSetOptionW(0, 37, 0, 0)

class SystemProxy:
    """Own and restore the Windows proxy without clobbering user changes."""

    def __init__(self, backup_path: Path, log):
        self.backup_path = Path(backup_path)
        self.log = log
        self.session = uuid.uuid4().hex

    def _read_backup(self):
        try:
            data = json.loads(self.backup_path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _remove_backup(self):
        try: self.backup_path.unlink()
        except OSError: pass

    def enable(self, port):
        if os.name != "nt": return
        expected = f"127.0.0.1:{int(port)}"

        # A stale backup means the previous process died. Repair it before
        # taking ownership so we never save FLY's own proxy as "original".
        if self.backup_path.exists():
            self.restore_orphan()

        original = _read_current()
        backup = {
            "version": 2,
            "owner_pid": os.getpid(),
            "session": self.session,
            "fly_proxy_server": expected,
            "original": original,
        }
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)
        save_json(self.backup_path, backup)
        try:
            _write({
                "ProxyEnable": 1,
                "ProxyServer": expected,
                "ProxyOverride": PROXY_OVERRIDE,
            })
        except Exception:
            # enable() has not returned yet, so the caller cannot know the
            # registry was partially touched. Restore here before re-raising.
            try:
                _write(original)
                self.log("[PROXY] 启用失败，已回滚原系统代理设置。")
                self._remove_backup()
            except Exception as rollback_error:
                self.log(f"[PROXY] 启用失败且回滚未完成：{rollback_error}；保留备份供下次启动恢复。")
            raise
        self.log(f"[PROXY] System proxy -> {expected}; unmatched destinations remain DIRECT.")

    def restore(self):
        if os.name != "nt" or not self.backup_path.exists():
            return
        backup = self._read_backup()
        if not backup:
            self.log("[PROXY] 代理备份损坏；为避免覆盖用户设置，不自动写注册表。")
            self._remove_backup()
            return

        original = backup.get("original")
        expected = str(backup.get("fly_proxy_server", ""))
        current = _read_current()
        still_ours = (
            current.get("ProxyEnable") == 1 and
            str(current.get("ProxyServer", "")) == expected
        )
        if still_ours and isinstance(original, dict):
            _write(original)
            self.log("[PROXY] System proxy restored.")
        else:
            self.log("[PROXY] 检测到系统代理已被用户/其他程序修改，不覆盖当前设置。")
        self._remove_backup()

    def restore_orphan(self):
        """Repair only a proxy state that can be proven to belong to FLY."""
        if os.name != "nt" or not self.backup_path.exists():
            return
        backup = self._read_backup()
        if not backup:
            self.log("[PROXY] 发现无法读取的旧代理备份，已丢弃而不改动系统代理。")
            self._remove_backup()
            return
        expected = str(backup.get("fly_proxy_server", ""))
        current = _read_current()
        if current.get("ProxyEnable") == 1 and str(current.get("ProxyServer", "")) == expected:
            original = backup.get("original")
            if isinstance(original, dict):
                self.log("[PROXY] Found proxy settings left by a previous FLY run; restoring.")
                _write(original)
        self._remove_backup()
