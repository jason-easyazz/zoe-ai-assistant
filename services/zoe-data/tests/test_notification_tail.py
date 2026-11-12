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
    tenv.conn.execute("DELETE FROM app_settings")                                       # a fresh member state (caps persist otherwise)
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


@pytest.mark.parametrize("flags", [{"replay": True}, {"dry": True}])
def test_a_replayed_or_dry_deterministic_reply_gets_no_tail_and_records_nothing(tenv, flags):
    seed(1)
    verified()
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1", **flags)) == "Done."
    assert nt._LAST == {} and nt._DAY == {} and nt._PENDING == {} and ns.ANNOUNCED == {}
    assert tenv.rows("SELECT COUNT(*) FROM app_settings")[0][0] == 0
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) != "Done."     # control: a normal reply
    assert tenv.rows("SELECT COUNT(*) FROM app_settings")[0][0] == 1


def test_the_voice_lane_passes_the_replay_guard():
    """The deterministic call site really threads it (AST, not a string match): ``replay=`` is a keyword of the call."""
    import ast
    tree = ast.parse((SVC / "routers" / "voice_tts.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "append_to_reply" and getattr(n.func.value, "id", "") == "_notification_tail"]
    assert calls and all(any(k.arg == "replay" for k in c.keywords) for c in calls)


def test_unreadable_caps_mean_no_tail_and_control(tenv, monkeypatch):
    seed(1)
    real = tenv.execute

    def unreadable(sql, params=()):                                                      # reads of the cap row fail; saves still work
        if sql.startswith("SELECT value FROM app_settings"):
            raise RuntimeError("connection reset")
        return real(sql, params)
    monkeypatch.setattr(tenv, "execute", unreadable)
    verified()
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) == "Done."
    monkeypatch.setattr(tenv, "execute", real)                                           # control: readable again
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) != "Done."


def test_an_unsaved_tail_is_never_said_or_counted_and_control(tenv):
    seed(1)
    tenv.conn.execute("CREATE TRIGGER no_save BEFORE INSERT ON app_settings BEGIN SELECT RAISE(ABORT, 'read only'); END")
    verified()
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) == "Done."
    assert nt._LAST == {} and nt._DAY == {} and nt._PENDING == {}

    async def brain():
        yield "Done."

    async def collect():
        return [d async for d in nt.append_stream(brain(), "lights on", MEMBER, "s1")]
    assert run(collect()) == ["Done."]                                                   # the stream says nothing either
    tenv.conn.execute("DROP TRIGGER no_save")                                            # control: the save works
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) != "Done."


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


def test_the_caps_survive_a_restart_and_control(tenv):
    seed(1)
    verified()
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s1")) != "Done."
    run(ns.create(MEMBER, "note", "New", "M", dedupe_id="new"))                       # something new, so only the 4 h cap can refuse
    nt.reset()                                                                         # a restart: every in-memory cap is gone
    verified("s2")
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s2")) == "Done."     # the persisted last-tail time still holds
    tenv.conn.execute("DELETE FROM app_settings")                                      # control: without the row it would fire
    nt.reset()
    verified("s3")
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s3")) != "Done."


def test_the_days_count_survives_a_restart(tenv):
    seed(1)
    for i in range(3):
        verified(f"s{i}")
        assert run(nt.append_to_reply("Done.", "lights on", MEMBER, f"s{i}")) != "Done."
        run(ns.create(MEMBER, "note", f"N{i}", "M", dedupe_id=f"n{i}"))
        tenv.clock.t += 4 * 3600 + 1
        nt.reset()                                                                     # a restart between each one
    verified("s9")
    assert run(nt.append_to_reply("Done.", "lights on", MEMBER, "s9")) == "Done."     # still 3 a day
