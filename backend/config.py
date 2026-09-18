from __future__ import annotations
import json, secrets, shutil
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Paths:
    app: Path
    @property
    def core_exe(self): return self.app / "core" / "mihomo.exe"
    @property
    def private(self): return self.app / "private"
    @property
    def runtime(self): return self.app / "runtime"
    @property
    def rules(self): return self.app / "rules"
    @property
    def node_source(self): return self.private / "node_source.json"
    @property
    def nodes_yaml(self): return self.private / "nodes.yaml"
    @property
    def app_settings(self): return self.private / "app_settings.json"

DEFAULT_NODE_SOURCE = {"mode": "file", "subscription_url": ""}
DEFAULT_APP_SETTINGS = {
    "game_exes": {},
    "browser": "auto",
    "mixed_port": 17890,
    "controller_port": 19090,
    "api_secret": "",
    "latency_test_url": "https://www.gstatic.com/generate_204",
    "latency_timeout_ms": 5000,
    "jp_keywords": ["Japan","JPN","JP","日本","Tokyo","Osaka","東京","东京","大阪","🇯🇵"]
}

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default.copy() if isinstance(default, dict) else default

def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def ensure_private_files(paths: Paths):
    paths.private.mkdir(parents=True, exist_ok=True)
    if not paths.node_source.exists():
        save_json(paths.node_source, DEFAULT_NODE_SOURCE)
    if not paths.app_settings.exists():
        save_json(paths.app_settings, DEFAULT_APP_SETTINGS)
    if not paths.nodes_yaml.exists():
        paths.nodes_yaml.write_text(
            "proxies:\n  # Put your own Clash/Mihomo node(s) here.\n",
            encoding="utf-8"
        )

def load_node_source(paths): return load_json(paths.node_source, DEFAULT_NODE_SOURCE)

def load_app_settings(paths):
    data = load_json(paths.app_settings, DEFAULT_APP_SETTINGS)
    changed = False
    for k, v in DEFAULT_APP_SETTINGS.items():
        if k not in data:
            data[k] = v.copy() if isinstance(v, (dict, list)) else v
            changed = True
    # v0.3 -> v0.4: nikke_exe moved into the generic game_exes map.
    old_nikke = str(data.pop("nikke_exe", "")).strip()
    if old_nikke:
        data.setdefault("game_exes", {})
        if not data["game_exes"].get("nikke"):
            data["game_exes"]["nikke"] = old_nikke
        changed = True
    if not str(data.get("api_secret", "")).strip():
        data["api_secret"] = secrets.token_hex(16)
        changed = True
    if changed:
        save_json(paths.app_settings, data)
    return data

def load_game_rule(paths, game_id):
    return json.loads((paths.rules / f"{game_id}.json").read_text(encoding="utf-8"))

def list_game_rules(paths):
    rules = []
    for f in sorted(paths.rules.glob("*.json")):
        try:
            rule = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rule.get("id") and rule.get("name"):
            rules.append(rule)
    rules.sort(key=lambda r: (int(r.get("sort", 99)), str(r.get("id"))))
    return rules

def node_source_is_configured(paths):
    src = load_node_source(paths)
    mode = str(src.get("mode","file")).strip().lower()
    if mode == "subscription":
        url = str(src.get("subscription_url","")).strip()
        return (url.startswith("http://") or url.startswith("https://"),
                "Subscription URL" if url else "Subscription URL is empty")
    if mode != "file":
        return False, f"Unknown node mode: {mode}"
    if not paths.nodes_yaml.exists():
        return False, "nodes.yaml not found"
    text = paths.nodes_yaml.read_text(encoding="utf-8", errors="replace")
    active = [x for x in text.splitlines() if x.strip() and not x.lstrip().startswith("#")]
    ok = any(x.strip()=="proxies:" for x in active) and any(x.lstrip().startswith("- name:") for x in active)
    return (True, "Local nodes.yaml") if ok else (False, "Paste a node into private\\nodes.yaml")

def copy_local_provider(paths, runtime_home):
    pdir = runtime_home / "provider"
    pdir.mkdir(parents=True, exist_ok=True)
    target = pdir / "nodes.yaml"
    shutil.copy2(paths.nodes_yaml, target)
    return target
