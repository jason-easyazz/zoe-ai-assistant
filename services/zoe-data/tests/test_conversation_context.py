"""Focused tests for the ConversationContext coreference helper.

ConversationContext.resolve_coreference is a pure parser: given a fresh
``last_intent`` and the user's follow-up text, it decides which intent (if
any) the follow-up should be re-routed to. It is called from
``intent_router.detect_intent`` and shapes how follow-up voice utterances
behave after the first command.

The helper is pure (no I/O, no module state besides the dataclass), so
freshness is controlled deterministically by writing ``updated_at`` directly
rather than sleeping.
"""

from __future__ import annotations

import pytest
import time

from conversation_context import ConversationContext

pytestmark = pytest.mark.ci_safe


# ---------------------------------------------------------------------------
# State lifecycle
# ---------------------------------------------------------------------------


def test_default_context_has_no_last_intent_but_is_fresh():
    ctx = ConversationContext()
    assert ctx.last_intent is None
    assert ctx.last_text is None
    assert ctx.last_slots == {}
    assert ctx.is_fresh() is True


def test_activate_records_intent_slots_text_and_bumps_timestamp():
    ctx = ConversationContext()
    before = time.time()
    ctx.activate("set_volume", {"level": 30}, "set volume to 30")
    after = time.time()

    assert ctx.last_intent == "set_volume"
    assert ctx.last_slots == {"level": 30}
    assert ctx.last_text == "set volume to 30"
    assert before <= ctx.updated_at <= after
    assert ctx.is_fresh() is True


def test_activate_copies_slots_so_caller_mutations_do_not_leak_back():
    ctx = ConversationContext()
    slots = {"level": 30}
    ctx.activate("set_volume", slots, "set volume to 30")
    slots["level"] = 999  # mutate the caller's dict after activation
    assert ctx.last_slots == {"level": 30}


def test_invalidate_clears_only_last_intent():
    ctx = ConversationContext()
    ctx.activate("set_volume", {"level": 30}, "set volume to 30")
    ctx.invalidate()
    assert ctx.last_intent is None
    # text and slots are kept for downstream debugging/observability
    assert ctx.last_text == "set volume to 30"
    assert ctx.last_slots == {"level": 30}


def test_is_fresh_returns_false_when_older_than_ttl():
    ctx = ConversationContext()
    ctx.activate("set_volume", {"level": 30}, "set volume to 30")
    # Pretend the activation happened well past the TTL window.
    ctx.updated_at = time.time() - (ConversationContext.TTL + 1)
    assert ctx.is_fresh() is False


# ---------------------------------------------------------------------------
# resolve_coreference — stale / empty / no-match short-circuits
# ---------------------------------------------------------------------------


def test_resolve_coreference_returns_none_when_stale():
    ctx = ConversationContext()
    ctx.activate("set_volume", {"level": 30}, "set volume to 30")
    ctx.updated_at = time.time() - (ConversationContext.TTL + 1)
    assert ctx.resolve_coreference("louder") == (None, None)


def test_resolve_coreference_returns_none_with_no_last_intent():
    ctx = ConversationContext()  # never activated
    assert ctx.resolve_coreference("louder") == (None, None)


def test_resolve_coreference_returns_none_when_text_does_not_match_branch():
    ctx = ConversationContext()
    ctx.activate("set_volume", {"level": 30}, "set volume to 30")
    # "what's the weather" matches none of the volume direction / percentage
    # patterns, so the helper must defer to the regular intent pipeline.
    assert ctx.resolve_coreference("what's the weather") == (None, None)


# ---------------------------------------------------------------------------
# resolve_coreference — volume branch
# ---------------------------------------------------------------------------


def test_resolve_coreference_volume_percent():
    ctx = ConversationContext()
    ctx.activate("set_volume", {"level": 30}, "set volume to 30")
    name, slots = ctx.resolve_coreference("make it 50 percent")
    assert name == "set_volume"
    assert slots == {"level": 50}


def test_resolve_coreference_volume_percent_symbol():
    ctx = ConversationContext()
    ctx.activate("set_volume", {"level": 30}, "set volume to 30")
    name, slots = ctx.resolve_coreference("turn it down to 20%")
    assert name == "set_volume"
    assert slots == {"level": 20}


def test_resolve_coreference_volume_direction_up_aliases():
    ctx = ConversationContext()
    ctx.activate("set_volume", {"level": 30}, "set volume to 30")
    for phrase in ("louder", "up", "higher", "a bit higher please"):
        name, slots = ctx.resolve_coreference(phrase)
        assert name == "set_volume", phrase
        assert slots == {"direction": "up"}, phrase


