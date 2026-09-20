from __future__ import annotations
import ctypes, json, os, socket, subprocess, threading, time, urllib.error, urllib.request
from pathlib import Path
from .config import Paths, load_app_settings
from .core_installer import (
    VALID, MISSING, REPAIRABLE, INVALID, TRANSIENT,
    CoreInspection, core_archive_path, inspect_core,
)
from .mihomo_config import build_runtime_config, redact_runtime_config
from .core_log_stream import start_stream
from .elevated_launch import ElevationCancelled, ElevationError, launch_elevated

class CoreError(RuntimeError): pass

class DialFailureTracker:
    """Track real outbound failures reported by mihomo."""
    def __init__(self, group="FLY-JP", window=20.0, trigger=5, cooldown=15.0, clock=time.time):
        self.group, self.window, self.trigger, self.cooldown = group, window, trigger, cooldown
        self._clock = clock
        self._fails = []
        self._last_trigger = 0.0
        self._lock = threading.Lock()

    def feed(self, line):
        if f"dial {self.group}" not in line or "error:" not in line:
            return False
        with self._lock:
            now = self._clock()
            self._fails = [t for t in self._fails if now - t < self.window]
            self._fails.append(now)
            if len(self._fails) < self.trigger or now - self._last_trigger <= self.cooldown:
                return False
            self._last_trigger = now
            self._fails.clear()
            return True

    def reset(self):
        with self._lock:
            self._fails.clear()
            self._last_trigger = 0.0

def _create_kill_on_close_job():
    if os.name != "nt":
        return None
    try:
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in
                        ("ReadOperationCount","WriteOperationCount","OtherOperationCount",
                         "ReadTransferCount","WriteTransferCount","OtherTransferCount")]
        class BASIC_LIMITS(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]
        class EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMITS),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED_LIMITS()
        info.BasicLimitInformation.LimitFlags = 0x2000
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None

