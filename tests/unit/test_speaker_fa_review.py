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
    # dual era: Resemblyzer acts, ResNet34 logged beside it
    pi("2026-10-16 06:49:45", "Wake word detected! Notifying Jetson and waking screen..."),
    pi("2026-10-16 06:49:48", "Recorded command: duration=4.56s chunks=57 stop=silence"),
    pi("2026-10-16 06:49:49", "Speaker ID (shadow): alex (0.719) - logged, not acted on"),
    pi("2026-10-16 06:49:49", "Speaker ID (dual, ResNet34): alex score=0.4167 accept=True source=local 192.2ms"),
    pi("2026-10-16 06:49:56", "turn_stream TTFA=0.59s transcript='how are you going'"),
    # Resemblyzer era with no dual line: no ResNet34 meaning -> not a row
    pi("2026-10-14 10:00:00", "Wake word detected! Notifying Jetson and waking screen..."),
    pi("2026-10-14 10:00:03", "Recorded command: duration=2.00s chunks=25 stop=silence"),
    pi("2026-10-14 10:00:04", "Speaker claim: alex (0.810)"),
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

    res, skipped = sfa.score_impostors(items, ["P"], base=tmp_path, embed_wav=embed_wav, match=match,
                                       strict=dict(short=0.309, long=0.351))
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
    assert sfa.guard(force=False, min_avail_mb=1500, check=no, mem=lambda: 9000) == "quiet hours"
    assert "MemAvailable" in sfa.guard(force=False, min_avail_mb=1500, check=yes, mem=lambda: 900)
    assert sfa.guard(force=False, min_avail_mb=1500, check=yes, mem=lambda: 1600) is None
