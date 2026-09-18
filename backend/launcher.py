from .config import load_game_rule

def log_hints(paths, game_ids, log):
    """Acceleration only sets up routing — nothing is opened or launched.
    Just tell the user where each game lives."""
    for gid in game_ids:
        rule = load_game_rule(paths, gid)
        name = rule.get("name", gid)
        mode = str(rule.get("launch_mode", "browser")).lower()
        if mode == "browser":
            url = str(rule.get("url", "")).strip()
            log(f"[READY] {name}: 在浏览器打开 {url}" if url else f"[READY] {name}")
        else:
            log(f"[READY] {name}: TUN 分流已生效，自行启动游戏即可。")
