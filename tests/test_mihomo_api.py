import unittest
from backend.mihomo_api import MihomoApi


class FakeMihomoApi(MihomoApi):
    def __init__(self):
        super().__init__(port=19090, secret="")
        self.calls = []

    def _request(self, method, path, data=None, timeout=8):
        self.calls.append((method, path))
        if path == "/providers/proxies":
            return {
                "providers": {
                    "USER1": {
                        "proxies": [
                            {"name": "🇯🇵 日本W01 | IEPL"},
                            {"name": "🇯🇵 日本W02 | IEPL"},
                        ]
                    }
                }
            }
        if path.startswith("/providers/proxies/USER1/"):
            return {"delay": 42}
        if path.startswith("/proxies/"):
            return {"delay": 88}
        raise AssertionError(f"Unexpected request: {method} {path}")


class MihomoApiDelayTests(unittest.TestCase):
    def test_provider_node_uses_provider_healthcheck(self):
        api = FakeMihomoApi()
        delay = api.delay("🇯🇵 日本W01 | IEPL", "https://www.gstatic.com/generate_204", 5000)
        self.assertEqual(delay, 42)

        paths = [path for _, path in api.calls]
        self.assertIn("/providers/proxies", paths)
        self.assertTrue(any(
            path.startswith("/providers/proxies/USER1/") and "/healthcheck?" in path
            for path in paths
        ))
        self.assertFalse(any(
            path.startswith("/proxies/%F0%9F%87%AF%F0%9F%87%B5") and "/delay?" in path
            for path in paths
        ))

    def test_non_provider_node_uses_legacy_delay_endpoint(self):
        api = FakeMihomoApi()
        delay = api.delay("Standalone JP", "https://www.gstatic.com/generate_204", 5000)
        self.assertEqual(delay, 88)

        paths = [path for _, path in api.calls]
        self.assertTrue(any(
            path.startswith("/proxies/Standalone%20JP/delay?")
            for path in paths
        ))


if __name__ == "__main__":
    unittest.main()