def _port_is_free(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()

class CoreManager:
    def __init__(self, paths: Paths, log, on_exit=None, on_line=None):
        self.paths, self.log = paths, log
        self.process = None
        self.reader_thread = None
        self._job = None
        self._stopping = False
        self._starting = False
        self.on_exit = on_exit
        self.on_line = on_line
        self._cleanup_lock = threading.Lock()
        self._verify_lock = threading.Lock()
        self._verify_cache = None
        self._stream_stop = None

    def is_installed(self):
        return self.paths.core_exe.exists() and core_archive_path(self.paths).exists()

    @staticmethod
    def _path_fingerprint(path):
        try:
            st = Path(path).stat()
            return (True, getattr(st, "st_dev", 0), getattr(st, "st_ino", 0),
                    st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        except FileNotFoundError:
            return (False,)
        except OSError:
            return None

    def _verify_fingerprint(self):
        return (self._path_fingerprint(self.paths.core_exe),
                self._path_fingerprint(core_archive_path(self.paths)))

    def invalidate_verify_cache(self):
        with self._verify_lock:
            self._verify_cache = None

    def verify_binary_status(self, force=False):
        """Single-flight integrity verification without executing mihomo.exe."""
        with self._verify_lock:
            fp = self._verify_fingerprint()
            if not force and self._verify_cache and self._verify_cache[0] == fp:
                return self._verify_cache[1]

            result = inspect_core(self.paths)
            # Transient read/sharing failures must never become sticky. Missing,
            # repairable and invalid states may be cached until files change.
            if result.state == TRANSIENT or fp is None or None in fp:
                self._verify_cache = None
            else:
                self._verify_cache = (self._verify_fingerprint(), result)
            return result

    def verify_binary(self, timeout=8):
        # timeout is kept for call-site compatibility; verification is pure I/O.
        return self.verify_binary_status().state == VALID

    def verified_state(self):
        """Return True/False for a cached verdict, None when verification is needed."""
        with self._verify_lock:
            fp = self._verify_fingerprint()
            cached = self._verify_cache
            if not cached or cached[0] != fp:
                return None
            result = cached[1]
            if result.state == VALID:
                return True
            if result.state in (MISSING, REPAIRABLE, INVALID):
                return False
            return None

    def verification_detail(self):
        with self._verify_lock:
            fp = self._verify_fingerprint()
            if self._verify_cache and self._verify_cache[0] == fp:
                return self._verify_cache[1]
        return None

    def is_running(self): return self.process is not None and self.process.poll() is None
    def _flags(self): return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def _assign_job(self, proc):
        if os.name != "nt":
            return
        try:
            if self._job is None:
                self._job = _create_kill_on_close_job()
            if self._job:
                if not ctypes.windll.kernel32.AssignProcessToJobObject(self._job, int(proc._handle)):
                    raise ctypes.WinError()
        except Exception as e:
            self.log(f"[CORE] Job 绑定失败（强杀 FLY 后可能留下内核进程）：{e}")

    def cleanup_orphans(self):
        if os.name != "nt" or not self.is_installed():
            return
        with self._cleanup_lock:
            if self.process is not None:
                return
            self._cleanup_orphans_locked()

    def _cleanup_orphans_locked(self):
        exe = str(self.paths.core_exe.resolve()).replace("'", "''")
        ps = ("$p = Get-Process -Name mihomo -ErrorAction SilentlyContinue | "
              f"Where-Object {{ $_.Path -eq '{exe}' }}; "
              "if ($p) { $p | Stop-Process -Force; ($p | Measure-Object).Count }")
        try:
            r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=6,
                               creationflags=self._flags())
            n = (r.stdout or "").strip()
            if n and n != "0":
                self.log(f"[CORE] 清理了 {n} 个上次残留的内核进程。")
                time.sleep(0.3)
        except Exception as e:
            self.log(f"[CORE] 残留进程检查已跳过：{e}")

    def _on_line_safe(self, line):
        if self.on_line:
            try: self.on_line(line)
            except Exception: pass

    def _watch_exit(self, proc):
        """提权进程没有管道可读——直接等句柄发信号。"""
        try:
            proc.wait(timeout=None)
        except Exception:
            pass
        self._notify_exit(proc)

    def _read_output(self, proc):
        try:
            if proc.stdout:
                for line in proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    self.log("[CORE] " + line)
                    if self.on_line:
                        try: self.on_line(line)
                        except Exception: pass
        except Exception as e:
            self.log(f"[CORE] log reader stopped: {e}")
        finally:
            self._notify_exit(proc)

    def _notify_exit(self, proc):
        stop = self._stream_stop
        if stop is not None:
            stop.set()
        if self._stopping or proc is not self.process:
            return
        try: code = proc.wait(timeout=2)
        except Exception: code = proc.poll()
        self.process = None
        if self._starting:
            self.log(f"[CORE] 内核在启动阶段退出（exit code: {code}）。")
            return
        self.log(f"[CORE] 内核意外退出（exit code: {code}）——加速已中断。")
        if self.on_exit:
            try: self.on_exit(code)
            except Exception as e: self.log(f"[CORE] exit handler failed: {e}")

    def validate(self, home, cfg):
        if not self.verify_binary():
            raise CoreError("Mihomo 内核文件无效或损坏，请删除 core\\mihomo.exe 后重新下载。")
        self.log("[CORE] 正在验证生成的配置...")
        try:
            result = subprocess.run(
                [str(self.paths.core_exe), "-t", "-d", str(home), "-f", str(cfg)],
                cwd=str(self.paths.app),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=self._flags(), timeout=15
            )
        except subprocess.TimeoutExpired as e:
            raise CoreError("Mihomo 配置验证超时。") from e
        output = (result.stdout or "").strip()
        if output:
            for line in output.splitlines():
                self.log("[CHECK] " + line)
        if result.returncode != 0:
            detail = "\n".join(output.splitlines()[-8:]) if output else "无详细输出"
            raise CoreError("Mihomo 拒绝生成的配置：\n" + detail)

    def _preflight_ports(self, settings):
        occupied = [p for p in (settings["mixed_port"], settings["controller_port"]) if not _port_is_free(p)]
        if occupied:
            raise CoreError(
                "FLY 所需本地端口已被其他程序占用：" + ", ".join(map(str, occupied)) +
                "。请关闭另一份 FLY/Clash/Mihomo，或修改端口后重试。")

    def _controller_ready(self, port, secret):
        url = f"http://127.0.0.1:{int(port)}/version"
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        req = urllib.request.Request(url, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=0.5) as resp:
                if resp.status != 200:
                    return False
                data = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
                return isinstance(data, dict) and bool(data)
        except Exception:
            return False

    def start(self, game_ids, elevate=False):
        """启动内核。elevate=True 时只提权 mihomo 本体（FLY 保持普通权限）。

        提权路径与普通路径的差异全部收在这里：
        - 启动方式：ShellExecuteEx(runas) 代替 Popen；
        - 日志通道：API /logs 流代替 stdout 管道；
        - 退出感知：句柄等待线程代替读管道到 EOF。
        其余（配置生成/校验、端口预检、API 就绪判定、stop 语义）完全一致。
        """
        self.stop()
        self.log("[CORE] 检查残留进程...")
        self.cleanup_orphans()
        settings = load_app_settings(self.paths)
        self._preflight_ports(settings)

        self.log("[CORE] 生成运行配置...")
        home, cfg, sensitive_urls = build_runtime_config(self.paths, game_ids, log=self.log)
        self.validate(home, cfg)

        with self._cleanup_lock:
            # Recheck immediately before Popen to narrow the TOCTOU window.
            self._preflight_ports(settings)
            self._stopping = False
            self._starting = True
            args = ["-d", str(home), "-f", str(cfg)]
            if elevate:
                self.log("[CORE] TUN 需要管理员权限：即将弹出 UAC，对象是已验证的 mihomo.exe 本体。")
                try:
                    self.process = launch_elevated(self.paths.core_exe, args,
                                                   cwd=str(self.paths.app))
                except ElevationCancelled as e:
                    self._starting = False
                    raise CoreError("已在 UAC 提示中取消授权；TUN 配置需要管理员权限才能启动内核。") from e
                except ElevationError as e:
                    self._starting = False
                    raise CoreError(
                        f"提权启动内核失败（{e}）。也可以手动以管理员身份运行 FLY 后重试。") from e
            else:
                self.process = subprocess.Popen(
                    [str(self.paths.core_exe)] + args,
                    cwd=str(self.paths.app),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=self._flags()
                )
        proc = self.process
        self._assign_job(proc)
        port = int(settings["controller_port"])
        secret = settings["api_secret"]
        if proc.stdout is not None:
            self.reader_thread = threading.Thread(target=self._read_output, args=(proc,), daemon=True)
            self.reader_thread.start()
        else:
            # 提权进程没有 stdout：日志走 API /logs 流，退出感知走句柄等待
            self._stream_stop, _ = start_stream(
                port, secret,
                on_line=self._on_line_safe, log=self.log,
                alive=lambda p=proc: p.poll() is None and not self._stopping)
            self.reader_thread = threading.Thread(target=self._watch_exit, args=(proc,), daemon=True)
            self.reader_thread.start()
        deadline = time.time() + 12
        while time.time() < deadline:
            if proc.poll() is not None or self.process is not proc:
                code = proc.poll()
                self._starting = False
                self.stop()
                raise CoreError(f"Mihomo 启动失败，exit code: {code}")
            if self._controller_ready(port, secret):
                self._starting = False
                redact_runtime_config(cfg, sensitive_urls)
                self.log("[CORE] Mihomo API 身份验证通过，内核就绪。")
                return
            time.sleep(0.2)

        self._starting = False
        code = proc.poll()
        self.stop()
        raise CoreError(
            f"Mihomo API 未在预期端口通过身份验证（controller={port}, exit={code}）。"
            "端口可能被占用或内核启动失败。")

    def stop(self):
        self._stopping = True
        self._starting = False
        stop = self._stream_stop
        if stop is not None:
            stop.set()
            self._stream_stop = None
        proc, self.process = self.process, None
        if not proc:
            return
        if proc.poll() is None:
            self.log("[CORE] Stopping Mihomo...")
            try:
                proc.terminate(); proc.wait(timeout=4)
            except Exception:
                try:
                    proc.kill(); proc.wait(timeout=2)
                except Exception:
                    self.log("[CORE] 无法结束提权内核进程，请在任务管理器中手动结束 mihomo.exe。")
        if hasattr(proc, "close"):
            try: proc.close()
            except Exception: pass
        self.log("[CORE] Stopped.")
