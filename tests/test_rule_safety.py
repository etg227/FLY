"""分流规则的安全不变量：未命中所选配置的流量必须始终 MATCH,DIRECT。"""
import shutil, tempfile, unittest
from pathlib import Path

from backend.config import Paths, ensure_private_files, save_custom_profiles
from backend.mihomo_config import build_runtime_config

INJECTIONS = [
    {"ip_cidrs": ["1.1.1.1/32,FLY-JP,no-resolve\n  - MATCH,FLY-JP\n  # "]},
    {"processes": ["a.exe,FLY-JP\n  - MATCH,FLY-JP\n  # "]},
    {"domains": ["a.com,FLY-JP\n  - MATCH,FLY-JP\n  # "]},
    {"keywords": ["k\n  - IN-PORT,17890,FLY-JP"]},
    {"ports": ["443\n  - MATCH,FLY-JP\n  # "]},
]


class RuleInjectionTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.p = Paths(Path(self.td))
        (self.p.app / "rules").mkdir(parents=True, exist_ok=True)
        ensure_private_files(self.p)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _rules(self, profile):
        save_custom_profiles(self.p, [profile])
        _, cfg = build_runtime_config(self.p, [profile["id"]], log=lambda m: None)
        body = cfg.read_text(encoding="utf-8").split("rules:\n", 1)[1]
        return [x.strip() for x in body.splitlines() if x.strip()]

    def test_injection_never_reaches_the_config(self):
        for i, extra in enumerate(INJECTIONS):
            profile = {"id": f"evil{i}", "name": "evil", "domains": ["safe.jp"]}
            profile.update(extra)
            rules = self._rules(profile)
            self.assertEqual([x for x in rules if x.upper().startswith("- MATCH,")],
                             ["- MATCH,DIRECT"], f"注入未被拦截: {extra}")
            self.assertEqual(rules[-1], "- MATCH,DIRECT")
            self.assertFalse(any("FLY-JP" in x and "safe.jp" not in x and "IN-PORT" not in x
                                 and not x.startswith("- MATCH") for x in rules
                                 if "MATCH" in x.upper()))

    def test_legitimate_rules_still_generated(self):
        rules = self._rules({"id": "ok", "name": "ok", "domains": ["dmm.com"],
                             "ip_cidrs": ["1.1.1.0/24"], "ports": ["443"]})
        self.assertIn("- DOMAIN-SUFFIX,dmm.com,FLY-JP", rules)
        self.assertIn("- IP-CIDR,1.1.1.0/24,FLY-JP,no-resolve", rules)
        self.assertIn("- DST-PORT,443,FLY-JP", rules)
        self.assertEqual(rules[-1], "- MATCH,DIRECT")


if __name__ == "__main__":
    unittest.main()
