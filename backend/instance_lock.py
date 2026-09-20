from __future__ import annotations
import ctypes, os

ERROR_ALREADY_EXISTS = 183

class SingleInstance:
    """Windows named-mutex guard for the whole FLY app."""
    def __init__(self, name=r"Local\FLY-etg227-main"):
        self.name = name
        self.handle = None

    def acquire(self):
        if os.name != "nt":
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return False
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def close(self):
        if self.handle and os.name == "nt":
            try: ctypes.windll.kernel32.CloseHandle(self.handle)
            except Exception: pass
        self.handle = None
