from __future__ import annotations
import copy, hashlib, ipaddress, json, os, re, secrets, shutil, tempfile, threading, time
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

_JSON_LOCKS = {}
_JSON_LOCKS_GUARD = threading.Lock()
_WARNINGS = []
_WARNINGS_LOCK = threading.Lock()

def _fallback(default):
    return copy.deepcopy(default)

def _warn(message):
    with _WARNINGS_LOCK:
        _WARNINGS.append(str(message))

def drain_config_warnings():
    with _WARNINGS_LOCK:
        out = list(_WARNINGS)
        _WARNINGS.clear()
    return out

def _lock_for(path: Path):
    key = str(Path(path).resolve())
    with _JSON_LOCKS_GUARD:
        return _JSON_LOCKS.setdefault(key, threading.RLock())

def load_json(path: Path, default, expect=None):
    """Read JSON defensively.

    Broken JSON and valid JSON with the wrong top-level type both fall back,
    but never silently: a warning is queued so the GUI can tell the user which
    file was ignored instead of making settings appear to vanish.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text)
    except FileNotFoundError:
        return _fallback(default)
    except Exception as e:
        _warn(f"{path.name} 无法解析，已使用安全默认值：{e}")
        return _fallback(default)
    if expect is not None and not isinstance(data, expect):
        _warn(f"{path.name} 顶层类型错误（应为 {getattr(expect,'__name__',expect)}），已使用安全默认值。")
        return _fallback(default)
    return data

def save_json(path: Path, data):
    """Atomic + serialized JSON write; readers never observe half-written data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    lock = _lock_for(path)
    with lock:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try: Path(tmp).unlink(missing_ok=True)
            except OSError: pass
            raise

def app_version(paths: Paths) -> str:
    try:
        return (paths.app / "VERSION").read_text(encoding="utf-8-sig").strip() or "dev"
    except OSError:
        return "dev"

def update_app_settings(paths, updates=None, mutator=None):
    """Serialize the full app-settings read/modify/write transaction."""
    with _lock_for(paths.app_settings):
        data = load_app_settings(paths)
        if mutator is not None:
            mutator(data)
        if updates:
            data.update(dict(updates))
        save_json(paths.app_settings, data)
        return data

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

def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []

def _safe_int(value, default, minimum=None, maximum=None):
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        n = int(default)
    if minimum is not None: n = max(int(minimum), n)
    if maximum is not None: n = min(int(maximum), n)
    return n

def _safe_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        s=value.strip().casefold()
        if s in ("1","true","yes","on"): return True
        if s in ("0","false","no","off",""): return False
    return bool(default)

def _valid_http_url(value):
    try:
        p = urlparse(str(value).strip())
        return p.scheme in ("http","https") and bool(p.hostname)
    except Exception:
        return False

def _normalize_jp_keywords(value):
    raw = _as_list(value)
    out, seen = [], set()
    for item in raw:
        s = str(item).strip()
        if len(s) < 2:
            continue
        key = s.casefold()
        if key not in seen:
            seen.add(key); out.append(s)
    return out or list(DEFAULT_APP_SETTINGS["jp_keywords"])

def load_node_source(paths):
    data = load_json(paths.node_source, DEFAULT_NODE_SOURCE, expect=dict)
    mode = str(data.get("mode", "file")).strip().lower()
    if mode not in ("file", "subscription"):
        _warn(f"node_source.json 的 mode={mode!r} 无效，已回落到 file。")
        mode = "file"
    urls, seen = [], set()
    for u in _as_list(data.get("subscription_urls")):
        s = str(u).strip()
        if _valid_http_url(s) and s not in seen:
            seen.add(s); urls.append(s)
    single = str(data.get("subscription_url", "") or "").strip()
    if _valid_http_url(single) and single not in seen:
        urls.insert(0, single)
    return {
        "mode": mode,
        "subscription_urls": urls,
        "subscription_url": urls[0] if urls else "",
    }

