"""Voice enrolment MODE on the panel daemon (`scripts/setup/zoe_voice_enroll_mode.py` + the daemon's "Enrollment mode" section).

The state machine is pure stdlib with every side effect injected, so it is pinned here with fake audio and a fake voice - no mic, no
network, no systemd. The daemon glue is loaded the way `test_voice_daemon_speaker_cache.py` loads it (pyaudio stubbed).

Groups (each has a break-the-fix control run by editing the module; see the PR):
  consent      the intro (what is kept) is spoken BEFORE anything is recorded; no voice -> no recording
  happy        three good captures -> uploaded together, confirmed aloud, reported
  retry        a bad capture is retried with spoken guidance; three bad ones end the run with nothing stored
  cancel       the word "stop", an external stop, a timeout: nothing is ever uploaded, and it is said so
  capture      assess_wav judges length, level, clipping and constant noise from the WAV alone
  screen       every step also goes up on the panel's talk screen (phrase, n of N, recording, tick, re-ask, done) BEFORE Zoe speaks it;
               every ending that is not a closing line clears it; the spoken prompt is unchanged
  glue         the daemon queues the command, runs it in the main loop, purges a forgotten profile, speaks in Zoe's voice, beeps
"""
from __future__ import annotations

import importlib.util
import io
import json
import math
import os
import struct
import sys
import threading
import types
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.ci_safe

_SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup"


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, _SETUP / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod            # a dataclass resolves its string annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


em = _load("zoe_voice_enroll_mode_under_test", "zoe_voice_enroll_mode.py")
announce = _load("zoe_voice_announce_under_test", "zoe_voice_announce.py")

RATE = 16000


def wav(duration=3.0, amp=8000, lead=0.3, freq=220, channels=1, square=False, steady=False, tail=0.2):
    """A synthetic capture: `lead` s of silence, then a pulsing tone (speech-like bursts), then `tail` s of silence."""
    n = int(RATE * duration)
    frames = bytearray()
    for i in range(n):
        t = i / RATE
        if t < lead or t > duration - tail:
            s = 0
        else:
            envelope = 1.0 if steady else (0.5 + 0.5 * math.sin(2 * math.pi * 3 * t)) ** 0.5
            wave_v = math.copysign(1.0, math.sin(2 * math.pi * freq * t)) if square else math.sin(2 * math.pi * freq * t)
            s = int(amp * envelope * wave_v)
        frames += struct.pack("<h", max(-32768, min(32767, s))) * channels
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


GOOD = wav()
SCRIPT = json.loads((Path(__file__).resolve().parents[2] / "services/zoe-data/lexicons_data/voice_enrollment_en.json").read_text())["script"]


class Rig:
    """Fake voice, mic, upload and clock; records the order of everything."""

    def __init__(self, captures=None, *, voice_ok=True, uploads=None, transcripts=None, tick=1.0, cancelled=None, max_total_s=90.0, **kw):
        self.events, self.said, self.uploaded, self.reports, self.whys = [], [], [], [], []
        self.captures = list(captures if captures is not None else [GOOD] * 3)
        self.upload_results = list(uploads if uploads is not None else [True] * 3)
        self.transcripts = list(transcripts or [])
        self.voice_ok, self.now, self.tick = voice_ok, 0.0, tick
        self.cancel_flag = cancelled or (lambda: False)
        self.run_obj = em.EnrollmentRun(
            {**SCRIPT, "phrases": ["alpha one", "bravo two", "charlie three"]}, say=self.say, record=self.record, upload=self.upload,
            name="Ann", transcribe=self.transcribe, report=lambda o, n, why="": self.reports.append((o, n)) or self.whys.append(why), cancelled=self.cancel_flag,
            clock=lambda: self.now, max_total_s=max_total_s, **kw)

    def say(self, text):
        self.events.append(("say", text))
        self.said.append(text)
        self.now += self.tick
        return self.voice_ok

    def record(self):
        self.events.append(("record",))
        self.now += self.tick * 5
        return self.captures.pop(0) if self.captures else GOOD

    def transcribe(self, w):
        return self.transcripts.pop(0) if self.transcripts else "hello there"

    def upload(self, w):
        self.events.append(("upload",))
        self.uploaded.append(w)
        r = self.upload_results.pop(0) if self.upload_results else True
        if isinstance(r, Exception):
            raise r
        return r

    def run(self):
        return self.run_obj.run()


# ── consent ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_intro_states_what_is_kept_before_anything_is_recorded():
    rig = Rig()
    rig.run()
    kinds = [e[0] for e in rig.events]
    assert rig.said[0].startswith("Hi Ann.") and "voice signature, never the recordings" in rig.said[0]
    assert kinds.index("say") < kinds.index("record")


