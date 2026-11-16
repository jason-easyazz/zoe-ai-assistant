"""``remind.set`` takes an absolute ``due_at`` (integer UTC epoch seconds) as well as ``in_minutes``: exactly one of the two.

The production shadow already holds the reminder's absolute instant, so it sends ``due_at``; the brain keeps ``in_minutes`` and never
sees ``due_at`` (a ``from: caller`` param the bridge hides). The read-back returns the STORED due instant as ``due_at`` however it
was set. A ``due_at`` in the past is accepted and stored as given (the driver records, it does not fire); one outside 2000..2100
(a millisecond timestamp, a negative number) is refused. Synthetic people and notes only.
"""
from __future__ import annotations

import pytest
from brain_rig import BrainBox
from world import NOW, SAM, household

from zoe_os.brain.bridge import BrainBridge, Turn
from zoe_os.brain.drivers import BRAIN, DUE_MAX, Reminders
from zoe_os.events import Event, Kind
from zoe_os.receipts import ONE_OF
from zoe_os.spine import Refused

HOUR = 3600


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.rem = Reminders(lambda: NOW).register(kernel)
    kernel.grants.install(BRAIN)
    kernel.grants.grant(SAM, "brain")
    yield kernel
    kernel.close()


def remind(k, **when):
    said = k.spine.publish(Event(Kind.UTTERANCE, "voice", SAM, payload={"text": "synthetic reminder request"}))
    return k.actions.act("brain", "remind.set", SAM, {"note": "synthetic note", "asked": said.seq, **when},
                         caused_by=(said.seq,))


def test_in_minutes_still_works_and_its_read_back_names_the_stored_due_instant(k):
    r = remind(k, in_minutes=10)
    assert r.verified and r.proof == {"note": "synthetic note", "due_at": int(NOW) + 600}
    assert k.rem.rows[SAM][0]["due"] == int(NOW) + 600


def test_due_at_is_stored_as_given_and_read_back(k):
    due = int(NOW) + 2 * HOUR
    r = remind(k, due_at=due)
    assert r.verified and r.proof == {"note": "synthetic note", "due_at": due}
    assert [row["due"] for row in k.rem.rows[SAM]] == [due]
    back = k.actions.undo(SAM, actor=SAM)                              # reversible like any reminder
    assert back.verified and k.rem.rows.get(SAM, []) == []


@pytest.mark.parametrize("when", [{}, {"in_minutes": 10, "due_at": int(NOW) + HOUR}])
def test_both_or_neither_is_refused_with_a_code_and_nothing_is_stored(k, when):
    with pytest.raises(Refused, match=ONE_OF):
        remind(k, **when)
    assert k.rem.rows.get(SAM, []) == []


def test_a_due_at_in_the_past_is_accepted_and_stored_as_given(k):
    past = int(NOW) - HOUR
    r = remind(k, due_at=past)
    assert r.verified and r.proof["due_at"] == past and k.rem.rows[SAM][0]["due"] == past


@pytest.mark.parametrize("bad", [DUE_MAX + 1, int(NOW) * 1000, -1, 0, True])
def test_a_due_at_absurdly_far_ahead_in_milliseconds_or_negative_is_refused(k, bad):
    with pytest.raises(Refused, match="wrong argument type"):
        remind(k, due_at=bad)
    assert k.rem.rows.get(SAM, []) == []
    assert remind(k, due_at=DUE_MAX).verified                        # the boundary itself is a date


def test_describe_declares_due_at_and_the_brain_never_sees_it():
    box = BrainBox()
    try:
        box.household()
        (entry,) = [a for a in box.client("brain").describe() if a["action"] == "remind.set"]
        assert entry["params"]["due_at"] == {"type": "integer", "required": False, "one_of": "when", "from": "caller",
                                             "minimum": 946_684_800, "maximum": DUE_MAX,
                                             "description": "the due instant, UTC epoch seconds"}
        bridge = box.bridge()
        (tool,) = [t["function"] for t in bridge.tools() if t["function"]["name"] == "remind_set"]
        assert tool["parameters"]["required"] == ["in_minutes", "note"]          # the brain's schema did not change
        assert "due_at" not in tool["parameters"]["properties"]
        turn = box.say(SAM, "synthetic reminder request")
        out = bridge.propose(turn, "remind_set", {"note": "n", "due_at": int(NOW) + HOUR})
        assert out.status == "invalid" and out.reason == "unknown argument"     # the model cannot set it
        assert bridge.propose(turn, "remind_set", {"note": "n"}).reason == ONE_OF
        assert bridge.propose(box.say(SAM, "synthetic"), "remind_set", {"note": "n", "in_minutes": 5}).status == "done"
        said = box.say(SAM, "synthetic shadow add")                    # a caller on the wire (the shadow) sends due_at
        r = box.client("brain").act("remind.set", SAM, {"note": "n", "due_at": int(NOW) + HOUR, "asked": said.cause},
                                    caused_by=[said.cause])
        assert r.verified and r.proof["due_at"] == int(NOW) + HOUR
        bridge.close()
    finally:
        box.close()


def test_the_bridge_hides_every_from_param_and_keeps_a_sole_alternative_required():
    vis = BrainBridge._visible({"params": {"a": {"type": "integer", "one_of": "w"}, "b": {"type": "integer", "one_of": "w",
                                                                                          "from": "caller"},
                                           "c": {"type": "integer", "from": "turn"}}})
    assert set(vis) == {"a"} and BrainBridge._required(vis) == ["a"]
    assert isinstance(Turn(SAM, 1), Turn)
