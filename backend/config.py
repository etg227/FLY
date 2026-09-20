from __future__ import annotations
import json, re, secrets, shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

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
    @property
    def custom_profiles(self): return self.private / "custom_profiles.json"

DEFAULT_NODE_SOURCE = {"mode": "file", "subscription_url": "", "subscription_urls": []}
DEFAULT_APP_SETTINGS = {
    "game_exes": {},
    "services_enabled": True,
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

def _fallback(default):
    return default.copy() if isinstance(default, dict) else default

def load_json(path: Path, default, expect=None):
    """expect: 期望的顶层类型。文件是合法 JSON 但类型不对时同样回落默认值——
    否则后续的 .get() 会在完全不相关的地方炸掉，甚至让程序起不来。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return _fallback(default)
    if expect is not None and not isinstance(data, expect):
        return _fallback(default)
    return data

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

def load_node_source(paths):
    data = load_json(paths.node_source, DEFAULT_NODE_SOURCE, expect=dict)
    urls = data.get("subscription_urls")
    if not isinstance(urls, list):
        urls = []
    urls = [str(u).strip() for u in urls if str(u).strip()]
    # v0.8.7 及更早只有单条 subscription_url —— 迁移进列表
    single = str(data.get("subscription_url", "")).strip()
    if single and single not in urls:
        urls.insert(0, single)
    data["subscription_urls"] = urls
    data["subscription_url"] = urls[0] if urls else ""
    return data

def load_app_settings(paths):
    data = load_json(paths.app_settings, DEFAULT_APP_SETTINGS, expect=dict)
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

# 这些字段会被原样拼进 mihomo 的 rules，任何逗号/井号/换行都可能注入新规则行，
# 进而绕过末尾的 MATCH,DIRECT 兜底。宁可丢弃可疑值，也不让它进配置。
RULE_FIELDS = ("domains", "keywords", "ip_cidrs", "processes", "ports")
_RULE_BAD = re.compile(r"[,#\r\n]")
_PORT_RE = re.compile(r"^\d{1,5}(-\d{1,5})?$")
_CIDR_RE = re.compile(r"^[0-9A-Fa-f:.]+/\d{1,3}$")
# 域名/关键字只挡 YAML 与规则分隔符，不限制字符集——IDN(中文域名)要能原样通过
_HOSTISH_BAD = re.compile(r"""[\s/'"\[\]{}]""")

def _clean_rule_values(key, values):
    """返回 (合法值, 被丢弃的值)。"""
    ok, bad = [], []
    for v in values:
        s = str(v).strip()
        if not s or _RULE_BAD.search(s):
            bad.append(s); continue
        if key == "ports" and not _PORT_RE.match(s):
            bad.append(s); continue
        if key == "ip_cidrs" and not _CIDR_RE.match(s):
            bad.append(s); continue
        if key in ("domains", "keywords") and _HOSTISH_BAD.search(s):
            bad.append(s); continue
        ok.append(s)
    return ok, bad

def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _normalize_profile(rule, source="builtin"):
    r = dict(rule or {})
    r["id"] = str(r.get("id","")).strip()
    r["name"] = str(r.get("name", r["id"])).strip()
    r["source"] = source
    r["sort"] = _safe_int(r.get("sort", 99), 99)
    r.setdefault("category", "Games" if source == "builtin" else "Custom")
    r.setdefault("launch_mode", "browser")
    for key in ("domains","keywords","ip_cidrs","processes","ports","latency_test_urls"):
        value = r.get(key, [])
        if isinstance(value, str):
            value = [value]
        r[key] = [str(x).strip() for x in value if str(x).strip()]
    dropped = []
    for key in RULE_FIELDS:
        r[key], bad = _clean_rule_values(key, r[key])
        dropped += [f"{key}={x!r}" for x in bad]
    r["invalid_values"] = dropped
    if r.get("latency_test_url") and not r["latency_test_urls"]:
        r["latency_test_urls"] = [str(r["latency_test_url"]).strip()]
    # full_browser is an explicit, clearly-labelled opt-in: only a profile that
    # declares it (and gets checked by the user) routes all browser traffic.
    r["full_browser"] = bool(r.get("full_browser"))
    # always_on profiles (common services) are merged into every start while
    # the master switch is enabled, instead of appearing as checkboxes.
    r["always_on"] = bool(r.get("always_on"))
    return r

def validate_profile(rule):
    """返回该配置中不符合分流规则语法、保存后会被忽略的值（不修改入参）。"""
    return list(_normalize_profile(rule).get("invalid_values", []))

def load_custom_profiles(paths):
    data = load_json(paths.custom_profiles, DEFAULT_CUSTOM_PROFILES, expect=dict)
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
        p.pop("invalid_values", None)
        if str(p.get("id","")).strip() and str(p.get("name","")).strip():
            cleaned.append(p)
    save_json(paths.custom_profiles, {"profiles": cleaned})

# Heuristic public-suffix handling for common two-level TLDs (co.jp, com.cn...).
_COMMON_SLD = {"co","com","net","org","gov","edu","ac","go","or","ne"}

def registrable_domain(host):
    host = str(host).strip().lower().strip(".")
    labels = [x for x in host.split(".") if x]
    if len(labels) >= 3 and labels[-2] in _COMMON_SLD and len(labels[-1]) <= 3:
        return ".".join(labels[-3:])
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host

def profile_from_url(raw_url, name=""):
    """Turn a pasted URL (or bare domain) into a browser routing profile."""
    raw = str(raw_url).strip()
    if not raw:
        raise ValueError("网址不能为空。")
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").strip().lower()
    if not host or "." not in host:
        raise ValueError("无法从输入中解析出域名。")
    domain = registrable_domain(host)
    pid = re.sub(r"[^a-z0-9]+", "-", domain).strip("-")
    return {
        "id": pid,
        "name": str(name).strip() or domain,
        "category": "Custom",
        "launch_mode": "browser",
        "url": f"https://{host}/",
        "domains": [domain],
        "keywords": [],
        "ip_cidrs": [],
        "processes": [],
        "ports": [],
        "latency_test_urls": [f"https://{host}/"],
    }

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
    profiles.extend(load_custom_profiles(paths))
    by_id = {p["id"]: p for p in profiles}
    profiles = list(by_id.values())
    profiles.sort(key=lambda r: (str(r.get("category","")), _safe_int(r.get("sort", 99), 99), str(r.get("id"))))
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
        urls = [u for u in src.get("subscription_urls", [])
                if u.startswith("http://") or u.startswith("https://")]
        return (bool(urls), f"{len(urls)} 条订阅" if urls else "订阅 URL 为空")
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
