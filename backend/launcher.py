from .config import load_profile_rule

def log_hints(paths, profile_ids, log):
    """Routing setup does not launch third-party apps; only show entry hints."""
    for pid in profile_ids:
        rule = load_profile_rule(paths, pid)
        name = rule.get("name", pid)
        mode = str(rule.get("launch_mode", "browser")).lower()
        url = str(rule.get("url", "")).strip()
        if rule.get("full_browser"):
            log(f"[READY] {name}：整浏览器代理已生效，直接在浏览器里打开游戏页面即可（{url or '任意页面'}）。")
        elif mode == "browser" and url:
            log(f"[READY] {name}：在浏览器打开 {url}")
        elif mode == "browser":
            log(f"[READY] {name}：域名分流已生效。")
        else:
            log(f"[READY] {name}：TUN/进程分流已生效，自行启动游戏即可。")
