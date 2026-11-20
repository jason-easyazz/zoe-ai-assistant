"""``ZOE_OS_LOOKUPS=kernel`` and ``ZOE_OS_MESSAGES=kernel`` (both default off; ``lookup_door.py``, ``message_door.py``).

LOOKUPS (weather, web search, page fetch): data leaves the box, nothing is changed. Told to the kernel afterwards, never asked,
never delayed; once per class per request; nobody identified acting = automatic, not recorded; a caller KNOWN to be off the
roster is refused before anything is sent; only who and the class cross.

MESSAGES: a member asking Zoe to send something asks first (the intercom, the panel toast, the sign-in link to their own
phone); a refusal sends nothing; kernel down = sent, ``not_checked``; EXCEPT an outside address, which is never sent
unchecked. Zoe's own notifications never reach the door. Only the channel class and member ids cross.

Flag off = main: the doors answer after one flag read, and the existing suites of every touched sender run unchanged.
Each test names the control that was run against it. Invented people and words only.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

import pytest

import auth_handoff
import kernel_door
import lookup_door
import message_door
import os_shadow
import user_erase_gate
from test_auth_handoff import _link_telegram, _start, hdb  # noqa: F401 - the hand-off's own fixture and helpers
from test_intercom import ADULT, KITCHEN, _rows, _say, house  # noqa: F401 - the simulated house
from test_kernel_door import AreaKernel

pytestmark = pytest.mark.ci_safe
SVC = Path(__file__).resolve().parents[1]
ANN, STRANGER = "member-ann", "member-zzz"


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ANN, ADULT, "jason"]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LOOKUPS", "kernel")
    monkeypatch.setenv("ZOE_OS_MESSAGES", "kernel")
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    token, done = kernel_door._ACTOR.set(None), lookup_door._DONE.set(None)
    yield k
    kernel_door._ACTOR.reset(token)
    lookup_door._DONE.reset(done)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


def _asks(door, n=0, timeout=2.0) -> list:
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(door.ops("door_ask")) < n:
        time.sleep(0.01)
    return door.ops("door_ask")


def _never(*a, **k):
    raise AssertionError("the kernel was reached")


# ---- lookups ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "off", "shadow"])
def test_with_the_lookups_flag_off_nothing_is_told_and_nobody_is_refused(door, monkeypatch, value):
    """Control run: with the flag check dropped from ``lookup_door.sent`` the stranger is refused."""
    monkeypatch.delenv("ZOE_OS_LOOKUPS") if value is None else monkeypatch.setenv("ZOE_OS_LOOKUPS", value)
    monkeypatch.setattr(os_shadow, "record_door", _never)
    kernel_door.note_actor(STRANGER)
    for kind in lookup_door.KINDS:
        lookup_door.sent(kind)


def test_a_lookup_with_nobody_acting_is_automatic_and_is_not_told(door, monkeypatch):
    """The wall panel's weather card, a night job's fetch. Control run: with ``sent`` recording as the panel when nobody is
    noted the stub raises."""
    monkeypatch.setattr(os_shadow, "record_door", _never)
    assert kernel_door.acting() is None
    lookup_door.sent(lookup_door.WEATHER)


@pytest.mark.asyncio
async def test_a_members_lookup_is_told_once_per_class_per_request_and_never_waits(door, caplog):
    """A search that fans out into many fetches is one ``web_search`` and one ``web_fetch``. The call returns at once even
    with a slow kernel. Control run: with the once-per-request set dropped four asks are made."""
    door.delay = 0.1
    os_shadow.roster()
    kernel_door.note_actor(ANN)
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        for kind in (lookup_door.WEB_SEARCH, lookup_door.WEB_FETCH, lookup_door.WEB_FETCH, lookup_door.WEB_SEARCH):
            lookup_door.sent(kind)
        assert time.monotonic() - started < 0.1
        asks = await asyncio.to_thread(_asks, door, 2)
        await asyncio.sleep(0.8)
    assert [(a["area"], a["verb"], a["person"]) for a in door.ops("door_ask")] == [("lookups", "lookup.web_search", ANN),
                                                                                  ("lookups", "lookup.web_fetch", ANN)]
    assert all(set(a) <= {"area", "verb", "person", "visibility", "stamps"} for a in asks)       # who and the class: nothing else
    kernel_door.note_actor(ANN)                                                         # the next request: told again
    lookup_door.sent(lookup_door.WEB_SEARCH)
    assert len(await asyncio.to_thread(_asks, door, 3)) == 3


def test_a_caller_known_to_be_off_the_roster_is_refused_before_anything_is_sent(door, monkeypatch):
    """It would leave the box in their name. Decided from the roster already in memory: no kernel call, no wait. A roster not
    read yet refuses nobody. Control run: with the roster check dropped the stranger's lookup is told and not refused."""
    kernel_door.note_actor(STRANGER)
    lookup_door.sent(lookup_door.WEATHER)                                               # roster not read yet: not refused
    os_shadow.roster()
    assert os_shadow._roster_seen() is not None
    kernel_door.note_actor(STRANGER)
    monkeypatch.setattr(os_shadow, "record_door", _never)
    for kind in lookup_door.KINDS:
        with pytest.raises(lookup_door.LookupRefused) as exc:
            lookup_door.sent(kind)
        assert exc.value.reason == "unknown_person" and isinstance(exc.value, kernel_door.Refused)