def load_app_settings(paths):
    raw = load_json(paths.app_settings, DEFAULT_APP_SETTINGS, expect=dict)
    data = _fallback(DEFAULT_APP_SETTINGS)
    changed = False

    game_exes = raw.get("game_exes", {})
    if isinstance(game_exes, dict):
        data["game_exes"] = {
            str(k): str(v).strip()
            for k, v in game_exes.items()
            if isinstance(v, str)
        }
    elif game_exes not in (None, {}):
        _warn("app_settings.json 的 game_exes 类型错误，已忽略。")
        changed = True

    data["services_enabled"] = _safe_bool(raw.get("services_enabled", True), True)
    data["mixed_port"] = _safe_int(raw.get("mixed_port", 17890), 17890, 1024, 65535)
    data["controller_port"] = _safe_int(raw.get("controller_port", 19090), 19090, 1024, 65535)
    if data["mixed_port"] == data["controller_port"]:
        data["controller_port"] = 19090 if data["mixed_port"] != 19090 else 19091
        _warn("mixed_port 与 controller_port 不能相同，controller_port 已自动调整。")
        changed = True

    data["api_secret"] = str(raw.get("api_secret", "") or "").strip()
    url = str(raw.get("latency_test_url", DEFAULT_APP_SETTINGS["latency_test_url"]) or "").strip()
    data["latency_test_url"] = url if _valid_http_url(url) else DEFAULT_APP_SETTINGS["latency_test_url"]
    data["latency_timeout_ms"] = _safe_int(raw.get("latency_timeout_ms", 5000), 5000, 1000, 30000)
    data["last_node"] = str(raw.get("last_node", "") or "").strip()
    data["sticky_max_delay_ms"] = _safe_int(raw.get("sticky_max_delay_ms", 1000), 1000, 100, 60000)
    data["jp_keywords"] = _normalize_jp_keywords(raw.get("jp_keywords"))

    old_nikke = str(raw.get("nikke_exe", "") or "").strip()
    if old_nikke and not data["game_exes"].get("nikke"):
        data["game_exes"]["nikke"] = old_nikke
        changed = True

    if not data["api_secret"]:
        data["api_secret"] = secrets.token_hex(16)
        changed = True

    # Persist normalization/migration so a malformed setting cannot keep
    # re-triggering problems on every launch.
    normalized_keys = set(DEFAULT_APP_SETTINGS)
    if (changed or set(raw.keys()) != normalized_keys or
            any(raw.get(k) != data.get(k) for k in normalized_keys)):
        save_json(paths.app_settings, data)
    return data

