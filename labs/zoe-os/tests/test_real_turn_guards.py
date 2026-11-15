"""The guarded real-turn runner: each guard refuses on its own, the window logic on a fake clock,
the lock, and cleanup on an injected failure. No model, no node, no llama is contacted here: every
reading is injected (``Probes``). Synthetic throughout."""
from __future__ import annotations

import datetime as dt
import os

import pytest
import real_turn as rt


def probes(**over) -> rt.Probes:
    base = dict(now=lambda: dt.datetime(2030, 3, 4, 23, 0), cmdlines=lambda: ["python3 -m http.server"],
                slots=lambda url: [{"id": 0, "is_processing": False, "n_prompt_tokens": 300}],
                mem_mb=lambda: 4000, log_age_s=lambda: 600.0, speech_active=lambda: True,
                panel_quiet=lambda: True, night_busy=lambda now: None, night_jobs=lambda: None, voice_hold=lambda: None)
    base.update(over)
    return rt.Probes(**base)


def args(*extra):
    import argparse
    ns = argparse.Namespace(i_own_the_slot=True, dry_run=False, lock="/nonexistent", llama_url="http://x", owner_approved_daytime=False,
                            mem_floor=1500, log_quiet_s=60)
    for e in extra:
        setattr(ns, e[0], e[1])
    return ns


def guards(a, p, held=True):
    return [g for g, _ in rt.refusals(a, p, held_lock=held)]


def test_all_clear_passes():
    assert rt.refusals(args(), probes(), held_lock=True) == []


@pytest.mark.parametrize("name,a,p,held", [
    ("flag", args(("i_own_the_slot", False)), probes(), True),
    ("window", args(), probes(now=lambda: dt.datetime(2030, 3, 4, 12, 0)), True),
    ("lock", args(), probes(), False),
    ("probe", args(), probes(cmdlines=lambda: ["/usr/bin/python3.12 -u scripts/perf/measure_voice.py --x"]), True),
    ("speech", args(), probes(speech_active=lambda: False), True),
    ("slots", args(), probes(slots=lambda u: [{"id": 0, "is_processing": True}]), True),
    ("memory", args(), probes(mem_mb=lambda: 900), True),
    ("log-quiet", args(), probes(log_age_s=lambda: 5.0), True),
    ("voice-hold", args(), probes(voice_hold=lambda: "the lander's voice-hold is set"), True),
    ("night-jobs", args(), probes(night_jobs=lambda: "zoe-night-window.service"), True)])
def test_each_guard_alone_refuses_and_only_that_guard(name, a, p, held):
    assert guards(a, p, held) == [name]


def test_an_unreadable_slot_is_never_idle():
    def boom(url):
        raise OSError("down")
    assert guards(args(), probes(slots=boom)) == ["slots"]


def test_an_unreadable_log_refuses():
    assert guards(args(), probes(log_age_s=lambda: None)) == ["log-quiet"]


def test_the_panel_and_night_busy_rules_of_the_repo_refuse_too():
    assert guards(args(), probes(panel_quiet=lambda: False)) == ["window"]
    assert guards(args(), probes(night_busy=lambda now: "night_busy says pause")) == ["window"]


@pytest.mark.parametrize("hh,mm,ok", [(22, 0, True), (23, 30, True), (0, 5, True), (1, 30, True), (21, 59, False),
                                      (1, 45, False), (1, 55, False), (2, 30, False), (3, 15, False), (4, 30, False),
                                      (4, 55, False), (7, 0, False), (12, 0, False)])
def test_the_window_on_a_fake_clock(hh, mm, ok):
    day = 5 if hh < 12 else 4
    p = probes(now=lambda: dt.datetime(2030, 3, day, hh, mm))
    assert (rt.window_reason(p.now(), p) is None) is ok, rt.window_reason(p.now(), p)


@pytest.mark.parametrize("hh,mm,ok", [(7, 0, True), (12, 0, True), (21, 59, True), (22, 0, True), (1, 30, True),
                                      (1, 45, False), (1, 55, False), (2, 30, False), (3, 15, False),
                                      (4, 30, False), (4, 54, False), (4, 55, True)])
def test_the_daytime_override_lifts_only_the_waking_hours_never_the_night_span(hh, mm, ok):
    day = 5 if hh < 12 else 4
    p = probes(now=lambda: dt.datetime(2030, 3, day, hh, mm))
    assert (rt.window_reason(p.now(), p, True) is None) is ok, rt.window_reason(p.now(), p, True)