@pytest.mark.asyncio
async def test_a_refused_web_lookup_sends_nothing_and_says_why(door, monkeypatch):
    """The page reader and the search tool answer the refusal's sentence and never reach the browser or a provider.
    Control run: with the door call dropped from ``zoe_agent._web_browse`` the stub browser is reached."""
    import zoe_agent

    os_shadow.roster()
    kernel_door.note_actor(STRANGER)
    monkeypatch.setattr("browser_broker.fetch_page_text", _never)
    monkeypatch.setattr(zoe_agent, "_ddg_search_sync", _never)
    monkeypatch.setattr(zoe_agent, "_cloak_search", _never)
    line = lookup_door.LookupRefused("unknown_person").line()
    assert await zoe_agent._web_browse("https://example.invalid/page", user_id=STRANGER) == line
    assert await zoe_agent._web_search_ddg("words to look up", user_id=STRANGER) == line
    assert await zoe_agent._web_research("words to look up", user_id=STRANGER) == line


def test_the_panel_with_nobody_identified_looks_up_as_the_panel(door):
    """Control run: with ``guest`` sent as a person the assertion fails."""
    os_shadow.roster()
    kernel_door.note_actor("guest")
    lookup_door.sent(lookup_door.WEATHER)
    ask = _asks(door, 1)[0]
    assert ask.get("via") == "panel" and ask["person"] == "panel" and ask["verb"] == "lookup.weather"


#: every function of the lookup modules that makes an outbound call, and the entry function whose door call covers it
LOOKUP_SENDERS = {
    ("routers/weather.py", "_geocode"): "itself", ("routers/weather.py", "_fetch_openmeteo_current"): "itself",
    ("routers/weather.py", "_fetch_openmeteo_forecast"): "itself", ("routers/weather.py", "_fetch_owm_current"): "itself",
    ("routers/weather.py", "_fetch_owm_forecast"): "itself", ("routers/weather.py", "search_locations"): "itself",
    ("research_evidence.py", "_fetch_ddg"): "fetch_web_fallback / fetch_web_fallback_results",
    ("research_evidence.py", "_fetch_tavily"): "fetch_web_fallback",
    ("research_evidence.py", "_fetch_page_price"): "fetch_web_fallback (price enrichment of its own results)",
    ("web_search_provider.py", "tavily_search_outcome"): "fetch_web_fallback / zoe_agent._web_search_ddg",
    ("browser_broker.py", "fetch_page_text"): "itself", ("browser_broker.py", "_execute"): "search_web / fetch_page_text / zoe_agent",
    ("zoe_agent.py", "_ddg_search_sync"): "_web_search_ddg", ("zoe_agent.py", "_cloak_search"): "_web_search_ddg",
    ("zoe_agent.py", "_google_maps_local_search"): "_web_research", ("zoe_agent.py", "_visit_with_search"): "_web_research",
    ("zoe_agent.py", "_web_research"): "itself",
    ("zoe_agent.py", "_web_browse"): "itself",
}
ENTRIES = {"routers/weather.py": {"_geocode": "WEATHER", "_fetch_openmeteo_current": "WEATHER", "_fetch_openmeteo_forecast": "WEATHER",
                                  "_fetch_owm_current": "WEATHER", "_fetch_owm_forecast": "WEATHER", "search_locations": "WEATHER"},
           "zoe_agent.py": {"_web_search_ddg": "WEB_SEARCH", "_web_browse": "WEB_FETCH", "_web_research": "WEB_SEARCH"},
           "browser_broker.py": {"search_web": "WEB_SEARCH", "fetch_page_text": "WEB_FETCH"},
           "research_evidence.py": {"fetch_web_fallback": "WEB_SEARCH", "fetch_web_fallback_results": "WEB_SEARCH"}}


