"""流量用量统计：区分“走日本节点”和“直连”，按天累计保存在 runtime\\usage.json。

Mihomo 的 uploadTotal/downloadTotal 只给出经过内核的总量（含直连），
订阅扣的却只是走代理的那部分，所以这里按连接的 chains 是否经过 FLY-JP 分开记账。
"""
from __future__ import annotations
import threading, time
from pathlib import Path

from backend.config import load_json, save_json


def _nonneg_int(value):
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return n if n > 0 else 0


class ConnectionMeter:
    """把 /connections 的快照转换成两次轮询之间的增量。

    每条连接的 upload/download 是累计值，所以按连接 id 记住上次看到的总量，
    只把差值计入。两次轮询之间就已关闭的连接，最后不到一个轮询周期的字节会漏记，
    对统计来说可以接受。
    """

    def __init__(self, group="FLY-JP"):
        self.group = group
        self._seen = {}

    def reset(self):
        self._seen = {}

    def feed(self, data):
        conns = data.get("connections") if isinstance(data, dict) else None
        if not isinstance(conns, list):
            conns = []
        proxied = direct = 0
        current = {}
        for c in conns:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id") or "")
            if not cid:
                continue
            total = _nonneg_int(c.get("upload")) + _nonneg_int(c.get("download"))
            prev = self._seen.get(cid, 0)
            delta = total - prev if total >= prev else total
            current[cid] = total
            chains = c.get("chains")
            if isinstance(chains, list) and self.group in chains:
                proxied += delta
            else:
                direct += delta
        self._seen = current
        return proxied, direct


class UsageStore:
    """按本地日期累计的用量；只保存在本机 runtime 目录。"""

    KEEP_DAYS = 62

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._dirty = False
        raw = load_json(self.path, {"days": {}}, expect=dict)
        days = raw.get("days") if isinstance(raw, dict) else None
        self._days = {}
        if isinstance(days, dict):
            for key, value in days.items():
                if isinstance(key, str) and len(key) == 10 and isinstance(value, dict):
                    self._days[key] = {"proxied": _nonneg_int(value.get("proxied")),
                                       "direct": _nonneg_int(value.get("direct"))}

    @staticmethod
    def _date(now=None):
        return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))

    def add(self, proxied, direct, now=None):
        proxied, direct = _nonneg_int(proxied), _nonneg_int(direct)
        if not proxied and not direct:
            return
        key = self._date(now)
        with self._lock:
            day = self._days.setdefault(key, {"proxied": 0, "direct": 0})
            day["proxied"] += proxied
            day["direct"] += direct
            self._dirty = True

    def day(self, key):
        with self._lock:
            d = self._days.get(key, {})
            return d.get("proxied", 0), d.get("direct", 0)

    def today(self, now=None):
        return self.day(self._date(now))

    def month(self, now=None):
        prefix = self._date(now)[:7]
        with self._lock:
            items = [v for k, v in self._days.items() if k.startswith(prefix)]
        return sum(v["proxied"] for v in items), sum(v["direct"] for v in items)

    def last_days(self, n=7, now=None):
        """[(YYYY-MM-DD, proxied, direct)]，从 n-1 天前到今天。"""
        base = time.time() if now is None else now
        out = []
        for i in range(n - 1, -1, -1):
            key = self._date(base - i * 86400)
            p, d = self.day(key)
            out.append((key, p, d))
        return out

    def save(self, now=None):
        with self._lock:
            if not self._dirty:
                return
            cutoff = self._date((time.time() if now is None else now) - self.KEEP_DAYS * 86400)
            self._days = {k: v for k, v in self._days.items() if k >= cutoff}
            snapshot = {"days": {k: dict(v) for k, v in sorted(self._days.items())}}
            self._dirty = False
        try:
            save_json(self.path, snapshot)
        except OSError:
            with self._lock:
                self._dirty = True
