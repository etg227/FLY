from __future__ import annotations
import ctypes, json, os, socket, subprocess, threading, time, urllib.error, urllib.request
from .config import Paths, load_app_settings
from .core_installer import CORE_VERSION
from .mihomo_config import build_runtime_config, redact_runtime_config

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

    def is_installed(self):
        try:
            return self.paths.core_exe.exists() and self.paths.core_exe.stat().st_size > 1024 * 1024
        except OSError:
            return False

    def verify_binary(self, timeout=8):
        if not self.is_installed():
            return False
        try:
            with self.paths.core_exe.open("rb") as f:
                if f.read(2) != b"MZ":
                    return False
            r = subprocess.run([str(self.paths.core_exe), "-v"],
                               cwd=str(self.paths.app), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout,
                               creationflags=self._flags())
            output = (r.stdout or "").lower()
            expected = CORE_VERSION.lstrip("v").lower()
            return r.returncode == 0 and "mihomo" in output and expected in output
        except Exception:
            return False

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

    def start(self, game_ids):
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
            self.process = subprocess.Popen(
                [str(self.paths.core_exe), "-d", str(home), "-f", str(cfg)],
                cwd=str(self.paths.app),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=self._flags()
            )
        proc = self.process
        self._assign_job(proc)
        self.reader_thread = threading.Thread(target=self._read_output, args=(proc,), daemon=True)
        self.reader_thread.start()

        port = int(settings["controller_port"])
        secret = settings["api_secret"]
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
        proc, self.process = self.process, None
        if not proc:
            return
        if proc.poll() is None:
            self.log("[CORE] Stopping Mihomo...")
            try:
                proc.terminate(); proc.wait(timeout=4)
            except Exception:
                try: proc.kill()
                except Exception: pass
        self.log("[CORE] Stopped.")
