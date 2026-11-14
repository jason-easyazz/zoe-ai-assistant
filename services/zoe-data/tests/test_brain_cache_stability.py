"""Brain prompt-cache stability (speed audit 2026-11-14 B3 / B6): flag-dark, byte-identical when off.

- With every flag off, the REAL turn assembly sends exactly the bytes it sent before these flags existed, over a synthetic
  corpus of turns (``fixtures/flue_prompt_golden.json``, generated from the code before this change).
- ZOE_BRAIN_VOLATILE_AFTER_USER=1: across two consecutive turns of one session, the prompt the sidecar renders for the
  second turn (the first turn's blocks ELIDED, as ZOE_BRAIN_ELIDE_STALE_BLOCKS does live) matches the first turn's cached
  prompt byte for byte up to the END of the first turn's words - hash-equal - for every scenario that used to diverge
  before the words. Flag off, the same comparison diverges before the words (the negative control: the miss was real).
- ZOE_CONTINUITY_CEILING_S bounds the continuity packet wait; unset = the 3.0 s of before.

The sidecar's elision is a Python port of labs/flue-zoe-brain-2x/src/context-blocks.ts ``stripContextBlocks``.
Synthetic data only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

import pytest

import flue_prompt_corpus as corpus
import flue_turn_rig as rig
import zoe_flue_client as zfc

pytestmark = pytest.mark.ci_safe

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "flue_prompt_golden.json").read_text())
FLAGS = ("ZOE_BRAIN_VOLATILE_AFTER_USER", "ZOE_CONTINUITY_CEILING_S", "ZOE_WRITE_CLAIM_CORRECTION")


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for k in FLAGS:
        monkeypatch.delenv(k, raising=False)


# ── flags off: byte-identical ────────────────────────────────────────────────────────────────────────────────────────
def test_the_corpus_and_the_golden_cover_the_same_turns():
    assert sorted(GOLDEN) == sorted(t[0] for t in corpus.TURNS)


@pytest.mark.parametrize("turn", corpus.TURNS, ids=[t[0] for t in corpus.TURNS])
async def test_flags_off_bytes_are_exactly_the_golden(monkeypatch, turn):
    assert await corpus.render(monkeypatch, turn) == GOLDEN[turn[0]]


@pytest.mark.parametrize("turn", corpus.TURNS, ids=[t[0] for t in corpus.TURNS])
async def test_the_continuity_ceiling_alone_changes_no_byte(monkeypatch, turn):
    monkeypatch.setenv("ZOE_CONTINUITY_CEILING_S", "0.4")
    assert await corpus.render(monkeypatch, turn) == GOLDEN[turn[0]]


async def test_negative_control_volatile_after_user_does_change_the_block_turns(monkeypatch):
    """The golden comparison can fail: with B3 on, every turn with a block ahead of the words differs, and a turn with
    no such block does not."""
    monkeypatch.setenv("ZOE_BRAIN_VOLATILE_AFTER_USER", "1")
    changed = {t[0] for t in corpus.TURNS if await corpus.render(monkeypatch, t) != GOLDEN[t[0]]}
    assert {"plain", "recall", "recall_marker", "offer", "bridge_notes_fastpath", "hop_preamble"} <= changed
    assert "no_blocks" not in changed


# ── flag on: what moved, and only that ───────────────────────────────────────────────────────────────────────────────
_ENV_RE = re.compile(r"^ zoe-[a-z][a-z-]*:[^\n]*\n")


def _strip_envelopes(msg: str) -> str:
    while True:
        m = _ENV_RE.match(msg)
        if not m:
            return msg
        msg = msg[m.end():]


def _expected_on(off_wire: str, words: str, risk: str = "", every_turn: str = "") -> str:
    """What the flag-on message must be, derived from the flag-off bytes: the words, then the risk / every-turn blocks
    that followed them, then the run that preceded them (verbatim, so its order and multiplicity are fixed), then the
    rest of what followed. Exact string, no sorting."""
    off = _strip_envelopes(off_wire)
    i = off.index(words)
    pre, post = off[:i].rstrip("\n"), off[i + len(words):]
    lead = words
    for b in (risk, every_turn):
        if b and f"\n{b}" in post:
            post = post.replace(f"\n{b}", "", 1)
            lead = f"{lead}\n{b}"
    return lead + (f"\n{pre}" if pre else "") + post


@pytest.mark.parametrize("turn", corpus.TURNS, ids=[t[0] for t in corpus.TURNS])
async def test_flag_on_exact_order_and_multiplicity_in_every_scenario(monkeypatch, turn):
    monkeypatch.setenv("ZOE_BRAIN_VOLATILE_AFTER_USER", "1")
    on = _strip_envelopes(await corpus.render(monkeypatch, turn))
    expected = _expected_on(GOLDEN[turn[0]], turn[1], turn[2].get("risk", ""), turn[2].get("every_turn", ""))
    assert on == expected
    for name, block in turn[2].items():                            # every block that was sent is sent exactly once
        if block and name not in ("persona", "manner", "register", "now_card") and block in GOLDEN[turn[0]]:
            assert on.count(block) == GOLDEN[turn[0]].count(block) == 1, name


async def _with_correction(monkeypatch, blocks, sid):
    """A REAL pending claim correction for ``sid`` (claim_correction.note -> _claim_correction_line -> take)."""
    import claim_correction

    cap = rig.install(monkeypatch, blocks)
    monkeypatch.setenv("ZOE_WRITE_CLAIM_CORRECTION", "1")
    claim_correction.note(sid, "en", ["reminder"])
    line = claim_correction.render("en", ("reminder",))
    assert line
    return cap, line


@pytest.mark.parametrize("flag", ["0", "1"])
async def test_the_real_claim_correction_moves_with_the_recall_packet(monkeypatch, flag):
    monkeypatch.setenv("ZOE_BRAIN_VOLATILE_AFTER_USER", flag)
    words = "What did I ask you to remind me about?"
    recall = corpus.mem("- synthetic reminder fact\n(pending correction: synthetic marker)")
    cap, line = await _with_correction(monkeypatch, {"identity": corpus.IDENTITY, "recall": recall}, "voice-panel-cc")
    on = _strip_envelopes(await rig.outbound(cap, words, session_id="voice-panel-cc"))
    if flag == "1":
        assert on == f"{words}\n{corpus.IDENTITY}\n{line}\n{recall}"
    else:   # off: exactly as before - the run, then the correction opening the user's message
        assert on == f"{corpus.IDENTITY}\n{recall}\n{line}\n{words}"


async def test_flag_on_safety_blocks_sit_right_after_the_words(monkeypatch):
    monkeypatch.setenv("ZOE_BRAIN_VOLATILE_AFTER_USER", "1")
    words = "I'm going to drive after two beers."
    risk = corpus.mem("- risk: synthetic")
    et = corpus.mem("- synthetic durable fact")
    blocks = {"identity": corpus.IDENTITY, "bridge": corpus.mem("x", "where you and this user left off"),
              "notes": corpus.mem("y", "notes from earlier conversations with this person"),
              "fastpath": corpus.mem("z", "what was just said in this same conversation"), "risk": risk, "every_turn": et}
    cap, line = await _with_correction(monkeypatch, blocks, "voice-panel-safe")
    on = _strip_envelopes(await rig.outbound(cap, words, session_id="voice-panel-safe"))
    assert on == "\n".join([words, risk, et, corpus.IDENTITY, blocks["bridge"], blocks["notes"], blocks["fastpath"], line])


@pytest.mark.parametrize("which", ["distress", "medical", "restraint"])
@pytest.mark.parametrize("flag", ["0", "1"])
async def test_distress_medical_restraint_answer_before_any_block(monkeypatch, which, flag):
    """These three answer in code before the turn assembles anything: with the flag on or off the fixed reply is the
    whole turn, in that precedence order, and no message reaches the sidecar."""
    import distress_handoff
    import medical_safety
    import restraint

    monkeypatch.setenv("ZOE_BRAIN_VOLATILE_AFTER_USER", flag)
    cap = rig.install(monkeypatch, {n: corpus.mem(n) for n in rig.BLOCK_NAMES if n != "verify"})
    order = []

    def stub(name, reply):
        async def _f(*_a, **_k):
            order.append(name)
            return reply
        return _f

    monkeypatch.setattr(distress_handoff, "handle", stub("distress", "FIXED-D" if which == "distress" else ""))
    monkeypatch.setattr(medical_safety, "handle", stub("medical", "FIXED-M" if which == "medical" else ""))
    monkeypatch.setattr(restraint, "handle_turn", stub("restraint", "FIXED-R" if which == "restraint" else ""))
    out = [c async for c in zfc.run_flue_brain_streaming("synthetic words", "voice-panel-sf", "rig-member")]
    assert out == [{"distress": "FIXED-D", "medical": "FIXED-M", "restraint": "FIXED-R"}[which]]
    assert order == ["distress", "medical", "restraint"][: ["distress", "medical", "restraint"].index(which) + 1]
    assert "msg" not in cap and cap["calls"] == []                 # nothing assembled, nothing sent


async def test_flag_on_the_envelope_lines_are_untouched(monkeypatch):
    monkeypatch.setenv("ZOE_BRAIN_VOLATILE_AFTER_USER", "1")
    for turn in corpus.TURNS:
        on = await corpus.render(monkeypatch, turn)
        head_on = on[: len(on) - len(_strip_envelopes(on))]
        off = GOLDEN[turn[0]]
        assert head_on == off[: len(off) - len(_strip_envelopes(off))], turn[0]


# ── the sidecar's elision (port of context-blocks.ts) and a llama-style prefix render ────────────────────────────────
_HOP_NOTE = "(you know this about me: "


def _open_type(line: str):
    if not line.endswith("]"):
        return None
    for o, _c in zfc._FLUE_CONTEXT_BLOCKS:
        if line == f"{o}]" or line.startswith(f"{o} "):
            return o
    return None


def strip_context_blocks(text: str) -> str:
    lines = text.split("\n")
    drop = [False] * len(lines)
    by_close = {c: o for o, c in zfc._FLUE_CONTEXT_BLOCKS}
    outstanding: dict = {}
    start = last_end = -1
    for i, raw in enumerate(lines):
        line = raw.rstrip()
        if start < 0 and line.startswith(_HOP_NOTE) and line.endswith(")"):
            drop[i] = True
            continue
        o = _open_type(line)
        if o:
            if start < 0:
                start = i
            outstanding[o] = outstanding.get(o, 0) + 1
            continue
        closes = by_close.get(line)
        if closes is None:
            continue
        if start >= 0:
            if outstanding.get(closes, 0) > 0:
                outstanding[closes] -= 1
            if all(n == 0 for n in outstanding.values()):
                for j in range(start, i + 1):
                    drop[j] = True
                start, last_end = -1, i
        elif last_end >= 0:
            for j in range(last_end + 1, i + 1):
                drop[j] = True
            last_end = i
    if start >= 0:
        for j in range(start, len(lines)):
            drop[j] = True
    if not any(drop):
        return text
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln, d in zip(lines, drop) if not d)).strip()


def test_the_port_strips_like_the_sidecar():
    assert strip_context_blocks("hi") == "hi"
    assert strip_context_blocks(f"{corpus.mem('x')}\nhi") == "hi"
    assert strip_context_blocks(f"hi\n{corpus.mem('x')}") == "hi"
    assert strip_context_blocks("hi\n(you know this about me: x)") == "hi"
    ts = (Path(__file__).resolve().parents[3] / "labs/flue-zoe-brain-2x/src/context-blocks.ts").read_text()
    assert "export function stripContextBlocks" in ts and "replace(/\\n{3,}/g, '\\n\\n').trim()" in ts


SYSTEM = "<system>synthetic soul + tools</system>"


def _render(history: "list[tuple[str, str]]", last_user: "str | None" = None) -> str:
    out = SYSTEM + "".join(f"<user>{u}</user><model>{m}</model>" for u, m in history)
    if last_user is not None:
        out += f"<user>{last_user}</user><model>"
    return out


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


SCENARIOS = ["plain", "recall", "recall_marker", "offer", "list_share", "bridge_notes_fastpath", "hop_preamble",
             "named_guest"]


async def _two_turns(monkeypatch, first: str, second: str = "plain"):
    t1 = next(t for t in corpus.TURNS if t[0] == first)
    t2 = next(t for t in corpus.TURNS if t[0] == second)
    u1 = _strip_envelopes(await corpus.render(monkeypatch, t1))
    u2 = _strip_envelopes(await corpus.render(monkeypatch, t2))
    reply = "Synthetic reply."
    cached = _render([(u1, reply)])                                   # what llama holds after turn 1 (u1 was the newest)
    prompt = _render([(strip_context_blocks(u1), reply)], last_user=u2)   # turn 2: u1 elided
    words_end = len(SYSTEM) + len("<user>") + u1.index(t1[1]) + len(t1[1])
    return cached, prompt, words_end


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.mark.parametrize("first", SCENARIOS)
async def test_flag_on_the_prefix_through_the_words_is_hash_equal_across_turns(monkeypatch, first):
    monkeypatch.setenv("ZOE_BRAIN_VOLATILE_AFTER_USER", "1")
    cached, prompt, words_end = await _two_turns(monkeypatch, first)
    assert _h(prompt[:words_end]) == _h(cached[:words_end])
    assert _common_prefix(cached, prompt) >= words_end


@pytest.mark.parametrize("first", SCENARIOS)
async def test_negative_control_flag_off_the_same_turns_diverge_before_the_words(monkeypatch, first):
    cached, prompt, words_end = await _two_turns(monkeypatch, first)
    assert _h(prompt[:words_end]) != _h(cached[:words_end])
    assert _common_prefix(cached, prompt) < words_end


@pytest.mark.parametrize("first", SCENARIOS)
async def test_flag_on_saves_at_least_the_words_on_every_scenario(monkeypatch, first):
    """Re-prefill measured as chars after the common prefix of turn 2's prompt: flag on is shorter by >= the words."""
    _c_off, p_off, _ = await _two_turns(monkeypatch, first)
    off = len(p_off) - _common_prefix(_c_off, p_off)
    monkeypatch.setenv("ZOE_BRAIN_VOLATILE_AFTER_USER", "1")
    c_on, p_on, _ = await _two_turns(monkeypatch, first)
    on = len(p_on) - _common_prefix(c_on, p_on)
    words = len(next(t for t in corpus.TURNS if t[0] == first)[1])
    assert off - on >= words


# ── B6: the continuity ceiling ───────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [(None, 3.0), ("0.4", 0.4), ("0", 3.0), ("-1", 3.0), ("abc", 3.0),
                                          ("11", 3.0), ("10", 10.0)])
def test_continuity_ceiling_reader(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("ZOE_CONTINUITY_CEILING_S", raw)
    assert zfc._continuity_timeout_s() == expected


async def test_continuity_ceiling_bounds_the_packet_wait(monkeypatch):
    async def slow(*_a, **_k):
        await asyncio.sleep(1.0)
        return {"packet": "- synthetic"}

    async def portrait(*_a, **_k):
        return ""

    monkeypatch.setattr(zfc, "_continuity_inject_enabled", lambda: True)
    monkeypatch.setattr(zfc, "is_continuity_turn", lambda *_a: True)
    monkeypatch.setattr(zfc, "_fetch_continuity_packet", slow)
    monkeypatch.setattr(zfc, "_fetch_portrait_line", portrait)
    monkeypatch.setenv("ZOE_CONTINUITY_CEILING_S", "0.1")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    block = await zfc._continuity_context_block("Ugh, rough day honestly.", "corpus-member")
    assert block == "" and loop.time() - t0 < 0.6
