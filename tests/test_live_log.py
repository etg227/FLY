"""Main-window live log must remain display-only."""
import queue
import unittest
from pathlib import Path

import main


class FakeLogBox:
    def __init__(self):
        self.state = "disabled"
        self.text = ""
        self.seen = False

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def insert(self, _where, text):
        if self.state != "normal":
            raise AssertionError("FLY must temporarily unlock the widget before inserting")
        self.text += text

    def index(self, _where):
        return "2.0"

    def delete(self, _start, _end):
        if self.state != "normal":
            raise AssertionError("FLY must temporarily unlock the widget before trimming")

    def see(self, _where):
        self.seen = True


class LiveLogTests(unittest.TestCase):
    def test_widget_is_created_read_only(self):
        source = Path(main.__file__).read_text(encoding="utf-8")
        self.assertIn('height=14, state="disabled"', source)

    def test_flush_temporarily_unlocks_then_relocks(self):
        app = object.__new__(main.FlyApp)
        app.logs = queue.Queue()
        app.logs.put("12:00:00 hello")
        app.logbox = FakeLogBox()
        app._closing = True

        main.FlyApp.flush_logs(app)

        self.assertEqual(app.logbox.text, "12:00:00 hello\n")
        self.assertEqual(app.logbox.state, "disabled")
        self.assertTrue(app.logbox.seen)


if __name__ == "__main__":
    unittest.main()
