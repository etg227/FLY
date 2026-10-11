"""Windows 系统托盘图标（通知区域），只用 ctypes 调 Win32 API，不引入第三方包。

托盘运行在独立线程里，有自己的隐藏窗口和消息循环。所有回调都在托盘线程里
被调用——调用方负责把它们转回 Tk 主线程（FlyApp 用 _ui 投递）。
非 Windows 平台上 available() 为 False，调用方应退回普通窗口行为。
"""
from __future__ import annotations
import sys, threading

WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF = {"info": 0x1, "ok": 0x1, "warn": 0x2, "err": 0x3}
MF_STRING, MF_GRAYED, MF_SEPARATOR = 0x0, 0x1, 0x800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
SM_CXSMICON, SM_CYSMICON = 49, 50
ERROR_CLASS_ALREADY_EXISTS = 1410
# 隐藏窗口的标题固定，第二个 FLY 进程据此找到正在运行的实例并请它显示主窗口
WINDOW_TITLE = "FLY.TrayWindow"
SHOW_MESSAGE = "FLY.ShowMainWindow"


def available():
    return sys.platform == "win32"


_api = None


def _load_api():
    """延迟加载：ctypes.wintypes 和 windll 只在 Windows 上可用。"""
    global _api
    if _api is not None:
        return _api
    import ctypes
    from ctypes import wintypes as w

    LRESULT = w.LPARAM
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
                    ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                    ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD), ("Data4", w.BYTE * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                    ("uCallbackMessage", w.UINT), ("hIcon", w.HICON), ("szTip", w.WCHAR * 128),
                    ("dwState", w.DWORD), ("dwStateMask", w.DWORD), ("szInfo", w.WCHAR * 256),
                    ("uTimeoutOrVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64),
                    ("dwInfoFlags", w.DWORD), ("guidItem", GUID), ("hBalloonIcon", w.HICON)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def sig(fn, res, *args):
        fn.restype, fn.argtypes = res, list(args)
        return fn

    api = type("Api", (), {})()
    api.ctypes, api.w, api.WNDPROC = ctypes, w, WNDPROC
    api.WNDCLASSEXW, api.NOTIFYICONDATAW, api.MSG, api.POINT = WNDCLASSEXW, NOTIFYICONDATAW, w.MSG, w.POINT
    api.GetModuleHandleW = sig(kernel32.GetModuleHandleW, w.HMODULE, w.LPCWSTR)
    api.RegisterClassExW = sig(user32.RegisterClassExW, w.ATOM, ctypes.POINTER(WNDCLASSEXW))
    api.CreateWindowExW = sig(user32.CreateWindowExW, w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
    api.DestroyWindow = sig(user32.DestroyWindow, w.BOOL, w.HWND)
    api.DefWindowProcW = sig(user32.DefWindowProcW, LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    api.GetMessageW = sig(user32.GetMessageW, w.BOOL, ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
    api.TranslateMessage = sig(user32.TranslateMessage, w.BOOL, ctypes.POINTER(w.MSG))
    api.DispatchMessageW = sig(user32.DispatchMessageW, LRESULT, ctypes.POINTER(w.MSG))
    api.PostMessageW = sig(user32.PostMessageW, w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    api.PostQuitMessage = sig(user32.PostQuitMessage, None, ctypes.c_int)
    api.RegisterWindowMessageW = sig(user32.RegisterWindowMessageW, w.UINT, w.LPCWSTR)
    api.LoadImageW = sig(user32.LoadImageW, w.HANDLE, w.HINSTANCE, w.LPCWSTR, w.UINT,
                         ctypes.c_int, ctypes.c_int, w.UINT)
    api.DestroyIcon = sig(user32.DestroyIcon, w.BOOL, w.HICON)
    api.GetSystemMetrics = sig(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
    api.CreatePopupMenu = sig(user32.CreatePopupMenu, w.HMENU)
    api.AppendMenuW = sig(user32.AppendMenuW, w.BOOL, w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR)
    api.SetMenuDefaultItem = sig(user32.SetMenuDefaultItem, w.BOOL, w.HMENU, w.UINT, w.UINT)
    api.TrackPopupMenu = sig(user32.TrackPopupMenu, w.BOOL, w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, w.HWND, w.LPVOID)
    api.DestroyMenu = sig(user32.DestroyMenu, w.BOOL, w.HMENU)
    api.GetCursorPos = sig(user32.GetCursorPos, w.BOOL, ctypes.POINTER(w.POINT))
    api.SetForegroundWindow = sig(user32.SetForegroundWindow, w.BOOL, w.HWND)
    api.FindWindowW = sig(user32.FindWindowW, w.HWND, w.LPCWSTR, w.LPCWSTR)
    api.Shell_NotifyIconW = sig(shell32.Shell_NotifyIconW, w.BOOL, w.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
    _api = api
    return api


class TrayIcon:
    """通知区域图标。

    on_activate(): 左键单击/双击图标。
    menu(): 返回 [(文字, 回调, 是否可用) 或 None(分隔线)]，第一项为默认（加粗）项。
    """

    CLASS_NAME = "FLYTrayWindow"

    _taskbar_created = _show_msg = -1

    def __init__(self, icon_path, tooltip, on_activate, menu, log=None):
        self.icon_path = str(icon_path)
        self.tooltip = tooltip
        self.on_activate, self.menu = on_activate, menu
        self.log = log or (lambda m: None)
        self.hwnd = None
        self.ready = threading.Event()
        self.error = None
        self.added = False
        self._hicon = None
        self._thread = None
        self._lock = threading.Lock()

    # ---- 线程 / 窗口 ----
    def start(self, timeout=5):
        self._thread = threading.Thread(target=self._run, name="fly-tray", daemon=True)
        self._thread.start()
        self.ready.wait(timeout)
        if self.error:
            raise RuntimeError(self.error)
        return self.hwnd is not None

    def _run(self):
        try:
            api = _load_api()
            hinst = api.GetModuleHandleW(None)
            self._wndproc = api.WNDPROC(self._proc)   # 必须保持引用，否则回调被回收会崩溃
            wc = api.WNDCLASSEXW()
            wc.cbSize = api.ctypes.sizeof(api.WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = self.CLASS_NAME + str(id(self))
            if not api.RegisterClassExW(api.ctypes.byref(wc)):
                err = api.ctypes.get_last_error()
                if err != ERROR_CLASS_ALREADY_EXISTS:
                    raise OSError(err, "RegisterClassExW failed")
            self._taskbar_created = api.RegisterWindowMessageW("TaskbarCreated")
            self._show_msg = api.RegisterWindowMessageW(SHOW_MESSAGE)
            self.hwnd = api.CreateWindowExW(0, wc.lpszClassName, WINDOW_TITLE, 0, 0, 0, 0, 0,
                                            None, None, hinst, None)
            if not self.hwnd:
                raise OSError(api.ctypes.get_last_error(), "CreateWindowExW failed")
            self._hicon = self._load_icon(self.icon_path)
            self._notify(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.ready.set()
            return
        self.ready.set()
        msg = api.MSG()
        while api.GetMessageW(api.ctypes.byref(msg), None, 0, 0) > 0:
            api.TranslateMessage(api.ctypes.byref(msg))
            api.DispatchMessageW(api.ctypes.byref(msg))

    def _proc(self, hwnd, msg, wparam, lparam):
        api = _api
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._safe(self.on_activate)
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                return 0
            if msg == self._show_msg:
                self._safe(self.on_activate)
                return 0
            if msg == self._taskbar_created:
                # 资源管理器重启后托盘图标会全部消失，需要重新添加
                self._notify(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP)
                return 0
            if msg == WM_CLOSE:
                api.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._notify(NIM_DELETE, 0)
                with self._lock:
                    self.hwnd = None
                if self._hicon:
                    api.DestroyIcon(self._hicon); self._hicon = None
                api.PostQuitMessage(0)
                return 0
        except Exception as e:
            self.log(f"[TRAY] {type(e).__name__}: {e}")
        return api.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _safe(self, fn, *args):
        try:
            fn(*args)
        except Exception as e:
            self.log(f"[TRAY] 回调出错：{type(e).__name__}: {e}")

    def _load_icon(self, path):
        api = _load_api()
        cx, cy = api.GetSystemMetrics(SM_CXSMICON) or 16, api.GetSystemMetrics(SM_CYSMICON) or 16
        return api.LoadImageW(None, path, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)

    def _notify(self, action, flags, info=None):
        api = _load_api()
        with self._lock:
            if not self.hwnd:
                return False
            nid = api.NOTIFYICONDATAW()
            nid.cbSize = api.ctypes.sizeof(api.NOTIFYICONDATAW)
            nid.hWnd, nid.uID, nid.uFlags = self.hwnd, 1, flags
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = self._hicon
            nid.szTip = (self.tooltip or "FLY")[:127]
            if info:
                title, text, kind = info
                nid.uFlags |= NIF_INFO
                nid.szInfoTitle = (title or "FLY")[:63]
                nid.szInfo = (text or " ")[:255]
                nid.dwInfoFlags = NIIF.get(kind, 0x1)
            ok = bool(api.Shell_NotifyIconW(action, api.ctypes.byref(nid)))
            if action == NIM_ADD:
                self.added = ok
            return ok

    def _show_menu(self):
        api = _api
        items = list(self.menu() or [])
        hmenu = api.CreatePopupMenu()
        actions = {}
        try:
            first = None
            for i, item in enumerate(items, 1):
                if item is None:
                    api.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                    continue
                label, callback, enabled = item
                api.AppendMenuW(hmenu, MF_STRING | (0 if enabled else MF_GRAYED), i, label)
                actions[i] = callback
                first = first or i
            if first:
                api.SetMenuDefaultItem(hmenu, first, 0)
            pt = api.POINT()
            api.GetCursorPos(api.ctypes.byref(pt))
            # 不先设前台窗口的话，点菜单外面菜单不会消失
            api.SetForegroundWindow(self.hwnd)
            cmd = api.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
                                     pt.x, pt.y, 0, self.hwnd, None)
            api.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            api.DestroyMenu(hmenu)
        if cmd in actions:
            self._safe(actions[cmd])

    # ---- 线程安全的外部接口 ----
    def set_tooltip(self, text):
        self.tooltip = text
        self._notify(NIM_MODIFY, NIF_TIP)

    def set_icon(self, path):
        path = str(path)
        if path == self.icon_path:
            return
        self.icon_path = path
        new = self._load_icon(path)
        if not new:
            return
        old, self._hicon = self._hicon, new
        self._notify(NIM_MODIFY, NIF_ICON)
        if old:
            _load_api().DestroyIcon(old)

    def balloon(self, title, text, kind="info"):
        self._notify(NIM_MODIFY, 0, info=(title, text, kind))

    def stop(self, timeout=3):
        hwnd = self.hwnd
        if hwnd:
            _load_api().PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)


def signal_existing_instance():
    """请已在运行的 FLY 显示主窗口；找到并通知成功返回 True。"""
    if not available():
        return False
    try:
        api = _load_api()
        hwnd = api.FindWindowW(None, WINDOW_TITLE)
        if not hwnd:
            return False
        return bool(api.PostMessageW(hwnd, api.RegisterWindowMessageW(SHOW_MESSAGE), 0, 0))
    except Exception:
        return False
