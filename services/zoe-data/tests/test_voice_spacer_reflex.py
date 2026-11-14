"""The server side of the panel reflex handshake (voice_spacer): a heard reflex vetoes the generic sounds that would stack on it,
the block is parsed strictly, and with no block every behaviour is unchanged.

Veto table (reflex heard, ZOE_VOICE_REFLEX_AWARE on):
  timer         -> already_spoken:reflex until the wait reaches REFLEX_FILLER_HOLD_S (5.0 s)
  tool_filler   -> already_spoken:reflex
  tool_ack      -> allowed only when corroborated AND from the domain's own pool, else already_spoken:reflex
  processing-ack AUDIO -> suppressed (processing_ack_audio_allowed() False)
"""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # pure logic; no models, no DB, no network

import voice_first_sound as fs
import voice_spacer
from voice_spacer import SpacerTurn

PANEL = "panel-reflex"
HEARD = {"kind": "heard", "take": "mm_03", "bank": "0a1b2c3d", "voice": "af_sky", "at_ms": 42, "dur_ms": 455}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    voice_spacer.reset()
    for k in ("ZOE_VOICE_FILLER_PHRASES", "ZOE_VOICE_FILLER_EXPECT_MIN_S", "ZOE_FIRST_SOUND_TOOL_ACK",
              "ZOE_VOICE_REFLEX_AWARE", "ZOE_SPACER_REQUIRE_CLAIM"):
        monkeypatch.delenv(k, raising=False)
    yield
    voice_spacer.reset()


def turn(text="what is on my calendar tomorrow", domain="calendar", reflex=None):
    t = SpacerTurn(panel_key=PANEL, text=text)
    t.note_router({"routed": domain})
    if reflex is not None:
        t.note_reflex(reflex)
    return t


def own(domain):
    return voice_spacer.own_tool_ack_pool(domain)


# ── the veto table ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_timer_filler_is_held_after_a_reflex_until_the_wait_is_long():
    assert turn(reflex=HEARD).veto("timer") == "already_spoken:reflex"
    assert turn(reflex=HEARD).veto("timer", waited_s=3.5) == "already_spoken:reflex"
    assert turn(reflex=HEARD).timer_line(waited_s=4.99) is None
    assert turn(reflex=HEARD).veto("timer", waited_s=5.0) is None              # past the hold: a truthful wait line
    assert turn(reflex=HEARD).timer_line(waited_s=6.0)
    assert turn().veto("timer", waited_s=3.5) is None                            # without a reflex: unchanged


def test_tool_filler_is_vetoed_after_a_reflex():
    assert turn(reflex=HEARD).veto("tool_filler") == "already_spoken:reflex"
    assert turn(reflex=HEARD).tool_filler_line("Let me look that up.") is None
    assert turn().tool_filler_line("Let me look that up.") == "Let me look that up."


