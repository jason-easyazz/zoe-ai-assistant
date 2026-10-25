"""Speaker false-accept review tooling (scripts/maintenance/speaker_fa_review.py) - pure logic only.

Synthetic Pi and zoe-data log lines in the real formats, planted labelled sheets with a known answer, and a fake
embedder for the offline public-impostor check: no model, no household audio, no network.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys
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


sfa = _load("speaker_fa_review", "scripts/maintenance/speaker_fa_review.py")
pvl = sys.modules["panel_voice_log"]


def pi(ts: str, msg: str) -> str:
    return f"{ts},000 [voice-daemon] INFO {msg}"


PI_LOG = [
    # Resemblyzer era with no dual line: no ResNet34 meaning -> not a row
    pi("2026-10-14 10:00:00", "Wake word detected! Notifying Jetson and waking screen..."),
    pi("2026-10-14 10:00:03", "Recorded command: duration=2.00s chunks=25 stop=silence"),
    pi("2026-10-14 10:00:04", "Speaker claim: alex (0.810)"),
    # dual era: Resemblyzer acts, ResNet34 logged beside it
    pi("2026-10-16 06:49:45", "Wake word detected! Notifying Jetson and waking screen..."),
    pi("2026-10-16 06:49:48", "Recorded command: duration=4.56s chunks=57 stop=silence"),
    pi("2026-10-16 06:49:49", "Speaker ID (shadow): alex (0.719) - logged, not acted on"),
    pi("2026-10-16 06:49:49", "Speaker ID (dual, ResNet34): alex score=0.4167 accept=True source=local 192.2ms"),
    pi("2026-10-16 06:49:56", "turn_stream TTFA=0.59s transcript='how are you going'"),
    # ResNet34 acting: the claim line is the ResNet34 claim
    pi("2026-10-19 20:00:00", "Wake word detected! Notifying Jetson and waking screen..."),
    pi("2026-10-19 20:00:03", "Recorded command: duration=1.84s chunks=23 stop=silence"),
    pi("2026-10-19 20:00:04", "Speaker claim: alex (0.250)"),
    pi("2026-10-19 20:00:06", "turn_stream TTFA=0.70s transcript='what is on my calendar today please and tomorrow'"),
    pi("2026-10-19 20:05:00", "Wake word detected! Notifying Jetson and waking screen..."),
    pi("2026-10-19 20:05:03", "Recorded command: duration=3.20s chunks=40 stop=silence"),
    pi("2026-10-19 20:05:06", "turn_stream TTFA=0.70s transcript='turn the lights off'"),
]

APP_LOG = [
    "2026-10-16T06:49:49+0800 INFO     routers.voice_tts: voice/command panel=zoe-touch-pi session=s1 user=guest len=18 "
    "[identity: identified=None bound=None panel_recent=None scope_user=None has_scope=False]",
    "2026-10-19T20:00:05+0800 INFO     routers.voice_tts: voice speaker verdict panel=zoe-touch-pi verified=True",
    "2026-10-19T20:00:05+0800 INFO     routers.voice_tts: voice/command panel=zoe-touch-pi session=s2 user=alex len=40 "
    "[identity: identified=alex bound=None panel_recent=None scope_user=alex has_scope=True]",
    "2026-10-19T20:05:04+0800 INFO     routers.voice_tts: voice/command panel=panel-pf6 session=t user=guest len=5 "
    "[identity: identified=None bound=None panel_recent=None scope_user=None has_scope=False]",
    "2026-10-19T20:05:05+0800 INFO     routers.voice_tts: voice/command panel=zoe-touch-pi session=s2 user=guest len=19 "
    "[identity: identified=None bound=None panel_recent=None scope_user=None has_scope=False]",
]


def _rows():
    tns = pvl.turns(pvl.events(PI_LOG))
    return sfa.build_rows(tns, sfa.server_events(APP_LOG), {}, since=dt.datetime(2026, 10, 13))


def test_rows_take_the_resnet34_score_of_each_era_and_the_server_verdict():
    rows = _rows()
    assert [r["model"] for r in rows] == ["resnet34-dual", "resnet34", "resnet34"]
    dual, acting, unscored = rows
    assert (dual["score"], dual["claimed_user"], dual["group"], dual["threshold"]) == ("0.4167", "alex", "long", "0.265")
    assert dual["server_identified"] == "none"
    assert (acting["group"], acting["threshold"], acting["panel_accept"]) == ("short", "0.233", "yes")
    assert (acting["server_identified"], acting["server_verified"]) == ("alex", "yes")
    assert acting["transcript"] == "what is on my calendar today please and tomorrow"
    assert (unscored["score"], unscored["panel_accept"], unscored["server_identified"]) == ("", "no", "none")
    assert sfa.disagreements(rows) == []


def test_server_events_keep_only_this_panel():
    ev = sfa.server_events(APP_LOG)
    assert [k for _ts, k, _d in ev] == ["command", "verdict", "command", "command"]
    assert all(d.get("identified") in (None, "alex") for _ts, k, d in ev if k == "command")


def test_length_group_and_threshold_mirror_the_panel_module():
    pytest.importorskip("numpy")
    rn34 = _load("zoe_speaker_resnet34", "scripts/setup/zoe_speaker_resnet34.py")
    assert sfa.LIVE_THRESHOLDS == dict(rn34.THRESHOLDS["far1"]) and sfa.SHORT_MAX_S == rn34.SHORT_MAX_S
    for s in (None, 0.5, 2.0, 2.01, 9.0, float("nan")):
        assert sfa.length_group(s) == rn34.length_group(s)



def test_merge_never_loses_a_label():
    old = [dict(turn_id="a", label="me", notes="kitchen"), dict(turn_id="gone", label="not me", notes="")]
    new = [dict(turn_id="a", label="", notes="", score="0.3"), dict(turn_id="b", label="", notes="")]
    out = sfa.merge(old, new)
    assert [(r["turn_id"], r["label"]) for r in out] == [("a", "me"), ("b", ""), ("gone", "not me")]
    assert out[0]["score"] == "0.3" and out[0]["notes"] == "kitchen"


def test_merge_keeps_server_evidence_a_later_collection_cannot_find():
    old = [dict(turn_id="a", label="me", notes="", server_identified="alex", server_verified="yes", clip="/c/a.wav")]
    new = [dict(turn_id="a", label="", notes="", server_identified="none", server_verified="", clip="")]   # app log compressed
    out = sfa.merge(old, new)[0]
    assert (out["label"], out["server_identified"], out["server_verified"], out["clip"]) == ("me", "alex", "yes", "/c/a.wav")
    fresh = sfa.merge(old, [dict(turn_id="a", label="", notes="", server_identified="bob", server_verified="no", clip="")])[0]
    assert (fresh["server_identified"], fresh["server_verified"]) == ("bob", "no")      # new evidence still wins


def test_negative_cosine_scores_are_parsed():
    lines = [pi("2026-10-20 20:00:00", "Wake word detected! Notifying Jetson and waking screen..."),
             pi("2026-10-20 20:00:02", "Recorded command: duration=1.20s chunks=15 stop=silence"),
             pi("2026-10-20 20:00:02", "Speaker claim: alex (-0.050)"),
             pi("2026-10-20 20:00:03", "Speaker ID (dual, ResNet34): alex score=-0.1200 accept=False source=local 90ms"),
             pi("2026-10-20 20:00:04", "Speaker ID (shadow): alex (-0.0300)")]
    t = pvl.turns(pvl.events(lines))[0]
    assert (t.claim_score, t.dual_score) == (-0.05, -0.12)


def test_scoring_stops_when_the_night_pause_closes_mid_run():
    seen = []
    items = [dict(source="libri", variant="clean", path=f"c{i}.wav") for i in range(60)]
    def window(b):
        seen.append(b)
        return "brain window" if len(seen) > 1 else None
    res, skipped, stopped = sfa.score_impostors(items, ["P"], base=Path("/nonexistent"), window=window,
                                                embed_wav=lambda raw: (None, 0.0), match=lambda v, p: ("x", 0.0, 1))
    assert stopped == "brain window" and len(seen) == 2 and skipped == 25   # 25 attempted, then stopped at clip 25


def test_labels_normalise():
    assert [sfa.norm_label(x) for x in ("Me", " m ", "y", "NOT ME", "n", "no", "skip", "", "maybe")] == \
        ["me", "me", "me", "not me", "not me", "not me", "skip", "", ""]


def _sheet():
    rows = []
    for i in range(20):                                      # owner: 18 accepted, 1 below the bar, 1 unscored
        score = "" if i == 0 else ("0.240" if i == 1 else "0.500")
        rows.append(dict(turn_id=f"m{i}", label="me", claimed_user="alex", group="long", score=score))
    for i in range(10):                                      # not the owner: one clears the long bar
        rows.append(dict(turn_id=f"n{i}", label="not me", claimed_user="alex", group="long",
                         score="0.300" if i == 0 else "0.100"))
    rows.append(dict(turn_id="x", label="skip", claimed_user="alex", group="long", score="0.9"))
    rows.append(dict(turn_id="y", label="", claimed_user="alex", group="long", score="0.9"))
    return rows


def test_rates_at_the_live_thresholds_and_a_global_what_if():
    r = sfa.rates(_sheet(), owner="alex")
    assert r["owner"] == "alex" and (r["n_me"], r["n_not_me"]) == (20, 10)
    assert (r["false_rejects"], r["unscored_me"], r["false_accepts"]) == (2, 1, 1)
    assert r["frr"] == pytest.approx(0.10) and r["far"] == pytest.approx(0.10)
    lo, hi = r["far_ci"]
    assert lo < 0.10 < hi
    strict = sfa.rates(_sheet(), owner="alex", global_threshold=0.35)
    assert strict["false_accepts"] == 0 and strict["false_rejects"] == 2
    loose = sfa.rates(_sheet(), owner="alex", global_threshold=0.20)
    assert loose["false_accepts"] == 1 and loose["false_rejects"] == 1


def test_a_me_row_claimed_as_someone_else_is_a_false_reject():
    rows = [dict(turn_id="1", label="me", claimed_user="sam", group="long", score="0.9"),
            dict(turn_id="2", label="me", claimed_user="alex", group="long", score="0.9")]
    assert sfa.rates(rows, owner="alex")["false_rejects"] == 1
    with pytest.raises(ValueError):                          # never inferred from the claims being graded
        sfa.rates(rows, owner=None)


def test_the_player_must_be_an_audio_player():
    assert sfa.player_argv("aplay -q") == ["aplay", "-q"]
    for bad in ("rm -f", "", "sh -c", "/bin/rm"):
        with pytest.raises(SystemExit):
            sfa.player_argv(bad)


def test_disagreement_flags_a_panel_accept_the_server_did_not_honour():
    rows = [dict(turn_id="1", model="resnet34", panel_accept="yes", server_identified="none"),
            dict(turn_id="2", model="resnet34", panel_accept="yes", server_identified="alex"),
            dict(turn_id="3", model="resnet34-dual", panel_accept="yes", server_identified="none")]
    assert sfa.disagreements(rows) == ["1"]


def test_sheet_is_private_and_round_trips(tmp_path):
    with pytest.raises(SystemExit):
        sfa.private_dir(REPO / "scripts", root=REPO)
    with pytest.raises(SystemExit):
        sfa.private_dir(tmp_path / "speaker-fa")              # outside ~/.zoe/voice-gate
    assert sfa.DEFAULT_DIR.parent == sfa.VOICE_GATE_ROOT
    d = sfa.private_dir(tmp_path / "speaker-fa", root=tmp_path)
    rows = _rows()
    sfa.write_sheet(d / "sheet.csv", rows)
    assert (os.stat(d).st_mode & 0o777) == 0o700 and (os.stat(d / "sheet.csv").st_mode & 0o777) == 0o600
    back = sfa.read_sheet(d / "sheet.csv")
    assert [r["turn_id"] for r in back] == [r["turn_id"] for r in rows] and set(back[0]) == set(sfa.FIELDS)



def test_offline_fa_scores_each_public_clip_against_the_profile(tmp_path):
    items = []
    for i, (src, var, secs, score) in enumerate([("libri", "clean", 1.5, 0.24), ("libri", "farfield", 4.0, 0.24),
                                                  ("kokoro", "clean", 3.0, 0.40), ("kokoro", "farfield", 3.0, None)]):
        (tmp_path / f"c{i}.wav").write_bytes(f"{secs}|{score}".encode())
        items.append(dict(source=src, variant=var, path=f"c{i}.wav"))
    items.append(dict(source="libri", variant="clean", path="missing.wav"))

    def embed_wav(raw):
        secs, score = raw.decode().split("|")
        return (None if score == "None" else float(score)), float(secs)

    def match(vec, profiles):
        assert profiles == ["P"]
        return ("alex", vec, 1)

    res, skipped, stopped = sfa.score_impostors(items, ["P"], base=tmp_path, embed_wav=embed_wav, match=match,
                                       strict=dict(short=0.309, long=0.351))
    assert stopped is None
    assert skipped == 2                                   # an unembeddable clip and an unreadable one
    assert [(r["group"], r["accept"], r["accept_strict"]) for r in res] == \
        [("short", True, False), ("long", False, False), ("long", True, True)]
    summ = sfa.summarise_impostors(res)
    assert summ["all"]["accepted"] == 2 and summ["all"]["n"] == 3 and summ["all"]["accepted_strict"] == 1
    assert summ["source=libri"]["accepted"] == 1 and summ["group=short"]["n"] == 1
    assert summ["kokoro/clean"]["max"] == pytest.approx(0.40)



def test_profiles_come_from_the_pi_read_only_or_fail_loudly(tmp_path):
    seen = {}

    def ok(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(profiles=[dict(user_id="alex", model_id="m")])))
    assert sfa.fetch_profiles(None, runner=ok) == [dict(user_id="alex", model_id="m")]
    assert "ssh" in seen["cmd"] and seen["cmd"][-1].startswith("cat ")
    with pytest.raises(RuntimeError):
        sfa.fetch_profiles(None, runner=lambda cmd, **kw: SimpleNamespace(returncode=255, stdout=""))
    f = tmp_path / "p.json"
    f.write_text(json.dumps([dict(user_id="x")]))
    assert sfa.fetch_profiles(str(f)) == [dict(user_id="x")]


def test_offline_fa_is_guarded_like_a_voice_harness():
    no = lambda name, force=None: SimpleNamespace(allowed=False, reason="quiet hours")   # noqa: E731
    yes = lambda name, force=None: SimpleNamespace(allowed=True, reason="ok")           # noqa: E731
    clear = lambda budget: None   # noqa: E731 - the real brain-window check reads the wall clock
    assert sfa.guard(force=False, min_avail_mb=1500, check=no, mem=lambda: 9000, window=clear) == "quiet hours"
    assert "MemAvailable" in sfa.guard(force=False, min_avail_mb=1500, check=yes, mem=lambda: 900, window=clear)
    assert sfa.guard(force=False, min_avail_mb=1500, check=yes, mem=lambda: 1600, window=clear) is None


# ------------------------------------------------------------------ the brain window (01:50-04:55), fake clocks

import harness_quiet as _hq   # noqa: E402 - on sys.path via speaker_fa_review (scripts/maintenance)

_REAL_BWV = _hq.brain_window_verdict   # captured once: _clock re-patches the module attribute within a test


def _clock(monkeypatch, when: dt.datetime):
    """Pin harness_quiet's brain-window verdict to ``when`` (the real one reads the wall clock)."""
    import harness_quiet
    monkeypatch.setenv("ZOE_NIGHT_BUSY", "/nonexistent/night_busy.sh")   # no operator gate: fall back to the fixed window
    monkeypatch.setattr(harness_quiet, "brain_window_verdict",
                        lambda name, budget_min=45, now=None: _REAL_BWV(name, budget_min=budget_min, now=when))
    monkeypatch.setattr(harness_quiet, "check", lambda *a, **k: SimpleNamespace(allowed=True, reason="ok"))
    return harness_quiet


