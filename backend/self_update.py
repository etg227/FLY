from __future__ import annotations
import atexit, os, sys, time
from pathlib import Path

_IS_WINDOWS = os.name == "nt"
_deferred = False

def _cleanup_old(root: Path):
    """删掉之前替换留下的旧启动器；还在运行的删不掉，下次再试。"""
    for old in root.glob("launcher.exe.old*"):
        try:
            old.unlink()
        except OSError:
            pass

def schedule_launcher_replace(root: Path, log=lambda m: None):
    """把更新包带来的 launcher.exe.new 换上去。

    Windows 不允许覆盖或删除正在运行的 exe，但**允许重命名**。v0.10 起
    FLY 主程序本身就是 launcher.exe（--run-main），整个会话期间这个文件都被
    占用；以前「等它释放再覆盖」的外部替换进程只重试 30 秒，几乎永远装不上，
    启动器自身的修复也就永远到不了用户手里。

    现在在当前进程里直接：launcher.exe → launcher.exe.old.<随机>，再把 .new
    移到 launcher.exe。正在运行的旧进程不受影响，下次启动用的就是新启动器。
    不起外部进程、不落临时脚本，也就不存在临时目录被锁或提权相关的问题。
    """
    root = Path(root)
    pending = root / "launcher.exe.new"
    target = root / "launcher.exe"
    if not _IS_WINDOWS:
        return False
    _cleanup_old(root)
    if not pending.exists():
        return False
    if _running_from(target):
        # 本进程就是从这个 launcher.exe 跑起来的（--run-main）。PyInstaller 每次
        # 懒加载模块都会按“路径”重新打开 exe 读内嵌的 PYZ；现在就把文件换掉，
        # 之后第一个没加载过的模块（如 encodings.idna）就会从新 exe 的错误偏移
        # 读出乱码：zlib.error: incorrect header check，FLY 直接启动失败。
        # 所以推迟到 FLY 退出时再换，那时不会再有懒加载。
        global _deferred
        if not _deferred:
            _deferred = True
            _register_exit(_replace_now, root, lambda m: None)
        log("[UPDATE] 新版 launcher.exe 已就绪，将在退出 FLY 时替换，下次启动生效。")
        return False
    return _replace_now(root, log)


def _register_exit(fn, *args):
    atexit.register(fn, *args)


def _running_from(target: Path):
    if not getattr(sys, "frozen", False):
        return False
    try:
        return (os.path.normcase(os.path.realpath(sys.executable)) ==
                os.path.normcase(os.path.realpath(str(target))))
    except (OSError, ValueError):
        return False


def _replace_now(root: Path, log=lambda m: None):
    pending = root / "launcher.exe.new"
    target = root / "launcher.exe"
    if not pending.exists():
        return False
    old = root / f"launcher.exe.old.{os.getpid()}.{int(time.time())}"
    moved_old = False
    try:
        if target.exists():
            os.replace(target, old)          # 运行中的 exe 只能改名，不能覆盖
            moved_old = True
        os.replace(pending, target)
    except OSError as e:
        if moved_old and not target.exists():
            try:
                os.replace(old, target)      # 回滚，绝不能让目录里没有启动器
            except OSError:
                pass
        log(f"[UPDATE] launcher.exe 替换失败（{e}），新版本保留为 launcher.exe.new，下次启动重试。")
        return False
    log("[UPDATE] launcher.exe 已更新，下次启动生效。")
    if moved_old:
        try:
            old.unlink()                     # 旧启动器已退出时可直接删掉
        except OSError:
            pass                             # 仍在运行：下次启动时清理
    return True
