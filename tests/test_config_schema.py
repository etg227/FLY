"""配置文件的类型健壮性：坏配置只能被忽略，不能让程序起不来。"""
import json, shutil, tempfile, unittest
from pathlib import Path

from backend.config import (Paths, ensure_private_files, list_routing_profiles,
                            load_app_settings, load_node_source, load_custom_profiles,
                            save_custom_profiles, validate_profile)


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
            save_custom_profiles(self.p, [{"id": "x", "name": "X", "sort": bad}])
            profiles = list_routing_profiles(self.p)          # 修复前这里抛 ValueError
            self.assertEqual([r["id"] for r in profiles], ["x"])
            self.assertEqual(profiles[0]["sort"], 99)

    def test_builtin_rule_file_with_bad_sort_is_tolerated(self):
        (self.p.app / "rules" / "bad.json").write_text(
            json.dumps({"id": "bad", "name": "Bad", "sort": "abc"}), encoding="utf-8")
        self.assertIn("bad", [r["id"] for r in list_routing_profiles(self.p)])


class TopLevelTypeTests(TempApp):
    def test_json_with_wrong_toplevel_type_falls_back(self):
        for text in ("[]", '"hello"', "123", "null"):
            self.p.node_source.write_text(text, encoding="utf-8")
            self.p.app_settings.write_text(text, encoding="utf-8")
            self.p.custom_profiles.write_text(text, encoding="utf-8")
            self.assertEqual(load_node_source(self.p)["subscription_urls"], [])
            self.assertIn("mixed_port", load_app_settings(self.p))
            self.assertEqual(load_custom_profiles(self.p), [])

    def test_json_with_bom_is_readable(self):
        self.p.node_source.write_text(
            '﻿{"mode":"subscription","subscription_urls":["https://a.jp/s"]}', encoding="utf-8")
        self.assertEqual(load_node_source(self.p)["subscription_urls"], ["https://a.jp/s"])


class ValidateProfileTests(unittest.TestCase):
    def test_reports_values_that_would_be_dropped(self):
        problems = validate_profile({"id": "x", "name": "X",
                                     "domains": ["ok.com", "a.com,FLY-JP\n  - MATCH,FLY-JP"],
                                     "ports": ["443", "*"],
                                     "ip_cidrs": ["1.1.1.1/32", "not-a-cidr"]})
        self.assertEqual(len(problems), 3)
        self.assertTrue(any("domains=" in x for x in problems))

    def test_clean_profile_reports_nothing(self):
        self.assertEqual(validate_profile(
            {"id": "x", "name": "X", "domains": ["dmm.com", "*.dmmgames.com"],
             "ports": ["443", "1000-2000"], "ip_cidrs": ["1.1.1.0/24", "2001:db8::/32"],
             "processes": ["My Game.exe"]}), [])


if __name__ == "__main__":
    unittest.main()
