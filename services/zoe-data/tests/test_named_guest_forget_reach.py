"""Named guests - forget reaches the panels and the unknown voices (``named_guest_memory.drop_from_panels``,
``named_guest_learning.forget_user``). Invented names only (the repo is public).

Before this, erasing a guest deleted the server's rows and left the print in every panel daemon's cache (memory + disk) until the
hourly sync, and erasing the member who holds the household's unknown voices left those voices behind.
"""
from __future__ import annotations

import re

import pytest

import contacts_store
import named_guest_learning as ngl
import named_guest_memory as ngm
import named_guests as ng
import voice_enrollment
from named_guest_support import (  # noqa: F401 - env is a fixture
    MID, OTHER, OWNER, add_guest, env, run, unit,
)

pytestmark = pytest.mark.ci_safe


@pytest.fixture
def told(monkeypatch):
    """The guest ids the panels were told to drop."""
    ids = []

    async def push(user_id):
        ids.append(user_id)
        return 2
    monkeypatch.setattr(voice_enrollment, "push_forget_to_panels", push)
    return ids


def pendant(db, pid, owner=OWNER):
    db.conn.execute("INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, created_at, updated_at, "
                    "expires_at) VALUES (?, ?, ?, ?, 'pendant', ?, 'x', 'x', 'x')", (f"pd-{pid}", owner, pid, MID, unit(9).tobytes()))


def unknown_voice(db, cid, holder):
    db.conn.execute("INSERT INTO panel_voice_clusters (id, user_id, model_id, centroid, n_turns, n_wake, sessions, days, tightness, "
                    "status, first_at, last_at, expires_at) VALUES (?, ?, ?, ?, 1, 1, '[]', '[]', 0, 'open', 'x', 'x', "
                    "'9999-12-31T00:00:00Z')", (cid, holder, MID, unit(3).tobytes()))
    db.conn.execute("INSERT INTO panel_voice_samples (id, cluster_id, embedding, session_id, day, created_at) "
                    "VALUES (?, ?, ?, 's', 'd', 'x')", (f"s-{cid}", cid, unit(3).tobytes()))


def test_forgetting_a_guests_voice_tells_the_panels_and_an_erase_of_nothing_tells_nobody(env, told):
    q = add_guest(env)
    assert run(ngm.erase(OTHER, q)) is False and told == []         # control: not their guest, nothing erased, no panel told
    assert run(ngm.erase(OWNER, q)) is True and told == [q]
    assert run(ngm.erase(OWNER, q)) is False and told == [q]        # already gone: not told twice


def test_the_entity_forget_tells_the_panels_about_panel_guests_only(env, told):
    q = add_guest(env)
    env.conn.execute("INSERT INTO people (id, user_id, name, visibility) VALUES ('rowan', ?, 'Rowan', 'personal')", (OWNER,))
    pendant(env, "rowan")
    assert run(contacts_store.forget_voiceprints(env, OWNER, re.compile(r"\browan\b", re.I))) >= 1
    assert told == []                                               # a pendant-only person was never on a panel
    assert run(contacts_store.forget_voiceprints(env, OWNER, re.compile(r"\bquinn\b", re.I))) >= 1
    assert told == [q] and env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]


def test_erasing_a_member_tells_the_panels_about_their_guests_and_erases_the_unknown_voices_they_hold(env, told):
    q = add_guest(env)
    add_guest(env, name="Rowan", owner=OTHER, vec=unit(8))
    unknown_voice(env, "held-by-owner", OWNER)
    unknown_voice(env, "held-by-other", OTHER)
    assert run(contacts_store.forget_user(OWNER, db=env)) >= 2
    assert told == [q]                                              # the other member's guest stays on the panels
    assert env.q("SELECT id FROM panel_voice_clusters") == [("held-by-other",)]
    assert env.q("SELECT cluster_id FROM panel_voice_samples") == [("held-by-other",)]


def test_a_panel_that_cannot_be_told_never_undoes_or_fails_the_erase(env, monkeypatch):
    async def down(user_id):
        raise RuntimeError("announce queue down")
    monkeypatch.setattr(voice_enrollment, "push_forget_to_panels", down)
    q = add_guest(env)
    run(ngm.remember(ng.Guest(q, "Quinn", OWNER), "s1", "hello", "hi"))
    assert run(ngm.erase(OWNER, q)) is True
    assert env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)] and env.q("SELECT COUNT(*) FROM named_guest_turns") == [(0,)]


def test_an_unknown_voice_erase_that_fails_raises_and_only_a_missing_table_is_nothing(env):
    class Db:
        def __init__(self, exc):
            self.exc = exc

        async def execute(self, *_a):
            raise self.exc
    with pytest.raises(RuntimeError):
        run(ngl.forget_user(Db(RuntimeError("connection lost")), OWNER))
    assert run(ngl.forget_user(Db(RuntimeError("no such table: panel_voice_samples")), OWNER)) == 0
