"""通过内核 RESTful API 的 GET /logs 拉取日志流。

提权启动的内核没有 stdout 管道，而 [CORE] 日志流承担两件事：
用户可见的诊断输出，以及 DialFailureTracker 的快速故障切换信号。
mihomo 的 /logs 是分块 HTTP 流，每行一个 {"type","payload"} JSON
（hub/route/server.go getLogs，v1.19.31 已核对）。

沉默期靠读超时打断，随后重连——本机回环重连成本可忽略；
level=warning 与 dial 失败日志的级别一致。
"""
from __future__ import annotations
import json, threading, time, urllib.parse, urllib.request

class CoreLogStream:
    def __init__(self, port, secret, on_line, log, stop_event, alive,
                 level="warning", read_timeout=15.0, reconnect_delay=0.5):
        self.port = int(port)
        self.secret = str(secret or "")
        self.on_line = on_line          # 喂给 DialFailureTracker 的原始 payload
        self.log = log                  # 用户可见日志
        self.stop = stop_event
        self.alive = alive              # 内核进程是否还在
        self.level = level
        self.read_timeout = read_timeout
        self.reconnect_delay = reconnect_delay
        # Controller traffic is always loopback and must not inherit either
        # Windows' system proxy or HTTP(S)_PROXY environment variables.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._warned = False

    def _url(self):
        qs = urllib.parse.urlencode({"level": self.level})
        return f"http://127.0.0.1:{self.port}/logs?{qs}"

    def _open(self):
        headers = {}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        req = urllib.request.Request(self._url(), headers=headers)
        return self._opener.open(req, timeout=self.read_timeout)

    def _emit(self, raw):
        raw = raw.strip()
        if not raw:
            return
        try:
            entry = json.loads(raw)
            payload = str(entry.get("payload", ""))
            kind = str(entry.get("type", "log"))
        except (ValueError, AttributeError):
            payload, kind = raw, "log"
        if not payload:
            return
        self.log(f"[CORE] {kind}: {payload}")
        if self.on_line:
            try:
                self.on_line(payload)
            except Exception:
                pass          # 日志钩子永远不能拖垮流线程

    def run(self):
        """线程入口：内核活着且未被叫停就保持订阅。"""
        while not self.stop.is_set() and self.alive():
            try:
                resp = self._open()
            except Exception as e:
                if not self._warned:
                    self._warned = True
                    self.log(f"[CORE] 日志流暂不可用（{e}），将持续重试。")
                if self.stop.wait(max(self.reconnect_delay, 2.0)):
                    return
                continue
            self._warned = False
            try:
                with resp:
                    while not self.stop.is_set() and self.alive():
                        line = resp.readline(1024 * 1024)  # 单行封顶 1MB，防异常内核撑爆内存
                        if not line:
                            break          # 服务端关闭，重连
                        self._emit(line.decode("utf-8", errors="replace"))
            except Exception:
                pass          # 读超时 / 连接被重置 → 走重连
            if self.stop.wait(self.reconnect_delay):
                return

def start_stream(port, secret, on_line, log, alive, level="warning"):
    """便捷入口：返回 (stop_event, thread)，线程已启动。"""
    stop = threading.Event()
    stream = CoreLogStream(port, secret, on_line, log, stop, alive, level=level)
    t = threading.Thread(target=stream.run, daemon=True, name="core-log-stream")
    t.start()
    return stop, t
