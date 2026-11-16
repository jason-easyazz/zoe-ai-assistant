"""The panel half of listen pace (scripts/setup/zoe_voice_listen_pace.py): a handed profile can never build a tail shorter than
the fastest level, anything malformed is today's tails, a wake drops the window profile, shadow never applies, and the outcome
body and the PACE line carry numbers and enums only. Synthetic values only; the cache lives in tmp_path.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_MOD = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_listen_pace.py"
_spec = importlib.util.spec_from_file_location("zoe_voice_listen_pace_under_test", _MOD)
P = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = P
_spec.loader.exec_module(P)

TOK = "tok_ABCDEFGH12"
PANEL = {"deep": 640, "quiet": 800, "clean": 640, "level": -2}
NEXT = {"deep": 720, "quiet": 880, "clean": 0, "level": 1, "ttl_s": 30}


def frame(**listen):
    base = {"v": 1, "apply": True, "panel": dict(PANEL), "token": TOK}
    base.update(listen)
    return {"type": "done", "listen": base}


def test_stdlib_only():
    import ast
    tree = ast.parse(_MOD.read_text())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= {"__future__", "json", "math", "os", "re", "tempfile", "dataclasses", "pathlib", "typing"}


@pytest.mark.parametrize("bad", [
    {"deep": 560, "quiet": 800, "clean": 0},          # deep under 640
    {"deep": 640, "quiet": 1280, "clean": 0},         # quiet over 1200
    {"deep": 640, "quiet": 800, "clean": 480},        # clean under 560
    {"deep": 640, "quiet": 640, "clean": 0},          # quiet not above deep
    {"deep": "640", "quiet": 800, "clean": 0},        # a string
    {"deep": True, "quiet": 800, "clean": 0},         # a bool
    {"deep": 640.0, "quiet": 800, "clean": 0},        # a float
    {"deep": 640, "quiet": 800},                      # missing
    {"deep": 640, "quiet": 800, "clean": 0, "level": 9},
    [640, 800, 0],
])
def test_a_profile_outside_the_floors_is_refused(bad):
    assert P.parse_profile(bad) is None


def test_the_fastest_and_slowest_levels_parse_and_map_to_endpointer_kwargs():
    fast = P.parse_profile({"deep": 640, "quiet": 800, "clean": 560})
    slow = P.parse_profile({"deep": 960, "quiet": 1200, "clean": 0})
    assert P.tail_kwargs(fast) == {"tail_deep_ms": 640, "tail_quiet_ms": 800, "tail_clean_ms": 560}
    assert P.tail_kwargs(slow)["tail_quiet_ms"] == 1200
    assert P.tail_kwargs(None) == {}                                   # today's globals, byte-identical


def test_deep_must_stay_above_the_speculative_tail():
    assert P.parse_profile({"deep": 640, "quiet": 800, "clean": 0}, speculative_tail_ms=640) is None
    assert P.parse_profile({"deep": 720, "quiet": 880, "clean": 0}, speculative_tail_ms=640) is not None


@pytest.mark.parametrize("mutate", [
    lambda f: f["listen"].update(v=2), lambda f: f["listen"].update(token="short"), lambda f: f["listen"].update(token="a b c d e f g h"),
    lambda f: f["listen"].update(apply="yes"), lambda f: f["listen"].update(panel={"deep": 1}),
    lambda f: f["listen"].update(next=dict(NEXT, ttl_s=600)), lambda f: f["listen"].update(next=dict(NEXT, ttl_s=True)),
    lambda f: f["listen"].pop("panel"), lambda f: f.pop("listen"), lambda f: f.update(listen="x"),
])
def test_a_malformed_listen_key_is_none(mutate):
    f = frame()
    mutate(f)
    assert P.parse_listen(f) is None
    assert P.parse_listen(None) is None and P.parse_listen("done") is None


def test_holder_off_takes_nothing_and_shadow_never_applies(tmp_path):
    off = P.PaceHolder(mode="off", path=str(tmp_path / "c.json"))
    assert off.note_frame(frame(), 0.0) is False and off.choose("wake", 0.0) == (None, "env")
    sh = P.PaceHolder(mode="shadow", path=str(tmp_path / "c.json"))
    assert sh.note_frame(frame(apply=False, next=dict(NEXT)), 0.0)
    assert sh.choose("followup", 1.0) == (None, "env")
    assert sh.would("followup", 1.0)[1] == "window" and sh.would("wake", 1.0)[1] == "panel"
    assert not (tmp_path / "c.json").exists()                           # a shadow profile is never cached


def test_holder_on_applies_window_until_ttl_or_wake(tmp_path):
    h = P.PaceHolder(mode="on", path=str(tmp_path / "c.json"))
    assert h.note_frame(frame(next=dict(NEXT)), 100.0)
    assert h.choose("wake", 101.0)[1] == "panel"                       # a wake turn: the panel profile
    assert h.choose("followup", 101.0) == (P.parse_profile(NEXT), "window")
    assert h.choose("conversation", 131.0)[1] == "panel"               # ttl over
    h.note_frame(frame(next=dict(NEXT)), 200.0)
    h.on_wake()
    assert h.choose("followup", 201.0)[1] == "panel"                    # any wake drops it
    h.note_frame(frame(next={"deep": 1}), 300.0)                        # malformed: nothing changes
    assert h.choose("wake", 301.0)[1] == "panel"


def test_a_profile_choose_refused_is_never_cached(tmp_path):
    """Local shadow + a server frame that says apply: choose() uses today's tails, so the cache must not learn the profile
    (a restart in "on" would otherwise apply a profile that was never used)."""
    path = str(tmp_path / "c.json")
    sh = P.PaceHolder(mode="shadow", path=path)
    assert sh.note_frame(frame(apply=True), 0.0)
    assert sh.choose("wake", 1.0) == (None, "env")
    assert not Path(path).exists()


def test_a_profile_is_cached_when_it_is_first_applied_not_when_it_first_arrived(tmp_path):
    path = str(tmp_path / "c.json")
    h = P.PaceHolder(mode="on", path=path)
    h.note_frame(frame(apply=False), 0.0)                              # the server in shadow: received, not applied
    assert h.choose("wake", 1.0) == (None, "env") and not Path(path).exists()
    h.note_frame(frame(apply=True), 2.0)                               # the same profile, now applied
    assert h.choose("wake", 3.0) == (P.parse_profile(PANEL), "panel")
    assert P.load_cache(path) == P.parse_profile(PANEL)


def test_the_cache_survives_a_restart_and_is_0600(tmp_path):
    path = str(tmp_path / "sub" / "listen_pace.json")
    h = P.PaceHolder(mode="on", path=path)
    h.note_frame(frame(), 0.0)
    assert h.choose("wake", 0.5)[1] == "panel"                        # cached when it is applied
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    cached = P.load_cache(path)
    assert cached == P.parse_profile(PANEL)
    h2 = P.PaceHolder(mode="on", path=path, cached=cached)            # restart, server not reached yet
    assert h2.choose("wake", 0.0) == (cached, "cache")
    Path(path).write_text("{not json")
    assert P.load_cache(path) is None
    Path(path).write_text(json.dumps({"v": 1, "panel": {"deep": 400, "quiet": 800, "clean": 0}}))
    assert P.load_cache(path) is None                                   # a hand-edited fast tail is refused
    assert P.load_cache(str(tmp_path / "missing.json")) is None


def test_settings_follow_the_daemon_convention():
    assert P.settings({}) == ("off", P.LISTEN_PACE_FILE)
    assert P.settings({"LISTEN_PACE": "ON", "LISTEN_PACE_FILE": "/x/y.json"}) == ("on", "/x/y.json")
    assert P.settings({"LISTEN_PACE": "maybe"})[0] == "off"


def test_outcome_body_is_numbers_and_enums_only():
    b = P.outcome_body(token=TOK, win="followup", rule="deep", close_ms=640, quiet_ms=720, fall="clean", still_talking=1,
                       talk_chunks=4, obs_ms=1500, muted_ms=0, reflex=False, level_used=-1)
    assert b["still_talking"] == 1 and b["level_used"] == -1 and b["frozen"] == "-"
    assert all(isinstance(v, int) or (isinstance(v, str) and len(v) <= 64) for v in b.values())
    good = dict(token=TOK, win="wake", rule="deep", close_ms=640, quiet_ms=720, fall="clean", still_talking=0, talk_chunks=0,
                obs_ms=1500, muted_ms=0)
    for k, v in (("token", ""), ("win", "tv"), ("rule", "x"), ("fall", "?"), ("still_talking", 2), ("obs_ms", "1500"),
                 ("frozen", "weird")):
        assert P.outcome_body(**dict(good, **{k: v})) is None, k


def test_freeze_reason_unknown_media_freezes():
    assert P.freeze_reason(media=None) == "media"
    assert P.freeze_reason(media=False) == ""
    assert P.freeze_reason(media=False, night=True, capped=True) == "night"


def test_pace_line_shape():
    used = P.parse_profile(PANEL)
    line = P.pace_line(mode="on", used=used, used_src="panel", would=used, would_src="panel", win="wake")
    assert line == "PACE mode=on win=wake used=640/800/640 src=panel would=640/800/640 would_src=panel level=-2 frozen=-"
    line = P.pace_line(mode="x", used=None, used_src="bogus", would=None, would_src="env", win="tv", frozen="nope")
    assert line == "PACE mode=off win=barge used=-1/-1/-1 src=env would=-1/-1/-1 would_src=env level=- frozen=-"
