from __future__ import annotations
import os, socket, subprocess, threading, time
from .config import Paths, load_app_settings
from .mihomo_config import build_runtime_config

class CoreError(RuntimeError): pass

class CoreManager:
    def __init__(self, paths: Paths, log):
        self.paths, self.log = paths, log
        self.process = None
        self.reader_thread = None

    def is_installed(self): return self.paths.core_exe.exists()
    def is_running(self): return self.process is not None and self.process.poll() is None
    def _flags(self): return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

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
