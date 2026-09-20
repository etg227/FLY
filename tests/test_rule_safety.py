"""Routing safety invariants."""
import shutil, tempfile, unittest
from pathlib import Path

from backend.config import Paths, ensure_private_files, save_custom_profiles, save_json
from backend.mihomo_config import build_runtime_config

INJECTIONS=[
 {"ip_cidrs":["1.1.1.1/32,FLY-JP,no-resolve\n  - MATCH,FLY-JP\n  # "]},
 {"processes":["a.exe,FLY-JP\n  - MATCH,FLY-JP\n  # "]},
 {"domains":["a.com,FLY-JP\n  - MATCH,FLY-JP\n  # "]},
 {"keywords":["k\n  - IN-PORT,17890,FLY-JP"]},
 {"ports":["443\n  - MATCH,FLY-JP\n  # "]},
]

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.mkdtemp()
        self.p=Paths(Path(self.td))
        (self.p.app/"rules").mkdir(parents=True,exist_ok=True)
        ensure_private_files(self.p)
    def tearDown(self): shutil.rmtree(self.td,ignore_errors=True)

    def _rules(self,profile):
        save_custom_profiles(self.p,[profile])
        _home,cfg,_sensitive=build_runtime_config(self.p,[profile["id"]],log=lambda m:None)
        body=cfg.read_text(encoding="utf-8").split("rules:\n",1)[1]
        return [x.strip() for x in body.splitlines() if x.strip()]

    def test_injection_never_reaches_config(self):
        for i,extra in enumerate(INJECTIONS):
            p={"id":f"evil{i}","name":"evil","domains":["safe.jp"]}
            for key, values in extra.items():
                if key == "domains":
                    p[key] = ["safe.jp"] + list(values)
                else:
                    p[key] = values
            rules=self._rules(p)
            self.assertEqual([x for x in rules if x.upper().startswith("- MATCH,")],["- MATCH,DIRECT"])
            self.assertEqual(rules[-1],"- MATCH,DIRECT")

    def test_legitimate_rules_and_private_direct(self):
        rules=self._rules({"id":"ok","name":"ok","domains":["dmm.com"],
                           "ip_cidrs":["1.1.1.0/24","2001:db8::/32"],"ports":["443"]})
        self.assertIn("- DOMAIN-SUFFIX,dmm.com,FLY-JP",rules)
        self.assertIn("- IP-CIDR,1.1.1.0/24,FLY-JP,no-resolve",rules)
        self.assertIn("- IP-CIDR6,2001:db8::/32,FLY-JP,no-resolve",rules)
        self.assertIn("- IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",rules)
        self.assertEqual(rules[-1],"- MATCH,DIRECT")

    def test_empty_profile_is_rejected(self):
        save_custom_profiles(self.p,[{"id":"empty","name":"Empty"}])
        with self.assertRaises(RuntimeError):
            build_runtime_config(self.p,["empty"],log=lambda m:None)

    def test_full_browser_is_process_tun_not_in_port(self):
        rules=self._rules({"id":"browser","name":"Browser","full_browser":True})
        self.assertTrue(any(x=="- PROCESS-NAME,chrome.exe,FLY-JP" for x in rules))
        self.assertFalse(any(x.startswith("- IN-PORT,") for x in rules))
        cfg=(self.p.runtime/"mihomo"/"config.yaml").read_text(encoding="utf-8")
        self.assertIn("tun:\n  enable: true",cfg)

    def test_process_name_with_comma_is_dropped(self):
        rules=self._rules({"id":"p","name":"P","domains":["safe.jp"],"processes":["game,x.exe"]})
        self.assertFalse(any("game,x.exe" in x for x in rules))

    def test_subscription_providers_get_unique_prefixes(self):
        save_json(self.p.node_source,{"mode":"subscription","subscription_urls":["https://a.test/sub","https://b.test/sub"]})
        import backend.mihomo_config as mc
        original=mc.select_usable_subscriptions
        mc.select_usable_subscriptions=lambda urls,*a,**k:list(urls)
        try:
            save_custom_profiles(self.p,[{"id":"x","name":"X","domains":["x.jp"]}])
            _,cfg,_=build_runtime_config(self.p,["x"],log=lambda m:None)
        finally:
            mc.select_usable_subscriptions=original
        text=cfg.read_text(encoding="utf-8")
        self.assertIn("additional-prefix: '[S1] '",text)
        self.assertIn("additional-prefix: '[S2] '",text)

if __name__=="__main__":
    unittest.main()
