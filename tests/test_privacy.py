"""Privacy regression tests."""
import tempfile, unittest
from pathlib import Path
from backend.privacy import redact_log_line
from backend.mihomo_config import redact_runtime_config

class PrivacyTests(unittest.TestCase):
    def test_persisted_log_redacts_targets_and_node_addresses(self):
        line=("dial FLY-JP https://secret.example/path?token=abc "
              "node.jp.example:443 35.72.161.13:443")
        safe=redact_log_line(line)
        self.assertNotIn("secret.example",safe)
        self.assertNotIn("token=abc",safe)
        self.assertNotIn("node.jp.example",safe)
        self.assertNotIn("35.72.161.13",safe)
        self.assertIn("<host:",safe)

    def test_persisted_log_redacts_ipv6_node_addresses(self):
        line=("dial tcp [2001:db8:1234::5]:443 failed; "
              "fallback 2404:6800:4008::200e")
        safe=redact_log_line(line)
        self.assertNotIn("2001:db8:1234::5",safe)
        self.assertNotIn("2404:6800:4008::200e",safe)
        self.assertIn("<ip6:",safe)

    def test_runtime_config_subscription_and_api_secret_are_redacted(self):
        p=Path(tempfile.mkdtemp())/"config.yaml"
        url="https://airport.example/sub?token=SECRET"
        p.write_text("secret: 'LOCAL-API-SECRET'\nurl: '"+url+"'\n",encoding="utf-8")
        redact_runtime_config(p,[url])
        text=p.read_text(encoding="utf-8")
        self.assertNotIn("SECRET",text)
        self.assertNotIn("LOCAL-API-SECRET",text)
        self.assertIn("<redacted-subscription-1>",text)
        self.assertIn("<redacted-api-secret>",text)

if __name__=="__main__":unittest.main()