def _fa_args(tmp_path, n_items: int, **kw):
    (tmp_path / "manifest.json").write_text(json.dumps([dict(file=f"c{i}.wav") for i in range(n_items)]))
    base = dict(force=True, min_avail_mb=0.0, impostors=str(tmp_path), limit=0, budget_min=0, profiles=None,
                model_dir=str(tmp_path), dir=str(tmp_path / "out"))
    base.update(kw)
    return SimpleNamespace(**base)


def test_window_refusal_follows_the_shared_rule(monkeypatch):
    _clock(monkeypatch, dt.datetime(2026, 10, 20, 2, 0))
    assert "brain window" in sfa.window_refusal(0)
    _clock(monkeypatch, dt.datetime(2026, 10, 20, 23, 0))
    assert sfa.window_refusal(45) is None


def test_offline_fa_refuses_a_start_inside_the_window_even_with_force(monkeypatch, tmp_path, capsys):
    _clock(monkeypatch, dt.datetime(2026, 10, 20, 3, 0))
    monkeypatch.setattr(sfa, "mem_available_mb", lambda: 99999.0)
    assert sfa.cmd_offline_fa(_fa_args(tmp_path, 10)) == 3
    assert "brain window" in capsys.readouterr().err


def test_offline_fa_refuses_a_run_that_would_still_be_alive_at_0150(monkeypatch, tmp_path, capsys):
    _clock(monkeypatch, dt.datetime(2026, 10, 20, 1, 30))              # fine for 0 minutes; 1,672 clips is ~33 min
    monkeypatch.setattr(sfa, "mem_available_mb", lambda: 99999.0)
    assert sfa.cmd_offline_fa(_fa_args(tmp_path, 1672)) == 3
    assert "brain window" in capsys.readouterr().err
    # a small smoke run (--limit 5) fits before 01:50
    assert sfa.window_refusal(sfa.estimate_budget_min(5)) is None