def test_no_voice_means_no_recording_at_all():
    rig = Rig(voice_ok=False)
    res = rig.run()
    assert (res.outcome, res.reason) == (em.FAILED, "no_voice")
    assert not any(e[0] in ("record", "upload") for e in rig.events)
    assert rig.reports == [(em.FAILED, 0)]


def test_an_intro_that_was_not_heard_means_nothing_is_recorded_even_if_later_lines_would_play():
    rig = Rig()
    plain_say = rig.say
    rig.run_obj._say = lambda text: False if text.startswith("Hi Ann") else plain_say(text)
    res = rig.run()
    assert (res.outcome, res.reason) == (em.FAILED, "no_voice")
    assert not any(e[0] in ("record", "upload") for e in rig.events)


def test_a_voice_that_raises_is_a_voice_that_is_unavailable():
    rig = Rig()
    rig.say = lambda t: (_ for _ in ()).throw(RuntimeError("tts down"))
    rig.run_obj._say = rig.say
    assert rig.run().reason == "no_voice"


# ── happy path ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_three_good_captures_are_uploaded_together_after_the_last_one_and_confirmed_aloud():
    rig = Rig()
    res = rig.run()
    assert (res.outcome, res.samples) == (em.DONE, 3)
    kinds = [e[0] for e in rig.events]
    assert kinds.count("record") == 3 and kinds.count("upload") == 3
    assert max(i for i, k in enumerate(kinds) if k == "record") < min(i for i, k in enumerate(kinds) if k == "upload")   # capture, THEN store
    prompts = [s for s in rig.said if s.startswith("Phrase")]
    assert prompts == ["Phrase 1. After the beep, say: alpha one", "Phrase 2. After the beep, say: bravo two",
                       "Phrase 3. After the beep, say: charlie three"]
    assert rig.said.count("Got it.") == 3 and rig.said[-1] == "That's it, Ann. I'll know your voice from now on."
    assert rig.reports == [(em.DONE, 3)]


def test_recordings_do_not_outlive_the_run():
    rig = Rig()
    rig.run()
    assert rig.run_obj._wavs == []


# ── retry ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_bad_capture_gets_spoken_guidance_and_a_second_try():
    quiet = wav(amp=300)
    rig = Rig(captures=[quiet, GOOD, GOOD, GOOD])
    res = rig.run()
    assert res.outcome == em.DONE and len(rig.uploaded) == 3          # only the good ones are stored
    assert "That was a bit quiet. A little louder, please." in rig.said
    assert [s for s in rig.said if s.startswith("Phrase 1")] == ["Phrase 1. After the beep, say: alpha one"] * 2


@pytest.mark.parametrize("bad,line", [(wav(duration=0.5), "I only caught a little"), (wav(amp=32767, square=True), "too noisy or too loud"),
                                      (None, "I only caught a little")])
def test_each_kind_of_bad_capture_has_its_own_guidance(bad, line):
    rig = Rig(captures=[bad, GOOD, GOOD, GOOD])
    rig.run()
    assert any(line in s for s in rig.said)


def test_three_bad_captures_end_the_run_with_nothing_stored_and_it_says_so():
    rig = Rig(captures=[wav(amp=300)] * 3)
    res = rig.run()
    assert (res.outcome, res.samples) == (em.FAILED, 0)
    assert rig.uploaded == [] and "I haven't kept anything" in rig.said[-1]


# ── cancel / timeout ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_saying_stop_cancels_cleanly_and_nothing_is_uploaded():
    rig = Rig(transcripts=["hey zoe what's the weather", "stop"])
    res = rig.run()
    assert (res.outcome, res.reason) == (em.CANCELLED, "said_stop")
    assert rig.uploaded == [] and rig.said[-1] == "Okay, I've stopped. I haven't kept anything."
    assert rig.reports == [(em.CANCELLED, 0)]


def test_stop_inside_a_long_phrase_is_not_a_cancel():
    assert em.is_cancel("stop", ["stop"]) and em.is_cancel("Oh, never mind!", ["never mind"])
    assert not em.is_cancel("please don't stop the music when I get home tonight", ["stop"])
    assert not em.is_cancel("", ["stop"]) and not em.is_cancel("hello", [])


def test_an_external_stop_ends_it_before_the_next_prompt():
    flag = {"stop": False}
    rig = Rig(cancelled=lambda: flag["stop"])
    orig = rig.record

    def record_then_stop():
        flag["stop"] = True
        return orig()

    rig.run_obj._record = record_then_stop
    res = rig.run()
    assert res.outcome == em.CANCELLED and rig.uploaded == []


