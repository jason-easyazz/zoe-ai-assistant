"""Voice failures from the panel, 2026-10-29 02:17-02:23 - each pinned, each red without its fix. Generic phrases only.

1. a bare "what else?" follows the previous turn's domain (or the brain), never a random fast domain (live: four of them after a
   memory answer all routed to ``lists``);
2. a list item stored with STT punctuation / duplication ("x? X") is normalised on add, and remove matches the artefact;
3. "And <item> to the shopping list" (STT: "Add" -> "And") is an add; "and what's on the list?" stays a query;
   an item already on the list is never added twice - the reply says so, for single and multi-item adds;
4. "turn the light" / "switch the light" (no on / off) asks "On or off?"; everything that already worked still does;
5. the wake word's non-word mishearings ("Azoi") are stripped, real words that start like it are not.

No DB, no network, no models.
"""
from __future__ import annotations

import pytest

import conversation_cues
import lexicons
import list_service
import skybridge_service as sky
import turn_continuation
from skybridge_service import classify_skybridge_intent as classify
from skybridge_service import resolve_skybridge_request
from stt_wake_strip import _strip_wake_word
from test_skybridge_service import FakeDb

pytestmark = pytest.mark.ci_safe


# ── 1. continuation follows the previous domain ──────────────────────────────
@pytest.fixture(autouse=True)
def _clean_continuation_state():
    turn_continuation._LAST.clear()
    yield
    turn_continuation._LAST.clear()


def _decision(routed, tool=None):
    d = {"domain": routed, "routed": routed, "score": 0.65, "scores": {routed: 0.65}, "ms": 1.0}
    if tool:
        d["two_stage"] = {"domain": routed, "tool": tool}
    return d


@pytest.mark.parametrize("text", ["what else?", "What else?", "and?", "tell me more", "anything else", "go on", "okay, what else",
                                  "and what else", "keep going"])
def test_continuation_cue_matches(text):
    assert conversation_cues.turn_continuation(text)


@pytest.mark.parametrize("text", ["what else do you know about me", "what's on the shopping list", "add milk and eggs",
                                  "what time is it", "anything else on my calendar today", "turn on the light", "", "continue the song"])
def test_continuation_cue_does_not_swallow_real_requests(text):
    assert not conversation_cues.turn_continuation(text)


def test_lexicon_examples_hold():
    sec = lexicons.load("en")["turn_continuation"]
    assert all(conversation_cues.turn_continuation(x) for x in sec["examples"])
    assert not any(conversation_cues.turn_continuation(x) for x in sec["not_continuations"])


def test_what_else_after_a_memory_turn_stays_on_memory_not_lists():
    turn_continuation.follow("panel", "what do you know about me?", _decision("memory", "recall_memory"), now=100.0)
    # the router's own (wrong) verdict on the bare words: lists, with a list tool
    out = turn_continuation.follow("panel", "What else?", _decision("lists", "show_list"), now=110.0)
    assert out["routed"] == "memory" and out["domain"] == "memory"
    assert "two_stage" not in out and out["continuation"] is True


def test_a_run_of_what_else_keeps_the_topic_alive():
    turn_continuation.follow("panel", "tell me about my day", _decision("chat"), now=0.0)
    turn_continuation.follow("panel", "anything else?", _decision("people"), now=50.0)
    again = turn_continuation.follow("panel", "what else?", _decision("lists"), now=100.0)   # 100 s after the topic, 50 s after the last
    assert again["routed"] == "chat"
    turn_continuation.follow("panel", "whats the weather", _decision("weather"), now=200.0)
    assert turn_continuation.follow("panel", "what else?", _decision("lists"), now=205.0)["routed"] == "chat"   # weather read -> brain


def test_continuation_never_lands_on_a_fast_domain():
    for prev in ("lists", "calendar", "weather", "time", "timers", "smart_home", "music"):
        turn_continuation._LAST.clear()
        turn_continuation.follow("p", "something specific", _decision(prev), now=0.0)
        assert turn_continuation.follow("p", "what else?", _decision("lists", "show_list"), now=5.0)["routed"] == "chat", prev


def test_continuation_outside_the_window_or_on_another_panel_goes_to_the_brain():
    turn_continuation.follow("a", "what do you know about me", _decision("memory"), now=0.0)
    assert turn_continuation.follow("a", "what else?", _decision("lists"), now=61.0)["routed"] == "chat"
    assert turn_continuation.follow("b", "what else?", _decision("lists"), now=1.0)["routed"] == "chat"