RULE_FIELDS = ("domains", "keywords", "ip_cidrs", "processes", "ports")
_RULE_BAD = re.compile(r"[,#\r\n]")
_PORT_RE = re.compile(r"^\d{1,5}(-\d{1,5})?$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.I)
_HOSTISH_BAD = re.compile(r"""[\s/'"\[\]{}]""")

def _idna_host(host):
    host = str(host or "").strip().lower().strip(".")
    if not host:
        return ""
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return ""

def _valid_port(value):
    s = str(value).strip()
    if not _PORT_RE.fullmatch(s):
        return False
    parts = [int(x) for x in s.split("-", 1)]
    if any(x < 1 or x > 65535 for x in parts):
        return False
    return len(parts) == 1 or parts[0] <= parts[1]

def _valid_cidr(value):
    try:
        ipaddress.ip_network(str(value).strip(), strict=False)
        return True
    except ValueError:
        return False

def _clean_rule_values(key, values):
    """Return (valid, rejected) after syntax and semantic validation."""
    ok, bad, seen = [], [], set()
    for v in _as_list(values):
        s = str(v).strip()
        if not s or _RULE_BAD.search(s):
            bad.append(s); continue
        if key == "ports" and not _valid_port(s):
            bad.append(s); continue
        if key == "ip_cidrs" and not _valid_cidr(s):
            bad.append(s); continue
        if key == "domains":
            if _HOSTISH_BAD.search(s):
                bad.append(s); continue
            s = _idna_host(s.lstrip("*."))
            if not s or "." not in s:
                bad.append(str(v).strip()); continue
        if key == "keywords" and (_HOSTISH_BAD.search(s) or len(s) > 128):
            bad.append(s); continue
        if key == "processes":
            if len(s) > 260 or "/" in s or "\\" in s:
                bad.append(s); continue
        k = s.casefold()
        if k not in seen:
            seen.add(k); ok.append(s)
    return ok, bad

def _normalize_profile(rule, source="builtin"):
    if not isinstance(rule, dict):
        rule = {}
    r = dict(rule)
    pid = str(r.get("id", "") or "").strip()
    r["id"] = pid if _ID_RE.fullmatch(pid) else ""
    r["name"] = re.sub(r"[\r\n\t]+", " ", str(r.get("name", r["id"]) or "")).strip()[:120]
    r["source"] = source
    r["sort"] = _safe_int(r.get("sort", 99), 99, -100000, 100000)
    r["category"] = re.sub(
        r"[\r\n\t]+", " ",
        str(r.get("category") or ("Games" if source == "builtin" else "Custom"))
    ).strip()[:80]
    mode = str(r.get("launch_mode", "browser") or "browser").strip().lower()
    r["launch_mode"] = mode if mode in ("browser", "tun") else "browser"

    dropped = []
    for key in RULE_FIELDS:
        cleaned, bad = _clean_rule_values(key, r.get(key, []))
        r[key] = cleaned
        dropped += [f"{key}={x!r}" for x in bad]

    latency = []
    for x in _as_list(r.get("latency_test_urls", [])):
        s = str(x).strip()
        if _valid_http_url(s):
            latency.append(s)
        elif s:
            dropped.append(f"latency_test_urls={s!r}")
    legacy = str(r.get("latency_test_url", "") or "").strip()
    if not latency and _valid_http_url(legacy):
        latency = [legacy]
    r["latency_test_urls"] = list(dict.fromkeys(latency))

    r["full_browser"] = _safe_bool(r.get("full_browser"), False)
    r["always_on"] = _safe_bool(r.get("always_on"), False)
    r["invalid_values"] = dropped
    return r

def validate_profile(rule):
    if not isinstance(rule, dict):
        return ["profile=必须是 JSON 对象"]
    problems = list(_normalize_profile(rule).get("invalid_values", []))
    pid = str(rule.get("id", "") or "").strip()
    if not _ID_RE.fullmatch(pid):
        problems.append(f"id={pid!r}")
    return problems

def profile_has_effect(profile):
    p = _normalize_profile(profile, str(profile.get("source","custom")) if isinstance(profile,dict) else "custom")
    return bool(p.get("full_browser") or any(p.get(k) for k in RULE_FIELDS))

def _builtin_profiles(paths):
    out = []
    for f in sorted(paths.rules.glob("*.json")):
        try:
            rule = json.loads(f.read_text(encoding="utf-8-sig"))
        except Exception as e:
            _warn(f"内置规则 {f.name} 无法解析，已跳过：{e}")
            continue
        p = _normalize_profile(rule, "builtin")
        if p["id"] and p["name"]:
            out.append(p)
    return out

def load_custom_profiles(paths):
    data = load_json(paths.custom_profiles, DEFAULT_CUSTOM_PROFILES, expect=dict)
    raw = data.get("profiles", [])
    if not isinstance(raw, list):
        _warn("custom_profiles.json 的 profiles 必须是数组，已忽略。")
        return []
    out = []
    for idx, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            _warn(f"自定义配置第 {idx} 项不是对象，已忽略。")
            continue
        p = _normalize_profile(item, "custom")
        if p["id"] and p["name"]:
            out.append(p)
        else:
            _warn(f"自定义配置第 {idx} 项的 id/name 无效，已忽略。")
    return out

def save_custom_profiles(paths, profiles):
    cleaned = []
    for item in _as_list(profiles):
        if not isinstance(item, dict):
            continue
        p = dict(item)
        p.pop("source", None)
        p.pop("invalid_values", None)
        pid = str(p.get("id", "") or "").strip()
        name = str(p.get("name", "") or "").strip()
        if _ID_RE.fullmatch(pid) and name:
            cleaned.append(p)
    save_json(paths.custom_profiles, {"profiles": cleaned})

_SHARED_TENANT_SUFFIXES = {
    "github.io","pages.dev","blogspot.com","appspot.com","workers.dev",
    "vercel.app","netlify.app","web.app","firebaseapp.com","herokuapp.com",
    "azurewebsites.net","onrender.com","railway.app","surge.sh",
    "amazonaws.com","cloudfront.net","notion.site","githubusercontent.com",
    "storage.googleapis.com","azureedge.net",
}

def registrable_domain(host):
    """Choose a conservative domain for a one-URL custom profile.

    We only widen the extremely common `www.example.tld` form. Arbitrary
    subdomains stay exact, which prevents api.foo.cloudfront.net or
    bucket.amazonaws.com from accidentally routing an entire shared platform.
    """
    host = _idna_host(host)
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    for suffix in _SHARED_TENANT_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return host
    if host.startswith("www.") and host.count(".") >= 2:
        return host[4:]
    return host

def profile_from_url(raw_url, name=""):
    raw = str(raw_url).strip()
    if not raw:
        raise ValueError("网址不能为空。")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("只支持 http:// 或 https:// 网站。")
    host = _idna_host(parsed.hostname or "")
    if not host or "." not in host:
        raise ValueError("无法从输入中解析出有效域名。")
    domain = registrable_domain(host)
    slug = re.sub(r"[^a-z0-9]+", "-", domain).strip("-")
    digest = hashlib.sha256(domain.encode("utf-8")).hexdigest()[:12]
    if not slug:
        pid = "site-" + digest
    elif len(slug) > 48:
        pid = slug[:35].rstrip("-") + "-" + digest
    else:
        pid = slug
    display = re.sub(r"[\r\n\t]+", " ", str(name)).strip()[:120]
    return {
        "id": pid,
        "name": display or domain,
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
    builtins = _builtin_profiles(paths)
    by_id = {p["id"]: p for p in builtins}
    for p in load_custom_profiles(paths):
        if p["id"] in by_id:
            _warn(f"自定义配置 id={p['id']!r} 与内置配置冲突，已忽略自定义项。")
            continue
        by_id[p["id"]] = p
    profiles = list(by_id.values())
    profiles.sort(key=lambda r: (str(r.get("category","")), _safe_int(r.get("sort",99),99), str(r.get("id"))))
    return profiles

def load_profile_rules(paths, profile_ids):
    wanted = [str(x) for x in _as_list(profile_ids)]
    index = {p["id"]: p for p in list_routing_profiles(paths)}
    missing = [x for x in wanted if x not in index]
    if missing:
        raise FileNotFoundError(f"Routing profile not found: {', '.join(missing)}")
    return [index[x] for x in wanted]

def load_profile_rule(paths, profile_id):
    return load_profile_rules(paths, [profile_id])[0]

def load_game_rule(paths, game_id): return load_profile_rule(paths, game_id)
def list_game_rules(paths): return list_routing_profiles(paths)

def _decode_nodes_file(path: Path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")

def node_source_is_configured(paths):
    src = load_node_source(paths)
    mode = str(src.get("mode","file")).strip().lower()
    if mode == "subscription":
        urls = src.get("subscription_urls", [])
        return (bool(urls), f"{len(urls)} 条订阅" if urls else "订阅 URL 为空")
    if mode != "file":
        return False, f"未知的节点来源：{mode}"
    if not paths.nodes_yaml.exists():
        return False, "未找到 nodes.yaml"
    try:
        text = _decode_nodes_file(paths.nodes_yaml)
    except OSError as e:
        return False, f"nodes.yaml 无法读取：{e}"
    has_proxies = bool(re.search(r'(?mi)^\s*(?:["\']?proxies["\']?)\s*:', text))
    has_name = bool(re.search(r'(?mi)(?:^\s*-\s*["\']?name["\']?\s*:|[{,]\s*["\']?name["\']?\s*:)', text))
    ok = has_proxies and has_name
    return (True, "本地 nodes.yaml") if ok else (False, "请把节点粘贴到 private\\nodes.yaml")

def copy_local_provider(paths, runtime_home):
    pdir = runtime_home / "provider"
    pdir.mkdir(parents=True, exist_ok=True)
    target = pdir / "nodes.yaml"
    shutil.copy2(paths.nodes_yaml, target)
    return target
