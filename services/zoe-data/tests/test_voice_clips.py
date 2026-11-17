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
    monkeypatch.setenv("ZOE_VOICE_CLIP_PENDING_FILE", str(tmp_path.parent / (tmp_path.name + "-pending.txt")))   # never the real ~/.zoe
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
    time.sleep(0.01)                                                                  # clip names carry milliseconds: two captures in one ms collide
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
    for line in ('await _label_member_clip(identified_user_id)', '_voice_clips.record("guest", _clip_guest.person_id)',
                 '_voice_clips.record("unknown", "", _learned_cluster)'):
        assert line in body
    assert body.count("_voice_clips.record(") == 2                                    # no verdict (gate off) = no label = legacy
    helper = src[src.index("async def _label_member_clip("):]
    helper = helper[:helper.index("\nasync def ")]
    assert 'asyncio.to_thread(_vc.record, "member", member)' in helper, "the member label goes through the late-write rule (member delete)"
    assert "asyncio.to_thread(_vc.drop_clip, clip, member)" in helper and "drop_current(" not in helper, \
        "the clip's file work runs in a worker thread, never on the event loop"


# ── a member delete's clip erase is STRICT: nothing it cannot read or remove is swallowed ───────────────────────────────────────

needs_permissions = pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores file permissions")


@needs_permissions
def test_a_corpus_that_cannot_be_listed_fails_the_member_erase_and_a_retry_finishes(corpus):
    clip = label(corpus, "turn-1", "member", ident="ann")
    os.chmod(corpus, 0o000)
    try:
        with pytest.raises(OSError):
            vc.erase_member("ann")
    finally:
        os.chmod(corpus, 0o700)
    assert os.path.exists(clip), "nothing was removed on a guess"
    assert vc.erase_member("ann") == 1 and not os.path.exists(clip)


@needs_permissions
def test_a_sidecar_that_cannot_be_read_fails_the_member_erase(corpus):
    label(corpus, "turn-1", "member", ident="ann")
    side = corpus / "turn-1.verdict.json"
    os.chmod(side, 0o000)
    try:
        with pytest.raises(OSError):
            vc.erase_member("ann")
    finally:
        os.chmod(side, 0o600)
    assert vc.erase_member("ann") == 1


def test_a_corrupt_sidecar_is_erased_only_when_it_can_name_the_member_and_unlabelled_clips_stay(corpus):
    """Strict mode does not fail on a corrupt label (that would block the delete forever) and does not skip it either: a label that
    carries the member's id as a JSON string may be theirs, so it goes with its clip; one that cannot name them stays. A clip with no
    sidecar at all (genuinely unattributed: the owner's open question) is untouched."""
    named = wav(corpus / "turn-1.wav")
    (corpus / "turn-1.verdict.json").write_text('{"v": 1, "kind": "member", "id": "ann", "ts": 1')        # torn write
    other = wav(corpus / "turn-2.wav")
    (corpus / "turn-2.verdict.json").write_text('{"v": 1, "kind": "member", "id": "annabel"')            # another member, torn
    legacy = wav(corpus / "turn-3.wav")
    assert vc.erase_member("ann") == 1
    assert not os.path.exists(named) and not (corpus / "turn-1.verdict.json").exists()
    assert os.path.exists(other) and os.path.exists(legacy)


@needs_permissions
def test_a_clip_that_cannot_be_removed_fails_the_member_erase_and_keeps_its_label(corpus):
    clip = label(corpus, "turn-1", "member", ident="ann")
    os.chmod(corpus, 0o500)                                          # listable and readable, nothing can be unlinked
    try:
        with pytest.raises(OSError):
            vc.erase_member("ann")
    finally:
        os.chmod(corpus, 0o700)
    assert os.path.exists(clip) and (corpus / "turn-1.verdict.json").exists(), "the label stays, so the retry finds the clip"


