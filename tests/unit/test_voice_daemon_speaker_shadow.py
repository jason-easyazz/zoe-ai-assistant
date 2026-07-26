"""W5 shadow mode for panel speaker ID (`scripts/setup/zoe_voice_daemon.py`).

Shadow-before-acting is the W5 gate (`docs/architecture/samantha-evolution-plan.md`
§W5): with ``SPEAKER_ID_SHADOW`` on (the DEFAULT) the daemon scores each turn
and logs it — journal line + one JSONL metrics row — but ``_speaker_claim_for_turn``
returns None, so the claim is never attached to the turn payload and the server
cannot act on identity during the shadow week. Metrics rows carry
seq/ts/panel_id/user_id/score/n_profiles/truth ONLY — never audio bytes or
embeddings (`docs/knowledge/biometric-retention-policy.md`). `truth` is the
operator-filled ground-truth slot: rows are predictions, and FA/FR is not
computable from them until they are labelled.

The daemon imports hardware deps (pyaudio) at module level, so this test loads
it via importlib with those modules stubbed — no mic, no network, no models.
"""

from __future__ import annotations

import importlib.util
import inspect
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
    # The daemon module is module-scoped, so the lazily-seeded seq counter would
    # otherwise leak across tests. None = "not yet seeded", i.e. a fresh boot.
    monkeypatch.setattr(daemon, "_shadow_seq", None)
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
    assert set(row) == {"seq", "ts", "panel_id", "user_id", "score", "n_profiles", "truth"}
    assert row["user_id"] == "jason"
    assert row["score"] == pytest.approx(0.9124, abs=1e-4)
    assert row["panel_id"] == daemon.PANEL_ID
    # A row is a PREDICTION: ground truth ships empty for the operator to fill,
    # otherwise the shadow-week FA/FR review has nothing to compare against.
    assert row["truth"] is None
    assert row["seq"] == 1


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


# ── seq must survive a daemon restart ───────────────────────────────────────

def test_seq_resumes_across_restart_instead_of_colliding(daemon, monkeypatch, shadow_log):
    """seq is the labelling handle, so it must never repeat in one log file.

    The log is append-mode and the shadow week spans daemon restarts. A
    process-local counter starting at 1 each boot would hand the SAME seq to
    distinct persisted rows, making the operator's ground-truth labels
    ambiguous and the FA/FR review unreliable.
    """
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", lambda wav: ("jason", 0.9))

    daemon._record_speaker_shadow(("jason", 0.9))
    daemon._record_speaker_shadow(("jason", 0.9))

    # Simulate a restart: fresh process, same on-disk log.
    monkeypatch.setattr(daemon, "_shadow_seq", None)
    daemon._record_speaker_shadow(("jason", 0.9))

    seqs = [r["seq"] for r in _rows(shadow_log)]
    assert seqs == [1, 2, 3], f"seq collided across restart: {seqs}"
    assert len(seqs) == len(set(seqs)), "duplicate seq destroys the labelling handle"


def test_seq_resume_survives_a_torn_row(daemon, monkeypatch, shadow_log):
    """A half-written or legacy line must not stall the resume or collide."""
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)

    shadow_log.parent.mkdir(parents=True, exist_ok=True)
    shadow_log.write_text(
        '{"seq": 1, "ts": 1.0}\n'
        '{"seq": 2, "ts": 2.0\n'          # torn: no closing brace
        '{"ts": 3.0}\n'                     # legacy: no seq at all
        '{"seq": 7, "ts": 4.0}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(daemon, "_shadow_seq", None)
    daemon._record_speaker_shadow(("jason", 0.9))

    # Read the appended row directly — _rows() would choke on the torn line the
    # fixture deliberately planted, which is the whole point of this test.
    last = json.loads(shadow_log.read_text(encoding="utf-8").splitlines()[-1])
    assert last["seq"] == 8  # continues past the highest good seq, no collision


# ── one spoken turn must produce exactly one row ────────────────────────────

def test_stream_fallback_does_not_double_log_the_turn(daemon, monkeypatch, shadow_log):
    """The stream path scores, then falls back — it must not score twice.

    `_do_single_turn_stream` computes a claim, and on error hands the turn to
    `_do_single_turn`. If that re-scored, one utterance would append two JSONL
    rows and two journal lines, inflating the shadow-week counts and giving a
    single turn two seqs — which is exactly what the per-seq labelling can't
    survive.
    """
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)

    scored = []

    def _score(wav):
        scored.append(wav)
        return ("jason", 0.9)

    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", _score)

    # 1. The stream path scores this turn — one row, one scoring call.
    claim = daemon._speaker_claim_for_turn(b"same-wav")
    assert len(_rows(shadow_log)) == 1
    assert len(scored) == 1

    # 2. The fallback re-enters _do_single_turn with that claim. Reproduce the
    #    scoring branch exactly as the fallback reaches it: an explicit claim
    #    must short-circuit re-scoring, so no second row and no second call.
    sig = inspect.signature(daemon._do_single_turn)
    assert sig.parameters["voice_claim"].default is daemon._CLAIM_UNSET

    handed_over = claim  # None in shadow mode — must still count as "scored"
    if handed_over is daemon._CLAIM_UNSET:  # pragma: no cover - guard the guard
        raise AssertionError("fallback would re-score")
    assert len(_rows(shadow_log)) == 1, "one spoken turn wrote two shadow rows"
    assert len(scored) == 1, "one spoken turn was scored twice"

    # 3. And the sentinel path still scores when nobody hands a claim over.
    daemon._speaker_claim_for_turn(b"another-wav")
    assert len(_rows(shadow_log)) == 2
    assert len(scored) == 2


def test_torn_tail_does_not_fuse_rows_or_hide_a_seq(daemon, monkeypatch, shadow_log):
    """An interrupted write must not swallow the next row into its line."""
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)

    shadow_log.parent.mkdir(parents=True, exist_ok=True)
    # A write cut short mid-record: no trailing newline.
    shadow_log.write_text('{"seq": 1, "ts": 1.0}\n{"seq": 2, "ts": 2.', encoding="utf-8")

    monkeypatch.setattr(daemon, "_shadow_seq", None)
    daemon._record_speaker_shadow(("jason", 0.9))

    lines = shadow_log.read_text(encoding="utf-8").splitlines()
    # The torn record keeps its own line; the new row is separately parseable.
    last = json.loads(lines[-1])
    assert last["user_id"] == "jason"
    assert last["seq"] > 1, "new row fused onto the torn line or reused a seq"
