"""Inherited synthetic capture directories must survive both shared conftests."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("conftest", ["tests/conftest.py", "services/zoe-data/tests/conftest.py"])
def test_inherited_capture_canary_survives_member_erase(tmp_path, conftest):
    canary = tmp_path / "canary"
    canary.mkdir()
    for suffix in ("txt", "wav"):
        (canary / f"tg-canary.{suffix}").write_text("synthetic canary")
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text((REPO / conftest).read_text())
    (suite / "test_member.py").write_text('''
import os
from pathlib import Path
import pytest
pytestmark = pytest.mark.ci_safe
# Check at collection, BEFORE the member-erase module is imported. Even the
# negative control with the old conftest cannot delete the canary.
assert os.environ["ZOE_TELEGRAM_STT_CAPTURE_DIR"] != os.environ["CANARY_DIR"]
import local_text_logs as ltl

def test_member_erase(tmp_path):
    root = ltl.telegram_capture_dir()
    assert root.is_relative_to(tmp_path)
    root.mkdir(parents=True)
    (root / "tg-owned.txt").write_text("synthetic owned")
    (root / "tg-owned.wav").write_bytes(b"RIFF")
    ltl.erase_all_text()
    assert list(root.iterdir()) == []
''')
    env = dict(os.environ, ZOE_TELEGRAM_STT_CAPTURE_DIR=str(canary), CANARY_DIR=str(canary),
               ZOE_FORGET_REACH_EXTRA="on", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(REPO / "services/zoe-data"))
    # One small subprocess file, no full-service import and no recursive test.
    result = subprocess.run([sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q",
                             str(suite / "test_member.py")], env=env, cwd=suite,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert len(list(canary.iterdir())) == 2
    assert all(p.read_text() == "synthetic canary" for p in canary.iterdir())