@needs_permissions
def test_the_lenient_readers_still_skip_what_they_cannot_read(corpus):
    label(corpus, "turn-1", "guest", ident="g1")
    os.chmod(corpus, 0o000)
    try:
        assert vc.erase_guests(["g1"]) == 0 and vc.sweep(days=0) == dict(removed=0, kept=0)
    finally:
        os.chmod(corpus, 0o700)


# ── a speaker resolved AFTER the member delete completed (round 10, item 3) ─────────────────────────────────────────────────────

def _late_resolution(corpus, monkeypatch, *, drop_fails):
    """The member delete has COMPLETED (its mark is recorded, its scan ran while the speaker was still unresolved and missed this
    unlabelled clip); then the turn resolves the speaker to the deleted member (``routers/voice_tts._label_member_clip``)."""
    import erase_watermark as ew
    import routers.voice_tts as vt

    ew.set_backend(ew.MemoryBackend())
    clip = wav(corpus / "turn-late.wav")

    async def go():
        bound = ew.voice_turn_begin()
        try:
            vc.begin()
            vc.note_saved(clip)
            await ew.record("ann")                          # the delete ran and finished while the speaker was unknown
            assert vc.erase_member("ann") == 0, "the delete's own scan cannot see an unlabelled clip"
            if drop_fails:
                real = vc._remove_strict

                def boom(side, wav_path):
                    raise PermissionError("busy")
                monkeypatch.setattr(vc, "_remove_strict", boom)
                await vt._label_member_clip("ann")
                monkeypatch.setattr(vc, "_remove_strict", real)
            else:
                await vt._label_member_clip("ann")
        finally:
            ew.voice_turn_end(bound)
    asyncio.run(go())
    return clip


def test_a_clip_resolved_to_a_deleted_member_is_dropped_at_resolution(corpus, monkeypatch):
    import erase_watermark as ew

    try:
        clip = _late_resolution(corpus, monkeypatch, drop_fails=False)
        assert not os.path.exists(clip) and not (corpus / "turn-late.verdict.json").exists()
    finally:
        ew.set_backend(None)


def test_a_failed_drop_is_retried_by_the_clip_loops_pending_drop_pass(corpus, monkeypatch):
    """The drop fails: the clip stays LABELLED as the deleted member's with a ``pending_drop`` marker, which is the retry - the clip
    loop's ``retry_pending_drops`` removes it although the delete itself completed long ago. A live member's clip stays, and so does a
    clip of the SAME id saved after the erase (a new turn, a re-created member): it carries no marker."""
    import erase_watermark as ew

    try:
        clip = _late_resolution(corpus, monkeypatch, drop_fails=True)
        meta = json.loads((corpus / "turn-late.verdict.json").read_text())
        assert os.path.exists(clip) and meta["id"] == "ann" and meta["pending_drop"] == 1
        live = label(corpus, "turn-bob", "member", ident="bob")
        later = label(corpus, "turn-ann-later", "member", ident="ann")
        assert vc.retry_pending_drops() == 1
        assert not os.path.exists(clip) and os.path.exists(live) and os.path.exists(later)
        assert vc.retry_pending_drops() == 0
    finally:
        ew.set_backend(None)


def test_the_clip_loop_runs_the_pending_drop_pass_whatever_the_retention_flag_says():
    src = Path(vc.__file__).read_text()
    loop = src[src.index("async def run_retention_loop("):]
    assert loop.index("retry_pending_drops") < loop.index("if retention_enabled():")


def test_a_drop_whose_marker_cannot_be_written_still_removes_the_clip(corpus, monkeypatch):
    clip = wav(corpus / "turn-nomark.wav")
    vc.begin()
    vc.note_saved(clip)

    def full_disk(path, body):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(vc, "_write", full_disk)
    assert vc.drop_current("ann") is True
    assert not os.path.exists(clip), "no marker, but the removal was still tried and succeeded"


