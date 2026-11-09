"""CI wrapper: run the estate Transcript-check node harness.

Pins, against the REAL js/touch-transcript-check.js (DOM shim whose innerHTML throws; no browser, no network):
  * a refused read (guest / expired / wrong card) shows the sign-in prompt and NO sentence;
  * the sentence is a live-caption line (.cvf .cvl) with exactly one highlighted word, "N of M" progress, and no text input anywhere;
  * touching the word shows one chip per model word + Play it / None of these / Skip; touching a chip sends exactly that alternative;
  * None of these -> Say it (listen + poll) / Fix on phone; the screen resumes where it was left; the sentence leaves the DOM after the privacy window;
  * home.html wires the stage, loads the script first, unmounts on navigation, and opens on a voice "yes".
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_transcript_check_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_transcript_check.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
