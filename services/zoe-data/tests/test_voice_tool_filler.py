"""Tool-turn filler: speak a short generic acknowledgement on the brain's
tool_call so voice first-audio comes fast, without ever speaking the raw
``__TOOL__`` / ``__THINKING__`` sentinels or claiming a result.

Pure unit tests over the helpers — no live brain, no TTS, no FastAPI app run.

    python -m pytest services/zoe-data/tests/test_voice_tool_filler.py -v
"""
from __future__ import annotations

import json

import routers.voice_tts as v


# ── enable flag ─────────────────────────────────────────────────────────────

def test_filler_flag_default_on_and_togglable(monkeypatch):
    monkeypatch.delenv("ZOE_VOICE_TOOL_FILLER", raising=False)
    assert v._voice_tool_filler_enabled() is True   # default on
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("ZOE_VOICE_TOOL_FILLER", off)
        assert v._voice_tool_filler_enabled() is False, off
    for on in ("1", "true", "YES"):
        monkeypatch.setenv("ZOE_VOICE_TOOL_FILLER", on)
        assert v._voice_tool_filler_enabled() is True, on


# ── tool name -> filler line ────────────────────────────────────────────────

def test_known_tools_get_specific_fillers():
    assert v._voice_tool_filler("calendar") == "Let me check your calendar."
    assert v._voice_tool_filler("reminders") == "Let me check your reminders."
    assert v._voice_tool_filler("weather") == "Let me check the weather."


def test_prefix_match_falls_to_family_filler():
    # e.g. a 'calendar_show' tool still maps to the calendar line.
    assert v._voice_tool_filler("calendar_show") == "Let me check your calendar."


def test_unknown_tool_uses_generic_default():
    assert v._voice_tool_filler("frobnicate") == v._VOICE_TOOL_FILLER_DEFAULT


def test_filler_never_claims_a_result():
    # No filler line may assert an outcome — they're spoken BEFORE the tool returns.
    forbidden = ("you have", "you've got", "here are", "i found", "nothing")
    for line in (*v._VOICE_TOOL_FILLERS.values(), v._VOICE_TOOL_FILLER_DEFAULT):
        low = line.lower()
        assert not any(f in low for f in forbidden), line


# ── sentinel parsing: only phase=start triggers a filler ────────────────────

def _tool(phase, name="calendar", **extra):
    return "__TOOL__:" + json.dumps({"phase": phase, "id": "x", "name": name, **extra})


def test_only_start_phase_yields_a_tool_name():
    assert v._voice_tool_name_from_sentinel(_tool("start")) == "calendar"
    # args / result phases must NOT re-trigger a filler.
    assert v._voice_tool_name_from_sentinel(_tool("args", args={"a": 1})) is None
    assert v._voice_tool_name_from_sentinel(_tool("result")) is None


def test_thinking_and_text_are_not_tool_starts():
    assert v._voice_tool_name_from_sentinel("__THINKING__:looking at calendar") is None
    assert v._voice_tool_name_from_sentinel("You've got nothing on this week.") is None


def test_malformed_sentinel_returns_none_not_raises():
    assert v._voice_tool_name_from_sentinel("__TOOL__:{not json") is None
    assert v._voice_tool_name_from_sentinel("__TOOL__:[]") is None
    # start with no name -> no filler (we don't speak for an anonymous tool).
    assert v._voice_tool_name_from_sentinel('__TOOL__:{"phase":"start","id":"x"}') is None


def test_sentinel_prefixes_cover_both_kinds():
    assert "__TOOL__:".startswith(v._VOICE_TOOL_SENTINEL_PREFIXES)
    assert "__THINKING__:".startswith(v._VOICE_TOOL_SENTINEL_PREFIXES)
    # Real spoken text must not be mistaken for a sentinel.
    assert not "Let me check.".startswith(v._VOICE_TOOL_SENTINEL_PREFIXES)


# ── gating simulation: mirror the streaming loop's decision exactly ─────────
#
# The streaming loop is a deep closure; this replays its filler-gating logic over
# a delta sequence so the contract is pinned without running the FastAPI route:
#   * speak filler on the FIRST tool_start when no audio has been emitted yet;
#   * never speak a sentinel as text;
#   * at most one filler per turn;
#   * suppress the filler when the brain led with its own spoken text.

def _simulate(deltas, *, filler_enabled=True):
    """Return (spoken_units, recorded_units) for a delta sequence."""
    spoken, recorded = [], []
    first_audio_set = False
    filler_emitted = False

    def emit(unit, *, record=True):
        nonlocal first_audio_set
        spoken.append(unit)
        if record:
            recorded.append(unit)
        first_audio_set = True

    for delta in deltas:
        if not delta:
            continue
        if delta.startswith(v._VOICE_TOOL_SENTINEL_PREFIXES):
            if not filler_emitted and not first_audio_set and filler_enabled:
                name = v._voice_tool_name_from_sentinel(delta)
                if name is not None:
                    filler_emitted = True
                    emit(v._voice_tool_filler(name), record=False)
            continue  # sentinels are NEVER spoken as text
        # plain text delta (sentence-level for the sim)
        emit(delta)
    return spoken, recorded


def test_filler_fires_once_before_silent_tool_turn():
    # Brain goes straight to the tool (no lead-in), then answers.
    deltas = [
        _tool("start"),
        _tool("args", args={"q": "this week"}),
        _tool("result"),
        "You've got nothing on this week.",
    ]
    spoken, recorded = _simulate(deltas)
    assert spoken == ["Let me check your calendar.", "You've got nothing on this week."]
    # The filler is spoken but NOT part of the recorded/persisted answer.
    assert recorded == ["You've got nothing on this week."]


def test_filler_suppressed_when_brain_leads_with_text():
    # Brain says its own lead-in BEFORE the tool — no extra filler (no double).
    deltas = [
        "Let's see what's coming up for you this week.",
        _tool("start"),
        _tool("result"),
        "You've got nothing on this week.",
    ]
    spoken, recorded = _simulate(deltas)
    assert spoken == [
        "Let's see what's coming up for you this week.",
        "You've got nothing on this week.",
    ]
    assert recorded == spoken  # both are real answer text, both recorded


def test_no_filler_on_plain_answer_turn():
    # No tool at all (instant/plain answer) -> no filler, nothing dropped.
    deltas = ["Geraldton is a coastal city in Western Australia."]
    spoken, recorded = _simulate(deltas)
    assert spoken == deltas and recorded == deltas


def test_filler_only_once_across_multiple_tool_calls():
    deltas = [_tool("start", name="calendar"), _tool("start", name="weather"), "Done."]
    spoken, _ = _simulate(deltas)
    assert spoken == ["Let me check your calendar.", "Done."]  # only one filler


def test_filler_disabled_drops_sentinels_but_stays_silent():
    deltas = [_tool("start"), _tool("result"), "Nothing this week."]
    spoken, recorded = _simulate(deltas, filler_enabled=False)
    assert spoken == ["Nothing this week."]  # sentinels still stripped, no filler
    assert recorded == ["Nothing this week."]
