"""FLUE_PREPOST (per-block pre-POST timing) and the PANEL_KV_MISS ``cause=`` field (speed audit 2026-11-14, A1 step 1 / A8).

Measurement only: these instruments must never change what is sent, and must never log text. Synthetic data only.
"""
from __future__ import annotations

import asyncio
import logging
import re

import pytest

import flue_turn_rig as rig
import zoe_flue_client as zfc

pytestmark = pytest.mark.ci_safe

PLANT = "PLANTED-zx81-octarine"   # must never appear in any instrument line
_LINE_RE = re.compile(
    r"^FLUE_PREPOST session=\S+ total_ms=\d+ blocks_ms=\d+ other_ms=\d+ awaited=\d+((?: [a-z_]+_ms=\d+)*)$")


def _instrument_lines(caplog, tag: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(tag)]


# ── the line format ──────────────────────────────────────────────────────────────────────────────────────────────────
async def test_timer_line_format_names_only_known_blocks():
    t = zfc._PrepostTimer()

    async def five():
        return 5

    assert await t.timed("recall", five()) == 5
    assert await t.timed("not_a_block", five()) == 5          # an unknown name is never a field
    t.ms["offer"] = 12.4
    line = t.render("voice-panel-a", now=t.t0 + 0.050)
    m = _LINE_RE.match(line)
    assert m, line
    assert "recall_ms=" in line and "offer_ms=12" in line and "not_a_block" not in line
    assert "total_ms=50" in line and "awaited=2" in line
    # fields come out in the fixed block order, never in insertion order
    assert line.index("recall_ms=") < line.index("offer_ms=")


async def test_timed_is_a_bare_await_for_exceptions():
    t = zfc._PrepostTimer()

    async def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        await t.timed("recall", boom())
    assert "recall" in t.ms                                     # still measured


async def test_one_line_per_turn_with_every_block_that_ran(monkeypatch, caplog):
    cap = rig.install(monkeypatch, {"recall": f"[MEMORY CONTEXT — x]\n{PLANT}\n[END MEMORY CONTEXT]",
                                    "identity": f"[Today — household context]\n{PLANT}\n[END Today]"},
                      delay_s={"recall": 0.02})
    caplog.set_level(logging.INFO, logger="zoe_flue_client")
    sent = await rig.outbound(cap, f"what did I say about {PLANT}?")
    assert PLANT in sent                                        # the rig really assembled the blocks
    lines = _instrument_lines(caplog, "FLUE_PREPOST")
    assert len(lines) == 1, lines
    m = _LINE_RE.match(lines[0])
    assert m, lines[0]
    fields = dict(f.split("=") for f in m.group(1).split())
    assert int(fields["recall_ms"]) >= 15
    # recall owns the turn: continuity / hop / risk never ran, so they have no field
    assert "continuity_ms" not in fields and "hop_ms" not in fields and "risk_ms" not in fields
    for name in ("identity", "fastpath", "bridge", "notes", "every_turn", "persona", "manner", "register", "now_card"):
        assert f"{name}_ms" in fields, name


async def test_no_text_in_any_instrument_line_planted_string(monkeypatch, caplog):
    blocks = {n: f"[MEMORY CONTEXT — {n}]\n{PLANT}\n[END MEMORY CONTEXT]" for n in rig.BLOCK_NAMES}
    blocks["verify"] = ""
    cap = rig.install(monkeypatch, blocks)
    caplog.set_level(logging.DEBUG)
    for i in range(3):
        await rig.outbound(cap, f"{PLANT} turn {i}", session_id="voice-panel-plant", uid=f"member-{PLANT}")
        zfc._log_prompt_cache("voice-panel-plant", {"prompt_cache": [{"prompt_n": 2000, "cache_n": 0}],
                                                     "context_budget": {"tools": 600 + i, "history": 50, "elided": 1}})
    for tag in ("FLUE_PREPOST", "PANEL_KV_MISS", "FLUE_CONTEXT_BUDGET"):
        lines = _instrument_lines(caplog, tag)
        assert lines, tag
        for line in lines:
            assert PLANT not in line and "member-" not in line, line


