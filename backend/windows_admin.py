import ctypes, os, subprocess, sys
from pathlib import Path

def is_admin():
    if os.name != "nt": return True
    try: return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception: return False

def relaunch_as_admin(games="", autostart=False):
    if os.name != "nt": return False
    try:
        args = []
        if not getattr(sys, "frozen", False):
            args.append(str(Path(sys.argv[0]).resolve()))
        if games:
            args += ["--games", str(games)]
        if autostart:
            args.append("--autostart")
        args.append("--takeover")
        exe = sys.executable
        # list2cmdline implements Windows CommandLineToArgvW-compatible quoting;
        # custom profile IDs can no longer break the elevated command line.
        params = subprocess.list2cmdline(args)
        r = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, str(Path.cwd()), 1)
        return r > 32
    except Exception:
        return False
