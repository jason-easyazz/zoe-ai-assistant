"""measure_first_sound.py (the first-sound latency probe): a failed synthesis is not a fast reply, and every sample closes its brain stream.

Greptile #1965: the probe stamped a "sound" time whether or not Kokoro returned audio (a failed ack / filler / first unit looked like the
fastest reply), and it stopped reading the brain stream without closing it (plain or prefetched), so later samples depended on garbage
collection. Synthetic: a scripted synthesizer and a scripted brain stream; no network, no live service, no database.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PERF = REPO / "scripts" / "perf"
ZOE = REPO / "services" / "zoe-data"
for path in (str(PERF), str(ZOE)):
    if path not in sys.path:
        sys.path.insert(0, path)


def _load():
    spec = importlib.util.spec_from_file_location("measure_first_sound", PERF / "measure_first_sound.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["measure_first_sound"] = mod
    spec.loader.exec_module(mod)
    return mod


mfs = _load()


class Brain:
    """A scripted brain stream that would keep going: only a CLOSE ends it (``closed`` is set by its own cleanup)."""

    def __init__(self, deltas):
        self.deltas, self.closed = list(deltas), False

    async def stream(self):
        try:
            for d in self.deltas:
                yield d
            while True:                       # the sidecar is still decoding when the probe has its first unit
                await asyncio.sleep(0.01)
                yield "and more words follow "
        finally:
            self.closed = True


def make_vt(ok_for):
    """A stand-in for routers.voice_tts: ``ok_for(text)`` decides whether synthesis returned audio."""
    calls: list = []

    async def synth(text):
        calls.append(text)
        await asyncio.sleep(0)
        return b"RIFF" if ok_for(text) else None

    def sentences(buf):
        done, _, rest = buf.rpartition(". ")
        return ([done + "."], rest) if done else ([], buf)

    return types.SimpleNamespace(
        calls=calls, _synthesize_kokoro_sidecar=synth, _VOICE_TOOL_SENTINEL_PREFIXES=("__TOOL__:",),
        _voice_tool_name_from_sentinel=lambda d: d.split(":", 1)[1], _voice_tool_filler_enabled=lambda: True,
        _voice_tool_filler=lambda name: f"filler for {name}", _fast_first_audio_enabled=lambda: False,
        _extract_first_unit=lambda b: (None, b), _extract_complete_sentences=sentences)


def make_fs(ack):
    import voice_first_sound as real

    return types.SimpleNamespace(dispatch_ack=lambda rr: ack, prefetch=real.prefetch)


def drive(brain, vt, fs, rr=None):
    """Run one timed turn; ``closed_at_return`` is read the instant the call returns, INSIDE the loop (``asyncio.run`` would otherwise
    shut a left-over async generator down afterwards and hide the missing close)."""
    async def go():
        rec = await mfs.time_first_sound(brain.stream(), rr or {}, vt, fs, t_post=0.0, t_kw=0.0)
        rec["closed_at_return"] = brain.closed
        return rec
    return asyncio.run(go())


DELTAS = ["The answer is on your calendar", " for tomorrow. ", "Anything else"]


def test_a_failed_synthesis_is_not_a_fast_reply():
    rec = drive(Brain(DELTAS), make_vt(lambda t: False), make_fs("One sec."))
    assert rec["first_audio_s"] is None and rec["no_audio"] is True
    assert rec["ack_sound_s"] is None and rec["filler_sound_s"] is None and rec["first_sound_s"] is None
    assert rec["synth_failed"] == ["ack", "first_unit"]


def test_the_filler_and_the_first_answer_chunk_are_stamped_only_when_audio_came_back():
    deltas = ["__TOOL__:calendar", "The answer is on your calendar", " for tomorrow. "]
    bad_filler = drive(Brain(deltas), make_vt(lambda t: not t.startswith("filler")), make_fs(None))
    assert bad_filler["filler_sound_s"] is None and "filler" in bad_filler["synth_failed"]
    assert bad_filler["first_audio_s"] == bad_filler["first_sound_s"] is not None          # the answer chunk is the first real sound
    bad_unit = drive(Brain(deltas), make_vt(lambda t: t.startswith("filler")), make_fs(None))
    assert bad_unit["first_sound_s"] is None and bad_unit["synth_failed"] == ["first_unit"]
    assert bad_unit["first_audio_s"] == bad_unit["filler_sound_s"] is not None              # the filler was audible: that IS first audio


def test_a_working_synthesis_is_timed_as_before():
    rec = drive(Brain(DELTAS), make_vt(lambda t: True), make_fs("One sec."))
    assert rec["no_audio"] is False and rec["synth_failed"] == []
    assert rec["ack_sound_s"] is not None and rec["first_audio_s"] == rec["ack_sound_s"] <= rec["first_sound_s"]


def test_the_summary_reports_silent_turns_apart_and_never_averages_them_in():
    fast_lie = {"cond": "base", "shape": "chat", "path": "brain", "no_audio": True, "first_audio_s": None, "synth_failed": ["first_unit"],
                "clip_s": 2.0, "first_unit_chars": 30}
    real = {"cond": "base", "shape": "chat", "path": "brain", "no_audio": False, "first_audio_s": 1.5, "synth_failed": [],
            "clip_s": 2.0, "first_unit_chars": 30}
    out = mfs.summarize([fast_lie, real, dict(real, first_audio_s=2.5)])["base/chat"]
    assert out["brain_turns"] == 2 and out["no_audio_turns"] == 1 and out["synth_failed"] == 1
    assert out["median_s"]["first_audio_s"] == 2.0


def test_the_plain_brain_stream_is_closed_after_the_first_audio():
    brain = Brain(DELTAS)
    rec = drive(brain, make_vt(lambda t: True), make_fs(None))
    assert rec["first_audio_s"] is not None
    assert rec["closed_at_return"] is True           # not left suspended for the garbage collector


def test_the_prefetched_brain_stream_is_closed_too():
    brain = Brain(DELTAS)
    rec = drive(brain, make_vt(lambda t: True), make_fs("One sec."))
    assert rec["ack_sound_s"] is not None
    assert rec["closed_at_return"] is True


def test_the_prefetched_stream_is_closed_even_when_the_ack_fails_before_it_is_read():
    brain = Brain(DELTAS)
    rec = drive(brain, make_vt(lambda t: False), make_fs("One sec."))
    assert rec["closed_at_return"] is True


def test_importing_the_probe_changes_no_environment_and_main_restores_it(monkeypatch):
    before = dict(os.environ)
    _load()
    assert dict(os.environ) == before
    with mfs._environment_restored():
        os.environ["ZOE_FIRST_SOUND_CLAUSE"] = "1"
        os.environ.pop("PATH", None)
    assert dict(os.environ) == before
