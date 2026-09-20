from pathlib import Path
from .config import Paths, copy_local_provider, load_app_settings, load_node_source, load_profile_rule
from .subscription import provider_cache_name, select_usable_subscriptions

def q(v): return "'" + str(v).replace("'", "''") + "'"

FAKE_IP_FILTER = [
    "*.lan", "*.local", "+.msftconnecttest.com", "+.msftncsi.com",
    "time.windows.com", "+.pool.ntp.org", "+.ntp.org",
    "+.qq.com", "+.steamserver.net",
]
DNS_NAMESERVERS = ["223.5.5.5", "119.29.29.29", "https://doh.pub/dns-query"]
DNS_BOOTSTRAP = ["223.5.5.5", "119.29.29.29"]

# When a full_browser profile is selected together with a TUN profile, the
# system proxy stays off and browser traffic rides TUN instead — match it by
# browser process name so the coverage survives the mixed selection.
BROWSER_PROCESSES = ["msedge.exe", "chrome.exe", "firefox.exe", "brave.exe"]

def _dedup(items):
    out, seen = [], set()
    for x in items:
        s = str(x).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower()); out.append(s)
    return out

def build_runtime_config(paths: Paths, profile_ids, log=None):
    """Build one Mihomo instance for any number of routing profiles.
    Safety invariant: every unmatched flow ends at MATCH,DIRECT."""
    log = log or (lambda m: None)
    if isinstance(profile_ids, str):
        profile_ids = [profile_ids]
    profiles = [load_profile_rule(paths, p) for p in profile_ids]
    for pid, profile in zip(profile_ids, profiles):
        for bad in profile.get("invalid_values", []):
            log(f"[RULE] 配置 {pid} 中的 {bad} 不符合分流规则语法，已忽略。")
    settings = load_app_settings(paths)
    source = load_node_source(paths)
    home = paths.runtime / "mihomo"
    home.mkdir(parents=True, exist_ok=True)

    mixed = int(settings.get("mixed_port",17890))
    ctrl = int(settings.get("controller_port",19090))
    secret = str(settings.get("api_secret","")).strip()

    configured_processes = []
    for pid, profile in zip(profile_ids, profiles):
        exe = str(settings.get("game_exes", {}).get(pid, "")).strip()
        if exe:
            configured_processes.append(Path(exe).name)
        configured_processes.extend(profile.get("processes", []))
    processes = _dedup(configured_processes)
    domains = _dedup(str(d).lower().lstrip("*.") for p in profiles for d in p.get("domains", []))
    keywords = _dedup(str(k).lower() for p in profiles for k in p.get("keywords", []))
    cidrs = _dedup(c for p in profiles for c in p.get("ip_cidrs", []))
    ports = _dedup(p for profile in profiles for p in profile.get("ports", []))
    full_browser = any(bool(p.get("full_browser")) for p in profiles)

    tun_mode = bool(processes) or any(str(p.get("launch_mode","browser")).lower()=="tun" for p in profiles)

    lines = [
        f"mixed-port: {mixed}",
        "allow-lan: false",
        "bind-address: 127.0.0.1",
        "mode: rule",
        "log-level: info",
        "ipv6: false",
        "unified-delay: true",
        "tcp-concurrent: true",
        "keep-alive-interval: 30",
        f"external-controller: 127.0.0.1:{ctrl}",
        f"secret: {q(secret)}",
        "find-process-mode: strict",
        "",
        "profile:",
        "  store-selected: false",
        "  store-fake-ip: true",
        "",
        "dns:",
        "  enable: true",
        "  ipv6: false",
        "  enhanced-mode: fake-ip",
        "  fake-ip-range: 198.18.0.1/16",
        "  fake-ip-filter:",
    ]
    lines += [f"    - {q(x)}" for x in FAKE_IP_FILTER]
    lines += ["  default-nameserver:"]
    lines += [f"    - {x}" for x in DNS_BOOTSTRAP]
    lines += ["  nameserver:"]
    lines += [f"    - {x}" for x in DNS_NAMESERVERS]
    lines += ["  proxy-server-nameserver:"]
    lines += [f"    - {x}" for x in DNS_BOOTSTRAP]
    lines += [""]

    if tun_mode:
        lines += [
            "tun:",
            "  enable: true",
            "  stack: system",
            "  auto-route: true",
            "  auto-detect-interface: true",
            "  strict-route: false",
            "  dns-hijack:",
            "    - any:53",
            "",
            "sniffer:",
            "  enable: true",
            "  sniff:",
            "    TLS:",
            "      ports: [443]",
            "    HTTP:",
            "      ports: [80]",
            "",
        ]
    else:
        lines += ["tun:", "  enable: false", ""]

    HEALTH_CHECK = [
        "    health-check:",
        "      enable: true",
        "      url: https://www.gstatic.com/generate_204",
        "      interval: 300",
        "      timeout: 5000",
        "      lazy: true",
    ]
    mode = str(source.get("mode","file")).strip().lower()
    lines += ["proxy-providers:"]
    provider_names = []
    if mode == "subscription":
        provider_dir = home / "provider"
        provider_dir.mkdir(parents=True, exist_ok=True)
        # Multiple subscriptions merge into one pool: if one provider's nodes
        # die, selection simply moves to another provider's Japan nodes.
        usable = select_usable_subscriptions(source.get("subscription_urls", []), provider_dir, log)
        if not usable:
            raise RuntimeError("所有订阅都无法访问且没有本地缓存，请检查网络或订阅链接。")
        for i, u in enumerate(usable, 1):
            name = f"USER{i}"
            provider_names.append(name)
            lines += [
                f"  {name}:",
                "    type: http",
                f"    url: {q(u)}",
                f"    path: ./provider/{provider_cache_name(u)}",
                "    interval: 3600",
            ] + HEALTH_CHECK
    else:
        copy_local_provider(paths, home)
        provider_names = ["USER"]
        lines += ["  USER:", "    type: file", "    path: ./provider/nodes.yaml"] + HEALTH_CHECK

    lines += [
        "",
        "proxy-groups:",
        "  - name: FLY-JP",
        "    type: select",
        "    use:",
    ]
    lines += [f"      - {n}" for n in provider_names]
    lines += [
        "",
        "rules:",
    ]
    rules_start = len(lines)

    if tun_mode:
        for d in domains:
            lines.append(f"  - AND,((NETWORK,udp),(DST-PORT,443),(DOMAIN-SUFFIX,{d})),REJECT")
        for k in keywords:
            lines.append(f"  - AND,((NETWORK,udp),(DST-PORT,443),(DOMAIN-KEYWORD,{k})),REJECT")
        if full_browser:
            for b in BROWSER_PROCESSES:
                lines.append(f"  - AND,((NETWORK,udp),(DST-PORT,443),(PROCESS-NAME,{b})),REJECT")

    for proc in processes:
        lines.append(f"  - PROCESS-NAME,{proc},FLY-JP")
    for d in domains:
        lines.append(f"  - DOMAIN-SUFFIX,{d},FLY-JP")
    for k in keywords:
        lines.append(f"  - DOMAIN-KEYWORD,{k},FLY-JP")
    for c in cidrs:
        lines.append(f"  - IP-CIDR,{c},FLY-JP,no-resolve")
    for port in ports:
        lines.append(f"  - DST-PORT,{port},FLY-JP")

    # Explicit opt-in only (a checked profile with full_browser: true): route
    # everything the browser sends through FLY-JP. Covers portals like
    # DMM/FANZA whose in-portal games load from unenumerable vendor domains.
    if full_browser:
        lines.append(f"  - IN-PORT,{mixed},FLY-JP")
        if tun_mode:
            for b in BROWSER_PROCESSES:
                lines.append(f"  - PROCESS-NAME,{b},FLY-JP")

    lines.append("  - MATCH,DIRECT")
    lines.append("")

    # 安全不变量：整份规则里 MATCH 只能有一条，且必须是末尾的 MATCH,DIRECT。
    # 任何注入若绕过了字段白名单，也会在这里被拦下，而不是静默变成全局代理。
    rule_lines = [x.strip() for x in lines[rules_start:] if x.strip()]
    matches = [x for x in rule_lines if x.upper().startswith("- MATCH,")]
    if len(matches) != 1 or rule_lines[-1] != "- MATCH,DIRECT":
        raise RuntimeError(
            "生成的分流规则未通过安全校验（MATCH,DIRECT 兜底规则异常），已拒绝启动。"
            "请检查自定义配置里的 domains / keywords / ip_cidrs / ports / processes。"
        )

    cfg = home / "config.yaml"
    cfg.write_text("\n".join(lines), encoding="utf-8")
    return home, cfg
