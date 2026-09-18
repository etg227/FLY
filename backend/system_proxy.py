from __future__ import annotations
import ctypes, json, os
from pathlib import Path

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
PROXY_OVERRIDE = "localhost;127.*;192.168.*;10.*;<local>"

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
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, int(values.get("ProxyEnable", 0)))
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, str(values.get("ProxyServer", "")))
        winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, str(values.get("ProxyOverride", "")))
    _refresh()

def _refresh():
    # Tell WinINET the settings changed so running browsers pick them up immediately.
    wininet = ctypes.windll.Wininet
    wininet.InternetSetOptionW(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
    wininet.InternetSetOptionW(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH

class SystemProxy:
    """Shimakaze-Go style: point the Windows system proxy at the local mixed
    port while accelerating, restore the user's original settings afterwards.
    The original settings are kept in a backup file so a crashed run can be
    repaired on the next start."""

    def __init__(self, backup_path: Path, log):
        self.backup_path = Path(backup_path)
        self.log = log

    def enable(self, port):
        if os.name != "nt": return
        if not self.backup_path.exists():
            self.backup_path.parent.mkdir(parents=True, exist_ok=True)
            self.backup_path.write_text(json.dumps(_read_current()), encoding="utf-8")
        _write({
            "ProxyEnable": 1,
            "ProxyServer": f"127.0.0.1:{int(port)}",
            "ProxyOverride": PROXY_OVERRIDE,
        })
        self.log(f"[PROXY] System proxy -> 127.0.0.1:{int(port)} (game domains only; the rest stays DIRECT).")

    def restore(self):
        if os.name != "nt": return
        if not self.backup_path.exists(): return
        try:
            values = json.loads(self.backup_path.read_text(encoding="utf-8"))
        except Exception:
            values = {"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": "<local>"}
        _write(values)
        try: self.backup_path.unlink()
        except OSError: pass
        self.log("[PROXY] System proxy restored.")

    def restore_orphan(self):
        """Called at startup: if a backup exists, a previous run died without
        restoring the proxy — put the user's settings back."""
        if os.name != "nt" or not self.backup_path.exists(): return
        self.log("[PROXY] Found proxy settings left over from a previous run; restoring.")
        self.restore()
