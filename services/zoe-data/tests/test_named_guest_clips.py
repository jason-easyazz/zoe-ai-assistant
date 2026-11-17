"""Hearing a saved unknown-voice clip and naming it (``named_guest_clips.py``). Invented names only (the repo is public).

Who may hear a clip is the whole point: a verified adult member, never a guest, an unverified voice, a child or a typed channel.
"""
from __future__ import annotations

import base64
import json
import time
import wave
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import named_guest_clips as ngc
import named_guest_learning as ngl
import voice_clips as vc
from named_guest_support import DEVICE, MID, OWNER, PANEL, env, run, unit  # noqa: F401 - env is a fixture

pytestmark = pytest.mark.ci_safe
T0 = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)

def clip(d, name, cluster, seconds=8):
    p = d / f"{name}.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x01\x00" * int(16000 * seconds))
    (d / f"{name}.verdict.json").write_text(json.dumps(dict(v=1, kind="unknown", id="", cluster=cluster, ts=int(time.time()))))
    return str(p)

@pytest.fixture
def clips(env, monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_NAMED_GUEST_CLIPS", "1")
    monkeypatch.setenv("ZOE_GUEST_VOICE_LEARN", "on")
    monkeypatch.setenv("ZOE_VOICE_SAMPLE_DIR", str(tmp_path))
    # The cluster is heard at T0 and expires 14 days later; naming it reads the learning module's clock. Unpinned that is the
    # wall clock, so six tests here went red on 2026-11-22 (T0 + 14 days).
    monkeypatch.setattr(ngl, "_now", lambda: T0 + timedelta(hours=6))
    ngc.reset()
    queued = []

    async def fake_queue(owner, panel, wav):
        queued.append((owner, panel, wav))
        ngc.issue_token(panel, wav)
        return True
    monkeypatch.setattr(ngc, "_queue", fake_queue)
    env.dir, env.queued = tmp_path, queued
    env.cluster = _cluster(env)
    yield env
    ngc.reset()

def _cluster(db):
    vec = unit(44)
    for i in range(3):
        o = np.random.default_rng(900 + i).standard_normal(256).astype(np.float32)
        o -= (o @ vec) * vec
        v = 0.9 * vec + 0.43 * o / np.linalg.norm(o)
        body = {"speaker": {"model_id": MID, "speech_s": 3.0, "embedding": base64.b64encode(v.astype(np.float32).tobytes()).decode()}}
        out = run(ngl.observe(body, DEVICE, session_id="s1", wake=True, text="hi", now=T0 + timedelta(minutes=i)))
    return out["cluster_id"]

def ask(text, uid=OWNER, verified=True, channel="voice", panel=PANEL):
    return run(ngc.tier(text, uid, "s", channel=channel, speaker_verified=verified, panel_id=panel))

def test_classify_is_pure_and_the_name_needs_a_pending_clip():
    assert ngc.classify("Hey Zoe, who was that this morning?", pending=False) == ("play", "")
    assert ngc.classify("play me the voices you didn't know", pending=False) == ("play", "")
    assert ngc.classify("who was that actor in that film", pending=False) is None            # ordinary speech is not a cue
    assert ngc.classify("that's Quinn", pending=False) is None                               # no clip playing: not an answer
    assert ngc.classify("that's Quinn", pending=True) == ("name", "Quinn")
    assert ngc.classify("it was quinn this morning", pending=True) == ("name", "Quinn")
    assert ngc.classify("I don't know", pending=True) == ("skip", "")
    assert ngc.classify("what's the weather", pending=True) is None                          # a command is never taken for a name
    assert ngc.classify("that's my", pending=True) is None

def test_off_by_default_and_needs_named_guests_on(clips, monkeypatch):
    clip(clips.dir, "a", clips.cluster)
    monkeypatch.delenv("ZOE_NAMED_GUEST_CLIPS")
    assert ask("who was that this morning") is None
    monkeypatch.setenv("ZOE_NAMED_GUEST_CLIPS", "1")
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "shadow")
    assert ask("who was that this morning") is None and clips.queued == []

def test_a_verified_member_hears_a_few_seconds_then_names_the_voice(clips):
    wavp = clip(clips.dir, "a", clips.cluster)
    out = ask("who was that this morning")
    assert out.intent == "named_guest_clip_play" and clips.queued == [(OWNER, PANEL, wavp)]
    tok = list(ngc._TOKENS)[0]
    got = ngc.redeem(tok, PANEL)
    assert got["content_type"] == "audio/wav"
    import io

    with wave.open(io.BytesIO(base64.b64decode(got["audio_base64"]))) as w:
        assert w.getnframes() == int(16000 * ngc.MAX_PLAY_S)                                 # a few seconds, not the clip
    assert ngc.redeem(tok, PANEL) is None                                                    # single use
    named = ask("that's Quinn")
    assert named.intent == "named_guest_clip_name" and "Quinn" in named.reply
    gid = clips.q("SELECT people_id FROM learned_voiceprints WHERE channel = 'panel'")[0][0]
    assert clips.q("SELECT name, user_id FROM people WHERE id = ?", (gid,)) == [("Quinn", OWNER)]
    assert vc.read(wavp)["kind"] == "guest" and vc.read(wavp)["id"] == gid                    # the clip is now that guest's
    assert clips.q("SELECT COUNT(*) FROM panel_voice_clusters") == [(0,)]
    assert ask("that's Rowan") is None                                                       # nothing pending any more

