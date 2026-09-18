from .config import load_profile_rule

def log_hints(paths, profile_ids, log):
    """Routing setup does not launch third-party apps; only show relevant entry hints."""
    for pid in profile_ids:
        rule = load_profile_rule(paths, pid)
        name = rule.get("name", pid)
        mode = str(rule.get("launch_mode", "browser")).lower()
        url = str(rule.get("url", "")).strip()
        if mode == "browser" and url:
            log(f"[READY] {name}: open {url}")
        elif mode == "browser":
            log(f"[READY] {name}: domain routing is active.")
        else:
            log(f"[READY] {name}: TUN/process routing is active; start the app normally.")