async def test_negative_control_the_planted_string_is_detectable(monkeypatch, caplog):
    """The planted-string check above can fail: a line that DID carry the text is caught."""
    caplog.set_level(logging.INFO)
    logging.getLogger("zoe_flue_client").info("FLUE_PREPOST session=s total_ms=1 %s", PLANT)
    assert any(PLANT in line for line in _instrument_lines(caplog, "FLUE_PREPOST"))


async def test_a_seam_reply_turn_logs_no_line_and_leaves_no_timer_to_adopt(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="zoe_flue_client")
    t = zfc._PrepostTimer()
    t.claimed = True
    zfc._PREPOST.set(t)
    assert zfc._prepost_timer_for_turn() is not t               # a claimed (stale) timer is never reused
    fresh = zfc._PrepostTimer()
    zfc._PREPOST.set(fresh)
    assert zfc._prepost_timer_for_turn() is fresh               # the wrapper's unclaimed one is adopted


# ── PANEL_KV_MISS cause ──────────────────────────────────────────────────────────────────────────────────────────────
def _env(**lines) -> str:
    return "".join(f" zoe-{k}:{v}\n" for k, v in lines.items())


def _struct(outbound: str, uid="m1", **budget) -> dict:
    s = zfc.prefix_structure(outbound, uid)
    s.update(budget)
    return s


BASE = _env(persona='"p"', manner='"m"', uid="m1")


@pytest.mark.parametrize("prev,cur,kw,cause", [
    (None, _struct(BASE + "hi"), {}, "cold"),
    (_struct(BASE + "hi"), _struct(BASE + "hi"), {"warm": False}, "cold"),
    (_struct(BASE + "hi"), _struct(BASE + "hi"), {"others_between": 1}, "slot_evicted"),
    (_struct(BASE + "hi"), _struct(BASE + "hi", uid="m2"), {}, "member_switch"),
    (_struct(BASE + "hi"), _struct(_env(persona='"p"', manner='"other"', uid="m1") + "hi"), {}, "system_changed"),
    (_struct(BASE + "hi"), _struct(_env(manner='"m"', persona='"p"', uid="m1") + "hi"), {}, "block_order_changed"),
    (_struct(BASE + "hi", tools=600), _struct(BASE + "hi", tools=900), {}, "tool_block_changed"),
    (_struct(BASE + "[MEMORY CONTEXT — x]\nfact\n[END MEMORY CONTEXT]\nhi", tools=600, history=10),
     _struct(BASE + "ok", tools=600, history=60, elided=1), {}, "elided_block"),
    (_struct(BASE + "[MEMORY CONTEXT — x]\nfact\n[END MEMORY CONTEXT]\nhi", tools=600, history=10),
     _struct(BASE + "ok", tools=600, history=60, elided=0), {}, "unknown"),     # elision off: the block was never removed
    (_struct(BASE + "hi", tools=600, history=900), _struct(BASE + "ok", tools=600, history=300), {}, "history_truncated"),
    (_struct(BASE + "hi", tools=600, history=10), _struct(BASE + "ok", tools=600, history=60), {}, "unknown"),
])
def test_cause_classification_on_synthetic_prefixes(prev, cur, kw, cause):
    kw = {"warm": True, **kw}
    assert zfc._kv_miss_cause(prev, cur, **kw) == cause
    assert cause in zfc.KV_MISS_CAUSES


def test_structure_holds_hashes_never_text():
    s = zfc.prefix_structure(_env(persona=f'"{PLANT}"', uid=PLANT) + f"{PLANT} words", PLANT)
    assert PLANT not in repr(s)
    assert s["env_order"] == ("persona", "uid") and s["block"] is False


def test_hop_note_line_counts_as_an_elidable_block():
    assert zfc._carries_context_block("hi\n(you know this about me: I sleep badly)")
    assert not zfc._carries_context_block("hi (you know this about me: inline, not a whole line)")


