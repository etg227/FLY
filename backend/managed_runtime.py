"""Optional tool-hub lifecycle protocol; standalone launches stay unchanged."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from backend.config import Paths, save_json


def resolve_paths(app_dir: Path, data_dir: str | None = None) -> Paths:
    """Explicit argument wins over the environment; never change the source root."""
    value = data_dir if data_dir is not None else os.environ.get("FLY_DATA_DIR")
    if value is not None and not value.strip():
        raise ValueError("FLY 数据目录不能为空。")
    return Paths(app_dir.resolve(), Path(value).expanduser().resolve() if value else None)


class ManagedRuntime:
    """Commands are scoped to one launch, so an old stop request cannot kill a new one.

    The owner polls on the Tk main thread and calls its existing on_close method;
    the manager must wait for process exit rather than treating a request as an ack.
    """

    def __init__(self, paths: Paths, session: str):
        if not re.fullmatch(r"[a-f0-9]{32}", session):
            raise ValueError("工具中心会话标识必须是32位小写十六进制字符串。")
        self.session = session
        self.directory = paths.runtime / "hub" / session
        self.ready_path = self.directory / "ready.json"
        self.command_path = self.directory / "command.json"

    def mark_ready(self):
        save_json(self.ready_path, {
            "schema": 1, "session": self.session, "pid": os.getpid(), "state": "ready",
        })

    def poll(self) -> str | None:
        try:
            command = json.loads(self.command_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None
        # Only the manager writes this file, with at most one pending command.
        try:
            self.command_path.unlink()
        except OSError:
            return None
        if not isinstance(command, dict):
            return None
        if command.get("schema") != 1 or command.get("session") != self.session:
            return None
        action = command.get("action")
        return action if action in ("show", "stop") else None

    def close(self):
        self.ready_path.unlink(missing_ok=True)
        self.command_path.unlink(missing_ok=True)
        try:
            self.directory.rmdir()
        except OSError:
            pass