def test_the_report_says_WHY_so_a_person_stopping_is_told_apart_from_a_daemon_shutdown():
    spoken_stop = Rig(transcripts=["stop"])
    spoken_stop.run()
    assert spoken_stop.whys == ["said_stop"]

    tapped = Rig(cancelled=lambda: "tapped_stop")
    res = tapped.run()
    assert (res.outcome, res.reason) == (em.CANCELLED, "tapped_stop") and tapped.whys == ["tapped_stop"]
    assert tapped.said[-1] == "Okay, I've stopped. I haven't kept anything."

    going_down = Rig(cancelled=lambda: "shutdown")
    res = going_down.run()
    assert (res.outcome, res.reason) == (em.CANCELLED, "shutdown") and going_down.whys == ["shutdown"]
    assert going_down.uploaded == []
    assert not any("stopped" in s for s in going_down.said)           # nobody is being addressed during a restart: silent

    unspecified = Rig(cancelled=lambda: True)
    assert unspecified.run().reason == "stopped"                      # a bare True is never mistaken for the person declining


def test_the_daemon_tells_a_shutdown_from_a_tap(daemon):
    daemon._shutdown.clear()
    daemon._enroll_cancel.clear()
    assert daemon._enroll_stop_reason() == ""
    daemon._enroll_cancel.set()
    assert daemon._enroll_stop_reason() == "tapped_stop"
    daemon._shutdown.set()
    try:
        assert daemon._enroll_stop_reason() == "shutdown"            # a restart that also finds the tap flag set is still a shutdown
    finally:
        daemon._shutdown.clear()
        daemon._enroll_cancel.clear()


def test_the_daemon_posts_the_reason_with_the_result(daemon, monkeypatch):
    posts = []
    monkeypatch.setattr(daemon, "_enroll_say", lambda t: True)
    monkeypatch.setattr(daemon, "_enroll_record_once", lambda pa: GOOD)
    monkeypatch.setattr(daemon, "stt_on_jetson", lambda w: "stop")
    monkeypatch.setattr(daemon, "_sync_speaker_profiles", lambda force=False: None)
    monkeypatch.setattr(daemon, "_api_post", lambda path, data, timeout=60, retries=None: posts.append((path, data)) or {"ok": True})
    assert daemon._handle_server_command(_cmd())
    daemon._run_enrollment_mode(object())
    assert ("/api/enrollment/result", {"status": "cancelled", "user_id": "ann", "samples": 0, "reason": "said_stop"}) in posts
    assert not [p for p, _ in posts if p == "/api/voice/enroll"]


def test_it_is_bounded_by_the_time_budget_and_stores_nothing():
    rig = Rig(tick=8.0, max_total_s=90.0, captures=[wav(amp=300)] * 12)
    res = rig.run()
    assert res.outcome == em.TIMEOUT and rig.uploaded == []
    assert rig.now < 90 + 8 * 8                                       # stopped soon after the budget, not after every retry
    assert "I haven't kept anything" in rig.said[-1]


def test_an_exception_anywhere_still_reports_and_clears():
    rig = Rig()
    rig.record = lambda: (_ for _ in ()).throw(OSError("mic gone"))
    rig.run_obj._record = rig.record
    res = rig.run()
    assert res.outcome == em.FAILED and rig.reports == [(em.FAILED, 0)] and rig.run_obj._wavs == []


# ── upload ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_refused_first_upload_stores_nothing_and_says_so():
    rig = Rig(uploads=[False])
    res = rig.run()
    assert (res.outcome, res.samples) == (em.FAILED, 0) and "couldn't save it" in rig.said[-1]


def test_a_failure_part_way_is_reported_as_partial_honestly():
    rig = Rig(uploads=[True, RuntimeError("boom")])
    res = rig.run()
    assert (res.outcome, res.samples) == (em.PARTIAL, 1) and "only saved part of it" in rig.said[-1]


def test_uploads_are_not_cut_short_by_the_capture_budget():
    rig = Rig(tick=30.0, max_total_s=90.0)           # three phrases burn the budget exactly; the finished capture must still be stored
    res = rig.run()
    assert res.outcome in (em.DONE, em.TIMEOUT)
    if res.outcome == em.DONE:
        assert len(rig.uploaded) == 3


# ── the script ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_defaults_cover_a_command_with_no_script_and_match_the_old_flow_phrases():
    flow = _load("zoe_enroll_flow_under_test", "zoe_enroll_flow.py")
    assert list(em.DEFAULT_PHRASES) == list(flow.VOICE_PHRASES)
    merged = em.merge_script({"intro": "", "phrases": []})
    assert merged["intro"] == em.DEFAULT_SCRIPT["intro"] and merged["phrases"] == list(em.DEFAULT_PHRASES)
    assert em.merge_script({"intro": "Hola"})["intro"] == "Hola"


