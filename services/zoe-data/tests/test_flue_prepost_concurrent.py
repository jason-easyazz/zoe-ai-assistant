"""ZOE_FLUE_PREPOST_CONCURRENT (speed audit 2026-11-14 A1 step 2): the always-awaited pre-POST blocks run concurrently.

The bytes must be exactly the sequential ones (the golden corpus, with and without the B3 flag); the precedence chain's
side-effecting blocks (offer cooldown, list share, recall gate) never run speculatively; the in-group dependencies hold
(the style request is stored before the register line renders; the named guest only when the identity block is empty);
and the wall time before the POST goes down. Synthetic data only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

import pytest

import flue_prompt_corpus as corpus
import flue_turn_rig as rig
import zoe_flue_client as zfc

pytestmark = pytest.mark.ci_safe

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "flue_prompt_golden.json").read_text())
ON = "ZOE_FLUE_PREPOST_CONCURRENT"


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for k in (ON, "ZOE_BRAIN_VOLATILE_AFTER_USER", "ZOE_CONTINUITY_CEILING_S"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("turn", corpus.TURNS, ids=[t[0] for t in corpus.TURNS])
async def test_flag_on_bytes_are_exactly_the_golden(monkeypatch, turn):
    monkeypatch.setenv(ON, "1")
    assert await corpus.render(monkeypatch, turn) == GOLDEN[turn[0]]


@pytest.mark.parametrize("turn", corpus.TURNS, ids=[t[0] for t in corpus.TURNS])
async def test_flag_on_equals_flag_off_with_volatile_after_user_too(monkeypatch, turn):
    monkeypatch.setenv("ZOE_BRAIN_VOLATILE_AFTER_USER", "1")
    off = await corpus.render(monkeypatch, turn)
    monkeypatch.setenv(ON, "1")
    assert await corpus.render(monkeypatch, turn) == off


async def test_flag_off_starts_no_task(monkeypatch):
    pp = zfc._PrepostTimer()
    pre = zfc._prefetch_for_turn(pp, "m1", "s1", "hi", {})
    assert pre.tasks == {} and pp.tasks == []


# ── what runs, and in what order ─────────────────────────────────────────────────────────────────────────────────────
async def _calls(monkeypatch, blocks, message="What is my favourite tea?", delay_s=None, **kwargs):
    cap = rig.install(monkeypatch, blocks, delay_s=delay_s)
    await rig.outbound(cap, message, **kwargs)
    return list(cap["calls"])


@pytest.mark.parametrize("flag", ["0", "1"])
async def test_the_precedence_chain_never_runs_speculatively(monkeypatch, flag):
    monkeypatch.setenv(ON, flag)
    calls = await _calls(monkeypatch, {"recall": corpus.mem("- synthetic"), "identity": corpus.IDENTITY})
    assert "recall" in calls
    for loser in ("continuity", "hop", "risk"):   # recall owns the turn: these never run, prefetch or not
        assert loser not in calls, (flag, loser)
    calls = await _calls(monkeypatch, {"offer": f"{corpus.OFFER}\nx\n{corpus.END_OFFER}"})
    assert "offer" in calls and "list_share" not in calls      # the offer won: the list share is not consumed


async def test_register_line_renders_after_the_style_request_is_stored(monkeypatch):
    monkeypatch.setenv(ON, "1")
    calls = await _calls(monkeypatch, {"register": "casual"}, delay_s={"register_apply": 0.05})
    assert calls.index("register_apply") < calls.index("register")


async def test_named_guest_only_when_the_identity_block_is_empty(monkeypatch):
    monkeypatch.setenv(ON, "1")
    assert "named_guest" not in await _calls(monkeypatch, {"identity": corpus.IDENTITY})
    assert "named_guest" in await _calls(monkeypatch, {"identity": ""})


async def test_a_replay_never_stores_a_style_request(monkeypatch):
    monkeypatch.setenv(ON, "1")
    assert "register_apply" not in await _calls(monkeypatch, {}, replay_isolation=True)


async def test_a_turn_closed_before_its_post_leaves_no_task_running(monkeypatch):
    monkeypatch.setenv(ON, "1")
    rig.install(monkeypatch, {}, delay_s={n: 5.0 for n in rig.BLOCK_NAMES})
    pp = zfc._PrepostTimer()
    pre = zfc._prefetch_for_turn(pp, "m1", "s1", "hi", {})
    assert len(pp.tasks) >= 9
    await asyncio.sleep(0)
    t0 = time.monotonic()
    pp.cancel_tasks()                                            # what run_flue_brain_streaming's finally does
    await asyncio.wait_for(asyncio.gather(*pp.tasks, return_exceptions=True), timeout=1.0)
    assert all(t.done() for t in pp.tasks) and time.monotonic() - t0 < 1.0   # not left to run out the 5 s
    assert sum(t.cancelled() for t in pp.tasks) >= 9            # every slow block was cancelled (the stub apply is instant)
    assert pre.tasks                                             # nothing was taken


# ── the wall time before the POST, on a synthetic chain ──────────────────────────────────────────────────────────────
# A plain household turn: the always-awaited group plus the every-turn and offer reads. Delays are synthetic stand-ins
# of the right order (single DB reads, 10-40 ms), not measurements of the live box.
DELAYS = {"offer": 0.03, "list_share": 0.02, "every_turn": 0.04, "fastpath": 0.025, "bridge": 0.015, "notes": 0.015,
          "identity": 0.03, "now_card": 0.02, "register_apply": 0.01, "register": 0.015, "manner": 0.01, "persona": 0.02}
_TOTAL_RE = re.compile(r"total_ms=(\d+)")


async def _prepost_ms(monkeypatch, caplog, flag: str) -> int:
    monkeypatch.setenv(ON, flag)
    caplog.clear()
    cap = rig.install(monkeypatch, {"identity": corpus.IDENTITY}, delay_s=DELAYS)
    await rig.outbound(cap, "Should I bring a jacket?")
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("FLUE_PREPOST")]
    return int(_TOTAL_RE.search(line).group(1))


async def test_flag_on_cuts_the_pre_post_wall_time(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="zoe_flue_client")
    off = min([await _prepost_ms(monkeypatch, caplog, "0") for _ in range(3)])
    on = min([await _prepost_ms(monkeypatch, caplog, "1") for _ in range(3)])
    serial = int(sum(DELAYS.values()) * 1000)
    print(f"\nPREPOST synthetic plain turn: sequential total_ms={off} concurrent total_ms={on} (sum of delays {serial} ms)")
    assert off >= 0.8 * serial                                   # the sequential path really pays the sum
    assert on <= 0.7 * off                                       # the group overlaps the chain
