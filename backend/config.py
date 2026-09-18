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
    def optional(self): return self.app / "optional"
    @property
    def node_source(self): return self.private / "node_source.json"
    @property
    def nodes_yaml(self): return self.private / "nodes.yaml"
    @property
    def app_settings(self): return self.private / "app_settings.json"
    @property
    def custom_profiles(self): return self.private / "custom_profiles.json"

DEFAULT_NODE_SOURCE = {"mode": "file", "subscription_url": ""}
DEFAULT_APP_SETTINGS = {
    "game_exes": {},
    "services_enabled": True,
    "enabled_optional": [],
    "browser": "auto",
    "mixed_port": 17890,
    "controller_port": 19090,
    "api_secret": "",
    "latency_test_url": "https://www.gstatic.com/generate_204",
    "latency_timeout_ms": 5000,
    "last_node": "",
    "sticky_max_delay_ms": 1000,
    "jp_keywords": ["Japan","JPN","JP","日本","Tokyo","Osaka","東京","东京","大阪","🇯🇵"]
}
DEFAULT_CUSTOM_PROFILES = {"profiles": []}

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default.copy() if isinstance(default, dict) else default

def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def app_version(paths: Paths) -> str:
    try:
        return (paths.app / "VERSION").read_text(encoding="utf-8-sig").strip() or "dev"
    except OSError:
        return "dev"

def ensure_private_files(paths: Paths):
    paths.private.mkdir(parents=True, exist_ok=True)
    if not paths.node_source.exists():
        save_json(paths.node_source, DEFAULT_NODE_SOURCE)
    if not paths.app_settings.exists():
        save_json(paths.app_settings, DEFAULT_APP_SETTINGS)
    if not paths.custom_profiles.exists():
        save_json(paths.custom_profiles, DEFAULT_CUSTOM_PROFILES)
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

def _normalize_profile(rule, source="builtin"):
    r = dict(rule or {})
    r["id"] = str(r.get("id","")).strip()
    r["name"] = str(r.get("name", r["id"])).strip()
    r["source"] = source
    r.setdefault("sort", 99)
    default_category = {"builtin": "Games", "optional": "Optional"}.get(source, "Custom")
    r.setdefault("category", default_category)
    r.setdefault("launch_mode", "browser")
    for key in ("domains","keywords","ip_cidrs","processes","ports","latency_test_urls"):
        value = r.get(key, [])
        if isinstance(value, str):
            value = [value]
        r[key] = [str(x).strip() for x in value if str(x).strip()]
    if r.get("latency_test_url") and not r["latency_test_urls"]:
        r["latency_test_urls"] = [str(r["latency_test_url"]).strip()]
    # full_browser is an explicit, clearly-labelled opt-in: only a profile that
    # declares it (and gets checked by the user) routes all browser traffic.
    r["full_browser"] = bool(r.get("full_browser"))
    # always_on profiles (common services) are merged into every start while
    # the master switch is enabled, instead of appearing as checkboxes.
    r["always_on"] = bool(r.get("always_on"))
    return r

def load_custom_profiles(paths):
    data = load_json(paths.custom_profiles, DEFAULT_CUSTOM_PROFILES)
    raw = data.get("profiles", []) if isinstance(data, dict) else []
    out = []
    for item in raw:
        if isinstance(item, dict):
            p = _normalize_profile(item, "custom")
            if p["id"] and p["name"]:
                out.append(p)
    return out

def save_custom_profiles(paths, profiles):
    cleaned = []
    for item in profiles:
        if not isinstance(item, dict):
            continue
        p = dict(item)
        p.pop("source", None)
        if str(p.get("id","")).strip() and str(p.get("name","")).strip():
            cleaned.append(p)
    save_json(paths.custom_profiles, {"profiles": cleaned})

def list_optional_profiles(paths):
    """The opt-in library: shipped with the repo but only loaded into the main
    list for users who enabled them (settings key enabled_optional)."""
    out = []
    if paths.optional.exists():
        for f in sorted(paths.optional.glob("*.json")):
            try:
                rule = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            p = _normalize_profile(rule, "optional")
            if p["id"] and p["name"]:
                out.append(p)
    return out

def list_routing_profiles(paths):
    profiles = []
    for f in sorted(paths.rules.glob("*.json")):
        try:
            rule = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        p = _normalize_profile(rule, "builtin")
        if p["id"] and p["name"]:
            profiles.append(p)
    enabled = {str(x) for x in load_app_settings(paths).get("enabled_optional", [])}
    profiles.extend(p for p in list_optional_profiles(paths) if p["id"] in enabled)
    profiles.extend(load_custom_profiles(paths))
    by_id = {p["id"]: p for p in profiles}
    profiles = list(by_id.values())
    profiles.sort(key=lambda r: (str(r.get("category","")), int(r.get("sort",99)), str(r.get("id"))))
    return profiles

def load_profile_rule(paths, profile_id):
    for r in list_routing_profiles(paths):
        if r["id"] == profile_id:
            return r
    raise FileNotFoundError(f"Routing profile not found: {profile_id}")

def load_game_rule(paths, game_id): return load_profile_rule(paths, game_id)
def list_game_rules(paths): return list_routing_profiles(paths)

def node_source_is_configured(paths):
    src = load_node_source(paths)
    mode = str(src.get("mode","file")).strip().lower()
    if mode == "subscription":
        url = str(src.get("subscription_url","")).strip()
        return (url.startswith("http://") or url.startswith("https://"),
                "订阅 URL" if url else "订阅 URL 为空")
    if mode != "file":
        return False, f"未知的节点来源：{mode}"
    if not paths.nodes_yaml.exists():
        return False, "未找到 nodes.yaml"
    text = paths.nodes_yaml.read_text(encoding="utf-8", errors="replace")
    active = [x for x in text.splitlines() if x.strip() and not x.lstrip().startswith("#")]
    ok = any(x.strip()=="proxies:" for x in active) and any(x.lstrip().startswith("- name:") for x in active)
    return (True, "本地 nodes.yaml") if ok else (False, "请把节点粘贴到 private\\nodes.yaml")

def copy_local_provider(paths, runtime_home):
    pdir = runtime_home / "provider"
    pdir.mkdir(parents=True, exist_ok=True)
    target = pdir / "nodes.yaml"
    shutil.copy2(paths.nodes_yaml, target)
    return target
