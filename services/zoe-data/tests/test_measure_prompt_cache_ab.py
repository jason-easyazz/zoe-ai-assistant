"""scripts/perf/measure_prompt_cache_ab.py (flag off vs on, consecutive turns): the pure parts and the dry lab envelope.

Synthetic data only. No model, no network, no database: the lab builds prompts through the REAL seam via the synthetic rig
(``flue_turn_rig``) and the live-slot mode is only checked for its REFUSALS.
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[3]
PERF = REPO / "scripts" / "perf"


def _load():
    spec = importlib.util.spec_from_file_location("measure_prompt_cache_ab", PERF / "measure_prompt_cache_ab.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["measure_prompt_cache_ab"] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()
from test_brain_cache_stability import strip_context_blocks  # noqa: E402  (the pinned port of the sidecar's elision)


# ── tokenizer, prefix, stats ─────────────────────────────────────────────────────────────────────────────────────────
def test_lcp():
    assert m.lcp([1, 2, 3], [1, 2, 9]) == 2
    assert m.lcp([], [1]) == 0
    assert m.lcp([1, 2], [1, 2, 3]) == 2


def test_approx_tokenizer_is_deterministic_and_prefix_consistent():
    t = m.ApproxTokenizer()
    a, b = "Tell me something about otters and rivers", "\nand more words here"
    assert t.encode(a + b) == t.encode(a + b)
    assert t.encode(a + b)[: len(t.encode(a)) - 1] == t.encode(a)[:-1]
    assert len(t.encode("supercalifragilistic")) >= 4          # a long word is several tokens
    assert t.encode("") == []


def _stored_turn(blocks_first: bool, words: str, block: str) -> str:
    return f"{block}\n{words}" if blocks_first else f"{words}\n{block}"


def test_flag_on_keeps_the_words_cached_and_flag_off_does_not():
    """The mechanism in one hand-built pair: turn 1 carries a block; turn 2 sees it elided. Block first: the divergence is at
    the start of turn 1's message. Block after the words: it is after them. The saving is the words, no more."""
    tok = m.ApproxTokenizer()
    system = "<bos>" + " ".join(["soul"] * 400) + "\n"
    block = "[MEMORY CONTEXT — x]\n" + "\n".join(f"- fact {i} alpha river stone" for i in range(80)) + "\n[END MEMORY CONTEXT]"
    words1, words2, reply = "What is my favourite tea and why do I like it so much", "And the weather", "Oolong, you said."
    res = {}
    for on in (False, True):
        u1, u2 = _stored_turn(not on, words1, block), words2
        p1 = m.render_prompt(system, [], u1)
        p2 = m.render_prompt(system, [(strip_context_blocks(u1), reply)], u2)
        res[on] = m.turn_stats(tok, m.cached_after(p1, reply), p2)
    assert res[True]["cache_n"] > res[False]["cache_n"]
    saved = res[False]["uncached"] - res[True]["uncached"]
    assert 5 <= saved <= 2 * len(tok.encode(words1)) + 12          # about the words (+ template), NOT the block
    assert abs(res[True]["total"] - res[False]["total"]) <= 2        # the same bytes, only reordered


def test_turn_stats_always_re_evaluates_the_last_token():
    tok = m.ApproxTokenizer()
    s = m.turn_stats(tok, "same text here", "same text here")
    assert s["uncached"] >= 1


def test_arm_order_is_abab():
    assert [m.arm_order(i) for i in range(4)] == [("off", "on"), ("on", "off"), ("off", "on"), ("on", "off")]


def _rows(offs, ons):
    rows = []
    for i, (a, b) in enumerate(zip(offs, ons)):
        rows += [{"pair": i, "arm": "off", "turns": [{"uncached": 9}, {"uncached": a}]},
                 {"pair": i, "arm": "on", "turns": [{"uncached": 9}, {"uncached": b}]}]
    return rows


def test_summarise_shape_medians_shares_and_pairing():
    s = m.summarise_shape(_rows([100, 900, 1000, 200], [90, 400, 950, 200]), 1)
    assert s["pairs"] == 4 and s["off_median"] == 550 and s["on_median"] == 300
    assert s["over_off"] == 0.5 and s["over_on"] == 0.25
    assert s["saved_median_tokens"] == 30 and s["on_better_pairs"] == 3 and s["on_worse_pairs"] == 0
    assert s["saved_median_s"] == round(30 * m.MS_PER_TOKEN / 1000, 3)