def test_a_guest_an_unverified_voice_a_child_or_a_typed_channel_hears_nothing(clips):
    clip(clips.dir, "a", clips.cluster)
    assert ask("who was that this morning", uid="guest") is None                             # a guest
    assert ask("who was that this morning", uid="guest-quinn") is None
    assert ask("who was that this morning", verified=False) is None                          # a voice the gate refused
    assert ask("who was that this morning", uid="member-kid") is None                        # a child member
    assert ask("who was that this morning", channel="chat") is None                          # typed
    assert ask("who was that this morning", verified=None) is None                           # no verdict and no fresh PIN
    assert clips.queued == [] and ngc._TOKENS == {}

def test_a_clip_is_only_named_by_the_member_it_was_played_to_at_that_panel(clips):
    clip(clips.dir, "a", clips.cluster)
    ask("who was that this morning")
    assert ask("that's Quinn", uid="member-b") is None                                       # someone else's clip
    assert ask("that's Quinn", uid="guest") is None
    assert ask("that's Quinn", verified=False) is None
    assert ask("that's Quinn", panel="panel-2") is None
    assert clips.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]
    assert ask("that's Quinn").intent == "named_guest_clip_name"                             # control: the right member, panel, verdict

def test_only_unknown_voices_that_still_exist_are_played_and_a_members_clip_never(clips):
    member = clips.dir / "m.wav"
    clip(clips.dir, "m", clips.cluster)
    (clips.dir / "m.verdict.json").write_text(json.dumps(dict(kind="member", id=OWNER, cluster=clips.cluster, ts=int(time.time()))))
    assert "haven't got" in ask("who was that this morning").reply and clips.queued == []
    clip(clips.dir, "gone", "no-such-cluster")                                               # its voice expired / was named: not offered
    assert "haven't got" in ask("who was that this morning").reply and clips.queued == []
    assert member.exists()

def test_a_name_that_is_a_members_is_refused_and_the_clip_stays_unknown(clips):
    wavp = clip(clips.dir, "a", clips.cluster)
    ask("who was that this morning")
    assert "can't give that voice" in ask("that's Blake").reply
    assert vc.read(wavp)["kind"] == "unknown"
    assert clips.q("SELECT COUNT(*) FROM panel_voice_clusters") == [(1,)]

def test_a_child_voice_is_played_and_named_like_any_other(clips):
    """Owner 2026-11-08: voice works the same for adults and children - the member hears and names a child's clip too."""
    clip(clips.dir, "a", clips.cluster)
    ask("who was that this morning")
    assert ask("that's Mia").intent == "named_guest_clip_name"
    gid = clips.q("SELECT people_id FROM learned_voiceprints")[0][0]
    clips.conn.execute("UPDATE people SET relationship = 'niece' WHERE id = ?", (gid,))
    import named_guests as ng

    g = run(ng._lookup(gid, MID, 0.6))
    assert g is not None and g.child is True and "child-safe" in ng.guest_line(g)

def test_the_clip_command_rides_the_announcement_lane_panel_scoped_and_without_audio():
    import voice_announce as va

    assert "voice_clip_play" in va.COMMAND_TRIGGERS and "voice_clip_play" in va.PANEL_SCOPED_TRIGGERS
    src = open(ngc.__file__).read()
    assert "audio_base64" not in src.split("async def _queue")[1].split("async def tier")[0]       # the row holds a token, never audio

def test_the_token_is_panel_bound_and_expires(clips):
    wavp = clip(clips.dir, "a", clips.cluster)
    tok = ngc.issue_token(PANEL, wavp)
    assert ngc.redeem(tok, "panel-2") is None and ngc.redeem(tok, PANEL) is None            # a wrong try burns it
    tok = ngc.issue_token(PANEL, wavp, ttl_s=-1)
    assert ngc.redeem(tok, PANEL) is None
    assert ngc.cut_wav("/nonexistent.wav") is None


def test_every_naming_route_relabels_the_saved_clips_so_a_forget_erases_the_audio(clips):
    # Codex on #2346: only the spoken-clip tier relabelled; a voice named any other way left its clips "unknown" after the cluster went
    wavp = clip(clips.dir, "b", clips.cluster)
    out = run(ngl.name_unknown(clips.cluster, "Quinn", OWNER))             # the shared path (what an answered ask / the API use)
    assert out is not None
    meta = vc.read(wavp)
    assert meta["kind"] == "guest" and meta["id"] == out["guest_id"]
    assert vc.erase_guests([out["guest_id"]], str(clips.dir)) == 1          # a forget now removes the audio


def test_cancelling_revokes_the_queued_playback(clips):
    # Codex on #2346: "cancel" after asking for a clip but before the poller plays it must stop the playback
    clip(clips.dir, "c", clips.cluster)
    assert ask("who was that this morning").intent == "named_guest_clip_play"
    tok = list(ngc._TOKENS)[0]
    out = ask("cancel")
    assert out is not None and out.intent == "named_guest_clip_skip"
    assert ngc.redeem(tok, PANEL) is None
