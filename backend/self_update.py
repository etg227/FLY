from __future__ import annotations
import os, subprocess, tempfile, time
from pathlib import Path

def schedule_launcher_replace(root: Path, log=lambda m: None):
    """Replace the frozen launcher after the currently running launcher exits.

    v0.8.10's updater can copy launcher.exe.new even though it cannot overwrite
    its own running executable. The newly updated main.py schedules this helper
    on first launch, so launcher security fixes reach existing users without a
    manual download.
    """
    root = Path(root)
    pending = root / "launcher.exe.new"
    target = root / "launcher.exe"
    if os.name != "nt" or not pending.exists():
        return False
    script = Path(tempfile.gettempdir()) / f"fly-launcher-update-{os.getpid()}-{int(time.time())}.cmd"
    body = (
        "@echo off\r\n"
        "setlocal\r\n"
        "for /L %%I in (1,1,30) do (\r\n"
        f'  move /Y "{pending}" "{target}" >nul 2>nul && goto done\r\n'
        "  timeout /t 1 /nobreak >nul\r\n"
        ")\r\n"
        ":done\r\n"
        'del "%~f0" >nul 2>nul\r\n'
    )
    try:
        script.write_text(body, encoding="utf-8")
        subprocess.Popen(["cmd.exe", "/c", str(script)],
                         cwd=str(root),
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log("[UPDATE] 已安排 launcher.exe 在旧启动器退出后安全替换。")
        return True
    except Exception as e:
        log(f"[UPDATE] launcher.exe 自动替换安排失败：{e}")
        return False
