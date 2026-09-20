"""以管理员身份只启动『已验证的』mihomo.exe，FLY 本体保持普通权限。

TUN 需要管理员，但把整套 Python GUI 提权，意味着用户可写目录里的任何
.py 被篡改后都能以管理员身份执行。改为只提权哈希钉死、启动前刚校验过
指纹的 mihomo 本体之后：

- UAC 弹窗里用户授权的对象是 mihomo.exe，而不是执行任意脚本的 python；
- 被篡改的 .py 只能以普通权限运行，拿不到管理员；
- 提权面收敛为「固定二进制 + 它读到的配置」。

实现要点（全部收在这个薄层里，方便在无 Windows 的环境下测试其余逻辑）：
- ShellExecuteExW("runas") + SEE_MASK_NOCLOSEPROCESS 拿回进程句柄。该句柄
  由 UAC 的 AppInfo 服务在创建进程时授予并复制给调用方，携带
  TERMINATE/SYNCHRONIZE 等权限——普通进程事后 OpenProcess 一个提权进程
  是拿不到这些权限的，这正是停止内核的唯一通道。
- 提权子进程没有 stdout 管道，日志改走内核 RESTful API 的 GET /logs
  （见 core_log_stream.py）。
"""
from __future__ import annotations
import os, subprocess

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_HIDE = 0
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x0
WAIT_TIMEOUT = 0x102
ERROR_CANCELLED = 1223
STILL_ACTIVE = 259

class ElevationError(RuntimeError):
    """提权启动失败（环境不支持 / API 失败）。"""

class ElevationCancelled(ElevationError):
    """用户在 UAC 提示中点了『否』。"""

def _real_backend():
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HANDLE),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    class Win:
        def shell_execute_elevated(self, exe, params, cwd):
            info = SHELLEXECUTEINFOW()
            info.cbSize = ctypes.sizeof(info)
            info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
            info.hwnd = None
            info.lpVerb = "runas"
            info.lpFile = str(exe)
            info.lpParameters = params
            info.lpDirectory = str(cwd) if cwd else None
            info.nShow = SW_HIDE
            if not shell32.ShellExecuteExW(ctypes.byref(info)):
                return None, ctypes.get_last_error() or kernel32.GetLastError()
            return info.hProcess, 0

        def wait(self, handle, ms):
            return kernel32.WaitForSingleObject(handle, ms)

        def exit_code(self, handle):
            code = ctypes.wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return (True, code.value) if ok else (False, None)

        def terminate(self, handle, code=1):
            return bool(kernel32.TerminateProcess(handle, code))

        def pid(self, handle):
            return int(kernel32.GetProcessId(handle))

        def close(self, handle):
            kernel32.CloseHandle(handle)

    return Win()

class ElevatedProcess:
    """把提权进程句柄包装成 Popen 风格的最小接口（无 stdout）。"""

    stdout = None

    def __init__(self, handle, win):
        self._handle = handle
        self._win = win
        self._closed = False
        self.returncode = None
        try:
            self.pid = win.pid(handle)
        except Exception:
            self.pid = None

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self._closed:
            return self.returncode
        # 先看信号态再取退出码：真实退出码恰好是 STILL_ACTIVE(259) 的极端
        # 情况也不会被误判成“仍在运行”。
        if self._win.wait(self._handle, 0) == WAIT_TIMEOUT:
            return None
        ok, code = self._win.exit_code(self._handle)
        self.returncode = code if ok else -1
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        ms = INFINITE if timeout is None else max(0, int(timeout * 1000))
        state = self._win.wait(self._handle, ms)
        if state == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(cmd="mihomo(elevated)", timeout=timeout)
        return self.poll()

    def terminate(self):
        if self.poll() is None and not self._win.terminate(self._handle, 1):
            raise OSError("TerminateProcess 失败（提权句柄可能已失效）")

    kill = terminate

    def close(self):
        if not self._closed:
            self._closed = True
            try: self._win.close(self._handle)
            except Exception: pass

def launch_elevated(exe, args, cwd, win=None):
    """UAC 提权启动 exe；返回 ElevatedProcess。用户拒绝→ElevationCancelled。"""
    if win is None:
        if os.name != "nt":
            raise ElevationError("提权启动仅在 Windows 上可用。")
        win = _real_backend()
    params = subprocess.list2cmdline([str(a) for a in args])
    handle, err = win.shell_execute_elevated(str(exe), params, cwd)
    if not handle:
        if err == ERROR_CANCELLED:
            raise ElevationCancelled("已在 UAC 提示中取消授权。")
        raise ElevationError(f"ShellExecuteEx 失败（错误码 {err}）。")
    return ElevatedProcess(handle, win)
