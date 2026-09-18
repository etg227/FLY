from pathlib import Path
from .config import Paths, copy_local_provider, load_app_settings, load_node_source, load_profile_rule

def q(v): return "'" + str(v).replace("'", "''") + "'"

FAKE_IP_FILTER = [
    "*.lan", "*.local", "+.msftconnecttest.com", "+.msftncsi.com",
    "time.windows.com", "+.pool.ntp.org", "+.ntp.org",
    "+.qq.com", "+.steamserver.net",
]
DNS_NAMESERVERS = ["223.5.5.5", "119.29.29.29", "https://doh.pub/dns-query"]
DNS_BOOTSTRAP = ["223.5.5.5", "119.29.29.29"]

def _dedup(items):
    out, seen = [], set()
    for x in items:
        s = str(x).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower()); out.append(s)
    return out

def build_runtime_config(paths: Paths, profile_ids):
    """Build one Mihomo instance for any number of routing profiles.
    Safety invariant: every unmatched flow ends at MATCH,DIRECT."""
    if isinstance(profile_ids, str):
        profile_ids = [profile_ids]
    profiles = [load_profile_rule(paths, p) for p in profile_ids]
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

    mode = str(source.get("mode","file")).strip().lower()
    lines += ["proxy-providers:", "  USER:"]
    if mode == "subscription":
        lines += [
            "    type: http",
            f"    url: {q(str(source.get('subscription_url','')).strip())}",
            "    path: ./provider/subscription.yaml",
            "    interval: 3600",
        ]
    else:
        copy_local_provider(paths, home)
        lines += ["    type: file", "    path: ./provider/nodes.yaml"]

    lines += [
        "    health-check:",
        "      enable: true",
        "      url: https://www.gstatic.com/generate_204",
        "      interval: 300",
        "      timeout: 5000",
        "      lazy: true",
        "",
        "proxy-groups:",
        "  - name: FLY-JP",
        "    type: select",
        "    use:",
        "      - USER",
        "",
        "rules:",
    ]

    if tun_mode:
        for d in domains:
            lines.append(f"  - AND,((NETWORK,udp),(DST-PORT,443),(DOMAIN-SUFFIX,{d})),REJECT")
        for k in keywords:
            lines.append(f"  - AND,((NETWORK,udp),(DST-PORT,443),(DOMAIN-KEYWORD,{k})),REJECT")

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

    lines.append("  - MATCH,DIRECT")
    lines.append("")

    cfg = home / "config.yaml"
    cfg.write_text("\n".join(lines), encoding="utf-8")
    return home, cfg
