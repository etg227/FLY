import ctypes, os, subprocess, sys
from pathlib import Path

def is_admin():
    if os.name != "nt": return True
    try: return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception: return False

def relaunch_as_admin(games="", autostart=False):
    """遗留入口：整程序提权重启。

    v0.8.11 起主流程不再使用——TUN 只提权 mihomo 本体（见
    elevated_launch.py），FLY 保持普通权限。保留此函数仅作为
    提权启动完全不可用时的手动兜底（用户自己右键管理员运行等价）。"""
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
