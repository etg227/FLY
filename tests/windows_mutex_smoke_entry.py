from __future__ import annotations
import sys, time
from pathlib import Path
from launcher import _LauncherMutex

out=Path(sys.argv[1])
hold=float(sys.argv[2]) if len(sys.argv)>2 else 0.0
guard=_LauncherMutex()
try:
    acquired=guard.acquire()
except OSError as e:
    out.write_text(f"error:{e.errno}:{e}",encoding="utf-8")
    raise
if not acquired:
    out.write_text("existing",encoding="utf-8")
    raise SystemExit(0)
out.write_text("acquired",encoding="utf-8")
try:
    if hold>0:
        time.sleep(hold)
finally:
    guard.close()