# ------------------------------------------------------------------ the server join stops at the next panel turn

RETRY_PI = [
    pi("2026-10-20 20:00:00", "Wake word detected! Notifying Jetson and waking screen..."),
    pi("2026-10-20 20:00:02", "Recorded command: duration=1.20s chunks=15 stop=silence"),
    pi("2026-10-20 20:00:02", "Speaker claim: alex (0.250)"),
    pi("2026-10-20 20:00:04", "Empty transcript with no audio - retry chime."),
    pi("2026-10-20 20:00:15", "Wake word detected! Notifying Jetson and waking screen..."),
    pi("2026-10-20 20:00:18", "Recorded command: duration=3.00s chunks=38 stop=silence"),
    pi("2026-10-20 20:00:18", "Speaker claim: alex (0.400)"),
    pi("2026-10-20 20:00:21", "turn_stream TTFA=0.70s transcript='what is on today'"),
]
RETRY_APP = [
    "2026-10-20T20:00:19+0800 INFO     routers.voice_tts: voice speaker verdict panel=zoe-touch-pi verified=True",
    "2026-10-20T20:00:19+0800 INFO     routers.voice_tts: voice/command panel=zoe-touch-pi session=s2 user=alex len=17 "
    "[identity: identified=alex bound=None panel_recent=None scope_user=alex has_scope=True]",
]


