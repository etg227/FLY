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

    def test_runtime_config_subscription_is_redacted(self):
        p=Path(tempfile.mkdtemp())/"config.yaml"
        url="https://airport.example/sub?token=SECRET"
        p.write_text("url: '"+url+"'\n",encoding="utf-8")
        redact_runtime_config(p,[url])
        text=p.read_text(encoding="utf-8")
        self.assertNotIn("SECRET",text)
        self.assertIn("<redacted-subscription-1>",text)

if __name__=="__main__":unittest.main()