def test_alarm_logs_the_cause_end_to_end(caplog, monkeypatch):
    monkeypatch.setattr(zfc, "_non_household_turns", lambda: 0)
    monkeypatch.setattr(zfc, "_background_calls", lambda: 0)
    caplog.set_level(logging.INFO, logger="zoe_flue_client")
    sid = "voice-panel-cause"
    zfc._PREFIX_STRUCT.pop(sid, None)
    zfc._panel_kv_prev.pop(sid, None)
    zfc._note_prefix_structure(sid, BASE + "[MEMORY CONTEXT — x]\nf\n[END MEMORY CONTEXT]\nhi", "m1")
    zfc._note_prefix_budget(sid, {"tools": 600, "history": 10, "elided": 1})
    assert zfc._panel_kv_alarm(sid, 40, 2000, "household", now=100.0) is None
    zfc._note_prefix_structure(sid, BASE + "thanks", "m1")
    zfc._note_prefix_budget(sid, {"tools": 600, "history": 80, "elided": 1})
    assert zfc._panel_kv_alarm(sid, 900, 1200, "household", now=110.0) == "warm"
    line = _instrument_lines(caplog, "PANEL_KV_MISS")[-1]
    assert line.endswith("cause=elided_block"), line


def test_context_budget_flags_a_tool_block_that_changed_mid_session(caplog):
    caplog.set_level(logging.INFO, logger="zoe_flue_client")
    sid = "voice-panel-tools"
    zfc._PREFIX_STRUCT.pop(sid, None)
    for tools in (600, 600, 900):
        zfc._note_prefix_structure(sid, BASE + "hi", "m1")
        zfc._log_prompt_cache(sid, {"context_budget": {"system": 1, "tools": tools, "history": 1, "tail": 1}})
    lines = _instrument_lines(caplog, "FLUE_CONTEXT_BUDGET")
    assert [line.endswith("tools_changed=1") for line in lines] == [False, False, True], lines
    assert "tools_changed" not in lines[1]                      # unchanged = the line exactly as before


# ── the household fallback's packet fetch, per call ──────────────────────────────────────────────────────────────────
def _household_path(monkeypatch, fetch_s: float):
    import memory_gate

    async def fetch(*_a, **_k):
        await asyncio.sleep(fetch_s)
        return "- synthetic fact about the garden"

    async def no_named(*_a, **_k):
        return []

    monkeypatch.setattr(zfc, "_recall_inject_enabled", lambda: True)
    monkeypatch.setattr(zfc, "_recall_floor_shape", lambda _m: "")
    monkeypatch.setattr(zfc, "_named_person_floor", no_named)
    monkeypatch.setattr(zfc, "is_continuity_turn", lambda *_a: False)
    monkeypatch.setattr(memory_gate, "household_questions_enabled", lambda: True)
    monkeypatch.setattr(memory_gate, "is_question_turn", lambda _m: True)
    monkeypatch.setattr(memory_gate, "_touch_tokens", lambda _m: {"garden"})
    monkeypatch.setattr(memory_gate, "packet_touches_question", lambda *_a: True)
    monkeypatch.setattr(zfc, "_fetch_for_prompt_packet", fetch)
    monkeypatch.setattr(zfc, "_HOUSEHOLD_FETCH_BUDGET_S", 0.05)


@pytest.mark.parametrize("fetch_s,exceeded", [(0.0, False), (0.2, True)])
async def test_household_fetch_ms_is_its_own_field_and_not_double_counted(monkeypatch, caplog, fetch_s, exceeded):
    _household_path(monkeypatch, fetch_s)
    caplog.set_level(logging.INFO, logger="zoe_flue_client")
    t = zfc._PrepostTimer()
    zfc._PREPOST.set(t)
    block = await t.timed("recall", zfc._recall_context_block("does the garden need water?", "m1"))
    assert bool(block) is not exceeded
    assert "household" in t.ms and "recall_fetch" not in t.ms
    line = t.render("s")
    fields = dict(f.split("=") for f in line.split()[2:])
    assert int(fields["blocks_ms"]) == int(fields["recall_ms"]) and fields["awaited"] == "1"   # nested: not summed
    if exceeded:
        assert int(fields["household_ms"]) >= 40
    else:
        assert "ms=" in _instrument_lines(caplog, "SEAM_HOUSEHOLD")[-1]
