"""W5 shadow mode for panel speaker ID (`scripts/setup/zoe_voice_daemon.py`).

Shadow-before-acting is the W5 gate (`docs/architecture/samantha-evolution-plan.md`
§W5): with ``SPEAKER_ID_SHADOW`` on (the DEFAULT) the daemon scores each turn
and logs it — journal line + one JSONL metrics row — but ``_speaker_claim_for_turn``
returns None, so the claim is never attached to the turn payload and the server
cannot act on identity during the shadow week. Metrics rows carry
boot/seq/ts/panel_id/user_id/score/n_profiles/truth ONLY — never audio bytes or
embeddings (`docs/knowledge/biometric-retention-policy.md`). `truth` is the
operator-filled ground-truth slot: rows are predictions, and FA/FR is not
computable from them until they are labelled. The labelling handle is the
(boot, seq) PAIR — `boot` is fresh per daemon start, so uniqueness never
depends on successfully re-reading the existing log.

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
    # The daemon module is module-scoped, so the process counter would otherwise
    # leak across tests. A fresh count == a fresh daemon start.
    import itertools
    monkeypatch.setattr(daemon, "_shadow_seq", itertools.count(1))
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
    assert set(row) == {"boot", "seq", "ts", "panel_id", "user_id", "score", "n_profiles", "source", "truth"}
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


# ── the labelling handle must never repeat ──────────────────────────────────

def test_handle_is_unique_across_restarts_without_reading_the_log(daemon, monkeypatch, shadow_log):
    """(boot, seq) must not collide even when the existing log is unreadable.

    Deriving the handle by re-scanning the file made every read failure — torn
    tail, invalid UTF-8, transient OSError — silently restart the counter and
    reuse handles already on disk. `boot` is per-process, so uniqueness is
    structural: no read happens, so no read can fail into a collision.
    """
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)

    daemon._record_speaker_shadow(("jason", 0.9))
    daemon._record_speaker_shadow(("jason", 0.9))
    first_boot = daemon._SHADOW_BOOT_ID

    # Restart with the log deliberately unreadable as text (invalid UTF-8) —
    # the old resume path seeded from 1 here and reused handles.
    with open(shadow_log, "ab") as f:
        f.write(b"\xff\xfe not utf-8 at all\n")
    import itertools
    monkeypatch.setattr(daemon, "_shadow_seq", itertools.count(1))
    monkeypatch.setattr(daemon, "_SHADOW_BOOT_ID", "restart2")
    daemon._record_speaker_shadow(("jason", 0.9))

    rows = [json.loads(l) for l in shadow_log.read_text(encoding="utf-8", errors="replace").splitlines()
            if l.strip().startswith("{")]
    handles = [(r["boot"], r["seq"]) for r in rows]
    assert len(handles) == len(set(handles)), f"handle collision: {handles}"
    assert (first_boot, 1) in handles and ("restart2", 1) in handles


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


def test_torn_tail_does_not_fuse_rows(daemon, monkeypatch, shadow_log):
    """An interrupted write must not swallow the next row into its line.

    Handle uniqueness no longer depends on this (see `boot`), but a fused line
    still costs the analysis BOTH records — the torn one and the good one that
    landed on top of it. Each row must stay independently parseable.
    """
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)

    shadow_log.parent.mkdir(parents=True, exist_ok=True)
    # A write cut short mid-record: no trailing newline.
    shadow_log.write_text('{"seq": 1, "ts": 1.0}\n{"seq": 2, "ts": 2.', encoding="utf-8")

    daemon._record_speaker_shadow(("jason", 0.9))

    lines = shadow_log.read_text(encoding="utf-8").splitlines()
    # The torn record keeps its own line; the new row is separately parseable.
    assert lines[-2] == '{"seq": 2, "ts": 2.', "torn record was mutated"
    last = json.loads(lines[-1])
    assert last["user_id"] == "jason"
    assert last["boot"] == daemon._SHADOW_BOOT_ID


# ── the gate must fail SAFE, not open ───────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected_shadow",
    [
        (None, True),        # unset — the documented default
        ("", True),          # empty `SPEAKER_ID_SHADOW=` in .env.voice
        ("   ", True),       # whitespace-only
        ("maybe", True),     # typo / unparseable
        ("TRUE", True),
        ("false", False),    # only an EXPLICIT false-y value lifts the gate
        ("0", False),
        ("no", False),
        ("OFF", False),
    ],
)
def test_shadow_gate_fails_safe_on_bad_env(monkeypatch, raw, expected_shadow):
    """An empty or unparseable value must NOT lift the W5 gate.

    This is a safety gate, not a feature flag: reading a half-edited
    `SPEAKER_ID_SHADOW=` as "off" would start attaching voice_user_id/voice_score
    to live turns with no shadow metrics — the exact ungated state W5 forbids.
    """
    import importlib.util as _ilu

    if raw is None:
        monkeypatch.delenv("SPEAKER_ID_SHADOW", raising=False)
    else:
        monkeypatch.setenv("SPEAKER_ID_SHADOW", raw)

    stubs = {n: MagicMock() for n in ("pyaudio",) if n not in sys.modules}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = _ilu.spec_from_file_location("zoe_voice_daemon_env_probe", _DAEMON_PATH)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.SPEAKER_ID_SHADOW is expected_shadow
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


# ── the row and the journal must not overstate what happened ────────────────

def test_journal_does_not_claim_logged_when_the_write_failed(daemon, monkeypatch, tmp_path, caplog):
    """A failed metrics write must not produce a journal line saying "logged".

    The write is best-effort by design (it must never cost the turn), but the
    journal claiming the turn was logged while no row landed makes the two
    disagree and silently over-counts scored turns in the W5 review.
    """
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW_LOG", str(tmp_path))  # a dir: unwritable
    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", lambda wav: ("jason", 0.9))

    with caplog.at_level("INFO"):
        assert daemon._speaker_claim_for_turn(b"wav") is None  # turn survives
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "NOT logged" in msgs
    assert "0.900 — logged" not in msgs


def test_record_reports_whether_the_row_landed(daemon, monkeypatch, tmp_path, shadow_log):
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    assert daemon._record_speaker_shadow(("jason", 0.9)) is True

    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW_LOG", str(tmp_path))  # unwritable
    assert daemon._record_speaker_shadow(("jason", 0.9)) is False


def test_n_profiles_is_null_for_a_server_resolved_claim(daemon, monkeypatch, shadow_log):
    """n_profiles describes the LOCAL cache — it must not be stated for a
    server-fallback claim, which was scored against a profile set this process
    never saw. A row showing n_profiles=0 beside a real user_id/score would
    contradict the field's documented meaning during FA/FR review."""
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)

    daemon._claim_ctx.source = "server"
    daemon._record_speaker_shadow(("jason", 0.88))
    row = _rows(shadow_log)[-1]
    assert row["source"] == "server"
    assert row["n_profiles"] is None
    assert row["user_id"] == "jason"

    # ...and a local claim still reports the cache size it was scored against.
    daemon._claim_ctx.source = "local"
    with daemon._profile_cache_lock:
        daemon._profile_cache["profiles"] = [{"user_id": "jason"}]
    daemon._record_speaker_shadow(("jason", 0.91))
    row = _rows(shadow_log)[-1]
    assert row["source"] == "local"
    assert row["n_profiles"] == 1


