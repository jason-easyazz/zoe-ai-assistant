"""The spoken side of household notifications (``notification_tail.py``, flag ``ZOE_NOTIFICATIONS``): one capped line at the end of
a verified member's own turn, and "read them". Spec: docs/knowledge/notifications.md. Each guard has a NEGATIVE CONTROL beside it.
Synthetic ids and text only.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

from datetime import datetime, timezone

import notification_tail as nt
import notifications_store as ns
import transcript_check as tc
from test_notifications_panel import MEMBER, OTHER, PANEL, SVC, env, run, seed  # noqa: F401

DAY = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)


class _Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def tenv(env, monkeypatch):
    import proactive.pull as pull
    monkeypatch.setattr(pull, "_quiet", lambda now: False)
    monkeypatch.setattr(tc, "_is_emotional", lambda text, member: text == "i feel awful")
    env.clock = _Clock()
    monkeypatch.setattr(nt, "_clock", env.clock)
    monkeypatch.setattr(nt, "_now", lambda: DAY)
    nt.reset()
    tc.reset()
    yield env
    nt.reset()
    tc.reset()


def verified(session="s1", member=MEMBER, verdict=True, panel=PANEL):
    nt.note_voice_turn(session, panel, scope_user=member, verdict=verdict)


# -- the tail ------------------------------------------------------------------------------------------------------------------
def test_the_tail_rides_a_verified_turn_once(tenv):
    seed(3)
    verified()
    out = run(nt.append_to_reply("Done, the kettle is on.", "put the kettle on", MEMBER, "s1"))
    assert out == ("Done, the kettle is on. You also have three notifications. You can check them on the panel, "
                   "or I can read them to you now.")
    tenv.clock.t += 60
    verified("s2")
    assert run(nt.append_to_reply("It's ten.", "what time is it", MEMBER, "s2")) == "It's ten."     # within 4 h: nothing


def test_an_unverified_voice_gets_no_tail_and_control(tenv):
    seed(1)
    verified(verdict=None)
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) == "Done."
    verified("s1", member=OTHER)                                                        # another member: their rows (none)
    assert run(nt.append_to_reply("Done.", "lights on", OTHER, "s1")) == "Done."
    verified()                                                                          # control
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) != "Done."


@pytest.mark.parametrize("said,reply", [("i feel awful", "I'm sorry."), ("lights on", "Which room?"), ("yes", "Okay.")])
def test_never_on_an_emotional_turn_a_question_or_an_answer(tenv, said, reply):
    seed(1)
    verified()
    assert run(nt.append_to_reply(reply, said, MEMBER, "s1")) == reply
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) != "Done."     # control


def test_quiet_hours_and_control(tenv, monkeypatch):
    import proactive.pull as pull
    seed(1)
    verified()
    monkeypatch.setattr(pull, "_quiet", lambda now: True)
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) == "Done."
    monkeypatch.setattr(pull, "_quiet", lambda now: False)
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) != "Done."


def test_one_tail_per_reply_the_transcript_check_wins(tenv):
    seed(1)
    verified()
    tc._PENDING["s1"] = (MEMBER, tc._clock())
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) == "Done."
    tc._PENDING.clear()
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) != "Done."     # control


def test_frequency_caps_and_nothing_new_nothing_said(tenv):
    seed(1)
    said = 0
    for i in range(6):
        verified(f"s{i}")
        if run(nt.append_to_reply("Done.", "lights on", MEMBER, f"s{i}")) != "Done.":
            said += 1
        run(ns.create(MEMBER, "note", f"New {i}", "M", dedupe_id=f"new{i}"))           # always something new to announce
        tenv.clock.t += 4 * 3600 + 1
    assert said == nt.TAIL_MAX_PER_DAY                                                  # 3 a day, however many 4 h gaps
    nt.reset()
    verified("t1")
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "t1")) != "Done."
    tenv.clock.t += 4 * 3600 + 1
    verified("t2")
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "t2")) == "Done."      # the same set is never announced twice


def test_the_stream_tail_is_one_line_after_real_text(tenv):
    seed(2)
    verified()

    async def brain():
        for d in ("__THINKING__:x", "The kettle ", "is on."):
            yield d

    async def collect():
        return [d async for d in nt.append_stream(brain(), "kettle", MEMBER, "s1")]
    out = run(collect())
    assert out[:3] == ["__THINKING__:x", "The kettle ", "is on."] and out[3].startswith(" You also have two notifications.")
    assert len(out) == 4


def test_flag_off_changes_nothing_on_the_voice_path(tenv, monkeypatch):
    seed(1)
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "off")
    verified()
    assert nt._TURNS == {}                                                              # not even noted
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) == "Done."
    assert run(nt.tier("read them", MEMBER, "s1", channel="voice")) is None
    src = (SVC / "zoe_flue_client.py").read_text()
    assert "if notification_tail.store.enabled() and not emotional_hold and not kwargs.get(\"replay_isolation\"):" in src


# -- "read them" ---------------------------------------------------------------------------------------------------------------
def test_read_them_reads_titles_counts_private_and_caps_at_three(tenv):
    seed(5, sensitive=(4,))
    verified()
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) != "Done."
    res = run(nt.tier("read them to me", MEMBER, "s1", channel="voice"))
    assert res.tier == nt.TIER
    assert "Title 4" not in res.reply                                                   # the sensitive one is never read
    assert res.reply.count("Title") == 3 and "And 1 more on the panel." in res.reply
    assert "One is private, it is on the panel." in res.reply
    assert tenv.pushed and tenv.pushed[-1][0] == PANEL


def test_read_them_from_someone_else_is_private_and_later_leaves_them(tenv):
    seed(2)
    verified()
    run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1"))
    verified("s1", member=OTHER)
    res = run(nt.tier("yes", OTHER, "s1", channel="voice"))
    assert res.reply == nt.PRIVATE_REPLY and "Title" not in res.reply
    nt.reset()
    tenv.clock.t += 5 * 3600
    verified()
    run(ns.create(MEMBER, "note", "Another", "M", dedupe_id="z"))
    run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1"))
    assert run(nt.tier("later", MEMBER, "s1", channel="voice")).reply == nt.LATER_REPLY
    assert run(ns.unread_count(MEMBER)) == 3
    assert run(nt.tier("read them", MEMBER, "s1", channel="voice")) is None            # the window closed with the answer
