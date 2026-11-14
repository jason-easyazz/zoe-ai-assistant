"""Named guests - consent: a guest is TOLD what Zoe keeps (``named_guest_disclosure.py``, migration 0083) and can ask to be forgotten
themselves (``named_guest_enrol._forget_me``). Both flags default off. Invented names only (the repo is public)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

import contacts_store
import named_guest_disclosure as ngd
import named_guest_enrol as nge
import named_guest_memory as ngm
import named_guests as ng
from named_guest_support import (  # noqa: F401 - env is a fixture
    OTHER, OWNER, PANEL, _migrate, add_guest, env, run, unit,
)

pytestmark = pytest.mark.ci_safe
TOLD = "Hi Quinn. I know your voice now; say forget me and I'll erase it."


@pytest.fixture
def quinn(env, monkeypatch):
    monkeypatch.setenv("ZOE_NAMED_GUEST_DISCLOSURE", "1")
    monkeypatch.setenv("ZOE_NAMED_GUEST_SELF_FORGET", "1")
    g = ng.Guest(add_guest(env), "Quinn", OWNER)
    ng.bind_turn(g)
    env.g = g
    return env


def marks(db):
    return db.q("SELECT user_id, people_id FROM named_guest_disclosures")


def block():
    return run(ng.brain_block("guest"))


def me(text="forget me", uid="guest", **kw):
    return run(nge.tier(text, uid, "s", channel="voice", speaker_verified=False, panel_id=PANEL, **kw))


# ── being told ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_both_flags_are_off_by_default_and_change_nothing(env):
    g = ng.Guest(add_guest(env), "Quinn", OWNER)
    ng.bind_turn(g)
    assert not run(ngd.due(g)) and "has not been told" not in block()
    assert run(ngm.remember(g, "s", "hello", TOLD)) and marks(env) == []
    assert me() is None and env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(1,)]


def test_a_guest_is_told_until_a_reply_really_tells_them_and_then_never_again(quinn, monkeypatch):
    assert "Quinn has not been told what you keep about them" in block()
    run(ngm.remember(quinn.g, "s", "what's the weather", "Sunny, about 24 degrees."))
    assert marks(quinn) == [] and "has not been told" in block()            # the reply did not tell them: the line rides again
    monkeypatch.setenv("ZOE_NAMED_GUEST_MEMORY", "0")
    run(ngm.remember(quinn.g, "s", "hi", TOLD))                              # recorded whether or not memory is on
    assert marks(quinn) == [(OWNER, quinn.g.person_id)] and "has not been told" not in block()
    assert run(ngd.after_turn(quinn.g, TOLD)) is False and len(marks(quinn)) == 1


def test_the_telling_is_for_the_guest_on_their_own_turn_only(quinn, monkeypatch):
    assert run(ng.brain_block(OWNER)) == ""                                  # never on a member's turn
    rowan = ng.Guest(add_guest(quinn, name="Rowan", vec=unit(8)), "Rowan", OWNER)
    run(ngd.after_turn(rowan, TOLD))
    assert "has not been told" in block()                                    # Rowan being told is not Quinn being told
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "shadow")
    assert not run(ngd.due(quinn.g)) and run(ngd.after_turn(quinn.g, TOLD)) is False


def test_the_telling_promises_only_what_is_on(quinn, monkeypatch):
    full = ngd.line(quinn.g)
    assert "you know their voice" in full and "remember your conversations" in full and 'say "forget me"' in full
    assert "recordings" not in full
    monkeypatch.setenv("ZOE_NAMED_GUEST_MEMORY", "0")
    monkeypatch.setenv("ZOE_NAMED_GUEST_SELF_FORGET", "0")
    monkeypatch.setenv("ZOE_VOICE_SAVE_AUDIO", "1")
    bare = ngd.line(quinn.g)
    assert "remember your conversations" not in bare and '"forget me"' not in bare and "anyone who lives here" in bare
    assert "recordings of what they say at the panel are kept;" in bare      # no retention sweep: no time limit is promised
    monkeypatch.setenv("ZOE_VOICE_CLIP_RETENTION", "1")
    assert "kept for up to 30 days" in ngd.line(quinn.g)


def test_an_unreadable_mark_means_not_told(quinn):
    run(ngd.after_turn(quinn.g, TOLD))
    assert not run(ngd.due(quinn.g))
    quinn.conn.execute("DROP TABLE named_guest_disclosures")
    assert run(ngd.due(quinn.g)) and run(ngd.after_turn(quinn.g, TOLD)) is False and "has not been told" in block()


def test_a_forgotten_guest_who_is_taught_again_is_told_again(quinn):
    add_guest(quinn, name="Rowan", owner=OTHER, vec=unit(8))
    rowan = ng.Guest(quinn.q("SELECT id FROM people WHERE name = 'Rowan'")[0][0], "Rowan", OTHER)
    run(ngd.after_turn(quinn.g, TOLD))
    run(ngd.after_turn(rowan, TOLD))
    assert run(ngm.erase(OWNER, quinn.g.person_id)) and marks(quinn) == [(OTHER, rowan.person_id)]
    add_guest(quinn)
    assert run(ngd.due(quinn.g))
    run(ngd.after_turn(quinn.g, TOLD))
    run(contacts_store.forget_user(OWNER, db=quinn))                         # the member erase takes the mark too
    assert marks(quinn) == [(OTHER, rowan.person_id)]


def test_the_migration_reruns_and_downgrades(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    for _ in range(2):
        _migrate(engine, "0083_named_guest_disclosures.py")
    with engine.connect() as c:
        assert [r[1] for r in c.exec_driver_sql("PRAGMA table_info(named_guest_disclosures)")] == ["user_id", "people_id", "told_at"]


# ── "forget me" ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_recognised_guest_who_says_forget_me_is_erased_and_this_turn_is_not_kept(quinn):
    rowan = add_guest(quinn, name="Rowan", vec=unit(8))
    run(ngm.remember(quinn.g, "s", "I'm training for a marathon", TOLD))
    run(ngm.remember(ng.Guest(rowan, "Rowan", OWNER), "s", "my cat is Biscuit", "ok"))
    out = me("hey zoe, forget me please")
    assert out.intent == "named_guest_forget_me" and "forgotten your voice and our conversations" in out.reply
    assert quinn.q("SELECT people_id FROM learned_voiceprints") == [(rowan,)]            # only their own
    assert quinn.q("SELECT people_id FROM named_guest_turns") == [(rowan,)] and marks(quinn) == []
    assert quinn.q("SELECT COUNT(*) FROM people WHERE name = 'Quinn'") == [(1,)]          # the member's contact stays
    # the post-turn pass still holds the guest it was handed: nothing about them is written back
    assert run(ngm.remember(quinn.g, "s", "forget me", TOLD)) is False
    assert quinn.q("SELECT people_id FROM named_guest_turns") == [(rowan,)] and marks(quinn) == []


def test_only_the_matched_guests_own_voice_can_ask(quinn, monkeypatch):
    assert me(uid=OWNER) is None                                             # a member's turn is never a guest's forget
    assert me(allow_writes=False) is None                                    # a read-only turn changes nothing
    assert me("forget quinn") is None and me("what's the weather") is None   # a name, or anything else, is not "forget me"
    ng.bind_turn(None)
    assert me() is None                                                      # an unrecognised voice: nobody to forget
    ng.bind_turn(quinn.g)
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "shadow")
    assert me() is None
    assert quinn.q("SELECT COUNT(*) FROM learned_voiceprints") == [(1,)]


def test_a_forget_me_that_fails_says_so_and_claims_nothing(quinn, monkeypatch):
    async def boom(owner, pid):
        raise RuntimeError("db down")
    monkeypatch.setattr(ngm, "erase", boom)
    out = me()
    assert "couldn't forget you" in out.reply
    assert quinn.q("SELECT COUNT(*) FROM learned_voiceprints") == [(1,)]