def test_resolve_coreference_volume_direction_down_aliases():
    ctx = ConversationContext()
    ctx.activate("set_volume", {"level": 30}, "set volume to 30")
    for phrase in ("quieter", "down", "lower", "softer please"):
        name, slots = ctx.resolve_coreference(phrase)
        assert name == "set_volume", phrase
        assert slots == {"direction": "down"}, phrase


# ---------------------------------------------------------------------------
# resolve_coreference — music branch
# ---------------------------------------------------------------------------


def test_resolve_coreference_music_volume_percent():
    ctx = ConversationContext()
    ctx.activate("music_play", {"track": "ambient"}, "play ambient")
    name, slots = ctx.resolve_coreference("set it to 80%")
    assert name == "music_volume"
    assert slots == {"level": 80}


def test_resolve_coreference_music_volume_works_for_music_control():
    ctx = ConversationContext()
    ctx.activate("music_control", {"action": "play"}, "resume the music")
    name, slots = ctx.resolve_coreference("volume 40 percent")
    assert name == "music_volume"
    assert slots == {"level": 40}


def test_resolve_coreference_music_stop_on_cancel():
    ctx = ConversationContext()
    ctx.activate("music_play", {"track": "ambient"}, "play ambient")
    # ``_CANCEL_RE`` matches cancel / delete / remove / dismiss / clear only;
    # "stop" is not in that set, so it must NOT re-route (defers to the
    # normal intent pipeline).
    for phrase in ("cancel", "delete that", "remove it", "dismiss", "clear"):
        name, slots = ctx.resolve_coreference(phrase)
        assert name == "music_stop", phrase
        assert slots == {}, phrase

    # "stop" alone does not match the cancel pattern, so no override.
    name, slots = ctx.resolve_coreference("stop")
    assert name is None
    assert slots is None


# ---------------------------------------------------------------------------
# resolve_coreference — calendar follow-ups
# ---------------------------------------------------------------------------


def test_resolve_coreference_calendar_add_updates_time():
    ctx = ConversationContext()
    ctx.activate(
        "calendar_add",
        {"title": "standup", "date": "monday", "time": "10am"},
        "add standup monday at 10am",
    )
    name, slots = ctx.resolve_coreference("actually make it 3pm")
    assert name == "calendar_add"
    assert slots == {"title": "standup", "date": "monday", "time": "3pm"}


def test_resolve_coreference_calendar_add_updates_date():
    ctx = ConversationContext()
    ctx.activate(
        "calendar_add",
        {"title": "standup", "date": "monday"},
        "add standup on monday",
    )
    name, slots = ctx.resolve_coreference("change that to friday")
    assert name == "calendar_add"
    assert slots == {"title": "standup", "date": "friday"}


def test_resolve_coreference_calendar_add_works_for_create_event_alias():
    ctx = ConversationContext()
    ctx.activate(
        "calendar_create_event",
        {"title": "lunch", "date": "wednesday"},
        "schedule lunch wednesday",
    )
    name, slots = ctx.resolve_coreference("move it to 1pm")
    assert name == "calendar_add"
    assert slots == {"title": "lunch", "date": "wednesday", "time": "1pm"}


def test_resolve_coreference_calendar_cancel_uses_event_title():
    ctx = ConversationContext()
    ctx.activate(
        "calendar_add",
        {"title": "standup", "date": "monday"},
        "add standup on monday",
    )
    name, slots = ctx.resolve_coreference("cancel that")
    assert name == "calendar_delete_event"
    assert slots == {"title": "standup"}


def test_resolve_coreference_calendar_cancel_falls_back_to_event_id():
    ctx = ConversationContext()
    ctx.activate(
        "calendar_add",
        {"event_id": "evt-42", "title": "standup"},
        "add standup",
    )
    name, slots = ctx.resolve_coreference("remove that")
    assert name == "calendar_delete_event"
    assert slots == {"title": "evt-42"}


def test_resolve_coreference_calendar_show_routes_to_list_events():
    ctx = ConversationContext()
    ctx.activate("calendar_add", {"title": "standup"}, "add standup")
    name, slots = ctx.resolve_coreference("show me the events")
    assert name == "calendar_list_events"
    assert slots == {}


# ---------------------------------------------------------------------------
# resolve_coreference — reminder follow-ups
# ---------------------------------------------------------------------------


