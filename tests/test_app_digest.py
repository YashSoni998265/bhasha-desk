"""The demo app re-checks approvals in the browser; it must agree with bhasha exactly."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bhasha.integrity import strings_digest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the app's digest code")
def test_the_browser_and_bhasha_compute_the_same_digest(tmp_path):
    tricky = {"z": 'a"b\\c\nd', "a": "😀 पेमेंट   ₹{x}", "m.k": "<b>{m}</b>", "ta": "இப்போது செலுத்தவும்"}
    (tmp_path / "tricky.json").write_text(json.dumps(tricky, ensure_ascii=False), encoding="utf-8")
    files = [REPO / "demo-app/locales/hi.json", REPO / "demo-app/locales/ta.json", tmp_path / "tricky.json"]
    run = subprocess.run(["node", str(REPO / "tests/digest_parity.mjs"), str(REPO / "demo-app/app.js"), *map(str, files)],
                         capture_output=True, text=True, check=True)
    from_browser = json.loads(run.stdout)
    for f in files:
        assert from_browser[str(f)] == strings_digest(json.loads(f.read_text(encoding="utf-8"))), f.name