def test_every_script_key_the_server_sends_is_one_the_machine_speaks():
    spoken = {"intro", "prompt", "retry_short", "retry_quiet", "retry_noise", "got", "done", "partial", "cancelled", "timeout", "failed",
              "store_failed", "intro_many", "retry_odd"}     # the last two: the 5-clip (ResNet34) enrolment
    screen = {"screen_say", "screen_recording", "screen_odd", "screen_saving", "screen_done", "screen_partial", "screen_failed"}   # the talk screen
    assert spoken <= set(SCRIPT) and screen <= set(SCRIPT) and set(SCRIPT) - spoken - screen == {"cancel_words"}


# ── capture quality ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_assess_wav_accepts_speech_and_judges_the_rest():
    assert em.assess_wav(wav()) == (True, "ok")
    assert em.assess_wav(wav(channels=2)) == (True, "ok")
    assert em.assess_wav(wav(duration=0.5)) == (False, "short")
    assert em.assess_wav(wav(amp=300)) == (False, "quiet")
    assert em.assess_wav(wav(amp=0)) == (False, "quiet")
    assert em.assess_wav(wav(amp=32767, square=True)) == (False, "noise")
    assert em.assess_wav(wav(duration=4.0, lead=0.0, tail=0.0, steady=True)) == (False, "noise")       # a fan, not a phrase
    assert em.assess_wav(b"not a wav") == (False, "short")


# ── the daemon glue ───────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def daemon():
    stubs = {n: MagicMock() for n in ("pyaudio",) if n not in sys.modules}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    sys.path.insert(0, str(_SETUP))                  # the daemon imports its sidecar modules from its own directory
    try:
        mod = _load("zoe_voice_daemon_enroll_under_test", "zoe_voice_daemon.py")
        yield mod
    finally:
        sys.path.remove(str(_SETUP))
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


@pytest.fixture(autouse=True)
def _clean(request):
    if "daemon" not in request.fixturenames:
        yield
        return
    d = request.getfixturevalue("daemon")
    d._enroll_request = None
    d._enroll_running.clear()
    d._enroll_cancel.clear()
    d._orb_activate_event.clear()
    d._recording_active.clear()
    yield
    d._enroll_request = None


def _cmd(**kw):
    return {"command": {"kind": "voice_enroll", "user_id": "ann", "name": "Ann", "max_total_s": 90, "script": SCRIPT, **kw}}


def test_the_poller_hands_a_command_to_the_handler_and_never_reads_it_aloud():
    spoken, handled = [], []
    p = announce.AnnouncePoller(fetch=lambda: [], speak=lambda a: spoken.append(a) or True, is_busy=lambda: False,
                                command=lambda a: handled.append(a) or True, ack=lambda a: None)
    assert p.deliver({"id": "1", "text": "", "command": {"kind": "voice_enroll"}, "expires_in_s": 60}, wait=lambda s: None) == "spoken"
    assert handled and not spoken
    p2 = announce.AnnouncePoller(fetch=lambda: [], speak=lambda a: spoken.append(a) or True, is_busy=lambda: False)
    assert p2.deliver({"id": "2", "text": "", "command": {}, "expires_in_s": 60}, wait=lambda s: None) == "speak_failed"
    assert not spoken                                                  # a poller without a handler still never speaks the row


def test_a_command_waits_for_an_idle_daemon_like_speech_does():
    busy = iter([True, True, False])
    handled = []
    p = announce.AnnouncePoller(fetch=lambda: [], speak=lambda a: True, is_busy=lambda: next(busy), command=lambda a: handled.append(a) or True)
    p.deliver({"id": "1", "text": "", "command": {"kind": "voice_enroll"}, "expires_in_s": 60}, wait=lambda s: None)
    assert handled


def test_a_stale_announce_module_disables_commands_loudly_instead_of_crashing(daemon, caplog):
    class Old:
        class AnnouncePoller:
            def __init__(self, *, fetch, speak, is_busy, poll_interval_s=5.0, logger=None, ack=None):
                pass

    assert daemon._enroll_poller_kwargs(Old) == {}
    assert daemon._enroll_poller_kwargs(announce) == {"command": daemon._handle_server_command}