def _join(pi_lines, app_lines):
    tns = pvl.turns(pvl.events(pi_lines))
    return sfa.build_rows(tns, sfa.server_events(app_lines), {}, since=dt.datetime(2026, 10, 13))


def test_a_turn_with_no_command_does_not_consume_the_next_turns_server_event():
    empty, retry = _join(RETRY_PI, RETRY_APP)
    assert (empty["outcome"], empty["server_identified"], empty["server_verified"]) == ("empty", "", "")
    assert (retry["outcome"], retry["server_identified"], retry["server_verified"]) == ("turn", "alex", "yes")


def test_a_command_too_short_for_the_panel_never_joins_a_server_event():
    lines = [pi("2026-10-20 20:00:00", "Wake word detected! Notifying Jetson and waking screen..."),
             pi("2026-10-20 20:00:01", "Recorded command: duration=0.20s chunks=3 stop=silence"),
             pi("2026-10-20 20:00:01", "Speaker claim: alex (0.250)"),
             pi("2026-10-20 20:00:01", "Command too short, ignoring.")] + RETRY_PI[4:]
    short, retry = _join(lines, RETRY_APP)
    assert (short["outcome"], short["server_identified"]) == ("too_short", "")
    assert retry["server_identified"] == "alex"


def test_each_turn_still_joins_its_own_command_in_order():
    pi_lines = RETRY_PI[:3] + [RETRY_PI[3].replace("Empty transcript with no audio - retry chime.",
                                                  "turn_stream TTFA=0.70s transcript='hello there'")] + RETRY_PI[4:]
    app = [RETRY_APP[0].replace("20:00:19", "20:00:03"), RETRY_APP[1].replace("20:00:19", "20:00:03").replace("alex", "bob")
           .replace("session=s2", "session=s1")] + RETRY_APP
    first, second = _join(pi_lines, app)
    assert (first["server_identified"], second["server_identified"]) == ("bob", "alex")


