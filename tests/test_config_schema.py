"""Configuration schema and custom-profile regression tests."""
import json, shutil, tempfile, unittest
from pathlib import Path

from backend.config import (
    Paths, ensure_private_files, list_routing_profiles, load_app_settings,
    load_node_source, load_custom_profiles, save_custom_profiles, validate_profile,
    profile_from_url, node_source_is_configured, save_json, registrable_domain,
)

class TempApp(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.p = Paths(Path(self.td))
        (self.p.app / "rules").mkdir(parents=True, exist_ok=True)
        ensure_private_files(self.p)
    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

class ProfileSchemaTests(TempApp):
    def test_non_numeric_sort_does_not_break_listing(self):
        for bad in ("abc", "1.5", None, [], {"a": 1}):
            save_custom_profiles(self.p, [{"id": "x", "name": "X", "sort": bad, "domains":["x.jp"]}])
            profiles = list_routing_profiles(self.p)
            self.assertEqual([r["id"] for r in profiles], ["x"])
            self.assertEqual(profiles[0]["sort"], 99)

    def test_nested_wrong_types_do_not_brick_profile_loading(self):
        save_json(self.p.custom_profiles, {"profiles":[{
            "id":"x","name":"X","domains":None,"ports":443,
            "processes":{"bad":"shape"},"latency_test_urls":None
        }]})
        profiles = load_custom_profiles(self.p)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["ports"], ["443"])
        self.assertEqual(profiles[0]["domains"], [])
        self.assertEqual(profiles[0]["processes"], [])

    def test_builtin_rule_file_with_bad_sort_is_tolerated(self):
        (self.p.app/"rules"/"bad.json").write_text(
            json.dumps({"id":"bad","name":"Bad","sort":"abc","domains":["bad.jp"]}), encoding="utf-8")
        self.assertIn("bad", [r["id"] for r in list_routing_profiles(self.p)])

    def test_custom_id_cannot_replace_builtin(self):
        (self.p.app/"rules"/"dmm.json").write_text(
            json.dumps({"id":"dmm","name":"Builtin","domains":["dmm.com"]}), encoding="utf-8")
        save_custom_profiles(self.p,[{"id":"dmm","name":"Evil","domains":["evil.example"]}])
        p = {x["id"]:x for x in list_routing_profiles(self.p)}["dmm"]
        self.assertEqual(p["name"], "Builtin")
        self.assertEqual(p["domains"], ["dmm.com"])

class SettingsSchemaTests(TempApp):
    def test_json_with_wrong_toplevel_type_falls_back(self):
        for text in ("[]", '"hello"', "123", "null"):
            self.p.node_source.write_text(text, encoding="utf-8")
            self.p.app_settings.write_text(text, encoding="utf-8")
            self.p.custom_profiles.write_text(text, encoding="utf-8")
            self.assertEqual(load_node_source(self.p)["subscription_urls"], [])
            self.assertIn("mixed_port", load_app_settings(self.p))
            self.assertEqual(load_custom_profiles(self.p), [])

    def test_nested_settings_are_normalized(self):
        save_json(self.p.app_settings, {
            "game_exes": [], "mixed_port":"bad", "controller_port":17890,
            "latency_timeout_ms":999999, "jp_keywords":["", "a", "日本"]
        })
        s=load_app_settings(self.p)
        self.assertIsInstance(s["game_exes"], dict)
        self.assertEqual(s["mixed_port"], 17890)
        self.assertNotEqual(s["controller_port"], s["mixed_port"])
        self.assertEqual(s["latency_timeout_ms"], 30000)
        self.assertNotIn("a", s["jp_keywords"])

    def test_json_with_bom_is_readable(self):
        self.p.node_source.write_text(
            '﻿{"mode":"subscription","subscription_urls":["https://a.jp/s"]}', encoding="utf-8")
        self.assertEqual(load_node_source(self.p)["subscription_urls"], ["https://a.jp/s"])

    def test_duplicate_subscription_urls_are_deduped(self):
        save_json(self.p.node_source, {"mode":"subscription","subscription_urls":[
            "https://a.jp/s","https://a.jp/s"
        ]})
        self.assertEqual(load_node_source(self.p)["subscription_urls"], ["https://a.jp/s"])

class NodesYamlTests(TempApp):
    def test_utf8_bom_and_utf16_are_accepted(self):
        sample='proxies:\n  - name: "JP"\n    type: ss\n'
        self.p.nodes_yaml.write_text(sample, encoding="utf-8-sig")
        self.assertTrue(node_source_is_configured(self.p)[0])
        self.p.nodes_yaml.write_text(sample, encoding="utf-16")
        self.assertTrue(node_source_is_configured(self.p)[0])

class CustomUrlTests(unittest.TestCase):
    def test_idn_domain_survives_and_gets_safe_id(self):
        p=profile_from_url("https://例子.测试/")
        self.assertTrue(p["id"])
        self.assertTrue(p["domains"][0].startswith("xn--"))
        self.assertIn("xn--", p["url"])

    def test_shared_host_is_not_widened(self):
        self.assertEqual(registrable_domain("user.github.io"), "user.github.io")
        self.assertEqual(registrable_domain("foo.pages.dev"), "foo.pages.dev")

class ValidateProfileTests(unittest.TestCase):
    def test_reports_injection_and_semantic_errors(self):
        problems=validate_profile({
            "id":"x","name":"X",
            "domains":["ok.com","a.com,FLY-JP\n  - MATCH,FLY-JP"],
            "ports":["443","70000","*"],
            "ip_cidrs":["1.1.1.1/32","not-a-cidr"],
            "processes":["game,x.exe"],
        })
        self.assertGreaterEqual(len(problems),5)

    def test_clean_profile_reports_nothing(self):
        self.assertEqual(validate_profile({
            "id":"x","name":"X","domains":["dmm.com","*.dmmgames.com"],
            "ports":["443","1000-2000"],"ip_cidrs":["1.1.1.0/24","2001:db8::/32"],
            "processes":["My Game.exe"]
        }), [])

if __name__=="__main__":
    unittest.main()