def test_the_enrol_command_is_queued_once_and_validated(daemon):
    assert daemon._handle_server_command(_cmd()) is True
    assert daemon._enroll_request_pending()
    assert daemon._handle_server_command(_cmd()) is False                     # the duplicate while one is queued
    daemon._enroll_request = None
    assert daemon._handle_server_command(_cmd(user_id="")) is False
    assert daemon._handle_server_command({"command": {"kind": "something_else"}}) is False
    assert not daemon._enroll_request_pending()


def test_the_main_loop_runs_enrolment_before_it_looks_for_a_wake_word(daemon):
    import inspect

    src = inspect.getsource(daemon.main)
    assert "_enroll_request_pending()" in src and src.index("_enroll_request_pending()") < src.index("_orb_activate_event.is_set()")
    assert "_run_enrollment_mode(pa)" in src
    assert "systemctl" not in src and "systemctl" not in inspect.getsource(daemon._run_enrollment_mode)    # the daemon never stops itself


def test_the_whole_run_uploads_with_consent_reports_and_hands_the_mic_back(daemon, monkeypatch):
    posts = []
    monkeypatch.setattr(daemon, "_enroll_say", lambda t: True)
    monkeypatch.setattr(daemon, "_enroll_record_once", lambda pa: GOOD)
    monkeypatch.setattr(daemon, "stt_on_jetson", lambda w: "")
    monkeypatch.setattr(daemon, "_sync_speaker_profiles", lambda force=False: None)
    monkeypatch.setattr(daemon, "_api_post", lambda path, data, timeout=60, retries=None: posts.append((path, data)) or {"ok": True})
    assert daemon._handle_server_command(_cmd())
    daemon._run_enrollment_mode(object())
    enrols = [d for p, d in posts if p == "/api/voice/enroll"]
    assert len(enrols) == 3 and all(d["consent"] is True and d["user_id"] == "ann" and d["panel_id"] == daemon.PANEL_ID for d in enrols)
    assert ("/api/enrollment/result", {"status": "done", "user_id": "ann", "samples": 3, "reason": "ok"}) in posts
    assert not daemon._recording_active.is_set() and not daemon._enroll_running.is_set() and daemon._enroll_request is None


def test_enrolment_pauses_ambient_capture_while_it_runs(daemon, monkeypatch):
    seen = {}

    def say(text):
        seen["recording_active"] = daemon._recording_active.is_set()
        seen["running"] = daemon._enroll_running.is_set()
        return False

    monkeypatch.setattr(daemon, "_enroll_say", say)
    monkeypatch.setattr(daemon, "_api_post", lambda *a, **k: {"ok": True})
    daemon._handle_server_command(_cmd())
    daemon._run_enrollment_mode(object())
    assert seen == {"recording_active": True, "running": True}


def test_zoes_voice_is_waited_for_not_replaced_by_a_robot(daemon, monkeypatch):
    replies = iter([{"audio_base64": "ESP", "provider": "espeak-ng"}, {"audio_base64": "KOK", "provider": "kokoro-sidecar", "content_type": "audio/wav"}])
    played = []
    monkeypatch.setattr(daemon, "_api_post", lambda *a, **k: next(replies))
    monkeypatch.setattr(daemon, "play_audio_b64", lambda audio, ctype="audio/wav": played.append(audio) or True)
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)
    assert daemon._enroll_say("Hi") is True
    assert played == ["KOK"]


def test_zoes_voice_unavailable_is_reported_not_faked(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_api_post", lambda *a, **k: {"ok": False, "error": "HTTP 503"})
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)
    monkeypatch.setattr(daemon, "play_audio_b64", lambda *a, **k: pytest.fail("nothing to play"))
    assert daemon._enroll_say("Hi") is False


def test_the_prompts_promise_a_beep_so_there_is_always_one(daemon, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "play_wake_beep", lambda: calls.append("wake"))
    monkeypatch.setattr(daemon, "play_follow_up_beep", lambda: calls.append("follow_up"))
    monkeypatch.setattr(daemon, "WAKE_BEEP_ENABLED", True)
    daemon._enroll_beep()
    monkeypatch.setattr(daemon, "WAKE_BEEP_ENABLED", False)
    daemon._enroll_beep()
    assert calls == ["wake", "follow_up"]


