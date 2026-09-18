"""FLY direct starter: launches the app windowless, no update check.
Build FLY.exe with scripts/BUILD_LAUNCHER.ps1 (PyInstaller --noconsole).
Uses the Win32 message box instead of tkinter to keep the exe small."""
from __future__ import annotations
import ctypes, os, shutil, subprocess, sys
from pathlib import Path

def msg(text, title="FLY"):
    ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def find_windowless():
    for name, args in (("pyw", ["-3"]), ("pythonw", [])):
        p = shutil.which(name)
        if p:
            return [p] + args
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        base = Path(local) / "Programs" / "Python"
        c = base / "Launcher" / "pyw.exe"
        if c.exists():
            return [str(c), "-3"]
        for d in sorted(base.glob("Python3*"), reverse=True):
            e = d / "pythonw.exe"
            if e.exists():
                return [str(e)]
    return None

def main():
    root = app_dir()
    main_py = root / "main.py"
    if not main_py.exists():
        msg("未找到程序文件。\n\n请先运行 launcher.exe 完成首次安装（它会自动拉取程序并安装环境）。")
        return
    py = find_windowless()
    if not py:
        msg("未检测到 Python 运行环境。\n\n请先运行 launcher.exe，它会自动下载安装。")
        return
    subprocess.Popen(py + [str(main_py)], cwd=str(root))

if __name__ == "__main__":
    main()
