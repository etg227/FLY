"""PyInstaller onefile 进程拉起外部子进程时的环境卫生。

onefile 进程会把 DLL 搜索目录设成自己的 _MEIxxxx（SetDllDirectoryW，
会被子进程继承），并在环境里留下 _PYI_* 标记和指向 _MEI 的 Tcl/Tk 路径。
任何比本进程活得久的子进程若从 _MEI 加载了文件，本进程退出时引导程序就
删不掉临时目录，弹出 "Failed to remove temporary directory"。

launcher.py 是独立打包的启动器、不 import backend，里面有一份同逻辑的副本。
"""
from __future__ import annotations
import ctypes, os, sys

_TCLTK = ("TCL_LIBRARY", "TK_LIBRARY", "TKPATH")

def child_env(base=None):
    env = dict(os.environ if base is None else base)
    if not getattr(sys, "frozen", False):
        return env
    for key in list(env):
        k = key.upper()
        if k.startswith("_PYI_") or k == "_MEIPASS2":
            env.pop(key, None)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        root = os.path.normcase(os.path.abspath(meipass))
        for key in list(env):
            if key.upper() not in _TCLTK:
                continue
            val = os.path.normcase(os.path.abspath(env[key]))
            if val == root or val.startswith(root + os.sep):
                env.pop(key, None)
    return env

class dll_search_reset:
    """spawn 期间撤掉继承性的 DLL 搜索目录，退出 with 立即恢复。"""
    def __enter__(self):
        self.prev = None
        if os.name == "nt" and getattr(sys, "frozen", False):
            try:
                k32 = ctypes.windll.kernel32
                buf = ctypes.create_unicode_buffer(32768)
                if k32.GetDllDirectoryW(len(buf), buf):
                    self.prev = buf.value or None
                k32.SetDllDirectoryW(None)
            except Exception:
                self.prev = None
        return self

    def __exit__(self, *exc):
        if self.prev:
            try: ctypes.windll.kernel32.SetDllDirectoryW(self.prev)
            except Exception: pass
        return False
