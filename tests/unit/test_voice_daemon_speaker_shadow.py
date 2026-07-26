"""W5 shadow mode for panel speaker ID (`scripts/setup/zoe_voice_daemon.py`).

Shadow-before-acting is the W5 gate (`docs/architecture/samantha-evolution-plan.md`
§W5): with ``SPEAKER_ID_SHADOW`` on (the DEFAULT) the daemon scores each turn
and logs it — journal line + one JSONL metrics row — but ``_speaker_claim_for_turn``
returns None, so the claim is never attached to the turn payload and the server
cannot act on identity during the shadow week. Metrics rows carry
ts/panel_id/user_id/score ONLY — never audio bytes or embeddings
(`docs/knowledge/biometric-retention-policy.md`).

The daemon imports hardware deps (pyaudio) at module level, so this test loads
it via importlib with those modules stubbed — no mic, no network, no models.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


@pytest.fixture(scope="module")
def daemon():
    """Import the daemon once with hardware deps stubbed."""
    stubs = {}
    for name in ("pyaudio",):
        if name not in sys.modules:
            stubs[name] = MagicMock()
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("zoe_voice_daemon_shadow_under_test", _DAEMON_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


@pytest.fixture()
def shadow_log(daemon, monkeypatch, tmp_path):
    path = tmp_path / "metrics" / "speaker_shadow_metrics.jsonl"
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW_LOG", str(path))
    return path


def _rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_shadow_mode_defaults_on(daemon):
    # Shadow-before-acting is the safe default: enabling SPEAKER_ID_ENABLED
    # alone must NOT let the server act on identity.
    assert daemon.SPEAKER_ID_SHADOW is True


def test_shadow_suppresses_claim_and_records_metrics_row(daemon, monkeypatch, shadow_log):
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", lambda wav: ("jason", 0.91237))

    assert daemon._speaker_claim_for_turn(b"wav-bytes") is None  # never attached

    rows = _rows(shadow_log)
    assert len(rows) == 1
    row = rows[0]
    # Retention policy: metadata only — exactly these keys, no audio/embeddings.
    assert set(row) == {"ts", "panel_id", "user_id", "score"}
    assert row["user_id"] == "jason"
    assert row["score"] == pytest.approx(0.9124, abs=1e-4)
    assert row["panel_id"] == daemon.PANEL_ID


def test_shadow_records_no_match_as_null_row(daemon, monkeypatch, shadow_log):
    # A no-match is a data point too (false-reject analysis needs it).
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", lambda wav: None)

    assert daemon._speaker_claim_for_turn(b"wav-bytes") is None

    rows = _rows(shadow_log)
    assert len(rows) == 1
    assert rows[0]["user_id"] is None
    assert rows[0]["score"] is None


def test_active_mode_returns_claim_and_writes_no_row(daemon, monkeypatch, shadow_log):
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", False)
    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", lambda wav: ("jason", 0.9))

    assert daemon._speaker_claim_for_turn(b"wav-bytes") == ("jason", 0.9)
    assert not shadow_log.exists()


def test_disabled_flag_short_circuits_without_scoring(daemon, monkeypatch, shadow_log):
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", False)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)

    def _boom(wav):  # pragma: no cover - must never run
        raise AssertionError("identify must not run when speaker ID is disabled")

    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", _boom)

    assert daemon._speaker_claim_for_turn(b"wav-bytes") is None
    assert not shadow_log.exists()


def test_metrics_write_failure_never_costs_the_turn(daemon, monkeypatch, tmp_path):
    # Point the log at an unwritable path (a directory) — the turn must survive.
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW_LOG", str(tmp_path))
    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", lambda wav: ("jason", 0.9))

    assert daemon._speaker_claim_for_turn(b"wav-bytes") is None