def test_an_offer_opens_a_listening_window_but_plain_speech_does_not(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_speak_announcement", lambda ann: True)
    daemon._speak_announcement_then_listen({"id": "1", "text": "Want to?", "follow_up": True})
    assert daemon._orb_activate_event.is_set()
    daemon._orb_activate_event.clear()
    daemon._speak_announcement_then_listen({"id": "2", "text": "Good morning"})
    assert not daemon._orb_activate_event.is_set()
    monkeypatch.setattr(daemon, "_speak_announcement", lambda ann: False)                  # an offer that was not heard opens nothing
    daemon._speak_announcement_then_listen({"id": "3", "text": "Want to?", "follow_up": True})
    assert not daemon._orb_activate_event.is_set()


def test_a_cancel_tap_reaches_the_running_enrolment(daemon):
    assert daemon._activate_request_allowed("localhost:7777", None, "127.0.0.1") is True   # the kiosk on the box
    daemon._enroll_cancel.set()
    assert daemon._enroll_cancel.is_set()


# ── forgetting reaches the box ────────────────────────────────────────────────────────────────────────────────────────────

def test_a_forget_command_drops_that_members_profile_from_memory_and_disk_at_once(daemon, monkeypatch, tmp_path):
    cache_file = tmp_path / ".zoe-voice" / "speaker_profiles.json"
    monkeypatch.setattr(daemon, "_PROFILE_CACHE_PATH", str(cache_file))
    synced = []
    monkeypatch.setattr(daemon, "_sync_speaker_profiles", lambda force=False: synced.append(force))
    with daemon._profile_cache_lock:
        daemon._profile_cache["profiles"] = [{"user_id": "ann", "embedding_base64": "AA=="}, {"user_id": "bob", "embedding_base64": "BB=="}]
    assert daemon._handle_server_command({"command": {"kind": "voice_profile_forget", "user_id": "ann"}}) is True
    with daemon._profile_cache_lock:
        assert [p["user_id"] for p in daemon._profile_cache["profiles"]] == ["bob"]
    on_disk = json.loads(cache_file.read_text())
    assert [p["user_id"] for p in on_disk["profiles"]] == ["bob"]
    assert oct(os.stat(cache_file).st_mode & 0o777) == "0o600" and synced == [True]
    assert "AA==" not in cache_file.read_text()


def test_a_forget_command_without_a_user_does_nothing(daemon, monkeypatch):
    assert daemon._handle_server_command({"command": {"kind": "voice_profile_forget", "user_id": " "}}) is False


# ── the talk screen: the phrase is shown as well as spoken ────────────────────────────────────────────────────────────────

def screen_rig(captures=None, **kw):
    """A Rig whose screen callback lands in the same event list as speech and recording, so ORDER can be asserted."""
    holder = {}
    rig = Rig(captures, screen=lambda state, **f: holder["rig"].events.append(("screen", state, f)), **kw)
    holder["rig"] = rig
    return rig


def screens(rig):
    return [(e[1], e[2]) for e in rig.events if e[0] == "screen"]


def test_each_step_emits_phrase_index_total_and_state():
    rig = screen_rig()
    assert rig.run().outcome == em.DONE
    got = screens(rig)
    assert [s for s, _ in got] == ["phrase", "recording", "accepted"] * 3 + ["saving", "done"]
    for n, word in enumerate(("alpha one", "bravo two", "charlie three"), start=1):
        phrase, recording, accepted = got[3 * (n - 1): 3 * n]
        assert phrase[1] == {"line": SCRIPT["screen_say"], "phrase": word, "index": n, "total": 3}
        assert recording[1] == {"line": SCRIPT["screen_recording"], "phrase": word, "index": n, "total": 3}
        assert accepted[1] == {"phrase": word, "index": n, "total": 3}
    assert got[-1] == ("done", {"line": SCRIPT["screen_done"]}) and got[-2] == ("saving", {"line": SCRIPT["screen_saving"]})


def test_the_phrase_is_on_screen_before_zoe_speaks_it_and_stays_up_while_recording():
    rig = screen_rig()
    rig.run()
    ev = rig.events
    for n in (1, 2, 3):
        i_screen = next(i for i, e in enumerate(ev) if e[0] == "screen" and e[1] == "phrase" and e[2]["index"] == n)
        i_say = next(i for i, e in enumerate(ev) if e[0] == "say" and e[1].startswith(f"Phrase {n}."))
        i_recshow = next(i for i, e in enumerate(ev) if e[0] == "screen" and e[1] == "recording" and e[2]["index"] == n)
        i_record = [i for i, e in enumerate(ev) if e[0] == "record"][n - 1]
        # up BEFORE she starts speaking it; "recording" (same phrase, still on screen) goes up before the mic opens; nothing clears it between
        assert i_screen < i_say < i_recshow < i_record
        assert not any(e[0] == "screen" and e[1] == "clear" for e in ev[i_screen:i_record])


def test_the_spoken_prompt_is_unchanged_by_the_screen():
    """The screen is IN ADDITION to Zoe speaking each phrase. Same run with and without a screen: identical speech, identical recordings."""
    plain, shown = Rig(), screen_rig()
    plain.run()
    shown.run()
    assert shown.said == plain.said and shown.uploaded == plain.uploaded
    assert [s for s in shown.said if s.startswith("Phrase")] == ["Phrase 1. After the beep, say: alpha one", "Phrase 2. After the beep, say: bravo two",
                                                                  "Phrase 3. After the beep, say: charlie three"]
    assert [e[0] for e in shown.events if e[0] != "screen"] == [e[0] for e in plain.events]


def test_a_screen_that_cannot_be_reached_never_changes_the_run():
    rig = Rig(screen=lambda state, **f: (_ for _ in ()).throw(RuntimeError("push down")))
    res = rig.run()
    assert (res.outcome, res.samples) == (em.DONE, 3) and len(rig.uploaded) == 3 and rig.said[-1].startswith("That's it")


def test_a_retried_capture_shows_the_same_phrase_again_with_no_tick_for_the_bad_one():
    rig = screen_rig([wav(amp=300), GOOD, GOOD, GOOD])
    assert rig.run().outcome == em.DONE
    first = [s for s, f in screens(rig)][:6]
    assert first == ["phrase", "recording", "phrase", "recording", "accepted", "phrase"]       # no tick between the two tries of phrase 1


def test_the_reask_state_is_shown_on_the_phrase_that_sounded_different():
    state = {"calls": 0}

    def build(embs):
        state["calls"] += 1
        return (None, [1]) if state["calls"] == 1 else ("PROFILE", [])

    rig = screen_rig(clips=3, embed=lambda w: [1.0], build=build, upload_profile=lambda p, n: True, legacy_upload=False)
    res = rig.run()
    assert res.outcome == em.DONE
    got = screens(rig)
    reask = [f for s, f in got if s == "reask"]
    assert reask and all(f["index"] == 2 and f["phrase"] == "bravo two" and f["total"] == 3 and f["line"] == SCRIPT["screen_odd"] for f in reask)
    # on screen as she says "that one sounded different", and the next states are the re-captured phrase 2
    ev = rig.events
    i_reask = next(i for i, e in enumerate(ev) if e[0] == "screen" and e[1] == "reask")
    i_say = next(i for i, e in enumerate(ev) if e[0] == "say" and e[1] == SCRIPT["retry_odd"])
    assert i_reask < i_say
    after = [s for s, f in got[[s for s, f in got].index("reask"):]]
    assert after[:4] == ["reask", "reask", "recording", "accepted"] and after[-2:] == ["saving", "done"]


@pytest.mark.parametrize("name,rig_kw,transcripts,outcome", [
    ("said stop", {}, ["stop"], em.CANCELLED),
    ("external stop", {"cancelled": lambda: "tapped_stop"}, None, em.CANCELLED),
    ("daemon shutdown", {"cancelled": lambda: "shutdown"}, None, em.CANCELLED),
    ("timeout", {"max_total_s": 10.0, "tick": 6.0}, None, em.TIMEOUT),
    ("no voice", {"voice_ok": False}, None, em.FAILED),
])
def test_every_ending_that_is_not_a_closing_line_clears_the_screen(name, rig_kw, transcripts, outcome):
    rig = screen_rig(transcripts=transcripts, **rig_kw)
    res = rig.run()
    assert res.outcome == outcome, name
    got = screens(rig)
    assert got[-1] == ("clear", {}), (name, got)
    assert not any(s in ("done", "partial", "failed") for s, _ in got), name


def test_a_stop_clears_the_screen_at_once_not_after_her_closing_line():
    rig = screen_rig(transcripts=["stop"])
    assert rig.run().outcome == em.CANCELLED
    ev = rig.events
    i_clear = next(i for i, e in enumerate(ev) if e[0] == "screen" and e[1] == "clear")
    i_line = next(i for i, e in enumerate(ev) if e[0] == "say" and e[1] == SCRIPT["cancelled"])
    assert i_clear < i_line
    assert [e[1] for e in ev if e[0] == "screen"].count("clear") == 1          # once, not again from the run's safety net


def test_a_crash_inside_the_run_still_clears_the_screen():
    rig = screen_rig()
    rig.run_obj._record = lambda: (_ for _ in ()).throw(RuntimeError("mic exploded"))
    res = rig.run()
    assert (res.outcome, res.reason) == (em.FAILED, "crashed")
    assert screens(rig)[-1] == ("clear", {})


def test_a_failed_run_shows_the_failure_line_and_does_not_also_clear_it():
    """The closing line is shown WITH the spoken one and the panel expires it itself; a clear right after would remove it unread."""
    rig = screen_rig(captures=[wav(amp=300)] * 3)
    assert rig.run().outcome == em.FAILED
    got = screens(rig)
    assert got[-1] == ("failed", {"line": SCRIPT["screen_failed"]}) and ("clear", {}) not in got


def test_a_partial_save_shows_its_own_closing_line():
    rig = screen_rig(uploads=[True, True, False])
    assert rig.run().outcome == em.PARTIAL
    assert screens(rig)[-1] == ("partial", {"line": SCRIPT["screen_partial"]})


def test_the_screen_never_carries_the_members_name_or_audio():
    rig = screen_rig()
    rig.run()
    blob = json.dumps(screens(rig))
    assert "Ann" not in blob
    assert not any(isinstance(v, (bytes, bytearray)) for _, f in screens(rig) for v in f.values())
    # NEGATIVE CONTROL: the check can fail - a script whose screen line used {name} WOULD put the name on the wall
    leaked = []
    leaky = Rig(screen=lambda state, **f: leaked.append((state, f)))
    leaky.run_obj.script["screen_done"] = "Done {name}"
    leaky.run()
    assert "Ann" in json.dumps(leaked)


def test_a_command_without_screen_lines_still_gets_english_ones():
    lines = []
    rig = Rig(screen=lambda state, **f: lines.append(f.get("line")))
    rig.run_obj.script = em.merge_script({k: v for k, v in rig.run_obj.script.items() if not k.startswith("screen_")})
    rig.run()
    assert "Say:" in lines and "Done - I'll recognise your voice" in lines


# ── the daemon posts it, to the server, and never lets it slow the run ────────────────────────────────────────────────────

def _run_daemon_enrolment(daemon, monkeypatch, api):
    monkeypatch.setattr(daemon, "_enroll_say", lambda t: True)
    monkeypatch.setattr(daemon, "_enroll_record_once", lambda pa: GOOD)
    monkeypatch.setattr(daemon, "stt_on_jetson", lambda w: "")
    monkeypatch.setattr(daemon, "_sync_speaker_profiles", lambda force=False: None)
    monkeypatch.setattr(daemon, "_api_post", api)
    assert daemon._handle_server_command(_cmd(script={**SCRIPT, "phrases": ["alpha one", "bravo two", "charlie three"]}))
    daemon._run_enrollment_mode(object())


def test_the_daemon_posts_each_step_to_the_screen_endpoint_without_the_members_identity(daemon, monkeypatch):
    posts = []
    _run_daemon_enrolment(daemon, monkeypatch,
                          lambda path, data, timeout=60, retries=None: posts.append((path, data, timeout, retries)) or {"ok": True})
    screen = [(d["state"], d) for p, d, t, r in posts if p == "/api/enrollment/screen"]
    assert [s for s, _ in screen] == ["phrase", "recording", "accepted"] * 3 + ["saving", "done"]
    assert screen[0][1] == {"state": "phrase", "line": "Say:", "phrase": "alpha one", "index": 1, "total": 3}
    assert all("user_id" not in d and "name" not in d and "panel_id" not in d for _, d in screen)       # the server uses the token's panel
    assert all(t <= 3 and r == 0 for p, d, t, r in posts if p == "/api/enrollment/screen")              # short, never retried
    assert len([1 for p, d, t, r in posts if p == "/api/voice/enroll"]) == 3                             # the run itself is unchanged


def test_a_server_that_has_no_screen_endpoint_costs_two_failed_posts_not_one_per_step(daemon, monkeypatch):
    posts = []

    def api(path, data, timeout=60, retries=None):
        posts.append(path)
        return {"ok": False, "error": "HTTP 404"} if path == "/api/enrollment/screen" else {"ok": True}

    _run_daemon_enrolment(daemon, monkeypatch, api)
    assert posts.count("/api/enrollment/screen") == 2                                    # then it stops asking
    assert posts.count("/api/voice/enroll") == 3 and "/api/enrollment/result" in posts    # the enrolment still completes


def test_a_screen_post_that_raises_does_not_break_the_enrolment(daemon, monkeypatch):
    posts = []

    def api(path, data, timeout=60, retries=None):
        posts.append(path)
        if path == "/api/enrollment/screen":
            raise ConnectionError("down")
        return {"ok": True}

    _run_daemon_enrolment(daemon, monkeypatch, api)
    assert posts.count("/api/voice/enroll") == 3 and "/api/enrollment/result" in posts


def test_the_failure_counter_is_reset_for_each_run(daemon, monkeypatch):
    daemon._enroll_screen_failures = 5
    posts = []
    _run_daemon_enrolment(daemon, monkeypatch, lambda path, data, timeout=60, retries=None: posts.append(path) or {"ok": True})
    assert posts.count("/api/enrollment/screen") == 11
