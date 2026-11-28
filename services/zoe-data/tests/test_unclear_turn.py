"""The first words after a wake that nothing claimed (2026-11-28): the strip knows three more wake renderings, the device confirm
guard knows four more sound-alikes, and a short possible mishear carries a hearing note to the brain - never a feelings reply.

Measured on a month of wake turns at the panel (the numbers live outside the repo): about one wake turn in five was a short
command heard as unrelated words; cutting the wake phrase out of the audio repaired 2 of 21 and broke 3-4 of 28 working commands,
so nothing here edits audio. Every phrasing below is invented (public repo); ``ci_safe`` (pure modules, committed lexicon data,
the sidecar faked).

Negative controls (run by hand when written, each went red, then restored):
* ``_MISHEARD_WAKE`` without "azoy|azai", ``_GREETING_WITH_NAME`` without "howdy", ``_STRONG_WAKE_LINE_RE`` without the "a"
  greeting: the three matching ``test_three_more_wake_renderings_are_stripped`` cases fail (the wake words stay in the text).
* "tune" / "chill" / "chilling" / "white" removed from ``lexicons_data/en.json`` "device_confirm":
  ``test_four_more_sound_alikes_have_the_light_shape`` fails.
* ``unclear_turn.shape`` returning True unconditionally: ``test_clear_words_are_never_called_unclear`` and
  ``test_no_distress_phrase_is_ever_called_unclear`` fail.
* the ``unclear_turn.block(True)`` hook neutralised in ``zoe_flue_client`` (``hearing_block = ""``):
  ``test_a_flagged_turn_carries_exactly_one_note_after_the_words`` fails (the wire is the unflagged bytes).
* ``noted`` without the ``wake`` (or the ``enabled()``) clause: ``test_only_the_first_turn_after_a_wake_at_the_panel_is_noted`` fails.
* ``kwargs.pop("hearing_note", None)`` removed from ``brain_dispatch._drop_flue_only_kwargs``:
  ``test_another_brain_lane_never_sees_the_argument`` fails (the argument would reach a lane that does not take it).
* the hearing-note hook moved ABOVE the distress hand-off in ``run_flue_brain_streaming`` cannot happen silently:
  ``test_a_flagged_distress_phrase_still_gets_the_fixed_hand_off_and_the_brain_is_not_asked`` stubs the brain turn to raise, and with
  ``ZOE_DISTRESS_HANDOFF=off`` (the in-suite control) the stub does raise.
* the distress (or the medical) floor block of ``run_flue_brain_streaming`` moved below the brain call: run IN the suite on a copy of
  the module (``test_negative_control_a_floor_moved_below_the_brain_call_is_caught``), and by hand on the real file -
  ``test_each_floor_answers_through_the_real_wrapper_and_the_brain_is_never_started`` goes red for that floor's phrases.
* ``import unclear_turn`` put back inside the function in either file: ``test_the_module_is_imported_once_at_module_top_never_on_the_turn_path``
  fails; ``if kwargs.get("hearing_note") is True:`` replaced by an unconditional ``unclear_turn.block(...)`` call:
  ``test_an_unflagged_turn_never_calls_the_module_in_the_seam`` fails.
The flag-off cases are in-suite controls.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # pure modules + committed lexicon data; the sidecar is faked; no DB, no model, no network

import device_confirm as dc
import distress_handoff as dh
import unclear_turn as ut
import zoe_flue_client as zc
from stt_wake_strip import _strip_wake_word

DISTRESS = json.loads((Path(__file__).parent / "fixtures" / "distress_labelled.json").read_text(encoding="utf-8"))
HANDOFF_PHRASES = sorted({t for s in ("dev", "held_out_1", "blind_2", "ideation_3", "overdose_4") for tiers in DISTRESS[s].values()
                          for label, items in tiers.items() if label == "handoff" for t in items})
GENTLE_PHRASES = sorted({t for s in ("dev", "held_out_1", "blind_2") for tiers in DISTRESS[s].values()
                         for label, items in tiers.items() if label == "gentle" for t in items})


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in (ut.FLAG, "ZOE_DEVICE_CONFIRM_GUARD", dh.ENV, "ZOE_MANNER_BLOCK", "ZOE_PERSONA_LAYER", "ZOE_FLUE_WIRE",
              "ZOE_FLUE_STREAM_ENABLED", "ZOE_SEAM_RECALL_INJECT", "ZOE_VERIFY_ON_CHALLENGE", "ZOE_TRIVIA_HEDGE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.setenv("ZOE_IDENTITY_BLOCK", "0")
    dc.reset()
    yield
    dc.reset()


# ── 1. the wake strip: three more renderings of the wake phrase, selected in the text (never cut from the audio) ─────────────────

@pytest.mark.parametrize("lines,want", [
    (["A Zoe.", "Start the womble timer"], "Start the womble timer"),         # "Hey" heard as the article: whole line only
    (["Howdy, Zoe.", "Open the gribble list"], "Open the gribble list"),      # "Hey" heard as "howdy": a whole line ...
    (["Howdy, Zoe. Open the gribble list"], "Open the gribble list"),         # ... or inline, always with the name
    (["Azoy start the womble timer"], "Start the womble timer"),              # two more spellings of the fused sound
    (["Azai.", "Stop"], "Stop"),
])
def test_three_more_wake_renderings_are_stripped(lines, want):
    assert _strip_wake_word(lines) == want


@pytest.mark.parametrize("lines", [
    ["A zoe is a crab larva"],                 # inline "a zoe": real words follow the article
    ["A zoe trope again"],
    ["Howdy, Joe.", "Open the gribble list"],  # "howdy" beside a weak homophone is a greeting to a person
    ["Howdy partner", "how are things"],
    ["A Zoe."],                                # a clip that is ONLY the wake phrase is left for the caller
    ["A", "Zoe shaped cake please"],           # a bare article is not a greeting half
])
def test_words_that_only_look_like_the_wake_are_kept(lines):
    assert _strip_wake_word(lines).lower() == " ".join(lines).lower()


# ── 2. the device confirm guard: four more sound-alikes ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,polarity", [
    ("tune the lamp on", "on"),
    ("chill off the lot", "off"),
    ("chilling the lines", "on"),
    ("switch the white off", "off"),
])
def test_four_more_sound_alikes_have_the_light_shape(text, polarity):
    assert dc.shape(text) == polarity
    ask = dc.decide(text, panel_key="panel-demo", claimed=lambda _t, _c: False)
    assert ask is not None and ask.command == f"turn {polarity} the light"


@pytest.mark.parametrize("text", ["tune the radio", "chill the wine", "the white one", "tune in to the white noise"])
def test_the_new_words_alone_offer_nothing(text):
    assert dc.shape(text) is None


# ── 3. the shape of a possible mishear ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Gribble the womble.",
    "Down the marble",
    "Should not womble up",
    "Kettle spoon.",
    "Hey Zoe, plum on the wire",               # wake / politeness words do not count
    "Nobody marble.",
])
def test_a_short_unclaimed_line_has_the_shape(text):
    assert ut.shape(text) is True


@pytest.mark.parametrize("text", [
    "What's the gribble",                      # a question word first
    "Gribble the womble?",                     # a question mark
    "I had a long day",                        # someone talking about themselves makes sense: the brain answers as always
    "My dog is poorly",
    "You are very quiet",
    "Thank you",                               # clear as it stands
    "Good morning",
    "Never mind",
    "",                                        # nothing said
    "please put the gribble on the womble by the marble shelf",   # too long to be a slip of four words
])
def test_clear_words_are_never_called_unclear(text):
    assert ut.shape(text) is False


def test_no_distress_phrase_is_ever_called_unclear():
    """Every hand-off phrase and every gentle-tier phrase of the labelled distress set: none gets the hearing note."""
    assert len(HANDOFF_PHRASES) >= 100 and len(GENTLE_PHRASES) >= 40
    assert [t for t in HANDOFF_PHRASES + GENTLE_PHRASES if ut.shape(t)] == []


# ── 4. who gets the note ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_only_the_first_turn_after_a_wake_at_the_panel_is_noted(monkeypatch):
    garble = "Gribble the womble."
    assert ut.noted(garble, wake=True, panel=True) is True
    assert ut.noted(garble, wake=False, panel=True) is False        # a follow-up window: mid-conversation
    assert ut.noted(garble, wake=True, panel=False) is False        # a member's own session (typed / push-to-talk)
    assert ut.noted("I had a long day", wake=True, panel=True) is False
    monkeypatch.setenv(ut.FLAG, "0")                                # the kill switch
    assert ut.noted(garble, wake=True, panel=True) is False and ut.block(True) == ""


def test_the_block_needs_the_explicit_flag_of_that_call():
    assert ut.block(True) == ut.BLOCK
    for other in (None, False, "", 0, 1, "true", "Gribble the womble."):       # only the lane's own True counts
        assert ut.block(other) == ""


def test_another_brain_lane_never_sees_the_argument():
    import brain_dispatch

    kwargs = {"voice_mode": True, "hearing_note": True}
    brain_dispatch._drop_flue_only_kwargs(kwargs)
    assert kwargs == {"voice_mode": True}


def test_the_log_line_never_carries_the_words(caplog):
    with caplog.at_level("INFO", logger="unclear_turn"):
        assert ut.noted("Gribble the womble.", wake=True, panel=True)
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "UNCLEAR_TURN noted" in line and "ribble" not in line.lower() and "womble" not in line.lower()


def test_the_note_says_what_to_do_and_what_not_to_do():
    low = ut.BLOCK.lower()
    assert ut.BLOCK.startswith("[MEMORY CONTEXT") and ut.BLOCK.endswith("[END MEMORY CONTEXT]")   # the pair the sidecar already elides
    for must in ("misheard", "didn't catch", "say it again", "plainly make sense", "answer as you normally would",
                 "do not guess at a feeling", "on their mind"):
        assert must in low


# ── 5. the wire: one line after the user's words, only for the marked turn ───────────────────────────────────────────────────────

@pytest.fixture
def sent(monkeypatch):
    """Run one turn through the real seam and return the message the sidecar would receive."""
    def turn(message, uid="zed_uid", **kwargs):
        cap = []

        async def fake_wire2(session_id, payload, **kw):
            cap.append(json.loads(payload)["body"])
            yield "ok"

        monkeypatch.setattr(zc, "_run_turn_aggregated_wire2", fake_wire2)

        async def go():
            return [c async for c in zc.run_flue_brain_streaming(message, "s1", uid, voice_mode=True, **kwargs)]

        asyncio.run(go())
        assert len(cap) == 1
        return cap[0]
    return turn


def test_a_flagged_turn_carries_exactly_one_note_after_the_words(sent, monkeypatch):
    garble = "Gribble the womble."
    plain = sent(garble)
    assert "hearing note" not in plain
    flagged = sent(garble, hearing_note=True)
    assert flagged == f"{plain}\n{ut.BLOCK}" and flagged.count("hearing note") == 1
    monkeypatch.setenv(ut.FLAG, "0")                 # the kill switch restores today's bytes even for a flagged turn
    assert sent(garble, hearing_note=True) == plain


def test_an_unflagged_turn_is_byte_identical_whatever_came_before(sent):
    before = sent("Tell me about marble runs")
    sent("Gribble the womble.", hearing_note=True)
    assert sent("Tell me about marble runs") == before and "hearing note" not in before


# ── 6. the distress floor is untouched ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["I want to die", "quiero morirme"])
def test_a_flagged_distress_phrase_still_gets_the_fixed_hand_off_and_the_brain_is_not_asked(monkeypatch, text):
    """Even if a hand-off phrase were flagged by force, the hand-off answers first: the reply is the same bytes as with no flag,
    the brain turn (where the hearing note is composed) never runs, and the note is nowhere in what is spoken."""
    def boom(*_a, **_k):
        raise AssertionError("the brain must not run on a hand-off turn")

    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", boom)

    async def go(**extra):
        dh._NOTIFIED.clear()
        sink = {}
        return [d async for d in zc.run_flue_brain_streaming(text, "s1", "demo_bar_1", voice_mode=True, outcome_sink=sink, **extra)], sink

    plain, _ = asyncio.run(go())
    assert ut.noted(text, wake=True, panel=True) is False        # the shape refuses it ...
    forced, sink = asyncio.run(go(hearing_note=True))            # ... and a forced flag changes nothing
    assert forced == plain and len(forced) == 1 and dh.build_reply(dh.detect(text)) == forced[0]
    assert "hearing note" not in forced[0] and "catch" not in forced[0].lower() and sink
    monkeypatch.setenv(dh.ENV, "off")                            # control: without the floor the (stubbed) brain IS reached
    with pytest.raises(AssertionError):
        asyncio.run(go(hearing_note=True))


# ── 7. the floors answer first: behaviour through the real wrapper, the brain faked ─────────────────────────────────────────────

MEDICAL_PHRASES = ["wren just ate a handful of dishwasher tablet powder what do i do",       # the medical floor's own synthetic cases
                   "my chest hurts, it feels so tight"]
FLOOR_CASES = [("distress", t) for t in HANDOFF_PHRASES[:6] + ["I want to die", "quiero morirme"]] + [("medical", t) for t in MEDICAL_PHRASES]
DISTRESS_BLOCK = ("    import distress_handoff\n\n    _dh = await", "    # The medical safety floor (ZOE_MEDICAL_SAFETY")
MEDICAL_BLOCK = ("    # The medical safety floor (ZOE_MEDICAL_SAFETY", "    message = _resolve_clarification(message, user_id, session_id)\n")
BRAIN_CALL = "    turn = _run_flue_brain_streaming_turn(\n"
AFTER_BRAIN_CALL = "    import role_guess_guard\n\n    turn = role_guess_guard.filter_stream("


def _drive(module, monkeypatch, text, **extra):
    """One turn through ``module.run_flue_brain_streaming`` (the real wrapper) with the brain turn faked. Returns (what was spoken,
    how many times the brain turn was started)."""
    import brief_first_turn
    import restraint
    from proactive import selector

    calls = []

    def fake_turn(*a, **k):
        calls.append(a[0] if a else "")

        async def gen():
            yield "BRAIN-REPLY"
        return gen()

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(module, "_run_flue_brain_streaming_turn", fake_turn)
    monkeypatch.setattr(brief_first_turn, "prepare", nothing)
    monkeypatch.setattr(selector, "prepare", nothing)
    monkeypatch.setattr(restraint, "handle_turn", nothing)
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    dh._NOTIFIED.clear()

    async def go():
        return [d async for d in module.run_flue_brain_streaming(text, "s1", "demo_bar_1", voice_mode=True, outcome_sink={}, **extra)]

    return asyncio.run(go()), len(calls)


def _floor_reply(kind, text):
    import medical_safety as ms

    dh._NOTIFIED.clear()
    if kind == "distress":
        return dh.build_reply(dh.detect(text))
    return asyncio.run(ms.handle(text, "demo_bar_1", dry=True, voice=True))


@pytest.mark.parametrize("kind,text", FLOOR_CASES)
@pytest.mark.parametrize("extra", [{}, {"hearing_note": True}], ids=["plain", "hearing_note_forced"])
def test_each_floor_answers_through_the_real_wrapper_and_the_brain_is_never_started(monkeypatch, kind, text, extra):
    """BEHAVIOUR, not source: a distress phrase (the labelled set) and a medical one (the medical floor's own cases) go through the
    real ``run_flue_brain_streaming``. The floor's fixed reply is the ONLY thing spoken, the brain turn is never started, and a
    forced ``hearing_note`` changes neither - the note is composed inside the brain turn that never runs.
    Negative control (run by hand on the real ``zoe_flue_client.py``, then restored): the distress block moved below the brain call
    -> all 16 distress cases red; the medical block moved below it -> all 4 medical cases red. Also in-suite on a copy, below."""
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")       # the same household for the expected line and the driven turn
    want = _floor_reply(kind, text)
    assert want and "catch" not in want.lower()
    spoken, brain_starts = _drive(zc, monkeypatch, text, **extra)
    assert brain_starts == 0, f"the brain was started on a {kind} turn"
    assert spoken == [want] and "BRAIN-REPLY" not in spoken and "hearing note" not in spoken[0]


def test_control_an_ordinary_turn_does_start_the_faked_brain(monkeypatch):
    spoken, brain_starts = _drive(zc, monkeypatch, "Tell me about marble runs")
    assert brain_starts == 1 and spoken == ["BRAIN-REPLY"]


def _wrapper_with_floor_below_the_brain_call(block):
    """A copy of the seam module whose distress (or medical) floor is MOVED below the brain call. The real file is untouched."""
    import types

    src = Path(zc.__file__).read_text(encoding="utf-8")
    start, end = src.index(block[0]), src.index(block[1])
    assert 0 < start < end < src.index(BRAIN_CALL) and src.count(AFTER_BRAIN_CALL) == 1
    moved = src[start:end]
    mutated = (src[:start] + src[end:]).replace(AFTER_BRAIN_CALL, moved + AFTER_BRAIN_CALL)
    mod = types.ModuleType("zoe_flue_client_floor_moved")
    mod.__file__ = zc.__file__
    exec(compile(mutated, zc.__file__, "exec"), mod.__dict__)
    return mod


@pytest.mark.parametrize("kind,block,text", [("distress", DISTRESS_BLOCK, "I want to die"), ("medical", MEDICAL_BLOCK, MEDICAL_PHRASES[0])])
def test_negative_control_a_floor_moved_below_the_brain_call_is_caught(monkeypatch, kind, block, text):
    """The in-suite control for the test above: with the floor moved BELOW the brain call the brain turn IS started on the same
    phrase, so ``brain_starts == 0`` (the assertion above) fails for that mutant - and passes for the real module."""
    assert _drive(zc, monkeypatch, text)[1] == 0
    mutant = _wrapper_with_floor_below_the_brain_call(block)
    _spoken, brain_starts = _drive(mutant, monkeypatch, text)
    assert brain_starts == 1, f"moving the {kind} floor below the brain call was NOT detected"


# ── 8. the flag is read first; an unflagged turn never calls the module ──────────────────────────────────────────────────────────

def _function_level_imports(path, name):
    import ast

    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    hits = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Import) and any(a.name == name for a in node.names):
                    hits.append(fn.name)
                if isinstance(node, ast.ImportFrom) and node.module == name:
                    hits.append(fn.name)
    top = [n for n in tree.body if isinstance(n, ast.Import) and any(a.name == name for a in n.names)]
    return hits, len(top)


def test_the_module_is_imported_once_at_module_top_never_on_the_turn_path():
    here = Path(zc.__file__).parent
    for path in (here / "zoe_flue_client.py", here / "routers" / "voice_tts.py"):
        nested, top = _function_level_imports(path, "unclear_turn")
        assert nested == [] and top == 1, (path.name, nested, top)


def test_importing_the_module_is_light_and_reads_no_flag():
    """Import-time: no floor / affect module, no seam, no environment read (the flag is read per call, in ``enabled``)."""
    import subprocess
    import sys

    code = ("import os, sys\n"
            "class Env(dict):\n"
            "    def __getitem__(s, k): raise AssertionError('env read at import: ' + k)\n"
            "    def get(s, k, d=None): raise AssertionError('env read at import: ' + k)\n"
            "before = set(sys.modules)\n"
            "real = os.environ; os.environ = Env()\n"
            "import unclear_turn\n"
            "os.environ = real\n"
            "new = set(sys.modules) - before\n"
            "bad = {'distress_handoff', 'emotional_turn_guard', 'medical_safety', 'zoe_flue_client', 'typed_env', 'flag_registry'} & new\n"
            "assert not bad, bad\n"
            "print('ok')\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(Path(zc.__file__).parent), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr[-400:]


def test_flag_off_decides_without_touching_the_words(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the shape must not run with the flag off")

    monkeypatch.setattr(ut, "shape", boom)
    monkeypatch.setenv(ut.FLAG, "0")
    assert ut.noted("Gribble the womble.", wake=True, panel=True) is False
    monkeypatch.delenv(ut.FLAG)                       # control: flag on, the shape IS consulted (and the stub raises inside noted -> False + a warning)
    called = []
    monkeypatch.setattr(ut, "shape", lambda t: called.append(t) or True)
    assert ut.noted("Gribble the womble.", wake=True, panel=True) is True and called == ["Gribble the womble."]


def test_an_unflagged_turn_never_calls_the_module_in_the_seam(sent, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("unclear_turn must not be called on a turn the lane did not flag")

    for name in ("block", "enabled", "shape", "noted"):
        monkeypatch.setattr(ut, name, boom)
    assert "hearing note" not in sent("Gribble the womble.")          # no argument = the module is never entered
    assert "hearing note" not in sent("Gribble the womble.", hearing_note=False)


def test_the_note_has_one_construction_site_inside_the_brain_turn():
    """Structure pin for the NOTE only; the floors' order is pinned by behaviour in section 7, not by source. Control by hand: a
    second ``unclear_turn.block(True)`` pasted into the wrapper -> red."""
    src = Path(zc.__file__).read_text(encoding="utf-8")
    assert src.count("unclear_turn.block(True)") == 1 and src.count('if kwargs.get("hearing_note") is True:') == 1
    wrapper = src[src.index("async def run_flue_brain_streaming("):src.index("async def _run_flue_brain_streaming_turn(")]
    assert "unclear_turn" not in wrapper
    voice = (Path(zc.__file__).parent / "routers" / "voice_tts.py").read_text(encoding="utf-8")
    assert voice.count("_hearing_note_kwargs(text, payload, caller)") == 2    # the streaming and the one-shot brain dispatch