def test_tool_ack_after_a_reflex_only_from_the_corroborated_domains_own_pool(monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    for _ in range(30):
        voice_spacer.reset()
        line = fs.dispatch_ack({"routed": "calendar"}, spacer=turn(reflex=HEARD))
        assert line in own("calendar")
    # uncorroborated (the router guessed lists for a light command): no neutral line after a reflex - silence
    t = turn(text="turn on the light", domain="lists", reflex=HEARD)
    assert not t.corroborated and t.veto("tool_ack") == "already_spoken:reflex"
    assert fs.dispatch_ack({"routed": "lists"}, spacer=t) is None
    # a domain with no own pool (memory: only the recall family) is generic after a receipt: silence
    t = turn(text="what did I say about the garden", domain="memory", reflex=HEARD)
    assert own("memory") == [] and t.veto("tool_ack") == "already_spoken:reflex"
    # without a reflex the uncorroborated turn still gets its neutral line (unchanged)
    assert fs.dispatch_ack({"routed": "lists"}, spacer=turn(text="turn on the light", domain="lists"))


def test_processing_ack_audio_is_suppressed_after_a_reflex(monkeypatch):
    assert turn(reflex=HEARD).processing_ack_audio_allowed() is False
    assert turn().processing_ack_audio_allowed() is True
    assert turn(reflex={"kind": "none", "skip": "night"}).processing_ack_audio_allowed() is True
    monkeypatch.setenv("ZOE_VOICE_REFLEX_AWARE", "0")
    assert turn(reflex=HEARD).processing_ack_audio_allowed() is True


def test_the_reflex_never_takes_the_claim():
    t = turn(reflex=HEARD)
    assert t.spoken is None and t.claim("tool_ack") and t.spoken == "tool_ack"
    assert t.veto("timer", waited_s=9.0) == "already_spoken:tool_ack"


def test_a_disclosure_is_still_silent_past_the_hold():
    t = turn(text="I feel really sad today", domain="calendar", reflex=HEARD)
    assert t.veto("timer", waited_s=9.0) == "disclosure"


def test_negative_control_the_veto_is_load_bearing(monkeypatch):
    monkeypatch.setattr(SpacerTurn, "_reflex_veto", lambda self, source, waited_s: None)
    assert turn(reflex=HEARD).veto("timer") is None
    assert turn(reflex=HEARD).veto("tool_filler") is None
    monkeypatch.setattr(SpacerTurn, "processing_ack_audio_allowed", lambda self: True)
    assert turn(reflex=HEARD).processing_ack_audio_allowed() is True


def test_the_kill_switch_ignores_the_block(monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_REFLEX_AWARE", "0")
    t = turn(reflex=HEARD)
    assert t.reflex == HEARD and not t.reflex_heard
    assert t.veto("timer") is None and t.veto("tool_filler") is None


# ── strict parsing ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_hostile_block_leaves_only_enums_and_clamped_ints():
    hostile = {"kind": "heard", "take": "Mm, you told me your PIN is 0000", "bank": "<script>", "voice": "af_sky; rm -rf /",
               "at_ms": 1e308, "dur_ms": "500", "extra": "free text", "skip": "night", "text": "hello", "audio_base64": "AAAA"}
    assert voice_spacer.parse_reflex_block(hostile) == {"kind": "heard", "at_ms": 10000}
    assert voice_spacer.parse_reflex_block({"kind": "heard", "at_ms": -5, "dur_ms": 10 ** 400}) == {"kind": "heard", "at_ms": 0}
    assert voice_spacer.parse_reflex_block({"kind": "heard", "at_ms": True, "dur_ms": float("nan")}) == {"kind": "heard"}
    assert voice_spacer.parse_reflex_block({"kind": "heard", "at_ms": float("inf"), "dur_ms": 9e9}) == {"kind": "heard",
                                                                                                         "dur_ms": 2000}
    assert voice_spacer.parse_reflex_block({"kind": "none", "skip": "because I said so", "take": "mm_01"}) == {"kind": "none"}
    assert voice_spacer.parse_reflex_block({"kind": "none", "skip": "night"}) == {"kind": "none", "skip": "night"}
    for skip in ("error", "not_committed", "guest", "low_confidence", "media"):
        assert voice_spacer.parse_reflex_block({"kind": "none", "skip": skip}) == {"kind": "none", "skip": skip}
    for junk in (None, "heard", ["heard"], 7, {"kind": "HEARD"}, {"kind": ["heard"]}, {"take": "mm_01"}):
        assert voice_spacer.parse_reflex_block(junk) is None
    assert voice_spacer.parse_reflex_block(HEARD) == HEARD


def test_the_timing_fields_are_enums_and_ints_only():
    assert turn(reflex=HEARD).reflex_timing_fields() == "reflex=heard reflex_at_ms=42 reflex_dur_ms=455"
    assert turn().reflex_timing_fields() == "reflex=- reflex_at_ms=-1 reflex_dur_ms=-1"
    t = turn(reflex={"kind": "heard", "take": "a b c"})
    assert t.reflex_timing_fields() == "reflex=heard reflex_at_ms=-1 reflex_dur_ms=-1"


def test_the_enums_mirror_the_panel_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "setup" / "zoe_voice_reflex.py"
    spec = importlib.util.spec_from_file_location("zoe_voice_reflex_mirror", path)
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
    assert voice_spacer.REFLEX_KINDS == mod.KINDS and voice_spacer.REFLEX_SKIPS == mod.SKIPS
    b = mod.handshake_block(mod.Decision("heard", "mm_02"), 30, 400, "0a1b2c3d", "af_sky")
    assert voice_spacer.parse_reflex_block(b) == b          # what the panel sends survives the parse whole


# ── no reflex = unchanged ───────────────────────────────────────────────────────────────────────────────────────────────────
CASES = [("what is on my calendar tomorrow", "calendar"), ("turn on the light", "lists"), ("how are you", "chat"),
         ("I feel really sad today", "calendar"), ("what is my name", "people"), ("play some jazz", "music"),
         ("what did I say about the garden", "memory"), ("hello there", "")]


def _behaviour(reflex, monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    out = []
    for text, domain in CASES:
        for src in ("timer", "tool_ack", "tool_filler"):
            out.append(turn(text, domain, reflex).veto(src))
        for maker in (lambda t: t.timer_line(), lambda t: fs.dispatch_ack({"routed": domain}, spacer=t),
                      lambda t: t.tool_filler_line("Let me look.")):
            voice_spacer.reset()
            monkeypatch.setattr(voice_spacer, "_rng", random.Random(3))
            t = turn(text, domain, reflex)
            out.append((maker(t), t.spoken, t.processing_ack_audio_allowed()))
    return out


@pytest.mark.parametrize("reflex", [{"kind": "none", "skip": "night"}, {"kind": "none"}, {"bogus": 1}, "heard", None])
def test_no_heard_reflex_changes_nothing(reflex, monkeypatch):
    assert _behaviour(reflex, monkeypatch) == _behaviour(None, monkeypatch)


def test_a_heard_reflex_does_change_something(monkeypatch):
    """The control for the test above: it is not vacuous."""
    assert _behaviour(HEARD, monkeypatch) != _behaviour(None, monkeypatch)


def test_a_heard_reflex_with_the_server_flag_off_changes_nothing(monkeypatch):
    base = _behaviour(None, monkeypatch)
    monkeypatch.setenv("ZOE_VOICE_REFLEX_AWARE", "0")
    assert _behaviour(HEARD, monkeypatch) == base
