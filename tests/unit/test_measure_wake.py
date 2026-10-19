"""Wake-word FAR/FRR instrument (scripts/perf/measure_wake.py + scripts/lib/panel_voice_log.py) - pure logic only.

No model, no household audio: frame-score traces are planted, the panel log is synthetic text in the daemon's own line
formats, and the corpus join runs on generated tones in tmp_path. Each instrument is checked against a planted answer
(a trace that must wake, one that must not, the daemon's window-expiry quirk), not assumed.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import struct
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mw = _load("measure_wake", "scripts/perf/measure_wake.py")
pvl = sys.modules["panel_voice_log"]


def line(ts: str, msg: str) -> str:
    return f"{ts},000 [voice-daemon] INFO {msg}"


# ------------------------------------------------------------------ the daemon's confirmation state machine

def test_three_hits_inside_the_window_wake_once():
    frames = [0.0] * 5 + [0.9, 0.9, 0.9] + [0.0] * 5
    assert mw.confirmed_wakes(frames, 0.45) == [pytest.approx(7 * 0.08)]


def test_two_hits_never_wake_and_a_threshold_above_the_peak_never_wakes():
    assert mw.confirmed_wakes([0.0, 0.9, 0.9, 0.0, 0.0], 0.45) == []
    assert mw.confirmed_wakes([0.6] * 20, 0.61) == []


def test_partial_count_expires_from_its_first_hit_not_a_sliding_window():
    # hits at 0.00, 0.96 then a gap, then 1.52 and 1.60: hits 2-4 sit inside 1.0 s of each other, but the daemon's
    # count started at 0.00, expired at 1.04 and restarted at 1.52 - so it NEVER wakes. A sliding window would.
    f = [0.0] * 25
    for i in (0, 12, 19, 20):
        f[i] = 0.9
    assert mw.confirmed_wakes(f, 0.45) == []
    f[21] = 0.9                                  # a third hit after the restart does wake
    assert len(mw.confirmed_wakes(f, 0.45)) == 1


def test_min_interval_suppresses_back_to_back_wakes():
    burst = [0.9, 0.9, 0.9, 0.0]
    frames = burst * 10                          # a confirm every 4 frames (0.32 s) for 3.2 s
    assert len(mw.confirmed_wakes(frames, 0.45, min_interval_s=5.0)) == 1
    assert len(mw.confirmed_wakes(frames, 0.45, min_interval_s=0.0)) == 10


# ------------------------------------------------------------------ curve and recommendation

def _clip(name, peak, n_hits=4, seconds=3.0):
    return dict(set=name, seconds=seconds, frames=[0.0] * 10 + [peak] * n_hits + [0.0] * 10)


def _planted():
    pos = [_clip("positive", round(0.50 + 0.01 * i, 2)) for i in range(40)]           # peaks 0.50 .. 0.89
    neg = [_clip("libri", 0.30, seconds=60.0) for _ in range(9)] + [_clip("libri", 0.47, seconds=60.0)]
    tv = [_clip("tv", 0.20), _clip("tv", 0.52)]
    return pos + neg + tv


def test_curve_counts_frr_fa_per_hour_and_tv():
    rows = mw.curve(_planted(), (0.45, 0.48, 0.60))
    r45, r48, r60 = rows
    assert r45["frr"] == 0.0 and r60["frr"] == pytest.approx(10 / 40)
    assert r45["sets"]["libri"]["wakes"] == 1 and r45["pooled_fa_per_hour"] == pytest.approx(1 / (600 / 3600))
    assert r48["sets"]["libri"]["wakes"] == 0
    assert r45["sets"]["tv"]["clips_woke"] == 1 and r60["sets"]["tv"]["clips_woke"] == 0


def test_recommend_is_the_highest_threshold_within_budget_with_tv_clean():
    rows = mw.curve(_planted(), mw.GRID)
    rec = mw.recommend(rows, live_threshold=0.45, frr_budget=0.05)
    # 2 of 40 positives lost (5 %) at 0.52 -> highest within 5 pp is 0.52; TV's 0.52 clip wakes at <= 0.52, so 0.53
    # is the first TV-clean threshold, and it loses 3/40 = 7.5 % -> nothing qualifies except the budget is exceeded.
    assert rec["threshold"] is None and "TV" in rec["reason"]
    rec10 = mw.recommend(rows, live_threshold=0.45, frr_budget=0.10)
    assert rec10["threshold"] == pytest.approx(0.54)            # 0.54 loses 4/40 = 10 %; 0.55 loses 12.5 %
    assert rec10["options"]["0.05"] is None
    # TV-clean is out of reach at 5 pp, so the threshold-only answer is reported beside the refusal
    assert rec["threshold_only"]["0.05"]["threshold"] == pytest.approx(0.52)
    assert rec["threshold_only"]["0.05"]["tv"] == "1/2"
    assert "Threshold-only options" in mw.render_recommendation(rec)


def test_recommend_cancels_the_replays_own_losses_and_refuses_tiny_sets():
    pos = [_clip("positive", 0.30)] * 10 + [_clip("positive", 0.80)] * 30   # 25 % lost even at the live threshold
    pos = pos + [_clip("tv", 0.10)]
    rec = mw.recommend(mw.curve(pos, mw.GRID), live_threshold=0.45, frr_budget=0.0)
    assert rec["live_frr"] == pytest.approx(0.25) and rec["threshold"] == pytest.approx(0.80)
    small = mw.recommend(mw.curve(pos[:5] + pos[-1:], mw.GRID), live_threshold=0.45)
    assert small["threshold"] is None and "too few" in small["reason"]


def test_no_recommendation_without_the_tv_set():
    # zero TV clips measured is NOT "TV 0/N": a missing or misspelled directory must not yield a recommendation
    pos = [_clip("positive", 0.80)] * 40
    rec = mw.recommend(mw.curve(pos, mw.GRID), live_threshold=0.45)
    assert rec["threshold"] is None and "TV" in rec["reason"]


def test_render_does_not_crash_on_partial_sets():
    rows = mw.curve(_planted(), mw.GRID)
    md = mw.render_curve(rows) + mw.render_peaks(_planted())
    assert "0.45" in md and "positive" in md
    assert "No recommendation" in mw.render_recommendation(dict(threshold=None, reason="x"))


# ------------------------------------------------------------------ the panel log (synthetic lines, daemon formats)

LOG = [
    line("2026-10-16 21:00:00", "wakeword near-miss: max_score=0.4400 (need 0.4500) - lower WAKEWORD_THRESHOLD"),
    line("2026-10-16 21:00:05", "Wake word detected! Notifying Jetson and waking screen..."),   # retry proxy (5 s)
    line("2026-10-16 21:00:08", "Recorded command: duration=3.20s chunks=40 stop=silence"),
    line("2026-10-16 21:00:09", "Speaker claim: alex (0.312)"),
    line("2026-10-16 21:00:12", "turn_stream TTFA=0.59s transcript='what time is it'"),
    line("2026-10-16 21:00:13", "Follow-up speech detected (VAD=0.51), recording..."),
    line("2026-10-16 21:00:15", "Recorded follow-up: duration=1.92s chunks=24 stop=silence"),
    line("2026-10-16 21:00:16", "Speaker ID (dual, ResNet34): alex score=0.3932 accept=True source=local 97.3ms"),
    line("2026-10-16 21:00:20", "turn_stream TTFA=0.70s transcript='thanks'"),
    line("2026-10-17 02:10:00", "Wake word detected! Notifying Jetson and waking screen..."),
    line("2026-10-17 02:10:03", "Recorded command: duration=1.60s chunks=20 stop=silence"),
    line("2026-10-17 02:10:04", "Empty transcript with no audio - retry chime."),
    line("2026-10-17 09:00:00", "wakeword near-miss: max_score=0.2000 (need 0.4500) - lower WAKEWORD_THRESHOLD"),
    "a line in no known format",
]


def test_turns_join_trigger_recording_speaker_and_outcome():
    tns = pvl.turns(pvl.events(LOG))
    assert [(t.trigger, t.outcome) for t in tns] == [("wake", "turn"), ("followup", "turn"), ("wake", "empty")]
    w = tns[0]
    assert (w.duration_s, w.claim_user, w.claim_score, w.transcript) == (3.2, "alex", 0.312, "what time is it")
    f = tns[1]
    assert (f.dual_user, f.dual_score, f.dual_accept, f.claim_score) == ("alex", 0.3932, True, None)


def test_nearmiss_summary_counts_band_outcomes_night_and_retry():
    s = mw.nearmiss_summary(pvl.events(LOG), since=dt.datetime(2026, 10, 16))
    assert s["near_misses"] == 2 and s["max"] == 0.44 and s["need"] == [0.45]
    assert s["at_or_above"]["0.44"] == 1 and s["wakes"] == 2
    assert s["wake_outcomes"] == {"turn": 1, "empty": 1}
    assert s["night_wakes_01_05"] == 1 and s["retry_after_near_miss"] == 1 and s["ramp_into_wake"] == 0
    later = mw.nearmiss_summary(pvl.events(LOG), since=dt.datetime(2026, 10, 17, 3))
    assert later["near_misses"] == 1 and later["wakes"] == 0
    assert "0.44" in mw.render_nearmiss(s)


def _tone_wav(path: Path, seconds: float, ts: dt.datetime) -> None:
    n = int(16000 * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", (i * 37) % 2000 - 1000) for i in range(n)))
    os.utime(path, (ts.timestamp(), ts.timestamp()))


def test_corpus_join_matches_by_time_and_exact_duration(tmp_path):
    _tone_wav(tmp_path / "210008_500.wav", 3.20, dt.datetime(2026, 10, 16, 21, 0, 8, 500000))   # the wake turn
    _tone_wav(tmp_path / "210015_400.wav", 1.92, dt.datetime(2026, 10, 16, 21, 0, 15, 400000))  # the follow-up
    _tone_wav(tmp_path / "210009_000.wav", 2.50, dt.datetime(2026, 10, 16, 21, 0, 9))           # wrong length
    _tone_wav(tmp_path / "021003_100.wav", 1.60, dt.datetime(2026, 10, 17, 2, 10, 3, 100000))   # the empty wake
    (tmp_path / "quarantine-x").mkdir()
    _tone_wav(tmp_path / "quarantine-x" / "210008_600.wav", 3.20, dt.datetime(2026, 10, 16, 21, 0, 8))
    sets = mw.panel_clips(LOG, str(tmp_path), positives_since=dt.datetime(2026, 7, 16))
    assert [Path(x).name for x in sets["positive"]] == ["210008_500.wav", "021003_100.wav"]
    assert [Path(x).name for x in sets["followup"]] == ["210015_400.wav"]
    old = mw.panel_clips(LOG, str(tmp_path), positives_since=dt.datetime(2026, 10, 17))
    assert old["excluded_old"] == 1 and len(old["positive"]) == 1


def test_clip_time_rejects_a_name_far_from_its_mtime():
    ts = dt.datetime(2026, 10, 16, 23, 59, 59)
    assert pvl.clip_time("235959_100.wav", ts.timestamp()) is not None
    assert pvl.clip_time("000001_000.wav", (ts + dt.timedelta(seconds=3)).timestamp()).day == 17
    assert pvl.clip_time("120000_000.wav", ts.timestamp()) is None
    assert pvl.clip_time("notaclip.wav", ts.timestamp()) is None


def test_fetch_lines_fails_loudly_when_the_pi_is_unreadable():
    def runner(cmd, **kw):
        assert cmd[:3] == ["timeout", "30", "ssh"] and "BatchMode=yes" in cmd
        return SimpleNamespace(returncode=255, stdout="")
    with pytest.raises(RuntimeError):
        pvl.fetch_lines(None, runner=runner)



# ------------------------------------------------------------------ the scorer, the guards, the pins

class _FakeModel:
    """Stands in for openwakeword.model.Model: records the call contract, scores = chunk energy."""
    instances: list = []

    def __init__(self, **kw):
        self.kw, self.resets, self.chunks = kw, 0, []
        _FakeModel.instances.append(self)

    def reset(self):
        import numpy as np
        self.resets += 1
        self.noise = float(np.random.randint(0, 1000))           # the draw OWW reset makes from np.random

    def predict(self, chunk):
        self.chunks.append(len(chunk))
        return {"hey_zoe": float(abs(chunk.astype("int64")).mean()) / 1000.0}



def test_scorer_feeds_daemon_sized_chunks_with_lead_and_tail_and_is_deterministic(tmp_path):
    np = pytest.importorskip("numpy")
    sc = mw.OwwScorer(tmp_path / "hey_zoe.onnx", tmp_path, model_factory=_FakeModel)
    m = _FakeModel.instances[-1]
    assert m.kw["inference_framework"] == "onnx" and m.kw["wakeword_models"] == [str(tmp_path / "hey_zoe.onnx")]
    assert m.kw["melspec_model_path"].endswith("melspectrogram.onnx")
    x = np.full(16000, 500, dtype=np.int16)                      # 1 s of signal
    f1 = sc.frames(x, lead_s=2.0, tail_s=1.0)
    noise1 = m.noise
    f2 = sc.frames(x, lead_s=2.0, tail_s=1.0)
    assert f1 == f2 and m.noise == noise1 and m.resets == 2       # seeded before every reset
    assert set(m.chunks) == {1280} and len(f1) == 50              # (2 + 1 + 1) s / 80 ms
    assert max(f1[:25]) == 0.0 and f1[25] == pytest.approx(0.5)   # the lead-in is silence, then the clip


def test_guard_refuses_quiet_hours_and_low_memory():
    no = lambda name, force=None: SimpleNamespace(allowed=False, reason="quiet hours")   # noqa: E731
    yes = lambda name, force=None: SimpleNamespace(allowed=True, reason="ok")           # noqa: E731
    assert mw.guard(force=False, min_avail_mb=1500, check=no, mem=lambda: 9999) == "quiet hours"
    assert "MemAvailable" in mw.guard(force=False, min_avail_mb=1500, check=yes, mem=lambda: 1200)
    assert mw.guard(force=False, min_avail_mb=1500, check=yes, mem=lambda: 2000) is None
    seen = {}

    def spy(name, force=None):
        seen["force"] = force
        return SimpleNamespace(allowed=True, reason="")
    mw.guard(force=True, min_avail_mb=0, check=spy, mem=lambda: None)
    assert seen["force"] is True


def test_guard_uses_the_shared_quiet_hours_rule(monkeypatch):
    import harness_quiet                                   # scripts/maintenance, on the path via measure_wake
    monkeypatch.delenv("ZOE_HARNESS_FORCE", raising=False)
    monkeypatch.delenv("ZOE_HARNESS_LANDING", raising=False)
    monkeypatch.setattr(harness_quiet, "panel_quiet", lambda now=None, **kw: True)
    monkeypatch.setattr(harness_quiet, "in_waking_hours", lambda now=None: True)
    assert "quiet hours" in mw.guard(force=False, min_avail_mb=0, mem=lambda: None)
    assert mw.guard(force=True, min_avail_mb=0, mem=lambda: None) is None
    monkeypatch.setattr(harness_quiet, "in_waking_hours", lambda now=None: False)
    assert mw.guard(force=False, min_avail_mb=0, mem=lambda: None) is None


def test_verify_pinned_against_a_lock(tmp_path):
    f = tmp_path / "hey_zoe.onnx"
    f.write_bytes(b"not a real model")
    lock = tmp_path / "models.lock"
    lock.write_text(json.dumps(dict(models=[dict(host="panel", path=".zoe-voice/hey_zoe.onnx", sha256=mw.sha256(f))])))
    assert mw.verify_pinned(f, lock) is None
    f.write_bytes(b"other bytes")
    assert "!=" in mw.verify_pinned(f, lock)
    assert "not found" in mw.verify_pinned(tmp_path / "missing.onnx", lock)


def test_the_repo_lock_pins_all_three_wake_files():
    for name in ("hey_zoe.onnx", "melspectrogram.onnx", "embedding_model.onnx"):
        entry = mw.lock_entry(name)
        assert entry and len(entry["sha256"]) == 64


def test_private_outputs_land_only_under_the_voice_gate_root(tmp_path):
    with pytest.raises(SystemExit):
        mw._private_dir(REPO / "docs", root=REPO)            # inside the checkout, even if named as the root
    with pytest.raises(SystemExit):
        mw._private_dir(tmp_path / "elsewhere")              # outside ~/.zoe/voice-gate
    assert mw.DEFAULT_OUT.parent == mw.VOICE_GATE_ROOT
    d = mw._private_dir(tmp_path / "wake", root=tmp_path)
    (tmp_path / "victim").write_text("keep")
    os.symlink(tmp_path / "victim", d / "link.json")
    with pytest.raises(OSError):
        mw._write_private(d / "link.json", "{}")             # never written through a symlink
    assert (tmp_path / "victim").read_text() == "keep"
    mw._write_private(d / "x.json", "{}")
    assert (os.stat(d).st_mode & 0o777) == 0o700
    assert (os.stat(d / "x.json").st_mode & 0o777) == 0o600
