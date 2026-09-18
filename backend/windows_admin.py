import ctypes, os, sys
from pathlib import Path

def is_admin():
    if os.name != "nt": return True
    try: return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception: return False

def relaunch_as_admin(games="", autostart=False):
    if os.name != "nt": return False
    try:
        extra = (f' --games "{games}"' if games else "") + (" --autostart" if autostart else "")
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = extra.strip()
        else:
            exe = sys.executable
            script = Path(sys.argv[0]).resolve()
            params = f'"{script}"' + extra
        r = ctypes.windll.shell32.ShellExecuteW(None,"runas",exe,params,str(Path.cwd()),1)
        return r > 32
    except Exception:
        return False