def test_a_drop_that_can_neither_mark_nor_remove_its_clip_is_listed_and_the_next_pass_removes_it(corpus, monkeypatch):
    """Both writes fail (an unwritable corpus): the clip's PATH goes into the pending-drop list (no member id), and the clip loop's
    pass removes it once the corpus is writable again, then drops the list."""
    clip = wav(corpus / "turn-stuck.wav")
    vc.begin()
    vc.note_saved(clip)

    def fail(*a, **k):
        raise PermissionError("read-only corpus")
    monkeypatch.setattr(vc, "_write", fail)
    real_remove = vc._remove_strict
    monkeypatch.setattr(vc, "_remove_strict", fail)
    with pytest.raises(OSError):
        vc.drop_current("ann")
    listed = Path(vc.pending_file()).read_text().split()
    assert listed == [clip] and "ann" not in Path(vc.pending_file()).read_text(), "the path only, never the member"
    monkeypatch.setattr(vc, "_remove_strict", real_remove)
    assert vc.retry_pending_drops() == 1
    assert not os.path.exists(clip) and not os.path.exists(vc.pending_file())


def test_an_append_does_not_wait_for_the_sweeps_removals_and_survives_its_prune(corpus):
    """The retry holds the pending-list lock only to read and to rewrite the list, never across its clip removals: an append made
    while a (slow) removal runs returns at once, and the rewrite (which re-reads the list under the lock) keeps it."""
    import threading

    first = wav(corpus / "turn-a.wav")
    Path(vc.pending_file()).write_text(first + "\n")
    late = str(corpus / "turn-b.wav")
    started, release, real = threading.Event(), threading.Event(), vc._remove_strict

    def slow_remove(side, clip):
        started.set()
        release.wait(5)                             # the sweep is mid-removal for as long as the test wants
        real(side, clip)
    vc._remove_strict = slow_remove
    try:
        t = threading.Thread(target=vc.retry_pending_drops)
        t.start()
        started.wait(2)
        t0 = time.monotonic()
        vc._note_pending(late)                      # a voice turn's failed drop: must not wait for the sweep
        waited = time.monotonic() - t0
        release.set()
        t.join(5)
    finally:
        vc._remove_strict = real
    assert waited < 0.5, f"the append waited {waited:.2f}s for the sweep"
    assert not os.path.exists(first) and Path(vc.pending_file()).read_text().split() == [late]


def test_a_voice_turns_clip_drop_never_blocks_the_event_loop(corpus, monkeypatch):
    """The drop's file work (and, on a double failure, the pending list's lock) runs in a worker thread: while it is held up, the event
    loop keeps serving other work (a ticker keeps ticking), and the turn's await returns once the drop is done."""
    import threading

    import erase_watermark as ew
    import routers.voice_tts as vt

    ew.set_backend(ew.MemoryBackend())
    clip = wav(corpus / "turn-slow.wav")
    held = threading.Event()
    real = vc._remove_strict

    def slow_remove(side, wav_path):
        held.wait(0.6)                              # a slow disk, or a sweep the drop would have waited for
        real(side, wav_path)
    monkeypatch.setattr(vc, "_remove_strict", slow_remove)

    async def go():
        bound = ew.voice_turn_begin()
        try:
            vc.begin()
            vc.note_saved(clip)
            await ew.record("ann")                   # deleted after the turn began: the clip must be dropped
            gaps, last, stop = [], [asyncio.get_running_loop().time()], [False]

            async def ticker():
                while not stop[0]:
                    await asyncio.sleep(0.01)
                    now = asyncio.get_running_loop().time()
                    gaps.append(now - last[0])
                    last[0] = now
            tick = asyncio.ensure_future(ticker())
            t0 = asyncio.get_running_loop().time()
            await asyncio.wait_for(vt._label_member_clip("ann"), timeout=5)
            took = asyncio.get_running_loop().time() - t0
            stop[0] = True
            await tick
            return gaps, took
        finally:
            ew.voice_turn_end(bound)
    try:
        gaps, took = asyncio.run(go())
    finally:
        ew.set_backend(None)
    assert took >= 0.5 and max(gaps) < 0.2, f"the loop stalled {max(gaps):.2f}s during the drop"
    assert not os.path.exists(clip) and not (corpus / "turn-slow.verdict.json").exists()