def test_missing_encoder_is_not_recorded_as_a_no_match(daemon, monkeypatch, shadow_log):
    """A panel without resemblyzer must not produce a week of fake no-matches.

    `_get_voice_encoder()` returns None when the dependency is missing, and the
    installer ships SPEAKER_ID_ENABLED=true. If that landed in the artifact as
    an ordinary null-user row, the shadow week would look like "the model never
    matched anyone" when in truth nothing was ever scored — and the operator
    would tune a threshold against data that does not exist.
    """
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    monkeypatch.setattr(daemon, "_get_voice_encoder", lambda: None)

    assert daemon._identify_speaker_from_wav(b"wav") is None
    daemon._record_speaker_shadow(None)

    row = _rows(shadow_log)[-1]
    assert row["source"] == "encoder_unavailable"
    assert row["user_id"] is None
    # ...and a genuine scored no-match still reads differently.
    daemon._claim_ctx.source = "local"
    daemon._record_speaker_shadow(None)
    assert _rows(shadow_log)[-1]["source"] == "local"


def test_journal_says_not_scored_when_the_encoder_is_missing(daemon, monkeypatch, shadow_log, caplog):
    """"No match" must not be printed when nothing was embedded.

    The row already carries source='encoder_unavailable'; a journal line saying
    "no match" alongside it reads as a real scored result and undoes the whole
    never-scored / scored-no-match distinction the W5 review depends on.
    """
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    monkeypatch.setattr(daemon, "_get_voice_encoder", lambda: None)

    with caplog.at_level("DEBUG"):
        assert daemon._speaker_claim_for_turn(b"wav") is None
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "NOT SCORED" in msgs
    assert "no match" not in msgs


def test_journal_failure_cannot_cause_a_second_row(daemon, monkeypatch, shadow_log):
    """A raise AFTER the row is written must not make the caller re-score.

    The stream path swallows exceptions from _speaker_claim_for_turn and leaves
    voice_claim at the sentinel, so its fallback would score again — a second
    metrics row and journal line for ONE spoken turn. Everything after the write
    is therefore contained.
    """
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", lambda wav: ("jason", 0.9))

    boom = MagicMock(side_effect=RuntimeError("journal exploded"))
    monkeypatch.setattr(daemon.log, "info", boom)

    # Must return normally (not raise), having written exactly one row.
    assert daemon._speaker_claim_for_turn(b"wav") is None
    assert len(_rows(shadow_log)) == 1