def test_a_real_turn_is_returned_untouched_and_remembered():
    d = _decision("lists", "show_list")
    assert turn_continuation.follow("p", "what's on the shopping list", d, now=0.0) is d


def test_voice_command_applies_the_continuation_rule_to_the_router_verdict():
    import inspect

    from routers import voice_tts

    src = inspect.getsource(voice_tts.voice_command)
    assert "_turn_cont.follow(" in src and src.index("_turn_cont.follow(") < src.index("_spacer.note_router(_router_decision)")


# ── 2. list item normalisation and tolerant matching ─────────────────────────
@pytest.mark.parametrize("raw,want", [("noodles? Noodles", "noodles"), ("Milk!", "Milk"), ("eggs eggs", "eggs"),
                                      ("what? why?", "what why"), ("peanut butter", "peanut butter"), ("mac and cheese", "mac and cheese")])
def test_normalize_item_text(raw, want):
    assert list_service.normalize_item_text(raw) == want


def test_item_key_is_case_plural_and_punctuation_insensitive():
    assert list_service.item_key("Eggs") == list_service.item_key("egg") == list_service.item_key("eggs!")
    assert list_service.item_key("noodles? Noodles") == list_service.item_key("noodles")
    assert list_service.item_key("bread") != list_service.item_key("butter")


def test_remove_matches_the_stored_artefact_and_a_plural():
    items = [{"id": "1", "text": "noodles? Noodles"}, {"id": "2", "text": "milk"}]
    assert sky._match_list_items_by_text(items, "noodles") == [items[0]]
    assert sky._match_list_items_by_text([{"id": "3", "text": "Eggs"}], "egg") == [{"id": "3", "text": "Eggs"}]
    # a plural said for a singular stored (and the artefact) - substring matching alone cannot find these
    assert sky._match_list_items_by_text([{"id": "4", "text": "noodle? Noodle"}], "noodles") == [{"id": "4", "text": "noodle? Noodle"}]
    assert sky._match_list_items_by_text([{"id": "5", "text": "Egg"}], "eggs") == [{"id": "5", "text": "Egg"}]
    assert sky._match_list_items_by_text(items, "butter") == []


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)


class _ListDb:
    """A minimal shim for list_service: one household list, scripted open items; records inserts."""

    def __init__(self, open_items=()):
        self.open_items = [dict(i) for i in open_items]
        self.inserts = []

    async def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith("SELECT"):
            if "FROM list_items" in sql and "list_id=?" in sql and "created_at" not in sql:
                return _Cursor(self.open_items)
            if "created_at" in sql:        # the 10 s replay probe
                return _Cursor([{"id": "replayed"}] if getattr(self, "replay", False) else [])
            return _Cursor([{"id": "L1"}])
        if "INSERT INTO list_items" in sql:
            self.inserts.append(params)
        return _Cursor([])


@pytest.mark.asyncio
async def test_add_stores_the_normalised_text():
    db = _ListDb()
    await list_service.add_item_to_list(db, user_id="u", list_type="shopping", list_name="Shopping", text="noodles? Noodles",
                                         new_list_visibility="family")
    assert [p[2] for p in db.inserts] == ["noodles"]


# ── 3. "And X to the shopping list" is an add; real queries stay queries ─────
@pytest.mark.parametrize("text,item", [("And eggs to the shopping list", "eggs"), ("And eggs to the shopping list.", "eggs"),
                                        ("At eggs to the shopping list", "eggs"), ("and bread to my shopping list", "bread")])
def test_stt_and_for_add_is_an_add(text, item):
    intent = classify(text, None)
    assert (intent.domain, intent.action, intent.item_text) == ("lists", "add_item", item)


@pytest.mark.parametrize("text", ["what's on the list?", "and what's on the list?", "and what's on the shopping list", "show the shopping list",
                                  "and go to the shopping list", "and what is on my shopping list?",
                                  "and navigate to the shopping list", "and head to the shopping list"])
def test_real_list_queries_stay_queries(text):
    intent = classify(text, None)
    assert intent is None or intent.action != "add_item"


_LIST = {"id": "list-1", "user_id": "family-admin", "name": "Groceries", "list_type": "shopping", "description": "", "visibility": "family"}


def _row(i, text, completed=False):
    return {"id": i, "list_id": "list-1", "text": text, "priority": "normal", "category": "", "quantity": "", "completed": completed}


def _inserts(db):
    return [c for c in db.executed if "INSERT INTO list_items" in str(c[0])]