def test_every_lookup_entry_point_tells_the_door_before_it_sends_and_no_sender_is_unlisted():
    """Each entry function's FIRST statement is the door call with its class; and the functions of the lookup modules that
    name an outside host are exactly the listed senders, so a new one fails until it is placed behind an entry point.
    Control run: the door call dropped from ``browser_broker.search_web`` fails; a new ``urlopen`` to a named host in
    ``research_evidence`` fails."""
    import re

    net = re.compile(r"urlopen\(|httpx\.|\.goto\(|_execute\(|tavily_search_outcome\(")
    host = re.compile(r"https?://(?!127\.0\.0\.1|localhost)[A-Za-z0-9.-]+\.[a-z]{2,}")
    for rel, wanted in ENTRIES.items():
        tree = ast.parse((SVC / rel).read_text("utf-8"))
        for fn in tree.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in wanted:
                body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant) else fn.body
                assert f"lookup_door.sent(lookup_door.{wanted[fn.name]})" in ast.unparse(body[0]), (rel, fn.name)
                wanted = {k: v for k, v in wanted.items() if k != fn.name}
        assert not wanted, (rel, wanted)
    found = set()
    for rel in ("routers/weather.py", "research_evidence.py", "web_search_provider.py", "zoe_agent.py"):
        src = (SVC / rel).read_text("utf-8")
        lines = src.split("\n")
        for fn in ast.walk(ast.parse(src)):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                text = "\n".join(lines[fn.lineno - 1:fn.end_lineno])
                if host.search(text) and net.search(text) and "api.telegram.org" not in text:
                    found.add((rel, fn.name))
    listed = {k for k in LOOKUP_SENDERS if k[0] != "browser_broker.py"}
    assert found <= listed | {("zoe_agent.py", "_web_search_ddg")}, found - listed
    weather = {k for k in found if k[0] == "routers/weather.py"}
    assert weather == {k for k in LOOKUP_SENDERS if k[0] == "routers/weather.py"}
    tools = (SVC / "mcp_server.py").read_text("utf-8")
    assert tools.count("lookup_door.sent(lookup_door.WEATHER)") == 2 and tools.count("api.open-meteo.com") == 2


# ---- messages --------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_with_the_messages_flag_off_nothing_is_asked(door, monkeypatch):
    """Control run: with the flag check dropped from ``message_door.ask`` the stub raises."""
    monkeypatch.setenv("ZOE_OS_MESSAGES", "off")
    monkeypatch.setattr(os_shadow, "ask_door", _never)
    assert message_door.stamps_now() is None
    for channel in (message_door.PANEL, message_door.TELEGRAM, message_door.EXTERNAL):
        assert await message_door.ask(channel, ANN, recipient=ANN, stamps_at=None) is None
    message_door.sent(None, True)


@pytest.mark.asyncio
async def test_a_send_asks_first_with_the_channel_and_member_ids_only_and_reports_how_it_ended(door):
    """Control run: with the recipient sent as the acting person the assertion fails."""
    stamps = message_door.stamps_now()
    assert stamps is not None
    asked = await message_door.ask(message_door.TELEGRAM, ANN, recipient=ADULT, stamps_at=stamps)
    panel = await message_door.ask(message_door.PANEL, "guest", stamps_at=stamps)
    message_door.sent(asked, True)
    message_door.sent(panel, False)
    a, p = _asks(door, 2)
    assert (a["area"], a["verb"], a["person"], a["owner"]) == ("messages", "send.telegram", ANN, ADULT)
    assert p["verb"] == "send.panel" and p.get("via") == "panel" and "owner" not in p
    assert all(set(x) <= {"area", "verb", "person", "owner", "visibility", "stamps", "via"} for x in (a, p))
    end = time.monotonic() + 2
    while time.monotonic() < end and len(door.ops("door_report")) < 2:
        await asyncio.sleep(0.02)
    assert [r["outcome"] for r in door.ops("door_report")] == ["done", "failed"]


@pytest.mark.parametrize("reason", ["adults_only", "not_owner", "unknown_person"])
@pytest.mark.asyncio
async def test_a_refused_send_raises_with_a_plain_sentence(door, reason):
    """Control run: with the refusal not raised in ``message_door.ask`` this returns a decision."""
    door.door = f"refuse:{reason}"
    with pytest.raises(message_door.MessageRefused) as exc:
        await message_door.ask(message_door.TELEGRAM, ANN, recipient=ANN, stamps_at=message_door.stamps_now())
    assert exc.value.reason == reason and exc.value.line() != message_door.MessageRefused.fallback


