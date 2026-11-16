"""The bridge adapters: ids only, never in the caller's way."""
from __future__ import annotations

import pytest

from zoe_os import bridge
from zoe_os.capability import Enforcer, GrantStore, Person
from zoe_os.events import Kind, Visibility
from zoe_os.spine import Spine

ADAPTERS = [{"skill": f"bridge.{n}", "emits": [k], "origin": True} for n, k in (
    ("brain", "turn_end"), ("reminders", "reminder_fired"), ("calendar", "calendar_change"),
    ("presence", "presence"))]
READER = {"skill": "reader", "reads": {k: "private" for k in
                                       ("turn_end", "reminder_fired", "calendar_change", "presence")}}


@pytest.fixture
def spine(tmp_path):
    store = GrantStore([Person("member-1")])
    for m in (*ADAPTERS, READER):
        store.install(m)
    store.grant("member-1", "reader")
    s = Spine(str(tmp_path / "s.db"), Enforcer(store))
    yield s
    s.close()


def all_four():
    return [
        bridge.turn_end(user_id="member-1", session_id="s-1", tool_names=("set_timer",), replay=False),
        bridge.reminder_fired(user_id="member-1", item_id="r-1", pending_id="p-1"),
        bridge.calendar_changed(user_id="member-1", event_id="e-1", change="created",
                                household_visible=False, starts_at=1.0),
        bridge.presence_changed(member_id="member-1", panel_id="panel-1", state="open", confidence=0.9),
    ]


def test_the_four_call_sites_publish_typed_private_events(spine):
    b = bridge.Bridge(spine)
    assert all(b.publish(ev) for ev in all_four()) and b.published == 4
    got = list(spine.replay("reader"))
    assert [e.kind for e in got] == [Kind.TURN_END, Kind.REMINDER_FIRED, Kind.CALENDAR_CHANGE,
                                     Kind.PRESENCE]
    assert all(e.visibility is Visibility.PRIVATE and e.subject == "member-1" for e in got)


def test_no_free_text_crosses_the_bridge():
    from zoe_os.events import CONTENT_KEYS
    for ev in all_four():
        assert not CONTENT_KEYS & set(ev.payload)


def test_a_replay_gate_turn_is_not_a_household_event(spine):
    assert bridge.turn_end(user_id="member-1", session_id="s", tool_names=(), replay=True) is None
    assert bridge.Bridge(spine).publish(None) is False


def test_the_bridge_never_raises_into_the_caller(spine):
    spine.authorizer.store.manifests.pop("bridge.reminders")     # an unregistered adapter
    b = bridge.Bridge(spine)
    assert b.publish(bridge.reminder_fired(user_id="member-1", item_id="r", pending_id="p")) is False
    assert b.dropped == 1
    assert bridge.Bridge(None).publish(all_four()[0]) is False    # the flag-off shape: no spine


def test_a_forget_on_the_bridge_reaches_the_spine(spine):
    b = bridge.Bridge(spine)
    for ev in all_four():
        b.publish(ev)
    assert b.forget(user_id="member-1", actor="member-1")
    assert [e.kind for e in spine.replay("reader")] == [Kind.FORGET]