@pytest.mark.asyncio
async def test_add_of_an_existing_item_makes_no_duplicate_and_says_so():
    db = FakeDb(lists=[dict(_LIST)], items_by_list={"list-1": [_row("i1", "Milk")]})
    result = await resolve_skybridge_request("add milk to the shopping list", "family-admin", db=db)
    assert _inserts(db) == []
    assert result["spoken_summary"] == "Milk's already on the shopping list."


@pytest.mark.asyncio
async def test_existing_item_match_ignores_case_plural_and_stored_punctuation():
    db = FakeDb(lists=[dict(_LIST)], items_by_list={"list-1": [_row("i1", "egg? Egg")]})
    result = await resolve_skybridge_request("add eggs to the shopping list", "family-admin", db=db)
    assert _inserts(db) == [] and "already on the shopping list" in result["spoken_summary"]


@pytest.mark.asyncio
async def test_stt_and_add_of_an_existing_item_is_not_added_twice():
    db = FakeDb(lists=[dict(_LIST)], items_by_list={"list-1": [_row("i1", "milk")]})
    result = await resolve_skybridge_request("And milk to the shopping list", "family-admin", db=db)
    assert result["intent"]["action"] == "add_item"
    assert _inserts(db) == []
    assert result["spoken_summary"] == "Milk's already on the shopping list."


@pytest.mark.asyncio
async def test_multi_item_add_adds_the_new_ones_and_names_the_existing():
    db = FakeDb(lists=[dict(_LIST)], items_by_list={"list-1": [_row("i1", "milk")]})
    result = await resolve_skybridge_request("add eggs and milk to the shopping list", "family-admin", db=db)
    assert [c[3] for c in _inserts(db)] == ["eggs"]
    assert result["spoken_summary"] == "Added eggs to the shopping list, and milk's already on it."


@pytest.mark.asyncio
async def test_multi_item_add_of_all_new_items_adds_each():
    db = FakeDb(lists=[dict(_LIST)], items_by_list={"list-1": []})
    result = await resolve_skybridge_request("add eggs and bread to the shopping list", "family-admin", db=db)
    assert [c[3] for c in _inserts(db)] == ["eggs", "bread"]
    assert result["spoken_summary"] == "Added eggs and bread to the shopping list."


@pytest.mark.asyncio
async def test_only_open_items_count_as_already_there():
    db = _ListDb(open_items=[])     # the shim serves the open-items query; a ticked-off item is simply not in it
    await list_service.add_item_to_list(db, user_id="u", list_type="shopping", list_name="Shopping", text="milk",
                                        new_list_visibility="family")
    assert [p[2] for p in db.inserts] == ["milk"]


@pytest.mark.asyncio
async def test_a_todo_task_with_and_is_one_task():
    db = _ListDb()
    out = await list_service.add_items_to_list(db, user_id="u", list_type="todo", list_name="Todo", text="call mum and dad",
                                               new_list_visibility="family")
    assert [p[2] for p in db.inserts] == ["call mum and dad"] and out["added"] == ["call mum and dad"]


def test_compound_foods_stay_one_item_and_lists_split():
    assert list_service.split_items("salt and pepper") == ["salt and pepper"]
    assert list_service.split_items("eggs, milk and bread") == ["eggs", "milk", "bread"]
    assert list_service.split_items("noodles? Noodles") == ["noodles"]


@pytest.mark.asyncio
async def test_direct_add_path_reports_already_there_without_a_second_row():
    db = _ListDb(open_items=[{"id": "x1", "text": "Milk"}])
    out = await list_service.add_items_to_list(db, user_id="u", list_type="shopping", list_name="Shopping", text="milk and eggs",
                                               new_list_visibility="family")
    assert out["already"] == ["Milk"] and out["added"] == ["eggs"]
    assert [p[2] for p in db.inserts] == ["eggs"]
    assert list_service.add_reply(out["added"], out["already"], "your shopping list") == \
        "Added eggs to your shopping list, and Milk's already on it."


# ── 4. bare "turn the light" asks on or off ──────────────────────────────────
@pytest.mark.parametrize("text", ["Turn the light", "Let's turn the light.", "switch the light", "turn the lamp", "flip the lamp",
                                  "could you turn the light please"])
def test_bare_verb_and_device_asks_on_or_off(text):
    intent = classify(text, None)
    assert (intent.domain, intent.action) == ("smart_home", "clarify_state")