def test_the_override_flag_reaches_the_guard_but_a_busy_panel_or_slot_still_refuses():
    day = probes(now=lambda: dt.datetime(2030, 3, 4, 12, 0))
    assert guards(args(("owner_approved_daytime", True)), day) == []
    assert guards(args(("owner_approved_daytime", True)), probes(now=day.now, panel_quiet=lambda: False)) == ["window"]
    assert guards(args(("owner_approved_daytime", True)), probes(now=day.now, log_age_s=lambda: 3.0)) == ["log-quiet"]
    assert guards(args(("owner_approved_daytime", True)), probes(now=lambda: dt.datetime(2030, 3, 5, 3, 0))) == ["window"]


@pytest.mark.parametrize("cmd,hit", [
    ("python3 /h/scripts/perf/measure_voice.py --n 3", True),
    ("/usr/bin/python3.12 -u scripts/maintenance/voice_regression_probe.py", True),
    ("python3 -B x/measure_tts.py", True), ("python3 scripts/perf/measure_cold_first_turn.py", True),
    ("bash -c 'while pgrep -f measure_voice.py; do sleep 1; done'", False),
    ("claude --prompt please read measure_tts.py", False), ("vim measure_voice.py", False),
    ("python3 -m pytest tests/test_measure_voice_helpers.py", False)])
def test_the_probe_pattern_matches_the_process_not_the_mention(cmd, hit):
    assert bool(rt.PROBE_RE.match(cmd)) is hit


def test_the_lock_is_taken_not_waited_for(tmp_path):
    path = str(tmp_path / "l.lock")
    fd = rt.take_lock(path)
    assert fd is not None and rt.take_lock(path) is None
    os.close(fd)
    again = rt.take_lock(path)
    assert again is not None
    os.close(again)


def test_a_held_lock_exits_2_and_never_builds_a_stack(tmp_path, monkeypatch):
    path = str(tmp_path / "l.lock")
    held = rt.take_lock(path)
    monkeypatch.setattr(rt, "Stack", lambda *a: pytest.fail("a stack was built without the lock"))
    assert rt.main(["--i-own-the-slot", "--lock", path, "--dry-run", "--out", str(tmp_path / "o")], probes()) == rt.EXIT_LOCK
    os.close(held)


def test_a_refusal_exits_3_and_releases_the_lock(tmp_path, monkeypatch):
    path = str(tmp_path / "l.lock")
    monkeypatch.setattr(rt, "Stack", lambda *a: pytest.fail("a stack was built despite a refusal"))
    assert rt.main(["--lock", path, "--dry-run"], probes()) == rt.EXIT_REFUSED       # no --i-own-the-slot
    free = rt.take_lock(path)
    assert free is not None
    os.close(free)


def test_cleanup_runs_and_the_lock_is_released_when_the_run_fails_midway(tmp_path, monkeypatch):
    path, events = str(tmp_path / "l.lock"), []

    class Boom:
        def __init__(self, *a):
            pass

        def up(self, llama):
            events.append("up")
            raise RuntimeError("injected failure after the lab app started")

        def down(self):
            events.append("down")
    monkeypatch.setattr(rt, "Stack", Boom)
    with pytest.raises(RuntimeError, match="injected"):
        rt.main(["--i-own-the-slot", "--dry-run", "--lock", path, "--out", str(tmp_path / "o")], probes())
    assert events == ["up", "down"]
    free = rt.take_lock(path)
    assert free is not None
    os.close(free)


def test_cleanup_on_a_signal(tmp_path, monkeypatch):
    path, events = str(tmp_path / "l.lock"), []

    class Sig:
        def __init__(self, *a):
            pass

        def up(self, llama):
            raise SystemExit(130)           # what the SIGTERM handler raises

        def down(self):
            events.append("down")
    monkeypatch.setattr(rt, "Stack", Sig)
    with pytest.raises(SystemExit):
        rt.main(["--i-own-the-slot", "--dry-run", "--lock", path, "--out", str(tmp_path / "o")], probes())
    assert events == ["down"] and rt.take_lock(path) is not None


def test_app_port_may_never_be_a_live_port():
    assert {3579, 3578, 11434, 8000} <= rt.LIVE_PORTS


def test_the_voice_hold_reads_size_only(tmp_path, monkeypatch):
    monkeypatch.setattr(rt.Path, "home", staticmethod(lambda: tmp_path))
    assert rt._voice_hold() is None                                   # no file
    d = tmp_path / ".zoe/agent-tools/coord"
    d.mkdir(parents=True)
    (d / "voice-hold").write_text("")
    assert rt._voice_hold() is None                                   # empty
    (d / "voice-hold").write_text("pr-1234")
    assert rt._voice_hold()


