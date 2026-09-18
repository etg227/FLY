from __future__ import annotations
import ctypes, os, socket, subprocess, threading, time
from .config import Paths, load_app_settings
from .mihomo_config import build_runtime_config

class CoreError(RuntimeError): pass

def _create_kill_on_close_job():
    """A Windows Job Object with KILL_ON_JOB_CLOSE: when our process dies for
    ANY reason (crash, task-manager kill), the OS closes the handle and takes
    the core down with us — no orphaned mihomo keeps proxying."""
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
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None

class CoreManager:
    def __init__(self, paths: Paths, log):
        self.paths, self.log = paths, log
        self.process = None
        self.reader_thread = None
        self._job = None

    def is_installed(self): return self.paths.core_exe.exists()
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
            self.log(f"[CORE] Job 绑定失败（不影响正常使用，仅影响强杀后的自动清理）：{e}")

    def cleanup_orphans(self):
        """Kill leftover mihomo processes from a crashed/killed previous run —
        matched strictly by OUR core path so a user's own Clash is untouched."""
        if os.name != "nt" or not self.is_installed():
            return
        exe = str(self.paths.core_exe.resolve()).replace("'", "''")
        ps = ("$p = Get-Process -Name mihomo -ErrorAction SilentlyContinue | "
              f"Where-Object {{ $_.Path -eq '{exe}' }}; "
              "if ($p) { $p | Stop-Process -Force; ($p | Measure-Object).Count }")
        try:
            r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=20,
                               creationflags=self._flags())
            n = (r.stdout or "").strip()
            if n and n != "0":
                self.log(f"[CORE] 清理了 {n} 个上次残留的内核进程。")
                time.sleep(0.5)  # let the ports free up
        except Exception:
            pass

    def _read_output(self):
        if not self.process or not self.process.stdout: return
        try:
            for line in self.process.stdout:
                line = line.rstrip()
                if line: self.log("[CORE] " + line)
        except Exception as e:
            self.log(f"[CORE] log reader stopped: {e}")

    def validate(self, home, cfg):
        if not self.is_installed():
            raise CoreError("Mihomo core is not installed.")
        result = subprocess.run(
            [str(self.paths.core_exe), "-t", "-d", str(home), "-f", str(cfg)],
            cwd=str(self.paths.app),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=self._flags(), timeout=25
        )
        if result.stdout.strip():
            for line in result.stdout.splitlines():
                self.log("[CHECK] " + line)
        if result.returncode != 0:
            raise CoreError("Mihomo rejected the generated configuration.")

    def start(self, game_ids):
        self.stop()
        self.cleanup_orphans()
        home, cfg = build_runtime_config(self.paths, game_ids)
        self.validate(home, cfg)
        self.process = subprocess.Popen(
            [str(self.paths.core_exe), "-d", str(home), "-f", str(cfg)],
            cwd=str(self.paths.app),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=self._flags()
        )
        self._assign_job(self.process)
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()
        port = int(load_app_settings(self.paths).get("controller_port",19090))
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    self.log("[CORE] Mihomo is ready.")
                    return
            except OSError:
                time.sleep(0.2)
        code = self.process.poll()
        self.stop()
        raise CoreError(f"Mihomo did not become ready. Exit code: {code}")

    def stop(self):
        proc, self.process = self.process, None
        if not proc: return
        if proc.poll() is None:
            self.log("[CORE] Stopping Mihomo...")
            try:
                proc.terminate(); proc.wait(timeout=4)
            except Exception:
                try: proc.kill()
                except Exception: pass
        self.log("[CORE] Stopped.")