def test_join_server_honours_an_upper_bound():
    t0 = dt.datetime(2026, 10, 20, 20, 0, 0)
    sev = [(dt.datetime(2026, 10, 20, 20, 0, 19), "command", dict(user="alex", identified="alex"))]
    assert sfa.join_server(t0, sev, set()) == dict(identified="alex")                       # unbounded: the old behaviour
    assert sfa.join_server(t0, sev, set(), until=dt.datetime(2026, 10, 20, 20, 0, 15)) == {}


def _gate(tmp_path, monkeypatch, rc: int):
    g = tmp_path / "night_busy.sh"
    g.write_text(f"#!/bin/sh\n[ \"$1\" = voice ] || exit 9\nexit {rc}\n")
    g.chmod(0o755)
    monkeypatch.setenv("ZOE_NIGHT_BUSY", str(g))


def test_window_refusal_follows_the_measured_night_gate_not_the_fixed_span(monkeypatch, tmp_path):
    _clock(monkeypatch, dt.datetime(2026, 10, 20, 3, 40))        # inside the OLD fixed 01:50-04:55 span
    _gate(tmp_path, monkeypatch, 1)                              # the window finished: may run
    assert sfa.window_refusal(10) is None
    _clock(monkeypatch, dt.datetime(2026, 10, 20, 5, 30))        # past the old span, but the gate says the box is busy
    _gate(tmp_path, monkeypatch, 0)
    assert sfa.window_refusal(10)


def test_write_private_is_atomic_a_failed_write_keeps_the_previous_sheet(tmp_path, monkeypatch):
    f = tmp_path / "sheet.csv"
    sfa.write_private(f, "labels-v1\n")
    assert f.read_text() == "labels-v1\n" and (f.stat().st_mode & 0o777) == 0o600

    def boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr(sfa.os, "fsync", boom)
    with pytest.raises(OSError):
        sfa.write_private(f, "labels-v2-partial\n")
    assert f.read_text() == "labels-v1\n"                                  # nothing destroyed
    assert [p.name for p in tmp_path.iterdir()] == ["sheet.csv"]            # and no temp left behind