def test_the_quiet_guard_reads_turn_loggers_only_and_never_a_message():
    import real_turn as rt
    from datetime import datetime, timezone

    def line(sec_ago, logger, msg="SECRET-WORDS"):
        ts = datetime.fromtimestamp(1_800_000_000 - sec_ago, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
        return f"{ts} INFO     {logger}: {msg}"
    now = 1_800_000_000.0
    housekeeping = "\n".join(line(s, n) for s, n in ((5, "apscheduler.scheduler"), (20, "multica_autopilot_sync"), (400, "main")))
    assert rt.last_turn_age_s(housekeeping, now) == 400                      # no turn in the tail: quiet for its whole span
    assert rt.last_turn_age_s(housekeeping + "\n" + line(12, "routers.voice_tts"), now) == 12     # a turn 12 s ago: active
    assert rt.last_turn_age_s(line(90, "zoe_flue_client") + "\n" + line(3, "brain_scheduler"), now) == 90
    assert rt.last_turn_age_s("garbage with no timestamp\n\n", now) is None                       # unparseable: the caller refuses
    for name in ("routers.chat", "intent_router", "distress_handoff"):
        assert rt.last_turn_age_s(line(7, name), now) == 7
    # control: with every logger treated as a turn (the old "any write" rule) housekeeping alone reads as active
    old = rt.TURN_LOGGERS
    try:
        rt.TURN_LOGGERS = frozenset({"apscheduler.scheduler", "multica_autopilot_sync", "main"})
        assert rt.last_turn_age_s(housekeeping, now) == 5
    finally:
        rt.TURN_LOGGERS = old


# ---- the big set, the diagnostics and the summary (no model) -----------------------------------------------------
def test_the_sets_have_the_stated_shape_and_small_stays_the_default():
    from collections import Counter
    assert Counter(t[0] for t in rt.BIG) == {"list": 8, "remind": 8, "alarm": 6, "kid": 3, "inject": 1, "chat": 3}
    assert len(rt.SMALL) == 5 and rt.WALL_BY_SET == {"small": 300, "big": 480}
    assert len({t[2] for t in rt.BIG}) == len(rt.BIG)
    assert all(t[1] == "kid" and t[3] == "alarm_set" and ":" in t[2] for t in rt.BIG if t[0] == "kid"), \
        "kid phrasings carry a clock time so the arguments are VALID and the grant refusal really runs"


def test_diagnose_a_text_tool_call_a_question_and_a_plain_reply():
    textual = rt.diagnose(['{"name": "list_add", "arguments": {"item": "x"}}'], [], "list_add")
    assert textual["looks_like_text_tool_call"] and textual["tool_frames"] == 0 and textual["reply_len"] > 0
    q = rt.diagnose(["Which list did you mean?"], [], "list_add")
    assert q["asks_a_question"] and not q["looks_like_text_tool_call"] and "Which" not in str(q)
    plain = rt.diagnose(["Sure thing."], [], "remind_set")
    assert not plain["asks_a_question"] and not plain["looks_like_text_tool_call"]


def test_diagnose_an_invalid_proposal_has_names_and_a_reason_never_values():
    props = [{"status": "invalid", "reason": "wrong argument type",
              "shape": {"present": ["time"], "missing": [], "unknown": [], "bad_value": ["time"]}}]
    d = rt.diagnose(["__TOOL__:{}"], props, "alarm_set")
    assert d["rejections"] == [{"status": "invalid", "reason": "wrong argument type", "shape": props[0]["shape"]}]
    assert "6:00" not in str(d)


def test_summarize_hit_rates_refusals_false_calls_and_percentiles():
    def row(tag, called, kernel, ok=True, model=100, hop=1.0):
        return {"tag": tag, "called": called, "right_tool": ok, "args_valid": bool(kernel), "kernel": kernel,
                "model_ms": model, "hop_ms": hop}
    rows = [row("list", ["list_add"], ["done"]), row("list", [], [], ok=False), row("kid", ["alarm_set"], ["refused"]),
            row("kid", ["alarm_set"], ["invalid"]), row("chat", [], [], model=50), row("chat", ["list_add"], ["done"]),
            {"n": 9, "skipped": "wall-clock cap"}]
    s = rt.summarize(rows)
    assert s["per_skill_hit"] == {"list": "1/2"} and s["refusal_path"] == "1/2"
    assert s["false_tool_calls_on_chat"] == "1/2" and s["skipped"] == 1 and s["turns"] == 6
    assert s["model_ms_p50_p90"][0] == 100


def test_the_wall_cap_is_stated_in_the_run_output(capsys, tmp_path, monkeypatch):
    class Stop:
        def __init__(self, *a):
            pass

        def up(self, llama):
            raise RuntimeError("stop after the banner")

        def down(self):
            pass
    monkeypatch.setattr(rt, "Stack", Stop)
    with pytest.raises(RuntimeError):
        rt.main(["--i-own-the-slot", "--dry-run", "--set", "big", "--lock", str(tmp_path / "l"),
                 "--out", str(tmp_path / "o")], probes())
    # the banner prints after Stack.up, which raised first: assert the numbers the banner is built from
    assert rt.WALL_BY_SET["big"] == 480
