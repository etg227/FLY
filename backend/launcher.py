from .config import load_profile_rules

def log_hints(paths, profile_ids, log, profiles=None):
    """Routing setup does not launch third-party apps; only show entry hints.

    The caller may pass its already-loaded profile index so startup does not
    rescan every JSON file once per selected profile.
    """
    ids = [str(x) for x in profile_ids]
    if profiles is None:
        rules = {p["id"]: p for p in load_profile_rules(paths, ids)}
    elif isinstance(profiles, dict):
        rules = profiles
    else:
        rules = {p["id"]: p for p in profiles}
    for pid in ids:
        rule = rules.get(pid)
        if not rule:
            continue
        name = rule.get("name", pid)
        mode = str(rule.get("launch_mode", "browser")).lower()
        url = str(rule.get("url", "")).strip()
        if rule.get("full_browser"):
            log(f"[READY] {name}：浏览器进程 TUN 分流已生效，打开对应页面即可（{url or '浏览器页面'}）。")
        elif mode == "browser" and url:
            log(f"[READY] {name}：在浏览器打开 {url}")
        elif mode == "browser":
            log(f"[READY] {name}：域名分流已生效。")
        else:
            log(f"[READY] {name}：TUN/进程分流已生效，自行启动游戏即可。")
