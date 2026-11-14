"""Passive named guests (``named_guest_learning.py``, migration 0073): an unknown voice that keeps addressing Zoe is grouped, asked
its name once Zoe is sure, and the answer makes it a named guest. Invented names only (the repo is public)."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import named_guest_learning as ngl
import named_guests as ng
from named_guest_support import (  # noqa: F401 - env is a fixture
    DEVICE, MID, OWNER, add_guest, env, run, unit,
)

pytestmark = pytest.mark.ci_safe
T0 = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def learn(env, monkeypatch):
    monkeypatch.setenv("ZOE_GUEST_VOICE_LEARN", "on")
    # The voices are heard at T0 (passed in as ``now=``) and expire 14 days later; the calls that take no ``now`` read the module
    # clock. Unpinned that is the wall clock, so every test that names or lists a voice went red on 2026-11-22 (T0 + 14 days).
    monkeypatch.setattr(ngl, "_now", lambda: T0 + timedelta(hours=6))
    ngl.bind_ask(False)
    yield env
    ngl.bind_ask(False)


def near(base, cos, seed):
    o = np.random.default_rng(seed).standard_normal(256).astype(np.float32)
    o -= (o @ base) * base
    o /= np.linalg.norm(o)
    return (cos * base + np.sqrt(1 - cos * cos) * o).astype(np.float32)


def turn(vec, caller=DEVICE):
    return {"speaker": {"model_id": MID, "speech_s": 3.0, "embedding": base64.b64encode(vec.astype(np.float32).tobytes()).decode()}}


def say(vec, n, *, start=T0, session="s1", wake=True, text="what's the weather", step=timedelta(minutes=1)):
    out = None
    for i in range(n):
        out = run(ngl.observe(turn(near(vec, 0.85, 1000 + i + sum(map(ord, session)) * 31)), DEVICE, session_id=session, wake=wake, text=text,
                              now=start + i * step))
    return out


def test_ready_and_the_name_answer_are_pure():
    t = dict(join=0.45, tight=0.5, min_turns=8, min_sessions=2, min_wake=2, expire_days=14)
    c = dict(n_turns=8, n_wake=2, sessions=["a", "b"], days=["2026-11-08"], tightness=0.7)
    assert ngl.ready(c, t)
    for k, v in (("n_turns", 7), ("n_wake", 1), ("sessions", ["a"]), ("tightness", 0.4)):
        assert not ngl.ready({**c, k: v}, t), k
    assert ngl.answer_name("I'm Quinn") == "Quinn" and ngl.answer_name("it's quinn.") == "Quinn" and ngl.answer_name("Quinn") == "Quinn"
    assert ngl.answer_name("I'm fine thanks") == "" and ngl.answer_name("what's the weather like") == ""


def test_off_does_nothing_and_a_browser_or_the_relay_is_never_heard(env, monkeypatch):
    v = unit(31)
    assert run(ngl.observe(turn(v), DEVICE, session_id="s", wake=True)) == {"action": "off"}
    monkeypatch.setenv("ZOE_GUEST_VOICE_LEARN", "on")
    for caller in ({**DEVICE, "via_relay": True}, {"source": "session"}):
        assert run(ngl.observe(turn(v), caller, session_id="s", wake=True))["action"] == "skip"
    assert env.q("SELECT COUNT(*) FROM panel_voice_samples") == [(0,)]


def test_a_voice_near_a_member_or_a_guest_is_never_kept_and_its_control(learn, monkeypatch):
    member = unit(32)
    learn.conn.execute("INSERT INTO speaker_profiles (id, user_id, embedding_blob, consent_at, model_id) VALUES ('p', ?, ?, 'x', ?)",
                       (OWNER, member.tobytes(), MID))
    learn.conn.commit()
    quiet_member = near(member, 0.30, 5)               # below the member's accept bar, above the FAR-1 % guard
    assert run(ngl.observe(turn(quiet_member), DEVICE, session_id="s", wake=True))["action"] == "member_like"
    g = unit(33)
    add_guest(learn, vec=g)
    assert run(ngl.observe(turn(near(g, 0.3, 6)), DEVICE, session_id="s", wake=True))["action"] == "member_like"
    assert learn.q("SELECT COUNT(*) FROM panel_voice_samples") == [(0,)]

    async def nobody(db, model_id):                   # break the fix: the guard off, the member's voice IS kept
        return []
    monkeypatch.setattr(ngl, "_known_voices", nobody)
    assert run(ngl.observe(turn(quiet_member), DEVICE, session_id="s", wake=True))["action"] == "kept"


def test_two_voices_never_merge(learn):
    a, b = unit(34), unit(35)
    ra = run(ngl.observe(turn(a), DEVICE, session_id="s", wake=True, now=T0))
    rb = run(ngl.observe(turn(b), DEVICE, session_id="s", wake=True, now=T0))
    assert ra["cluster_id"] != rb["cluster_id"]
    assert learn.q("SELECT COUNT(*) FROM panel_voice_clusters") == [(2,)]


def test_zoe_asks_only_when_sure_then_the_answer_makes_a_named_guest(learn):
    v = unit(36)
    assert say(v, 6, session="s1")["action"] == "kept"          # one conversation is not enough
    out = say(v, 2, session="s2", start=T0 + timedelta(hours=3))
    assert out["action"] == "ask"
    ngl.bind_ask(True)
    assert "What's your name?" in run(ng.brain_block("guest"))
    ngl.bind_ask(False)
    named = run(ngl.observe(turn(near(v, 0.85, 77)), DEVICE, session_id="s2", wake=False, text="I'm Quinn",
                            now=T0 + timedelta(hours=3, minutes=3)))
    assert named["action"] == "named" and named["guest"].name == "Quinn" and named["guest"].owner_user_id == OWNER
    assert learn.q("SELECT COUNT(*) FROM panel_voice_clusters") == [(0,)]
    assert learn.q("SELECT channel FROM learned_voiceprints") == [("panel",)]
    assert run(ng.accept({"speaker": {"model_id": MID, "speech_s": 3.0, "guest": named["guest"].person_id, "guest_score": 0.8}},
                         DEVICE, member_identified=False)).name == "Quinn"


def test_shadow_never_asks(learn, monkeypatch):
    monkeypatch.setenv("ZOE_GUEST_VOICE_LEARN", "shadow")
    v = unit(37)
    say(v, 6, session="s1")
    assert say(v, 4, session="s2", start=T0 + timedelta(hours=3))["action"] == "kept"


def test_a_voice_that_only_ever_leaks_into_listening_windows_is_never_asked(learn):
    v = unit(38)
    say(v, 6, session="s1", wake=False)
    assert say(v, 6, session="s2", wake=False, start=T0 + timedelta(hours=3))["action"] == "kept"


def test_an_answer_can_never_claim_an_existing_name(learn):
    add_guest(learn, name="Quinn", vec=unit(39))
    v = unit(40)
    say(v, 6, session="s1")
    assert say(v, 2, session="s2", start=T0 + timedelta(hours=3))["action"] == "ask"
    for name in ("Quinn", "Blake"):                           # an existing guest's, a member's
        out = run(ngl.observe(turn(near(v, 0.85, 78)), DEVICE, session_id="s2", wake=False, text=f"I'm {name}",
                              now=T0 + timedelta(hours=3, minutes=3)))
        assert out["action"] == "name_refused"
    assert learn.q("SELECT COUNT(*) FROM learned_voiceprints") == [(1,)]


def test_an_answer_after_the_window_is_not_a_name(learn):
    v = unit(41)
    say(v, 6, session="s1")
    say(v, 2, session="s2", start=T0 + timedelta(hours=3))
    late = run(ngl.observe(turn(near(v, 0.85, 79)), DEVICE, session_id="s3", wake=True, text="I'm Quinn",
                           now=T0 + timedelta(hours=5)))
    assert late["action"] != "named"


def test_an_unconfirmed_voice_expires(learn):
    say(unit(42), 3)
    assert learn.q("SELECT COUNT(*) FROM panel_voice_clusters") == [(1,)]
    run(ngl.observe(turn(unit(43)), DEVICE, session_id="s", wake=True, now=T0 + timedelta(days=20)))
    assert learn.q("SELECT COUNT(*) FROM panel_voice_clusters") == [(1,)]      # only the new voice is left
    assert learn.q("SELECT COUNT(*) FROM panel_voice_samples") == [(1,)]


def test_the_owner_names_lists_and_erases_unknown_voices(learn):
    a = say(unit(44), 3)["cluster_id"]
    b = say(unit(45), 2)["cluster_id"]
    listed = run(ngl.list_unknown())
    assert {v["id"] for v in listed} == {a, b} and all("centroid" not in v and "embedding" not in v for v in listed)
    out = run(ngl.name_unknown(a, "rowan", "member-b"))
    assert out["name"] == "Rowan"
    assert learn.q("SELECT user_id, visibility FROM people WHERE id = ?", (out["guest_id"],)) == [("member-b", "personal")]
    assert run(ngl.name_unknown(b, "Rowan", OWNER)) is None                    # a name is never claimed twice
    assert run(ngl.erase_unknown(b)) == 1
    assert learn.q("SELECT COUNT(*) FROM panel_voice_samples") == [(0,)]


def test_naming_a_voice_takes_its_twin_clusters_with_it_and_an_expired_voice_is_never_named(learn):
    v = unit(46)
    a = run(ngl.observe(turn(v), DEVICE, session_id="s", wake=True, now=T0))["cluster_id"]
    twin = near(v, 0.40, 9)                                   # the same person, a second acoustic cluster (below JOIN)
    b = run(ngl.observe(turn(twin), DEVICE, session_id="s", wake=True, now=T0))["cluster_id"]
    other = run(ngl.observe(turn(unit(47)), DEVICE, session_id="s", wake=True, now=T0))["cluster_id"]
    assert len({a, b, other}) == 3
    assert run(ngl.name_unknown(a, "Quinn", OWNER))
    left = {r[0] for r in learn.q("SELECT id FROM panel_voice_clusters")}
    assert left == {other}                                    # the twin went with it; a stranger's voice stays
    learn.conn.execute("UPDATE panel_voice_clusters SET expires_at = '2000-01-01T00:00:00Z'")
    learn.conn.commit()
    assert run(ngl.name_unknown(other, "Rowan", OWNER)) is None
    assert learn.q("SELECT COUNT(*) FROM panel_voice_clusters") == [(0,)]


def test_a_forgotten_name_is_never_brought_back_by_a_returning_voice(learn, monkeypatch):
    import memory_forgotten as mf

    async def forgotten(uid, text, **_k):
        return text == "Quinn"
    monkeypatch.setattr(mf, "matches", forgotten)
    cid = say(unit(48), 2)["cluster_id"]
    assert run(ngl.name_unknown(cid, "Quinn", OWNER)) is None
    assert learn.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]


def test_the_hourly_retention_sweep_expires_unknown_voices(learn):
    import omi_recordings

    say(unit(49), 2)
    learn.conn.execute("UPDATE panel_voice_clusters SET expires_at = '2000-01-01T00:00:00Z'")
    learn.conn.commit()
    run(omi_recordings._sweep_voices())
    assert learn.q("SELECT COUNT(*) FROM panel_voice_clusters") == [(0,)] and learn.q("SELECT COUNT(*) FROM panel_voice_samples") == [(0,)]

