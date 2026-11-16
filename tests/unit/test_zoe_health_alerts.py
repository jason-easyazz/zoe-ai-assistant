"""The health check's owner messages, the planned-window marker and its writer (docs/knowledge/health-alerts.md).
Hermetic: fakes and tmp_path only, nothing live, nothing sent; times are epoch numbers, so no time-zone dependence."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
MAINT = REPO / "scripts" / "maintenance"
WRITER = MAINT / "maintenance_window.sh"


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, MAINT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


health = _load("zoe_health", "zoe_health_alerts_t")
health.MAINT_MARKER = REPO / "no-such-dir" / "OPEN"
clw = _load("zoe_crash_loop_watch", "zoe_clw_alerts_t")
C = health.Check

# ── every check: its down words (alerted on the 2nd failing run, 10 minutes after the 1st) and its recovery ──────────
SELF = "Nothing for you to do yet; it normally restarts by itself, and I'll tell you when it's back."
WAIT = "Nothing for you to do yet; I'll tell you when it's back."
GONE = "It stopped about 10 minutes ago and has not come back by itself."
SEEN = "I noticed it about 10 minutes ago."
EXPECTED = [
    ("zoe-data :8000", "no-answer",
     "I'm not reachable at all. Nothing that talks to me (the panel, the app, chat) can get an answer. "
     f"{GONE} {SELF} (zoe-data: no-answer)",
     "I'm reachable again. It was out for 25 minutes."),
    ("llama-server :11434", "no-answer",
     "I can't think right now. I can hear you, but I can't answer questions or do what you ask. "
     f"{GONE} {SELF} (llama-server: no-answer)",
     "I can think again. It was out for 25 minutes."),
    ("flue-zoe-brain-2x :3579", "error-reply",
     "I can't hold a conversation right now. You may get no reply, or a short apology instead of an answer. "
     f"{GONE} {SELF} (flue-zoe-brain-2x: error-reply)",
     "I can hold a conversation again. It was out for 25 minutes."),
    ("kokoro-tts :10201", "no-answer",
     "I've lost my voice. I can still hear you and answer on screen, but I can't speak. "
     f"{GONE} {SELF} (kokoro-tts: no-answer)",
     "My voice is back. It was out for 25 minutes."),
    ("functiongemma-router :11436", "timed-out",
     "I'm slow at working out what you want. I still answer, but picking the right action is slower and less "
     f"reliable. {GONE} {SELF} (functiongemma-router: timed-out)",
     "I'm quick at working out what you want again. It was out for 25 minutes."),
    ("postgres", "not-ready",
     "I can't reach my memory. I can't remember or look anything up: lists, reminders and what I know are out of "
     f"reach. {GONE} {WAIT} (database: not-ready)",
     "I can reach my memory again. It was out for 25 minutes."),
    ("containers", "zoe-ui",
     "One of my background helpers is unwell. Most things still work, but whatever that helper does for me may be "
     f"slow or missing. {GONE} {WAIT} (containers: zoe-ui)",
     "My background helpers are all healthy again. It was out for 25 minutes."),
    ("failed units", "zoe-dreaming.service",
     "One of my scheduled jobs did not finish. Everything still works for now, but that job needs to run properly "
     f"next time. {SEEN} Nothing for you to do yet; I'll tell you once it's sorted. (jobs: zoe-dreaming.service)",
     "My scheduled jobs are all finishing again. That lasted 25 minutes."),
    ("failed units", "zoe-backup.service",
     "My latest backup did not finish. Nothing is lost, but the newest copy of your data is older than it should be. "
     f"{SEEN} Nothing for you to do yet; I'll tell you once it's sorted. (jobs: zoe-backup.service)",
     "My scheduled jobs are all finishing again. That lasted 25 minutes."),
    ("night-window timer", "inactive/enabled",
     f"My overnight routine is switched off. Tonight I won't do my overnight thinking and tidying up. {SEEN} It won't "
     "sort itself out, so it needs someone technical to look at it; nothing else breaks meanwhile. "
     "(zoe-night-window.timer: inactive/enabled)",
     "My overnight routine is switched back on. That lasted 25 minutes."),
    ("restore drill", "stale",
     "My practice restore from backup has stopped running. The backups may well be fine, but I can't promise they "
     f"would restore until one passes. {SEEN} It needs someone technical to look at it this week; nothing breaks "
     "meanwhile. (restore-drill: stale)",
     "My practice restore is running again. That lasted 25 minutes."),
    ("disk /", "93% full",
     f"I'm running out of storage. If it fills up I won't be able to save anything new. {SEEN} It won't clear by "
     "itself; some old files need tidying up, so ask someone technical soon. (disk: 93% full)",
     "I have enough storage again. That lasted 25 minutes."),
    ("memory", "650 MB free",
     "I'm running short of working memory. I may be slow to answer, or miss a request now and then. "
     f"{SEEN} {WAIT} (memory: 650 MB free)",
     "My working memory is back to normal. That lasted 25 minutes."),
    ("forget ledger", "unconfigured",
     "Forgetting isn't permanent right now. If you ask me to forget something I'll hide it, but I can't promise it's "
     f"gone for good. {SEEN} It needs a setting that someone technical can add; nothing else is affected. "
     "(forget-ledger: unconfigured)",
     "Forgetting is permanent again. That lasted 25 minutes."),
    ("owner alerts", "auth failure",
     f"My messages to you aren't getting through. Telegram is refusing them, so you may miss news from me. {GONE} "
     "I'm keeping them and will send them as soon as it works; the Telegram setup may need a look. "
     "(telegram: auth failure)",
     "My messages are getting through to you again. It was out for 25 minutes."),
]


def _episode(name: str, code: str) -> tuple[list[str], list[str], list[str]]:
    down, up = [C(name, False, "x", code=code)], [C(name, True, "x", code="ok")]
    first, st = health.transitions(down, {}, now=0.0)
    alert, st = health.transitions(down, st, now=600.0)
    back, _ = health.transitions(up, st, now=1500.0)
    return first, alert, back


@pytest.mark.parametrize("name,code,down,back", EXPECTED, ids=[f"{n}|{c}" for n, c, _d, _b in EXPECTED])
def test_every_check_has_plain_down_and_recovered_words(name, code, down, back):
    first, alert, recovered = _episode(name, code)
    assert first == []                                   # the two-consecutive-failures rule
    assert alert == [down]
    assert recovered == [back]


def test_every_probe_check_name_has_words_of_its_own():
    """A new check added to probe() without plain words would fall back to the generic sentence: catch that here."""
    def run(argv, timeout=5.0):
        return 0, ""
    names = {c.name for c in health.probe(http=lambda p: (True, "HTTP 200"), run=run, night_open=False,
                                          forget_ledger=lambda: "configured", planned=(None, None, ""))}
    names.add(health.alerting_check(lambda: None).name)
    covered = {n for n, *_ in EXPECTED}
    assert names == covered
    for n in names:
        assert health.words(C(n, False, "x"))["headline"] != health._FALLBACK["headline"], n


def test_a_recovery_is_only_ever_sent_for_something_that_was_alerted():
    down, up = [C("kokoro-tts :10201", False, "x", code="no-answer")], [C("kokoro-tts :10201", True, "x")]
    _, st = health.transitions(down, {}, now=0.0)        # one failing run: never alerted
    lines, st = health.transitions(up, st, now=300.0)
    assert lines == [] and st["kokoro-tts :10201"]["alerted"] is False


def test_several_things_at_once_are_one_message():
    trio = [C("kokoro-tts :10201", False, "x", code="no-answer"), C("llama-server :11434", False, "x", code="no-answer"),
            C("postgres", False, "x", code="not-ready")]
    _, st = health.transitions(trio, {}, now=0.0)
    lines, st = health.transitions(trio, st, now=300.0)
    assert lines == [
        "A few things went wrong at once:\n"
        "- I've lost my voice. I can still hear you and answer on screen, but I can't speak.\n"
        "- I can't think right now. I can hear you, but I can't answer questions or do what you ask.\n"
        "- I can't reach my memory. I can't remember or look anything up: lists, reminders and what I know are out "
        "of reach.\n"
        "It started about 5 minutes ago. Nothing for you to do yet; I'll tell you as each one comes back. "
        "(kokoro-tts: no-answer; llama-server: no-answer; database: not-ready)"]
    disk = C("disk /", False, "x", code="95% full")
    lines, st = health.transitions(trio + [disk], st, now=900.0)
    assert lines == []
    mixed = [C("kokoro-tts :10201", True, "x"), C("llama-server :11434", True, "x"),
             C("postgres", False, "x", code="not-ready"), disk]
    lines, _ = health.transitions(mixed, st, now=1200.0)
    assert len(lines) == 2                                               # one message: send() joins the paragraphs
    assert lines[0].startswith("I'm running out of storage.")           # the new problem first
    assert lines[1] == "My voice is back after 20 minutes.\nI can think again after 20 minutes."


def test_send_makes_one_message_through_the_unchanged_delivery_path(monkeypatch):
    got = []

    class _Ops:
        @staticmethod
        def deliver(code, text):
            got.append((code, text))
            return "delivered", "-"
    monkeypatch.delenv("ZOE_HEALTH_NO_ALERT", raising=False)
    monkeypatch.setattr(health, "_ops", lambda: _Ops)
    assert health.send(["First paragraph.", "Second paragraph."]) is True
    assert got == [("health", "First paragraph.\n\nSecond paragraph.")]
    assert health.send([]) is True and len(got) == 1


# ── dropping out again and again: said once ──────────────────────────────────────────────────────────────────────

H = 3600.0
# the owner's twelve-hour sample: the speech service dropped out five times, each back 10-30 minutes later
SAMPLE = [(1.5 * H, 15), (5 * H + 600, 20), (6 * H, 15), (8 * H + 300, 30), (12 * H + 600, 15)]


def _replay(outages, start=1 * H, end=13 * H, planned=False):
    st: dict = {}
    sent = []
    t = start
    while t <= end:
        down = any(b <= t < b + m * 60 for b, m in outages)
        if planned and down:
            check = C("kokoro-tts :10201", True, "skipped: planned test window", skipped=True)
        else:
            check = C("kokoro-tts :10201", not down, "x", code="ok" if not down else "no-answer")
        lines, st = health.transitions([check], st, now=t)
        if lines:
            sent.append((t, lines))
        t += 300
    return sent, st


def test_the_owners_twelve_hour_sample_replayed():
    """Before this change the sample was 10 messages (5 outage + 5 recovery pairs; the old code replayed the same way
    gives 10). Now: two ordinary pairs, then ONE "keeps dropping out" message, then quiet. With the planned-window
    marker written by the window tooling, the same drops send nothing."""
    sent, st = _replay(SAMPLE)
    assert len(sent) == 5
    assert sent[4][1] == [
        "My voice keeps dropping out: 3 times in the last 5 hours, for about 18 minutes each time. It is out again "
        "now. When it is out: I can still hear you and answer on screen, but I can't speak. Rather than message you "
        "every time, I'll only tell you if a drop lasts more than 60 minutes, and once it has stayed steady for 6 "
        "hours. (kokoro-tts: keeps dropping out)"]
    assert st["kokoro-tts :10201"]["flap"] is True and st["kokoro-tts :10201"]["flap_n"] == 5
    assert _replay(SAMPLE, planned=True)[0] == []


def test_dropping_out_ends_with_one_steady_line_and_a_long_drop_is_still_told():
    sent, st = _replay(SAMPLE, end=19 * H)
    assert sent[-1][1] == ["My voice has stayed steady for 6 hours now, so I think the dropouts are over. It dropped "
                           "out 5 times in all."]
    assert st["kokoro-tts :10201"]["flap"] is False
    # while it keeps dropping out, a drop that lasts more than an hour is told, and its recovery too
    long = SAMPLE[:3] + [(8 * H, 90)]
    sent, _ = _replay(long, end=10 * H)
    told = [ln for _t, lines in sent for ln in lines]
    assert told[-2].startswith("I've lost my voice. This drop has lasted 60 minutes, longer than the earlier ones.")
    assert told[-1] == "My voice is back. It was out for 90 minutes."


def test_an_undelivered_dropping_out_summary_is_said_again():
    sent, st = _replay(SAMPLE[:2] + [(6 * H, 15)], end=6 * H + 300)
    before = _replay(SAMPLE[:2] + [(6 * H, 15)], end=6 * H)[1]
    assert "keeps dropping out" in sent[-1][1][0]
    kept = health.undelivered(before, st)
    assert kept["kokoro-tts :10201"]["flap"] is False
    lines, _ = health.transitions([C("kokoro-tts :10201", False, "x", code="no-answer")], kept, now=6 * H + 600)
    assert lines and "keeps dropping out" in lines[0]


# ── the planned-window marker ───────────────────────────────────────────────────────────────────────────────────

NOW = 1_800_000_000.0


def _marker(tmp_path, body) -> Path:
    p = tmp_path / "OPEN"
    p.write_text(body if isinstance(body, str) else json.dumps(body))
    return p


def test_a_valid_marker_opens_the_window(tmp_path):
    w, over, note = health.read_planned_window(NOW, _marker(tmp_path, {"until": NOW + 900, "kind": "test"}))
    assert w == {"kind": "test", "until": NOW + 900} and over is None and "skipped" in note


@pytest.mark.parametrize("body", [
    {"until": NOW - 3 * 3600, "kind": "test"},                          # expired long ago
    {"until": NOW + 2 * 3600 + 1, "kind": "test"},                      # too far ahead
    {"kind": "test"},                                                   # no until
    {"until": NOW + 900},                                               # no kind
    {"until": NOW + 900, "kind": "party"},                              # kind outside the closed set
    {"until": NOW + 900, "kind": "test", "note": "x"},                  # anything else in it
    {"until": "soon", "kind": "test"}, {"until": True, "kind": "test"}, [NOW + 900, "test"],
    "not json", '{"until": NaN, "kind": "test"}', '{"until": Infinity, "kind": "test"}',
])
def test_a_bad_or_stale_marker_is_ignored(tmp_path, body):
    w, over, note = health.read_planned_window(NOW, _marker(tmp_path, body))
    assert w is None and over is None and note.startswith("planned-window marker ignored")


def test_no_marker_is_no_window(tmp_path):
    assert health.read_planned_window(NOW, tmp_path / "OPEN") == (None, None, "")


def _db_down(argv, timeout=5.0):
    if argv[:2] == ["docker", "exec"]:
        return 1, ""
    return 0, ""


def test_a_planned_window_skips_the_speech_service_but_the_database_still_alerts():
    planned = ({"kind": "test", "until": NOW + 900}, None, "")
    st: dict = {}
    sent = []
    for i in range(3):
        checks = health.probe(http=lambda port: (False, "URLError"), run=_db_down, night_open=False, planned=planned)
        speech = [c for c in checks if c.name.startswith("kokoro-tts")]
        assert speech and speech[0].skipped and speech[0].ok
        lines, st = health.transitions(checks, st, now=NOW + 300 * i)
        sent += lines
    text = "\n".join(sent)
    assert "I can't reach my memory." in text and "I can't think right now." in text   # not expected to drop
    assert "voice" not in text


def test_a_failure_counted_before_the_window_starts_afresh():
    k = "kokoro-tts :10201"
    _, st = health.transitions([C(k, False, "x", code="no-answer")], {}, now=NOW)
    _, st = health.transitions([C(k, True, "skipped", skipped=True)], st, now=NOW + 300)
    assert st[k]["fails"] == 0
    lines, st = health.transitions([C(k, False, "x", code="no-answer")], st, now=NOW + 600)
    assert lines == [], "one failure after the window is not an outage"
    # an outage already told before the window stays told, and its recovery still comes
    _, st = health.transitions([C(k, False, "x", code="no-answer")], {}, now=NOW)
    _, st = health.transitions([C(k, False, "x", code="no-answer")], st, now=NOW + 300)
    _, st = health.transitions([C(k, True, "skipped", skipped=True)], st, now=NOW + 600)
    assert st[k]["alerted"] is True
    lines, _ = health.transitions([C(k, True, "x")], st, now=NOW + 900)
    assert lines == ["My voice is back. It was out for 15 minutes."]


def _hostile(tmp_path, kind):
    p = tmp_path / "OPEN"
    if kind == "dir":
        p.mkdir()
    elif kind == "perm":
        p.write_text(json.dumps({"until": NOW + 900, "kind": "test"}))
        p.chmod(0)
    elif kind == "bytes":
        p.write_bytes(b'{"until": 1800000900, "kind": "te\xff\xfe"}')
    elif kind == "deep":
        p.write_text("[" * 100000 + "]" * 100000)
    elif kind == "big":
        p.write_text('{"until": 1800000900, "kind": "test", "pad": "' + "x" * 100000 + '"}')
    else:
        p.write_text(kind)
    return p


HOSTILE = ['{"until":1800000900,"kind":[]}', '{"until":1800000900,"kind":{"a":{"b":[1]}}}', '{"until":1800000900,"kind":null}',
           '{"until":1800000900,"kind":3}', '{"until":1800000900,"kind":true}', '[]', '[[[[]]]]', 'null', '7', '"test"',
           '{"until":[],"kind":"test"}', '{"until":{"x":1},"kind":"test"}', '{"until":null,"kind":"test"}',
           '{"until":"1800000900","kind":"test"}', '{"until":1e999,"kind":"test"}', '{"until":-1e999,"kind":"test"}',
           '{"until":1' + "0" * 5000 + ',"kind":"test"}', '{"until":-1' + "0" * 400 + ',"kind":"test"}',
           '{"until":1800000900,"kind":"test","kind":[]}', '', '\x00\x00', 'dir', 'perm', 'bytes', 'deep', 'big']


@pytest.mark.parametrize("body", HOSTILE, ids=range(len(HOSTILE)))
def test_a_hostile_marker_never_stops_the_check_and_silences_nothing(tmp_path, monkeypatch, body):
    p = _hostile(tmp_path, body)
    monkeypatch.setattr(health, "MAINT_MARKER", p)
    try:
        planned = health.read_planned_window(NOW)           # no path given: the real call site's route
        assert planned[0] is None and planned[1] is None
        st: dict = {}
        sent = []
        for i in range(2):
            checks = health.probe(http=lambda port: (False, "URLError"), run=lambda a, timeout=5.0: (0, ""),
                                  night_open=False, planned=planned)
            speech = [c for c in checks if c.name.startswith("kokoro-tts")]
            assert speech and not speech[0].skipped and not speech[0].ok and len(checks) >= len(health.SPINE)
            lines, st = health.transitions(checks, st, now=NOW + 300 * i)
            sent += lines
        assert any("I've lost my voice." in ln for ln in sent)
    finally:
        if p.exists() and not p.is_dir():
            p.chmod(0o600)


def test_probe_reads_a_hostile_marker_itself_and_still_probes(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "MAINT_MARKER", _marker(tmp_path, '{"until":1800000900,"kind":[]}'))
    checks = health.probe(http=lambda port: (False, "URLError"), run=lambda a, timeout=5.0: (0, ""), night_open=False)
    assert [c for c in checks if c.name.startswith("kokoro-tts")][0].ok is False


def test_a_window_that_overran_says_so_in_the_first_alert(tmp_path):
    marker = _marker(tmp_path, {"until": NOW, "kind": "test"})
    st: dict = {}
    sent = []
    for t in (NOW + 300, NOW + 600):
        planned = health.read_planned_window(t, marker)
        assert planned[0] is None and planned[1] == {"kind": "test", "until": NOW}
        checks = health.probe(http=lambda port: (port != 10201, "URLError"), run=lambda a, timeout=5.0: (0, ""),
                              night_open=False, planned=planned)
        checks = [c for c in checks if c.name.startswith("kokoro-tts")]   # only the speech service is under test
        assert checks and checks[0].overrun == {"kind": "test", "until": NOW}
        lines, st = health.transitions(checks, st, now=t)
        sent += lines
    assert sent == [
        "I've lost my voice. I can still hear you and answer on screen, but I can't speak. The planned test window "
        "ended 10 minutes ago, but my voice has not come back. It may have been left switched off; if it isn't back "
        "soon, someone technical needs to start it again. (kokoro-tts: no-answer, window overran)"]


def test_several_failures_keep_each_ones_own_guidance_including_an_overrun():
    over = C("kokoro-tts :10201", False, "x", code="no-answer", overrun={"kind": "test", "until": NOW})
    db = C("postgres", False, "x", code="not-ready")
    _, st = health.transitions([over, db], {}, now=NOW + 300)
    lines, _ = health.transitions([over, db], st, now=NOW + 600)
    assert lines == [
        "A few things went wrong at once:\n"
        "- I've lost my voice. I can still hear you and answer on screen, but I can't speak. The planned test window "
        "ended 10 minutes ago, but my voice has not come back. It may have been left switched off; if it isn't back "
        "soon, someone technical needs to start it again.\n"
        "- I can't reach my memory. I can't remember or look anything up: lists, reminders and what I know are out "
        "of reach.\n"
        "It started about 5 minutes ago. Nothing for you to do yet about the rest; I'll tell you as each one comes "
        "back. (kokoro-tts: no-answer, window overran; database: not-ready)"]


def _steady_run(outage_h, extra_h):
    """One long outage then recovery; returns (state after recovery, sent lines, t of recovery)."""
    k = "kokoro-tts :10201"
    down = C(k, False, "x", code="no-answer")
    st: dict = {}
    sent: list = []
    t = 0.0
    # two earlier drops make it "keeps dropping out"
    for b, m in ((0, 10), (3600, 10), (7200, 10)):
        for tt in range(0, m * 60 + 300, 300):
            lines, st = health.transitions([down if tt < m * 60 else C(k, True, "x")], st, now=b + tt)
            sent += lines
    assert st[k]["flap"] is True
    start = 4 * H
    t = start
    while t < start + outage_h * H:
        lines, st = health.transitions([down], st, now=t)
        sent += lines
        t += 300
    rec = t
    lines, st = health.transitions([C(k, True, "x")], st, now=rec)
    sent += lines
    return st, sent, rec


def test_steady_is_counted_from_the_last_recovery_not_the_outage_start():
    k = "kokoro-tts :10201"
    st, sent, rec = _steady_run(6, 0)
    assert sent[-1].startswith("My voice is back. It was out for 6 hours.")
    assert not any("the dropouts are over" in ln for ln in sent), "a 6-hour outage that just recovered is not steady"
    assert st[k]["flap"] is True and st[k]["incidents"][-1][1] == rec, "the recovery time survives the pruning"
    for dt in (60, 3 * H, 6 * H - 300):                      # still dropping-out mode: suppression is not reset early
        lines, st = health.transitions([C(k, True, "x")], st, now=rec + dt)
        assert lines == [] and st[k]["flap"] is True
    lines, st = health.transitions([C(k, True, "x")], st, now=rec + 6 * H)
    assert lines == ["My voice has stayed steady for 6 hours now, so I think the dropouts are over. It dropped out 4 "
                     "times in all."]
    assert st[k]["flap"] is False
    # a drop right after a long one is still suppressed (not reset early): a new short drop stays in the summary
    st2, _s, rec2 = _steady_run(6, 0)
    _, st2 = health.transitions([C(k, False, "x", code="no-answer")], st2, now=rec2 + 300)
    lines, _ = health.transitions([C(k, False, "x", code="no-answer")], st2, now=rec2 + 600)
    assert lines == []


# ── the writer ───────────────────────────────────────────────────────────────────────────────────────────────────

def _writer(tmp_path, *args):
    env = {**os.environ, "MAINTENANCE_WINDOW_DIR": str(tmp_path / "mw")}
    return subprocess.run(["bash", str(WRITER), *args], capture_output=True, text=True, env=env, timeout=20)


def test_the_writer_opens_and_closes_a_window_the_health_check_honours(tmp_path):
    t0 = time.time()
    r = _writer(tmp_path, "open", "test", "15")
    assert r.returncode == 0, r.stderr
    marker = tmp_path / "mw" / "OPEN"
    body = json.loads(marker.read_text())
    assert set(body) == {"until", "kind"} and body["kind"] == "test"
    assert t0 + 900 - 5 <= body["until"] <= time.time() + 900 and int(r.stdout) == body["until"]
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert [p.name for p in marker.parent.iterdir()] == ["OPEN"], "no temp file left behind"
    w, _over, _note = health.read_planned_window(time.time(), marker)
    assert w == {"kind": "test", "until": float(body["until"])}
    assert _writer(tmp_path, "close").returncode == 0 and not marker.exists()
    assert _writer(tmp_path, "close").returncode == 0                   # closing twice is fine


@pytest.mark.parametrize("args", [("open", "test", "121"), ("open", "test", "0"), ("open", "party", "10"),
                                  ("open", "test", "ten"), ("open", "test"), ("bogus",), ()])
def test_the_writer_refuses_too_long_or_malformed(tmp_path, args):
    r = _writer(tmp_path, *args)
    assert r.returncode == 2 and not (tmp_path / "mw" / "OPEN").exists()
    assert _writer(tmp_path, "open", "replay", "120").returncode == 0      # exactly the bound is allowed


# ── the crash-loop watch speaks the same way ─────────────────────────────────────────────────────────────────────

def test_crash_loop_watch_words():
    assert clw.loop_text("kokoro-tts.service", 6, "in the last 15 minutes", 40, "activating") == (
        "🔴 I've lost my voice. I can still hear you and answer on screen, but I can't speak. It keeps crashing and "
        "restarting: 6 times in the last 15 minutes. It is still trying by itself; I'll tell you when it is steady "
        "again. (kokoro-tts.service crash-looping, 40 restarts in all, activating)")
    assert clw.recovery_text("kokoro-tts.service", 40) == (
        "🟢 My voice is back. It has stopped crashing. (kokoro-tts.service recovered, 40 restarts in all)")
    assert clw.loop_text("zoe-data.service", 37, "since I last checked", 37, "activating") == (
        "🔴 I'm not reachable at all. Nothing that talks to me (the panel, the app, chat) can get an answer. I keep "
        "crashing and restarting: 37 times since I last checked. I am still trying by myself; I'll tell you when I am "
        "steady again. (zoe-data.service crash-looping, 37 restarts in all, activating)")
    assert clw.recovery_text("zoe-data.service", 37) == (
        "🟢 I'm reachable again. I have stopped crashing. (zoe-data.service recovered, 37 restarts in all)")
    for unit in clw.OTHER_UNITS:
        assert clw.plain_words(unit) is not clw._GENERIC_WORDS, unit


# ── the guard: no message reads like a log ───────────────────────────────────────────────────────────────────────

_LOG_SHAPE = re.compile(r":\d{2,5}\b|\b(?:8000|11434|3579|10201|11436)\b|\b[A-Z][A-Za-z]*(?:Error|Exception|Expired)\b"
                        r"|DOWN|HTTP \d{3}|/home/|~/|journalctl|ActiveState=|\brc=")


def _all_messages() -> list[str]:
    out = []
    for name, code, _d, _b in EXPECTED:
        _f, alert, back = _episode(name, code)
        out += alert + back
    trio = [C(n, False, "x", code=c) for n, c, _d, _b in EXPECTED[:6]]
    _, st = health.transitions(trio, {}, now=0.0)
    out += health.transitions(trio, st, now=300.0)[0]
    out += [ln for _t, lines in _replay(SAMPLE, end=19 * H)[0] for ln in lines]
    out += [ln for _t, lines in _replay(SAMPLE[:3] + [(8 * H, 90)], end=10 * H)[0] for ln in lines]
    over = C("kokoro-tts :10201", False, "URLError", code="no-answer", overrun={"kind": "replay", "until": NOW})
    _, st = health.transitions([over], {}, now=NOW + 300)
    out += health.transitions([over], st, now=NOW + 600)[0]
    for unit in (clw.UNIT,) + clw.OTHER_UNITS:
        out += [clw.loop_text(unit, 6, "in the last 15 minutes", 40, "activating"), clw.recovery_text(unit, 40)]
    return out


def test_no_message_reads_like_a_log():
    msgs = _all_messages()
    assert len(msgs) > 40
    bad = [m for m in msgs if _LOG_SHAPE.search(m)]
    assert bad == []
    # the first sentence never names a unit or service
    units = [u.removesuffix(".service") for u in (clw.UNIT,) + clw.OTHER_UNITS] + list(health.SPINE)
    for m in msgs:
        head = m.split(". ")[0]
        assert not any(u in head for u in units), m


def test_the_guard_catches_the_old_messages():
    """Negative control: the strings the owner actually received trip the guard."""
    for old in ("Zoe health: DOWN: kokoro-tts :10201 (URLError)", "Zoe health: recovered: kokoro-tts :10201 (HTTP 200)",
                "DOWN: memory (MemAvailable 821 MB)", "Check: journalctl --user -u zoe-data / ~/.zoe-logs/x.log"):
        assert _LOG_SHAPE.search(old), old
