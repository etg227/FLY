from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import launcher


class DummyUi:
    def __init__(self):
        self.logs=[]
        self.statuses=[]
    def log(self,msg): self.logs.append(str(msg))
    def status(self,msg): self.statuses.append(str(msg))
    def progress(self,_): pass


def make_payload(root: Path):
    payload=root/"bootstrap"
    (payload/"backend").mkdir(parents=True)
    (payload/"rules").mkdir()
    (payload/"scripts").mkdir()
    (payload/"main.py").write_text("print('embedded-main')\n",encoding="utf-8")
    (payload/"launcher.py").write_text("# embedded launcher source\n",encoding="utf-8")
    (payload/"VERSION").write_text("9.9.9\n",encoding="utf-8")
    (payload/"backend"/"config.py").write_text("X=1\n",encoding="utf-8")
    (payload/"rules"/"x.json").write_text("{}\n",encoding="utf-8")
    (payload/"scripts"/"x.py").write_text("pass\n",encoding="utf-8")
    manifest={
        "schema":1,
        "managed_dirs":["backend","rules","scripts"],
        "managed_root":[
            "main.py","launcher.py","README.md","LICENSE",
            "START_FLY_DEBUG.bat",".gitignore",
            "update-manifest.json","launcher.exe.new",
        ],
    }
    (payload/"update-manifest.json").write_text(
        json.dumps(manifest),encoding="utf-8")
    return payload


class EmbeddedBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp())
        self.bundle=self.tmp/"bundle"
        self.root=self.tmp/"install"
        self.root.mkdir()
        self.payload=make_payload(self.bundle)
        self.ui=DummyUi()

    def test_empty_install_is_seeded_from_embedded_payload(self):
        with mock.patch.object(launcher.sys,"_MEIPASS",str(self.bundle),create=True):
            seeded=launcher.seed_embedded_bootstrap(self.root,self.ui)
        self.assertTrue(seeded)
        self.assertEqual((self.root/"VERSION").read_text().strip(),"9.9.9")
        self.assertTrue((self.root/"main.py").exists())
        self.assertTrue((self.root/"backend"/"config.py").exists())
        self.assertTrue((self.root/"rules"/"x.json").exists())

    def test_existing_complete_install_is_not_overwritten(self):
        (self.root/"main.py").write_text("local\n",encoding="utf-8")
        (self.root/"VERSION").write_text("10.0.0\n",encoding="utf-8")
        (self.root/"backend").mkdir()
        (self.root/"backend"/"config.py").write_text("LOCAL=1\n",encoding="utf-8")
        with mock.patch.object(launcher.sys,"_MEIPASS",str(self.bundle),create=True):
            seeded=launcher.seed_embedded_bootstrap(self.root,self.ui)
        self.assertFalse(seeded)
        self.assertEqual((self.root/"main.py").read_text(encoding="utf-8"),"local\n")

    def test_first_run_does_not_require_release_api_or_external_python(self):
        (self.root/"main.py").write_text("pass\n",encoding="utf-8")
        (self.root/"VERSION").write_text("9.9.9\n",encoding="utf-8")
        (self.root/"backend").mkdir()
        (self.root/"backend"/"config.py").write_text("X=1\n",encoding="utf-8")
        expected=[sys.executable,"--run-main",str(self.root)]
        with mock.patch.object(launcher,"seed_embedded_bootstrap",return_value=True), \
             mock.patch.object(launcher,"latest_release",
                               side_effect=AssertionError("network must not be touched")), \
             mock.patch.object(launcher,"ensure_core"), \
             mock.patch.object(launcher.sys,"frozen",True,create=True):
            command=launcher.run_flow(self.root,self.ui)
        self.assertEqual(command,expected)

    def test_missing_embedded_and_missing_main_has_clear_error(self):
        empty_bundle=self.tmp/"empty-bundle"; empty_bundle.mkdir()
        with mock.patch.object(launcher.sys,"_MEIPASS",str(empty_bundle),create=True), \
             mock.patch.object(launcher,"latest_release",
                               side_effect=OSError("offline")):
            with self.assertRaises(RuntimeError) as ctx:
                launcher.run_flow(self.root,self.ui)
        self.assertIn("不含可用的离线启动包",str(ctx.exception))


if __name__=="__main__":
    unittest.main(verbosity=2)
