"""Deterministic robustness stress tests for malformed state and concurrency."""
import json, random, shutil, tempfile, threading, unittest
from pathlib import Path

from backend.config import (
    Paths, _normalize_profile, ensure_private_files, load_app_settings,
    profile_from_url, save_json, update_app_settings,
)
from backend.privacy import redact_log_line
from backend.subscription import parse_userinfo, describe_userinfo

RNG = random.Random(227)

class TempApp(unittest.TestCase):
    def setUp(self):
        self.td=Path(tempfile.mkdtemp())
        self.paths=Paths(self.td)
        (self.td/"rules").mkdir(parents=True,exist_ok=True)
        ensure_private_files(self.paths)
    def tearDown(self):
        shutil.rmtree(self.td,ignore_errors=True)

class ConfigFuzzTests(TempApp):
    def test_profile_normalizer_survives_hostile_nested_types(self):
        corpus=[None,True,False,0,1,-1,1.5,"","false","\nMATCH,FLY-JP",
                [],{},[None,{},443,"a,b","正常"],{"x":1}]
        fields=["domains","keywords","ip_cidrs","processes","ports",
                "latency_test_urls","full_browser","always_on","sort","category","name"]
        for i in range(500):
            p={"id":f"f{i}","name":"x","domains":["safe.jp"]}
            for field in RNG.sample(fields,RNG.randint(0,min(6,len(fields)))):
                p[field]=RNG.choice(corpus)
            out=_normalize_profile(p,"custom")
            self.assertIsInstance(out,dict)
            for key in ("domains","keywords","ip_cidrs","processes","ports","latency_test_urls"):
                self.assertIsInstance(out[key],list)

    def test_settings_loader_survives_json_serializable_type_matrix(self):
        vals=[None,True,False,0,1,-1,1.5,"","false","999999",[],{},["a"],{"x":"y"}]
        for i in range(150):
            raw={
                "game_exes":RNG.choice(vals),
                "services_enabled":RNG.choice(vals),
                "mixed_port":RNG.choice(vals),
                "controller_port":RNG.choice(vals),
                "latency_timeout_ms":RNG.choice(vals),
                "sticky_max_delay_ms":RNG.choice(vals),
                "jp_keywords":RNG.choice(vals),
                "last_node":RNG.choice(vals),
            }
            save_json(self.paths.app_settings,raw)
            s=load_app_settings(self.paths)
            self.assertIsInstance(s["game_exes"],dict)
            self.assertTrue(1024 <= s["mixed_port"] <= 65535)
            self.assertTrue(1024 <= s["controller_port"] <= 65535)
            self.assertNotEqual(s["mixed_port"],s["controller_port"])
            self.assertTrue(1000 <= s["latency_timeout_ms"] <= 30000)
            self.assertIsInstance(s["services_enabled"],bool)

    def test_concurrent_settings_mutators_do_not_lose_entries(self):
        errors=[]
        def worker(i):
            try:
                key=f"g{i}"
                def mutate(data):
                    exes=dict(data.get("game_exes",{}))
                    exes[key]=fr"C:\Games\{key}.exe"
                    data["game_exes"]=exes
                update_app_settings(self.paths,mutator=mutate)
            except Exception as e:
                errors.append(e)
        threads=[threading.Thread(target=worker,args=(i,)) for i in range(32)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(errors,[])
        exes=load_app_settings(self.paths)["game_exes"]
        self.assertEqual(set(exes),{f"g{i}" for i in range(32)})

class CustomUrlRobustnessTests(unittest.TestCase):
    def test_non_http_schemes_are_rejected(self):
        for value in ("ftp://example.com","file://example.com","ws://example.com","javascript://example.com"):
            with self.assertRaises(ValueError):
                profile_from_url(value)

    def test_long_domains_get_collision_resistant_ids(self):
        a="a"*55+"x.example.com"
        b="a"*55+"y.example.com"
        pa,pb=profile_from_url("https://"+a),profile_from_url("https://"+b)
        self.assertNotEqual(pa["id"],pb["id"])
        self.assertLessEqual(len(pa["id"]),64)

    def test_display_name_control_characters_are_removed(self):
        p=profile_from_url("https://example.com","hello\nFAKE LOG\tname")
        self.assertNotIn("\n",p["name"])
        self.assertNotIn("\t",p["name"])

class ParserRobustnessTests(unittest.TestCase):
    def test_subscription_userinfo_random_numbers_never_raise(self):
        values=["inf","-inf","nan","1e9999","-1","0","123","abc","",str(10**100)]
        for _ in range(300):
            header=";".join(
                f"{RNG.choice(['total','upload','download','expire'])}={RNG.choice(values)}"
                for _ in range(RNG.randint(1,8))
            )
            info=parse_userinfo(header)
            text=describe_userinfo(info)
            self.assertIsInstance(text,str)

    def test_log_redaction_is_idempotent_and_removes_obvious_secrets(self):
        line="visit https://secret.example/path?token=abc via node.example.jp:443 35.72.161.13"
        once=redact_log_line(line)
        twice=redact_log_line(once)
        self.assertNotIn("secret.example",once)
        self.assertNotIn("token=abc",once)
        self.assertNotIn("35.72.161.13",once)
        self.assertIsInstance(twice,str)

if __name__=="__main__":
    unittest.main()
