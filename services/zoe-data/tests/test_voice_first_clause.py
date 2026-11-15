"""W1 first-clause streaming, the voice-loop half (synthetic text only):

  * ``_extract_first_unit_ex`` names the rule that cut the first unit; the two-tuple ``_extract_first_unit`` (imported by
    scripts/perf) returns exactly the same unit and rest;
  * ``VOICE TIMING`` carries the fs_* fields (integers, enums, counts; never text) and is byte-unchanged without them;
  * ``tts_waterfall._synthesize_kokoro_sidecar``: the default request is byte-identical; ``style_len`` / ``speed`` are sent
    only when passed, and a passed speed replaces the per-chunk delivery mapper;
  * the stream loop under ZOE_KOKORO_STYLE_PIN: ONE (style_len, speed) for every reply chunk, decided once from the first
    unit; with the flag off every synth call is the plain one-argument call it was.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

import routers.voice_tts as vt
import tts_waterfall as tw
import voice_first_sound as fs

pytestmark = pytest.mark.ci_safe

_FLAGS = ("ZOE_FIRST_SOUND_CLAUSE", "ZOE_FIRST_SOUND_POLISH_EARLY", "ZOE_KOKORO_STYLE_PIN", "ZOE_KOKORO_STYLE_PIN_LEN",
          "ZOE_EXPRESSIVE_TTS", "ZOE_EXPRESSIVE_MIN_CHARS", "ZOE_EXPRESSIVE_GENTLE_FROM_H", "ZOE_EXPRESSIVE_GENTLE_TO_H")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in _FLAGS:
        monkeypatch.delenv(k, raising=False)


BUFFERS = [
    "Sure, it's sunny. ", "Sure, I can help with that, and I really would like to",
    "The weather this morning across the whole region is looking quite pleasant, and ",
    "word " * 30, "short", "Honestly, I think the best thing you can do is rest, drink",
]


@pytest.mark.parametrize("clause", [False, True])
def test_ex_names_the_rule_and_the_two_tuple_form_is_unchanged(monkeypatch, clause):
    if clause:
        monkeypatch.setenv("ZOE_FIRST_SOUND_CLAUSE", "1")
    kinds = []
    for buf in BUFFERS:
        unit, rest, kind = vt._extract_first_unit_ex(buf)
        assert vt._extract_first_unit(buf) == (unit, rest)
        assert (kind == "") == (unit is None)
        kinds.append(kind)
    assert kinds == ["sentence", "clause" if clause else "", "clause" if clause else "clause60", "softcap", "",
                     "clause" if clause else ""]


def test_style_pin_flags(monkeypatch):
    assert fs.style_pin_enabled() is False and fs.style_pin_len() == 120
    monkeypatch.setenv("ZOE_KOKORO_STYLE_PIN", "1")
    monkeypatch.setenv("ZOE_KOKORO_STYLE_PIN_LEN", "9999")
    assert fs.style_pin_enabled() is True and fs.style_pin_len() == 510
    monkeypatch.setenv("ZOE_KOKORO_STYLE_PIN_LEN", "0")
    assert fs.style_pin_len() == 1


# -- VOICE TIMING ------------------------------------------------------------------------------------------------------

_BASE = ("VOICE TIMING turn=t1 session=s1 path=stream pre_brain_ms=100 memory_packet_ms=0 "
         "(history=0 memory=0 domain=0) brain_ttft_ms=250 llm_first_token_ms=-1 packet=skipped")


def _timing_line(caplog, **kw):
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=vt.logger.name):
        vt._log_voice_timing(turn="t1", session_id="s1", path="stream", pre_brain_s=0.1, ctx_timings={},
                             brain_ttft_s=0.25, llm_first_token_s=None, packet_state="skipped", **kw)
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("VOICE TIMING")]
    return line


def test_voice_timing_is_unchanged_without_first_sound(caplog):
    assert _timing_line(caplog) == _BASE


def test_voice_timing_carries_integer_first_sound_fields(caplog):
    line = _timing_line(caplog, first_sound={
        "unit": "clause", "unit_chars": 41, "text_to_unit_s": 0.3, "unit_s": 0.9, "synth_s": 0.21, "pcm_s": 1.2,
        "lead": "none", "pin": True, "pin_len": 120, "units": 3})
    assert line == _BASE + (" fs_unit=clause fs_unit_chars=41 fs_text_to_unit_ms=300 fs_unit_ms=900 fs_synth_ms=210"
                            " fs_pcm_ms=1200 fs_lead=none fs_pin=1 fs_pin_len=120 fs_units=3")
    # never text: an unknown enum value is logged as none, absent times as -1
    line = _timing_line(caplog, first_sound={"unit": "Hello there friend", "lead": "Let me check", "units": 0})
    assert line.endswith(" fs_unit=none fs_unit_chars=0 fs_text_to_unit_ms=-1 fs_unit_ms=-1 fs_synth_ms=-1"
                         " fs_pcm_ms=-1 fs_lead=none fs_pin=0 fs_pin_len=0 fs_units=0")
    assert "Hello" not in line and "check" not in line


# -- the sidecar request ------------------------------------------------------------------------------------------------

class _Client:
    def __init__(self):
        self.posts = []
        self.is_closed = False

    async def post(self, url, json=None):
        self.posts.append(json)
        return type("R", (), {"status_code": 200, "content": b"RIFF"})()


def _post(monkeypatch, text, **kw):
    client = _Client()

    async def voice(v=None):
        return "af_sky"

    monkeypatch.setattr(tw, "_kokoro_http_client", lambda: client)
    monkeypatch.setattr(tw.voice_settings, "resolve_tts_voice", voice)
    assert asyncio.run(tw._synthesize_kokoro_sidecar(text, **kw)) == b"RIFF"
    return client.posts[0]


def test_default_request_is_byte_identical(monkeypatch):
    assert _post(monkeypatch, "Hello there.") == {"text": "Hello there.", "voice": "af_sky"}
    monkeypatch.setenv("ZOE_EXPRESSIVE_TTS", "1")
    monkeypatch.setenv("ZOE_EXPRESSIVE_GENTLE_FROM_H", "12")
    monkeypatch.setenv("ZOE_EXPRESSIVE_GENTLE_TO_H", "12")
    long_warm = "I'm sorry, that sounds hard, and you can take your time with it whenever you like."
    assert _post(monkeypatch, long_warm) == {"text": long_warm, "voice": "af_sky", "speed": 0.88}   # the mapper, as before


def test_style_len_and_speed_are_sent_only_when_passed(monkeypatch):
    monkeypatch.setenv("ZOE_EXPRESSIVE_TTS", "1")
    monkeypatch.setenv("ZOE_EXPRESSIVE_GENTLE_FROM_H", "12")
    monkeypatch.setenv("ZOE_EXPRESSIVE_GENTLE_TO_H", "12")
    long_warm = "I'm sorry, that sounds hard, and you can take your time with it whenever you like."
    # a passed speed is the turn's ONE speed: the per-chunk mapper (0.88 here) is not consulted
    assert _post(monkeypatch, long_warm, style_len=120, speed=1.0) == {
        "text": long_warm, "voice": "af_sky", "speed": 1.0, "style_len": 120}
    assert _post(monkeypatch, "Hello there.", style_len=7) == {"text": "Hello there.", "voice": "af_sky", "style_len": 7}


# -- the stream loop ----------------------------------------------------------------------------------------------------

SENTENCES = ["I'm sorry, that sounds hard. ", "Here is a second sentence that runs a fair bit longer than sixty characters. ",
             "Take it slowly."]


@pytest.mark.asyncio
@pytest.mark.parametrize("pin", [False, True])
async def test_stream_pins_one_style_and_speed_per_turn(monkeypatch, caplog, pin):
    from test_voice_stream_screen import _turn, _wire

    async def agent(*_a, **_k):
        for s in SENTENCES:
            yield s

    _wire(monkeypatch, agent)
    calls = []

    async def tts(text, **kw):
        calls.append((text, kw))
        return b"RIFF-wav"

    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", tts)
    monkeypatch.setenv("ZOE_EXPRESSIVE_TTS", "1")
    monkeypatch.setenv("ZOE_EXPRESSIVE_GENTLE_FROM_H", "12")   # no quiet hours: the run is the same at any clock time
    monkeypatch.setenv("ZOE_EXPRESSIVE_GENTLE_TO_H", "12")
    if pin:
        monkeypatch.setenv("ZOE_KOKORO_STYLE_PIN", "1")
        monkeypatch.setenv("ZOE_KOKORO_STYLE_PIN_LEN", "200")

    with caplog.at_level(logging.INFO, logger=vt.logger.name):
        resp = await _turn()
        chunks = [c async for c in resp.body_iterator]
    assert json.loads(chunks[-1].decode())["reply"] == "".join(SENTENCES).strip()
    assert [t for t, _kw in calls] == [s.strip() for s in SENTENCES]
    # pinned: the warm first unit ("I'm sorry", 28 chars: too short for the per-chunk mapper) decides 0.88 for the WHOLE reply
    want = {"style_len": 200, "speed": 0.88} if pin else {}
    assert [kw for _t, kw in calls] == [want] * 3
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("VOICE TIMING")]
    assert len(lines) == 1
    assert "fs_unit=sentence fs_unit_chars=28 " in lines[0]
    assert lines[0].endswith(f"fs_pin={int(pin)} fs_pin_len={200 if pin else 0} fs_units=3")
    assert "sorry" not in lines[0]
