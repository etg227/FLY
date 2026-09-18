from pathlib import Path
from .config import Paths, copy_local_provider, load_app_settings, load_game_rule, load_node_source

def q(v): return "'" + str(v).replace("'", "''") + "'"

# Domains that must resolve to their real IP even in fake-ip mode, otherwise
# Windows connectivity checks, NTP and LAN discovery misbehave under TUN.
FAKE_IP_FILTER = [
    "*.lan",
    "*.local",
    "+.msftconnecttest.com",
    "+.msftncsi.com",
    "time.windows.com",
    "+.pool.ntp.org",
    "+.ntp.org",
    "+.qq.com",
    "+.steamserver.net",
]

# Domestic-first DNS: the DoH-only setup from v0.3 (1.1.1.1 / dns.google)
# is unreachable from a mainland network without a proxy, which broke both
# DIRECT traffic and the initial subscription download.
DNS_NAMESERVERS = ["223.5.5.5", "119.29.29.29", "https://doh.pub/dns-query"]
DNS_BOOTSTRAP = ["223.5.5.5", "119.29.29.29"]

def build_runtime_config(paths: Paths, game_ids):
    """game_ids: one id or a list of ids — all selected games share one core,
    their rules are merged so several games accelerate at the same time."""
    if isinstance(game_ids, str):
        game_ids = [game_ids]
    rules = [load_game_rule(paths, g) for g in game_ids]
    settings = load_app_settings(paths)
    source = load_node_source(paths)
    home = paths.runtime / "mihomo"
    home.mkdir(parents=True, exist_ok=True)

    mixed = int(settings.get("mixed_port",17890))
    ctrl = int(settings.get("controller_port",19090))
    secret = str(settings.get("api_secret","")).strip()
    tun_mode = any(str(r.get("launch_mode","browser")).lower() == "tun" for r in rules)

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
            # Sniff SNI so processes that connect by raw IP still hit the
            # domain rules correctly under TUN.
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
        # lazy: the app runs its own latency tests on the Japan candidates,
        # so there is no need to probe every node in the subscription.
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

    def dedup(items):
        out, seen = [], set()
        for x in items:
            if x and x.lower() not in seen:
                seen.add(x.lower()); out.append(x)
        return out

    processes = []
    for gid, rule in zip(game_ids, rules):
        exe = str(settings.get("game_exes", {}).get(gid, "")).strip()
        if exe:
            name = Path(exe).name
            if name:
                processes.append(name)
        processes.extend(str(p).strip() for p in rule.get("processes", []))
    processes = dedup(processes)

    domains = dedup([str(d).strip().lower().lstrip("*.") for r in rules for d in r.get("domains", [])])
    keywords = dedup([str(k).strip().lower() for r in rules for k in r.get("keywords", [])])
    cidrs = dedup([str(c).strip() for r in rules for c in r.get("ip_cidrs", [])])

    # Under TUN, QUIC (UDP 443) to game hosts would bypass or hang on nodes with
    # broken UDP; rejecting it forces a clean TCP fallback through the proxy.
    # Placed BEFORE the routing rules so it wins the match.
    if tun_mode:
        for d in domains:
            lines.append(f"  - AND,((NETWORK,udp),(DST-PORT,443),(DOMAIN-SUFFIX,{d})),REJECT")
        for k in keywords:
            lines.append(f"  - AND,((NETWORK,udp),(DST-PORT,443),(DOMAIN-KEYWORD,{k})),REJECT")

    for proc in processes:
        lines.append(f"  - PROCESS-NAME,{proc},FLY-JP")

    for d in domains:
        lines.append(f"  - DOMAIN-SUFFIX,{d},FLY-JP")

    # DOMAIN-KEYWORD catches CDN hosts such as prd-game-a-granbluefantasy.akamaized.net
    # that DOMAIN-SUFFIX cannot express.
    for k in keywords:
        lines.append(f"  - DOMAIN-KEYWORD,{k},FLY-JP")

    for c in cidrs:
        lines.append(f"  - IP-CIDR,{c},FLY-JP,no-resolve")

    lines.append("  - MATCH,DIRECT")
    lines.append("")

    cfg = home / "config.yaml"
    cfg.write_text("\n".join(lines), encoding="utf-8")
    return home, cfg