@pytest.mark.parametrize("how", ["slow", "gone", "old_kernel"])
@pytest.mark.asyncio
async def test_a_kernel_that_cannot_answer_lets_a_household_send_go_and_never_an_outside_address(door, how):
    """The one place "kernel down" means NO for a message: an address that is not a known contact or channel of the
    household. Control run: with the ``EXTERNAL`` rule dropped the outside send proceeds ``not_checked``."""
    stamps = message_door.stamps_now()
    if how != "gone":       # (asked and allowed: sent. Not before "gone": an open connection outlives the listener)
        assert (await message_door.ask(message_door.EXTERNAL, ANN, stamps_at=stamps)).decision == "allow"
    if how == "slow":
        door.delay = 0.8
    elif how == "gone":
        door.close()
    else:
        door.door = "old_kernel"
    for channel in (message_door.PANEL, message_door.TELEGRAM):
        assert (await message_door.ask(channel, ANN, recipient=ANN, stamps_at=stamps)).decision == "not_checked"
    with pytest.raises(message_door.MessageRefused) as exc:
        await message_door.ask(message_door.EXTERNAL, ANN, stamps_at=stamps)
    assert exc.value.reason == "unavailable"


async def test_the_intercom_asks_before_it_queues_and_a_refusal_queues_nothing(door, house):  # noqa: F811
    """A member's words on another panel. Control run: with the ask dropped from ``intercom.handle`` the refused
    announcement is queued."""
    res = await _say("tell the kitchen dinner's ready")
    assert res is not None and res.reply == "Okay, I've told the kitchen." and len(await _rows(house)) == 1
    ask = _asks(door, 1)[0]
    assert (ask["verb"], ask["person"]) == ("send.panel", ADULT) and "owner" not in ask
    assert "dinner" not in json.dumps(door.calls, default=str)
    door.door = "refuse:adults_only"
    res = await _say("tell the kitchen the bus is here")
    assert res is not None and res.reply == message_door.MessageRefused("adults_only").line()
    assert len(await _rows(house)) == 1                                                 # nothing more was queued


async def test_the_sign_in_link_asks_before_it_is_sent_to_the_members_own_phone(door, hdb, monkeypatch):  # noqa: F811
    """Control run: with the ask dropped from ``auth_handoff.send_to_phone`` the refused link is sent."""
    monkeypatch.setenv("ZOE_TELEGRAM_BOT_TOKEN", "123:abc")
    _link_telegram(hdb.path)
    calls = []

    async def fake_send(chat_id, text, url):
        calls.append(chat_id)
        return True

    monkeypatch.setattr(auth_handoff, "_telegram_send", fake_send)
    h = await _start()
    assert (await auth_handoff.send_to_phone(h["id"]))["status"] == "sent" and len(calls) == 1
    ask = _asks(door, 1)[0]
    assert (ask["verb"], ask["person"], ask["owner"]) == ("send.telegram", "jason", "jason")
    assert calls[0] not in json.dumps(door.calls, default=str)                          # the chat id never crosses
    door.door = "refuse:adults_only"
    h2 = await _start()
    r = await auth_handoff.send_to_phone(h2["id"])
    assert r == {"ok": False, "status": "unavailable", "detail": message_door.MessageRefused("adults_only").line()} and len(calls) == 1


def test_zoes_own_notifications_never_reach_the_door_and_the_member_senders_do():
    """Automatic sends (reminders, change notices, the day review, proactive nudges) go through ``telegram_notify.send`` and
    ``routers/push.send_push_to_user``; neither they nor their callers name the door. The three member-driven senders do, and
    in each the ask comes before the send. Control run: a ``message_door.ask`` planted in ``reminder_delivery`` fails this."""
    for rel in ("telegram_notify.py", "routers/push.py", "reminder_delivery.py", "change_notices.py", "day_review_raise.py",
                "omi_review_digest.py", "proactive/engine.py", "voice_announce.py"):
        assert "message_door" not in (SVC / rel).read_text("utf-8"), rel
    for rel, fn_name, send in (("intercom.py", "handle", "enqueue_announcement"), ("auth_handoff.py", "send_to_phone", "_telegram_send"),
                               ("mcp_server.py", "_execute_tool", "action_type=\"panel_announce\"")):
        src = (SVC / rel).read_text("utf-8")
        fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_name)
        text = "\n".join(src.split("\n")[fn.lineno - 1:fn.end_lineno])
        assert 0 < text.index("message_door.ask(") < text.index(send), rel
    assert tuple(os_shadow.AREAS["messages"].verbs) == (message_door.PANEL, message_door.TELEGRAM, message_door.PUSH, message_door.EXTERNAL)
    assert tuple(os_shadow.AREAS["lookups"].verbs) == tuple(f"lookup.{k}" for k in lookup_door.KINDS)