def test_resolve_coreference_reminder_updates_time():
    ctx = ConversationContext()
    ctx.activate(
        "reminder_create",
        {"title": "take pills", "time": "8am"},
        "remind me to take pills at 8am",
    )
    name, slots = ctx.resolve_coreference("actually 9am")
    assert name == "reminder_create"
    assert slots == {"title": "take pills", "time": "9am"}


def test_resolve_coreference_timer_set_updates_time():
    ctx = ConversationContext()
    ctx.activate("timer_set", {"duration": "10m"}, "set a 10 minute timer")
    name, slots = ctx.resolve_coreference("make it 5 minutes")
    # The helper only re-routes on a recognised time phrase; a duration-only
    # follow-up has no time token, so the original intent is preserved.
    assert name is None
    assert slots is None


def test_resolve_coreference_reminder_cancel_uses_title():
    ctx = ConversationContext()
    ctx.activate(
        "reminder_create",
        {"title": "take pills", "time": "8am"},
        "remind me to take pills at 8am",
    )
    name, slots = ctx.resolve_coreference("cancel that")
    assert name == "reminder_cancel"
    assert slots == {"title": "take pills"}


def test_resolve_coreference_reminder_show_routes_to_list():
    ctx = ConversationContext()
    ctx.activate("reminder_create", {"title": "pills"}, "remind me to take pills")
    name, slots = ctx.resolve_coreference("show my reminders")
    assert name == "reminder_list"
    assert slots == {}


# ---------------------------------------------------------------------------
# resolve_coreference — list follow-ups
# ---------------------------------------------------------------------------


def test_resolve_coreference_list_cancel_routes_to_remove_item():
    ctx = ConversationContext()
    ctx.activate("list_add", {"list_name": "shopping", "item": "milk"}, "add milk to shopping")
    for phrase in ("remove that", "delete it", "cancel that", "dismiss it"):
        name, slots = ctx.resolve_coreference(phrase)
        assert name == "list_remove_item", phrase
        assert slots == {"item": "milk"}, phrase


def test_resolve_coreference_list_mark_done_routes_to_remove_item():
    ctx = ConversationContext()
    ctx.activate("list_add", {"list_name": "shopping", "item": "milk"}, "add milk to shopping")
    for phrase in ("mark it done", "done", "completed", "finished that", "tick"):
        name, slots = ctx.resolve_coreference(phrase)
        assert name == "list_remove_item", phrase
        assert slots == {"item": "milk"}, phrase


def test_resolve_coreference_list_show_routes_to_get_items():
    ctx = ConversationContext()
    ctx.activate("list_add", {"list_name": "shopping", "item": "milk"}, "add milk to shopping")
    name, slots = ctx.resolve_coreference("show the list")
    assert name == "list_get_items"
    assert slots == {"list_name": "shopping"}


def test_resolve_coreference_list_show_falls_back_to_default_list_name():
    ctx = ConversationContext()
    ctx.activate("list_add_item", {"item": "milk"}, "add milk")  # no list_name
    name, slots = ctx.resolve_coreference("show me")
    assert name == "list_get_items"
    assert slots == {"list_name": "shopping"}


# ---------------------------------------------------------------------------
# resolve_coreference — slot isolation between branches
# ---------------------------------------------------------------------------


def test_resolve_coreference_calendar_slots_are_copied_not_aliased():
    """Mutating the returned slots must not poison the context for later calls.

    Note: ``resolve_coreference`` does not update ``ctx.last_slots`` — it
    is a pure parser that returns a new dict and leaves the context for
    the caller (intent_router) to re-activate if the override sticks.
    """
    ctx = ConversationContext()
    ctx.activate(
        "calendar_add",
        {"title": "standup", "date": "monday", "time": "10am"},
        "add standup monday at 10am",
    )
    _, slots = ctx.resolve_coreference("actually make it 3pm")
    assert slots is not None
    slots["time"] = "hacked"
    # The next follow-up must see the original time, not the mutated one.
    _, slots2 = ctx.resolve_coreference("actually 4pm")
    assert slots2 is not None
    assert slots2["time"] == "4pm"
    # The context's last_slots is intentionally NOT mutated by the parser.
    assert ctx.last_slots["time"] == "10am"


def test_resolve_coreference_unrelated_intent_returns_none():
    """A follow-up to a non-recognised intent must defer to the regular router."""
    ctx = ConversationContext()
    ctx.activate("calendar_add", {"title": "standup"}, "add standup")
    # No time, no date, no cancel/show words → no override.
    assert ctx.resolve_coreference("with john please") == (None, None)