@pytest.mark.parametrize("text,action", [("turn on the light", "turn_on"), ("turn off the lamp", "turn_off"), ("Turn the light on", "turn_on"),
                                         ("switch the lamp off", "turn_off"), ("can you turn the light on please", "turn_on"),
                                         ("dim the light to 40 percent", "set_brightness")])
def test_working_commands_are_unchanged(text, action):
    assert classify(text, None).action == action


def test_scene_and_entity_marker_still_win():
    assert classify("activate the movie scene", None).action == "activate_scene"
    assert classify("turn on @light.kitchen", None).entity_id == "light.kitchen"
    assert classify("turn @light.kitchen off", None).action == "turn_off"


@pytest.mark.parametrize("text", ["Turn bedroom lamp red", "turn of the lights", "turn the lamp up", "turn the light to blue"])
def test_other_shapes_are_not_claimed_as_on_or_off(text):
    intent = classify(text, None)
    assert intent is None or intent.action != "clarify_state"


def test_turn_without_a_device_is_not_claimed():
    intent = classify("turn the page", None)
    assert intent is None or intent.action != "clarify_state"


@pytest.mark.asyncio
async def test_the_clarify_reply_is_one_short_question():
    from smart_home_service import resolve_smart_home

    out = await resolve_smart_home(classify("turn the light", None))
    assert out["handled"] is True and out["spoken_summary"] == "On or off?"


# ── 5. wake-word mishearings ─────────────────────────────────────────────────
@pytest.mark.parametrize("lines,want", [(["Azoi, Turn the white."], "Turn the white."), (["A zoi, turn the light"], "Turn the light"),
                                         (["Azoi.", "turn the light"], "Turn the light"), (["Hey", "Azoi turn it on"], "Turn it on")])
def test_misheard_wake_word_is_stripped(lines, want):
    assert _strip_wake_word(lines) == want


@pytest.mark.parametrize("lines", [["A zoe is a cat"], ["Azure sky today"], ["a zoo trip please"]])
def test_real_words_that_start_alike_are_kept(lines):
    assert _strip_wake_word(lines).lower() == lines[0].lower()


def test_already_reply_grammar_for_plurals_and_mixed_adds():
    assert list_service.add_reply([], ["eggs"], "the shopping list") == "Eggs are already on the shopping list."
    assert list_service.add_reply([], ["milk", "bread"], "the shopping list") == "Milk and bread are already on the shopping list."
    assert list_service.add_reply(["milk"], ["eggs"], "the shopping list") == "Added milk to the shopping list, and eggs are already on it."


@pytest.mark.asyncio
async def test_direct_path_notifies_and_audits_only_items_really_added(monkeypatch):
    import intent_router
    audited = []
    monkeypatch.setattr(intent_router.logger, "info", lambda msg, *a, **k: audited.append(msg % a if a else msg))

    seen = []

    async def fake_notify(kind, data):
        seen.append(data["item"]["text"])

    class _Ctx:
        async def __aenter__(self):
            return _ListDb(open_items=[{"id": "x1", "text": "Milk"}])

        async def __aexit__(self, *a):
            return False

    import database

    monkeypatch.setattr(database, "get_db_ctx", lambda: _Ctx())
    monkeypatch.setattr(intent_router, "_notify_lists_ui", fake_notify)
    out = await intent_router._execute_list_add_direct(
        intent_router.Intent("list_add", {"item": "milk and eggs", "list_type": "shopping"}), "jason")
    assert seen == ["eggs"]                       # the existing item and the unsplit phrase are never announced
    assert sum("list_item_audit op=add" in line for line in audited) == 1
    assert out == "Added eggs to your shopping list, and Milk's already on it."


@pytest.mark.asyncio
async def test_direct_path_reports_nothing_for_a_replayed_add(monkeypatch):
    """A 10 s replay of the same turn still ANSWERS as added (idempotency), but inserted nothing: no audit line, no UI notification."""
    import database
    import intent_router

    seen, audited = [], []

    async def fake_notify(kind, data):
        seen.append(data)

    db = _ListDb()
    db.replay = True

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(database, "get_db_ctx", lambda: _Ctx())
    monkeypatch.setattr(intent_router, "_notify_lists_ui", fake_notify)
    monkeypatch.setattr(intent_router.logger, "info", lambda msg, *a, **k: audited.append(msg % a if a else msg))
    out = await intent_router._execute_list_add_direct(
        intent_router.Intent("list_add", {"item": "milk", "list_type": "shopping"}), "jason")
    assert out == "Added milk to your shopping list."
    assert seen == [] and db.inserts == [] and not any("list_item_audit" in line for line in audited)
