"""Usage accounting: proxied vs direct split and daily persistence."""
import json, shutil, tempfile, time, unittest
from pathlib import Path

from backend.usage_stats import ConnectionMeter, UsageStore
from backend.subscription import quota_from_userinfo


def conn(cid, up, down, chains):
    return {"id": cid, "upload": up, "download": down, "chains": chains}


class MeterTests(unittest.TestCase):
    def test_splits_proxied_and_direct_and_counts_only_deltas(self):
        m = ConnectionMeter()
        self.assertEqual(m.feed({"connections": [
            conn("a", 100, 900, ["JP 01", "FLY-JP"]),
            conn("b", 10, 40, ["DIRECT"]),
        ]}), (1000, 50))
        self.assertEqual(m.feed({"connections": [
            conn("a", 150, 1350, ["JP 01", "FLY-JP"]),   # +500
            conn("b", 10, 40, ["DIRECT"]),               # +0
            conn("c", 0, 200, ["JP 02", "FLY-JP"]),      # new +200
        ]}), (700, 0))

    def test_closed_then_reused_id_or_counter_reset_is_not_negative(self):
        m = ConnectionMeter()
        m.feed({"connections": [conn("a", 0, 1000, ["FLY-JP"])]})
        self.assertEqual(m.feed({"connections": [conn("a", 0, 300, ["FLY-JP"])]}), (300, 0))

    def test_garbage_is_ignored(self):
        m = ConnectionMeter()
        for bad in (None, [], {"connections": "x"}, {"connections": [None, 5, {"id": ""},
                    {"id": "z", "upload": "NaN", "download": -5, "chains": "FLY-JP"}]}):
            self.assertEqual(m.feed(bad), (0, 0))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.path = Path(self.td) / "usage.json"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_accumulates_per_day_and_persists(self):
        now = time.mktime((2026, 10, 11, 12, 0, 0, 0, 0, -1))
        s = UsageStore(self.path)
        s.add(100, 10, now=now)
        s.add(50, 5, now=now)
        s.add(7, 0, now=now - 86400)
        s.save(now=now)
        s2 = UsageStore(self.path)
        self.assertEqual(s2.today(now=now), (150, 15))
        self.assertEqual(s2.month(now=now), (157, 15))
        days = s2.last_days(7, now=now)
        self.assertEqual(len(days), 7)
        self.assertEqual(days[-1], ("2026-10-11", 150, 15))
        self.assertEqual(days[-2], ("2026-10-10", 7, 0))

    def test_month_excludes_previous_month(self):
        now = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1))
        s = UsageStore(self.path)
        s.add(1, 0, now=now - 86400)
        s.add(2, 0, now=now)
        self.assertEqual(s.month(now=now), (2, 0))

    def test_old_days_are_pruned_and_corrupt_file_is_tolerated(self):
        self.path.write_text("{not json", encoding="utf-8")
        s = UsageStore(self.path)
        now = time.mktime((2026, 10, 11, 12, 0, 0, 0, 0, -1))
        s.add(1, 1, now=now - 100 * 86400)
        s.add(2, 2, now=now)
        s.save(now=now)
        days = json.loads(self.path.read_text(encoding="utf-8"))["days"]
        self.assertEqual(list(days), ["2026-10-11"])


class QuotaTests(unittest.TestCase):
    def test_quota_fields(self):
        q = quota_from_userinfo({"upload": 10, "download": 80, "total": 100, "expire": 1798732800000})
        self.assertEqual((q["used"], q["remaining"], q["total"]), (90, 10, 100))
        self.assertAlmostEqual(q["ratio"], 0.9)
        self.assertTrue(q["low"])
        self.assertEqual(q["expire"], 1798732800)

    def test_missing_or_unlimited(self):
        self.assertIsNone(quota_from_userinfo(None))
        self.assertIsNone(quota_from_userinfo({"total": 0}))
        q = quota_from_userinfo({"download": 5})
        self.assertIsNone(q["ratio"])
        self.assertFalse(q["low"])


if __name__ == "__main__":
    unittest.main()
