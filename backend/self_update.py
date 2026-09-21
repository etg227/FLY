from __future__ import annotations
import os, subprocess, sys
from pathlib import Path

# 替换动作跑在一个独立的解释器进程里，源码通过 -c 传参，不落盘。
#
# 之前的做法是往 %TEMP% 写一个文件名可预测的 .cmd 再执行它。TUN 模式下
# 整个 FLY 是以管理员身份运行的，于是就成了「提权进程执行同用户可写目录里
# 的脚本」——同用户的非提权进程可以抢先占位或在写入与执行之间掉包，拿到
# 管理员权限的代码执行。不写文件就没有这个窗口。
_IS_WINDOWS = os.name == "nt"

_REPLACER = (
    "import os,sys,time\n"
    "src,dst=sys.argv[1],sys.argv[2]\n"
    "ok=False\n"
    "for _ in range(30):\n"
    "    try:\n"
    "        os.replace(src,dst)\n"
    "        ok=True\n"
    "        break\n"
    "    except OSError:\n"
    "        time.sleep(1)\n"
    "sys.exit(0 if ok else 3)\n"
)

def schedule_launcher_replace(root: Path, log=lambda m: None, spawn=None):
    """Replace launcher.exe after the currently running copy releases it.

    Source-mode FLY can use its Python interpreter as the detached replacer.
    Frozen standalone FLY is itself launcher.exe, so sys.executable -c would
    merely re-enter the launcher. In that mode use the system PowerShell
    executable with an inline retry loop and pass paths through environment
    variables (no temp script and no command-line interpolation).
    """
    root = Path(root)
    pending = root / "launcher.exe.new"
    target = root / "launcher.exe"
    if not _IS_WINDOWS or not pending.exists():
        return False
    spawn = spawn or subprocess.Popen
    try:
        if getattr(sys, "frozen", False):
            ps = (
                "$src=$env:FLY_REPLACE_SRC; $dst=$env:FLY_REPLACE_DST; "
                "$ok=$false; "
                "for($i=0;$i -lt 30;$i++){"
                "try { Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop; "
                "$ok=$true; break } "
                "catch { Start-Sleep -Seconds 1 }}; "
                "if($ok){exit 0}else{exit 3}"
            )
            env = os.environ.copy()
            env["FLY_REPLACE_SRC"] = str(pending)
            env["FLY_REPLACE_DST"] = str(target)
            spawn(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-WindowStyle", "Hidden", "-Command", ps],
                cwd=str(root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            if not sys.executable:
                log("[UPDATE] 找不到当前解释器，launcher.exe 自动替换已跳过。")
                return False
            spawn([sys.executable, "-c", _REPLACER, str(pending), str(target)],
                  cwd=str(root),
                  stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL,
                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log("[UPDATE] 已安排 launcher.exe 在当前程序退出后安全替换。")
        return True
    except Exception as e:
        log(f"[UPDATE] launcher.exe 自动替换安排失败：{e}")
        return False
