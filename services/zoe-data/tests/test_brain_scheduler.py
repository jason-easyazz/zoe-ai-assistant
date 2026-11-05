"""``brain_scheduler``: no daytime 4B call outside a person's turn unless it is a declared exception.

The choke point is a wrapper on ``httpx.AsyncClient.send``; every case below drives a REAL ``httpx`` request through an
in-memory transport, so the wrapper, the stack-derived caller label and the policy are exercised together. Each guarantee has a
negative control: the wrapper uninstalled, the scheduler off, the call outside the day window or off the brain port must NOT
count - otherwise the positive assertions could be passing for the wrong reason.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

import brain_scheduler as bs
import brain_slot_guard as g

pytestmark = pytest.mark.ci_safe

BRAIN = "http://127.0.0.1:11434/v1/chat/completions"
PERTH = ZoneInfo("Australia/Perth")


def _at(h, m=0):
    return datetime(2026, 11, 5, h, m, tzinfo=PERTH)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in (bs.ENV_MODE, bs.ENV_DAY, bs.ENV_ALLOW, bs.ENV_POST_TURN_S):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.delenv("GEMMA_SERVER_URL", raising=False)
    bs.uninstall()
    bs.reset()
    g.reset()
    yield
    bs.uninstall()
    bs.reset()
    g.reset()


def _client():
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": 1})))


async def idle_caller(url=BRAIN, method="post"):
    async with _client() as c:
        return await (c.post(url, json={}) if method == "post" else c.get(url))


# -- the day window -----------------------------------------------------------

@pytest.mark.parametrize("h,m,day", [(6, 59, False), (7, 0, True), (12, 0, True), (21, 59, True), (22, 0, False), (3, 0, False)])
def test_day_window_edges_in_household_time(h, m, day):
    assert bs.is_day(_at(h, m)) is day


def test_day_window_is_household_time_not_utc():
    # 23:30 UTC on the 4th is 07:30 in Perth: day.
    assert bs.is_day(datetime(2026, 11, 4, 23, 30, tzinfo=ZoneInfo("UTC"))) is True


def test_bad_day_window_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv(bs.ENV_DAY, "nonsense")
    assert bs.is_day(_at(12)) and not bs.is_day(_at(23))


# -- pure policy ----------------------------------------------------------------

def test_daytime_idle_call_would_defer_in_log_mode_and_is_deferred_in_enforce(monkeypatch):
    d = bs.decide("some_job.run", now=_at(14))
    assert (d.cls, d.decision, d.allowed) == (bs.IDLE, "would_defer", True)
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    d = bs.decide("some_job.run", now=_at(14))
    assert (d.decision, d.allowed) == ("deferred", False)


def test_night_idle_call_is_allowed_even_in_enforce(monkeypatch):
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    d = bs.decide("some_job.run", now=_at(3))
    assert d.allowed and d.reason == "night"


def test_turn_declared_and_request_classes_are_allowed_by_day_in_enforce(monkeypatch):
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    for label in ("zoe_agent.run_zoe_agent", "nlu_extractor._call_with_tool", "turn:portrait_refresh",
                  "wake_prewarm", "prefix_keeper", "contact_backfill._llm_extract_people"):
        d = bs.decide(label, now=_at(14))
        assert d.allowed and d.cls != bs.IDLE, label


def test_post_turn_pass_is_allowed_only_while_a_household_turn_is_recent(monkeypatch):
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    # No household turn in this process: not derived from a live turn -> idle (the negative control).
    d = bs.decide("person_extractor_llm", now=_at(14))
    assert d.cls == bs.IDLE and not d.allowed
    g.turn_started()
    g.turn_finished()
    d = bs.decide("person_extractor_llm", now=_at(14))
    assert d.cls == bs.POST_TURN and d.allowed
    monkeypatch.setattr(g, "_clock", lambda: g._last_activity + 901)
    assert bs.decide("person_extractor_llm", now=_at(14)).cls == bs.IDLE
    monkeypatch.setenv(bs.ENV_POST_TURN_S, "2000")
    assert bs.decide("person_extractor_llm", now=_at(14)).cls == bs.POST_TURN


def test_a_fast_path_turn_that_never_entered_the_slot_still_makes_the_post_turn_pass_fresh(monkeypatch):
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    assert bs.decide("person_extractor_llm", now=_at(14)).cls == bs.IDLE   # negative control: no turn of any kind yet
    g.note_turn_completed()   # what _run_voice_memory_passes / _persist_memory_candidates do for a fast-path reply
    d = bs.decide("person_extractor_llm", now=_at(14))
    assert d.cls == bs.POST_TURN and d.allowed
    assert g._last_activity == 0.0, "the wake 'hot' timer must not be touched"
    monkeypatch.setattr(g, "_clock", lambda: g._post_turn_stamp + 901)
    assert bs.decide("person_extractor_llm", now=_at(14)).cls == bs.IDLE


def test_extra_exception_label_via_env(monkeypatch):
    assert bs.decide("harness.x", now=_at(14)).cls == bs.IDLE
    monkeypatch.setenv(bs.ENV_ALLOW, "harness.x, other")
    assert bs.decide("harness.x", now=_at(14)).cls == bs.EXCEPTION


def test_off_mode_is_a_noop(monkeypatch):
    monkeypatch.setenv(bs.ENV_MODE, "off")
    bs.admit("some_job.run", now=_at(14))
    assert bs.STATS["idle_4b_calls"] == 0


# -- the choke point, end to end through httpx ------------------------------------

async def test_idle_daytime_brain_call_is_counted_with_its_caller_in_log_mode(monkeypatch, caplog):
    monkeypatch.setattr(bs, "is_day", lambda now=None: True)
    assert bs.install() is True and bs.install() is False   # idempotent
    with caplog.at_level("INFO", logger="brain_scheduler"):
        r = await idle_caller()
    assert r.status_code == 200   # log-only: the call went through
    assert bs.STATS["idle_4b_calls"] == 1 and bs.STATS["would_defer"] == 1
    (caller,) = bs.STATS["by_caller"]
    assert caller.endswith(".idle_caller")
    line = next(m for m in caplog.messages if m.startswith("BRAIN_SCHED"))
    assert "class=idle" in line and "decision=would_defer" in line and "mode=log" in line and "idle=1" in line


async def test_enforce_refuses_the_idle_daytime_call_before_it_reaches_the_transport(monkeypatch):
    monkeypatch.setattr(bs, "is_day", lambda now=None: True)
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    bs.install()
    sent = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))) as c:
        with pytest.raises(bs.BrainSchedulerDeferred):
            await c.post(BRAIN, json={})
    assert sent == [] and bs.STATS["deferred"] == 1 and bs.STATS["idle_4b_calls"] == 1


async def test_negative_controls_do_not_count(monkeypatch):
    monkeypatch.setattr(bs, "is_day", lambda now=None: True)
    # wrapper not installed
    await idle_caller()
    assert bs.STATS["idle_4b_calls"] == 0
    bs.install()
    # non-generation endpoints of the same server, and a different port
    await idle_caller("http://127.0.0.1:11434/slots", "get")
    await idle_caller("http://127.0.0.1:11434/health", "get")
    await idle_caller("http://127.0.0.1:11434/tokenize")
    await idle_caller("http://127.0.0.1:8000/v1/chat/completions")
    assert bs.STATS["idle_4b_calls"] == 0
    # outside the day window
    monkeypatch.setattr(bs, "is_day", lambda now=None: False)
    await idle_caller()
    assert bs.STATS["idle_4b_calls"] == 0 and bs.STATS["night_idle"] == 1
    # scheduler off
    monkeypatch.setattr(bs, "is_day", lambda now=None: True)
    monkeypatch.setenv(bs.ENV_MODE, "off")
    await idle_caller()
    assert bs.STATS["idle_4b_calls"] == 0


async def test_declare_names_the_caller_and_a_turn_label_is_never_idle(monkeypatch):
    monkeypatch.setattr(bs, "is_day", lambda now=None: True)
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    bs.install()
    with bs.declare("turn:good_evening"):
        await idle_caller()
    assert bs.STATS["turn"] == 1 and bs.STATS["idle_4b_calls"] == 0
    with pytest.raises(bs.BrainSchedulerDeferred):
        await idle_caller()   # same function, no declaration: idle


async def test_run_background_labels_the_post_turn_passes_through_the_real_gate(monkeypatch):
    monkeypatch.setattr(bs, "is_day", lambda now=None: True)
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    monkeypatch.setenv(g.ENV, "on")
    monkeypatch.setenv(g.ENV_QUIET_S, "0")
    bs.install()
    # Negative control first: no household turn yet -> the same labelled call is idle and refused.
    with pytest.raises(bs.BrainSchedulerDeferred):
        await g.run_background("person_extractor_llm", idle_caller)
    g.turn_started()
    g.turn_finished()
    r = await g.run_background("person_extractor_llm", idle_caller)
    assert r.status_code == 200 and bs.STATS["post_turn"] == 1


def test_note_counts_the_flue_routed_exceptions():
    bs.note("wake_prewarm")
    bs.note("prefix_keeper")
    assert bs.STATS["exception"] == 2 and bs.STATS["idle_4b_calls"] == 0


# -- drift pins -------------------------------------------------------------------

def test_every_run_background_label_in_the_service_is_declared():
    root = Path(__file__).resolve().parents[1]
    labels = set()
    for p in root.glob("*.py"):
        labels |= set(re.findall(r'run_background\(\s*"([a-z_]+)"', p.read_text(encoding="utf-8")))
    assert labels, "found no run_background call sites: the pin is blind"
    assert labels <= set(bs.DECLARED), f"undeclared post-turn labels would count as idle: {sorted(labels - set(bs.DECLARED))}"


def test_undeclared_label_is_idle():   # negative control for the pin above
    assert bs.classify("brand_new_job")[0] == bs.IDLE


def test_the_scheduler_is_installed_at_startup():
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "brain_scheduler.install()" in src


# -- cross-review fixes (#2296): fail-open, endpoint matching, guard-off label, overnight window -------------------------------

async def test_a_scheduler_fault_never_breaks_the_request(monkeypatch):
    monkeypatch.setattr(bs, "is_day", lambda now=None: True)
    bs.install()

    def boom():
        raise RuntimeError("label discovery broke")

    monkeypatch.setattr(bs, "_caller_label", boom)
    r = await idle_caller()   # log mode (and enforce): a fault is swallowed, the request goes through
    assert r.status_code == 200
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    assert (await idle_caller()).status_code == 200
    # negative control: the intentional refusal still propagates
    monkeypatch.undo()
    monkeypatch.setattr(bs, "is_day", lambda now=None: True)
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    with pytest.raises(bs.BrainSchedulerDeferred):
        await idle_caller()


@pytest.mark.parametrize("url,hit", [
    ("http://127.0.0.1:11434/v1/chat/completions", True),
    ("http://localhost:11434/v1/chat/completions", True),
    ("http://[::1]:11434/v1/chat/completions", True),
    ("http://127.0.0.1:11434/slots", False),
    ("http://127.0.0.1:11435/v1/chat/completions", False),
])
def test_loopback_brain_matches_any_loopback_spelling(url, hit):
    assert bs.is_brain_generation(httpx.Request("POST", url)) is hit


def test_remote_brain_matches_only_itself_and_https_defaults_to_443(monkeypatch):
    monkeypatch.setenv("GEMMA_SERVER_URL", "https://brain.example.test/v1")
    assert bs.is_brain_generation(httpx.Request("POST", "https://brain.example.test/v1/chat/completions"))
    assert not bs.is_brain_generation(httpx.Request("POST", "http://127.0.0.1:443/v1/chat/completions"))
    assert not bs.is_brain_generation(httpx.Request("POST", "https://other.example.test/v1/chat/completions"))


async def test_the_label_survives_the_slot_guard_being_off(monkeypatch):
    monkeypatch.setattr(bs, "is_day", lambda now=None: True)
    monkeypatch.setenv(bs.ENV_MODE, "enforce")
    monkeypatch.setenv(g.ENV, "off")
    bs.install()
    g.turn_started()
    g.turn_finished()
    r = await g.run_background("person_extractor_llm", idle_caller)   # declared post-turn: allowed
    assert r.status_code == 200 and bs.STATS["post_turn"] == 1


def test_overnight_day_window_wraps_and_equal_bounds_are_invalid(monkeypatch):
    monkeypatch.setenv(bs.ENV_DAY, "22:00-07:00")
    assert bs.is_day(_at(23)) and bs.is_day(_at(3)) and not bs.is_day(_at(12))
    monkeypatch.setenv(bs.ENV_DAY, "08:00-08:00")
    assert bs.is_day(_at(12)) and not bs.is_day(_at(23))   # invalid -> default 07:00-22:00


def test_the_scheduler_is_installed_before_any_background_loop():
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert src.index("brain_scheduler.install()") < src.index("_wait_for_brain_startup(), name=")
