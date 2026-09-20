from __future__ import annotations
import ctypes, os

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

class SingleInstance:
    """Windows named-mutex guard; takeover launches may wait for the old UI."""
    def __init__(self, name=r"Local\FLY-etg227-main"):
        self.name = name
        self.handle = None
        self.owned = False

    def acquire(self, wait_ms=0):
        if os.name != "nt":
            self.owned = True
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, True, self.name)
        if not handle:
            return False
        existed = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        if not existed:
            self.handle, self.owned = handle, True
            return True
        # We did not get initial ownership of an existing mutex. Elevated
        # takeover may wait briefly for the old non-admin instance to close.
        rc = kernel32.WaitForSingleObject(handle, max(0, int(wait_ms)))
        if rc == WAIT_OBJECT_0:
            self.handle, self.owned = handle, True
            return True
        kernel32.CloseHandle(handle)
        return False

    def close(self):
        if self.handle and os.name == "nt":
            try:
                if self.owned:
                    ctypes.windll.kernel32.ReleaseMutex(self.handle)
            except Exception:
                pass
            try: ctypes.windll.kernel32.CloseHandle(self.handle)
            except Exception: pass
        self.handle = None
        self.owned = False
