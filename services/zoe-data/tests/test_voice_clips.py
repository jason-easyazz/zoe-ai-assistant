"""The panel clip corpus (``voice_clips.py``): the server's speaker verdict beside each saved clip, owner-voice metrics that keep member
and legacy clips only, and the 30-day retention for a non-member's clip. Invented ids only (the repo is public)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

import pytest

import voice_clips as vc

pytestmark = pytest.mark.ci_safe
DAY = 86400
LIB = str(Path(__file__).resolve().parents[3] / "scripts" / "lib")

def wav(path, seconds=0.5):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01" * int(16000 * seconds))
    return str(path)

def label(d, name, kind, age_days=0, ident="", cluster=""):
    p = wav(d / f"{name}.wav")
    if kind:
        (d / f"{name}.verdict.json").write_text(json.dumps(dict(v=1, kind=kind, id=ident, cluster=cluster, ts=int(time.time() - age_days * DAY))))
    return p

@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_SAMPLE_DIR", str(tmp_path))
    monkeypatch.setenv("ZOE_VOICE_SAVE_AUDIO", "1")
    monkeypatch.setenv("ZOE_VOICE_CLIP_VERDICT", "1")
    return tmp_path

def capture(src):
    from routers import voice_tts

    vc.begin()
    asyncio.run(voice_tts._maybe_capture_stt(src, "hello"))
    return vc.current_clip()

def test_a_saved_clip_gets_the_servers_verdict_beside_it(corpus, tmp_path_factory):
    src = wav(tmp_path_factory.mktemp("src") / "in.wav")
    clip = capture(src)
    assert clip and clip.startswith(str(corpus)) and vc.read(clip) is None          # saved, not yet judged = legacy
    assert vc.record("unknown", "", "cluster-1") is True
    got = vc.read(clip)
    assert got["kind"] == "unknown" and got["cluster"] == "cluster-1" and "ts" in got
    assert oct(os.stat(vc.sidecar_path(clip)).st_mode & 0o777) == "0o600"
    clip2 = capture(src)                                                              # a NEW transcription never labels the old clip
    assert clip2 != clip and vc.read(clip2) is None
    assert vc.record("member", "member-a") and vc.read(clip2)["id"] == "member-a" and vc.read(clip)["kind"] == "unknown"

def test_the_label_is_flag_gated_and_needs_a_saved_clip(corpus, tmp_path_factory, monkeypatch):
    src = wav(tmp_path_factory.mktemp("src") / "in.wav")
    clip = capture(src)
    monkeypatch.setenv("ZOE_VOICE_CLIP_VERDICT", "0")
    assert vc.record("guest", "g1") is False and not os.path.exists(vc.sidecar_path(clip))
    monkeypatch.setenv("ZOE_VOICE_CLIP_VERDICT", "1")
    assert vc.record("nonsense") is False
    vc.begin()                                                                        # nothing saved this turn (capture off / failed)
    assert vc.record("guest", "g1") is False

def test_retention_deletes_only_old_guest_and_unknown_clips(corpus):
    old_guest = label(corpus, "a", "guest", 31, "g1")
    old_unknown = label(corpus, "b", "unknown", 40)
    young = label(corpus, "c", "unknown", 5)
    old_member = label(corpus, "d", "member", 400, "member-a")
    legacy = label(corpus, "e", None)
    os.utime(legacy, (time.time() - 900 * DAY,) * 2)                                  # ancient, but never judged: untouched
    out = vc.sweep(str(corpus))
    assert out["removed"] == 2
    for gone in (old_guest, old_unknown):
        assert not os.path.exists(gone) and not os.path.exists(vc.sidecar_path(gone))
    for kept in (young, old_member, legacy):
        assert os.path.exists(kept)
    assert os.path.exists(vc.sidecar_path(old_member))

def test_retention_control_a_broken_sweep_would_have_deleted_members(corpus, monkeypatch):
    """Break the guard: if the kind filter let a member through, the old member clip would be gone - the assertion above sees that."""
    member = label(corpus, "m", "member", 400, "member-a")
    monkeypatch.setattr(vc, "NON_MEMBER", ("guest", "unknown", "member"))
    vc.sweep(str(corpus))
    assert not os.path.exists(member)

def test_naming_a_cluster_relabels_its_clips_and_forgetting_a_guest_erases_theirs(corpus):
    a = label(corpus, "a", "unknown", 1, cluster="c1")
    b = label(corpus, "b", "unknown", 1, cluster="c2")
    assert vc.relabel_cluster("c1", "guest", "g1", str(corpus)) == 1
    assert vc.read(a)["kind"] == "guest" and vc.read(a)["id"] == "g1" and vc.read(b)["kind"] == "unknown"
    assert vc.erase_guests(["g1"], str(corpus)) == 1
    assert not os.path.exists(a) and os.path.exists(b)
    assert vc.erase_guests(["g1"], str(corpus)) == 0

def test_owner_voice_metrics_keep_member_and_legacy_clips_only(corpus):
    sys.path.insert(0, LIB)
    import panel_voice_log as pvl

    assert pvl.VERDICT_SUFFIX == vc.SUFFIX                                           # the reader and the writer agree on the file
    paths = [label(corpus, "m", "member", 1, "member-a"), label(corpus, "g", "guest", 1, "g1"), label(corpus, "u", "unknown", 1),
             label(corpus, "l", None)]
    assert [os.path.basename(p) for p in pvl.owner_voice_clips(paths)] == ["m.wav", "l.wav"]
    assert pvl.clip_verdict(paths[1]) == "guest" and pvl.clip_verdict(paths[3]) is None
    (corpus / "bad.wav").write_bytes(b"x")
    (corpus / "bad.verdict.json").write_text("not json")
    assert pvl.clip_verdict(str(corpus / "bad.wav")) is None                         # unreadable = legacy, never a guess

def test_every_owner_voice_consumer_filters_by_verdict():
    root = Path(__file__).resolve().parents[3] / "scripts"
    for rel, needle in (("perf/speaker_bakeoff.py", "clip_verdict"), ("perf/measure_speaker_adapt.py", "owner_voice_clips"),
                        ("perf/omi_voice_calibrate.py", "owner_voice_clips")):
        assert needle in (root / rel).read_text(), rel
    # said-vs-did replay keeps every clip: the words are the test, so the probe does not filter by verdict
    assert "clip_verdict" not in (root / "maintenance/voice_regression_probe.py").read_text()

def test_a_window_turn_the_gate_refused_is_labelled_unknown(corpus, tmp_path_factory):
    """The wiring: a no-wake follow-up whose voice the gate refused is dropped, and its saved clip is marked, not left as the owner's."""
    from routers import voice_tts

    clip = capture(wav(tmp_path_factory.mktemp("src") / "in.wav"))
    payload = {"window": "followup", "speaker": {"verified": False, "model_id": "x"}}
    got = asyncio.run(voice_tts._window_speaker_drop(payload, {"source": "device"}, "hello", "panel-1", route="turn"))
    assert got is not None and got.get("skipped") and vc.read(clip)["kind"] == "unknown"
    clip2 = capture(wav(tmp_path_factory.mktemp("src2") / "in.wav"))                  # control: a wake turn is not a window turn
    assert asyncio.run(voice_tts._window_speaker_drop({"speaker": {"verified": False}}, {"source": "device"}, "hi", "panel-1",
                                                      route="turn")) is None
    assert vc.read(clip2) is None

def test_voice_command_labels_the_clip_member_guest_or_unknown():
    src = (Path(__file__).resolve().parents[1] / "routers" / "voice_tts.py").read_text()
    body = src[src.index("async def voice_command("):]
    body = body[:body.index("\n@router.")]
    for line in ('_voice_clips.record("member", identified_user_id)', '_voice_clips.record("guest", _clip_guest.person_id)',
                 '_voice_clips.record("unknown", "", _learned_cluster)'):
        assert line in body
    assert body.count("_voice_clips.record(") == 3                                    # no verdict (gate off) = no label = legacy