def test_verdict_thresholds_and_offline_label():
    small = {"a": {"saved_median_tokens": 20.0}, "b": {"saved_median_tokens": -5.0}}
    v = m.verdict(small, live=False)
    assert v.startswith("NOT improved") and "WORSE in b" in v and "NOT assessed" in v
    big = {"a": {"saved_median_tokens": 310.0}, "b": {"saved_median_tokens": 0.0}}
    assert m.verdict(big, live=True, shape_changes=0).startswith("IMPROVED in a")
    assert "reply-shape changes: 2" in m.verdict(big, live=True, shape_changes=2)


def test_reply_shape_changed():
    a = {"len": 100, "sha": "x"}
    assert not m.reply_shape_changed(a, {"len": 100, "sha": "y"})          # a different hash alone is not a shape change
    assert not m.reply_shape_changed(a, {"len": 80, "sha": "y"})
    assert m.reply_shape_changed(a, {"len": 40, "sha": "y"})
    assert m.reply_shape_changed(a, {"len": 0, "sha": "y"})
    assert not m.reply_shape_changed(None, a)


# ── the live-slot guard ──────────────────────────────────────────────────────────────────────────────────────────────
_OK = dict(is_live_brain=True, own_slot=True, perf_env=True, lock_held=True, probe_running=False, slot_busy=False,
           household_idle_s=3600.0)


def test_live_slot_goes_only_when_every_condition_holds():
    assert m.live_slot_refusals(**_OK) == []
    for k, bad in (("own_slot", False), ("perf_env", False), ("lock_held", False), ("probe_running", True), ("slot_busy", True),
                   ("slot_busy", None), ("household_idle_s", 30.0), ("household_idle_s", None)):
        assert m.live_slot_refusals(**{**_OK, k: bad}), (k, bad)          # each one alone refuses (negative control)


def test_a_scratch_server_needs_only_the_lock():
    assert m.live_slot_refusals(**{**_OK, "is_live_brain": False, "own_slot": False, "perf_env": False, "slot_busy": None,
                                   "household_idle_s": None}) == []
    assert m.live_slot_refusals(**{**_OK, "is_live_brain": False, "lock_held": False})


def test_main_refuses_live_slot_without_the_ownership_flag(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(m, "complete", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(m, "take_harness_lock", lambda: object())
    monkeypatch.setattr(m, "collect_guard_inputs", lambda base: {"probe_running": False, "slot_busy": False, "household_idle_s": 9999.0})
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(sys, "argv", ["x", "--live-slot"])
    assert m.main() == 2
    assert "--i-own-the-slot" in capsys.readouterr().err
    assert sent == []                                                       # nothing was sent


# ── the lab: real prompt building, flag in this process only, nothing written ────────────────────────────────────────
@pytest.fixture
def lab(monkeypatch):
    monkeypatch.delenv(m.FLAG, raising=False)
    return m.Lab(pin=False)


async def test_lab_wires_use_the_flag_only_inside_the_call(lab, monkeypatch):
    spec = m.shape_recall_then_plain(random.Random(1))
    off = await lab.wires(spec, False, "voice-panel-ab-t1")
    assert m.FLAG not in os.environ                                         # restored: the flag never outlives the call
    on = await lab.wires(spec, True, "voice-panel-ab-t2")
    assert m.FLAG not in os.environ
    assert off[0] != on[0] and off[0].count("[MEMORY CONTEXT") == on[0].count("[MEMORY CONTEXT")
    assert on[0].startswith(spec[0].words) and not off[0].startswith(spec[0].words)     # words first only with the flag on
    assert all("zoe-replay" not in w for w in on + off)                    # the dry envelope's own line is removed again
    again = await lab.wires(spec, False, "voice-panel-ab-t3")
    assert again == off                                                     # flag off = the same bytes every time


async def test_offline_conversation_shows_the_saving_is_about_the_words_not_the_block(lab):
    system = m.system_prefix()
    tok = m.ApproxTokenizer()
    got = {}
    for name in ("recall_then_plain", "plain_control", "now_card_change"):
        spec = m.SHAPES[name](random.Random(f"{name}:0"))
        r = {}
        for arm in ("off", "on"):
            r[arm] = await m.run_conversation(lab, spec, arm == "on", f"voice-panel-ab-{name}-{arm}", system, 1, tok=tok)
        got[name] = (r["off"]["turns"][1]["uncached"], r["on"]["turns"][1]["uncached"])
        assert r["off"]["turns"][1]["reply"] == r["on"]["turns"][1]["reply"]   # offline replies are scripted identically
    assert got["recall_then_plain"][1] < got["recall_then_plain"][0]       # the flag does shorten the re-prefill ...
    assert got["recall_then_plain"][0] - got["recall_then_plain"][1] < 120  # ... by about the words, nowhere near a recall block
    assert got["now_card_change"][0] >= got["now_card_change"][1]           # never worse offline
