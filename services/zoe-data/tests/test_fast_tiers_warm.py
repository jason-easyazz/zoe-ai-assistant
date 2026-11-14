"""A6 step 2: the first fast_tiers.resolve after a cold start does not run the tiers' lazy imports on the event loop.

The cold case needs a process where nothing is imported yet, so it runs in a subprocess: a 10 ms ticker beside the first
resolve() (a fake 400 ms route, off the loop as in test_router_off_loop). Negative control: the same subprocess with the warm-up
skipped (``_WARM = True``) shows the import block. Equivalence: warm() changes no answer - the same utterances give the same
DispatchResult fields whether resolve() ran its warm-up or not.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SERVICE = Path(__file__).resolve().parents[1]
_CHILD = r"""
import asyncio, sys, time
sys.path.insert(0, sys.argv[1])
import expert_dispatch, fast_tiers, semantic_router
expert_dispatch.is_enabled = lambda: True
semantic_router.is_enabled = lambda: True
semantic_router.route = lambda t: (time.sleep(0.4), {"domain": "chat", "scores": {"chat": 0.9}})[1]
if sys.argv[2] == "skip":
    fast_tiers._WARM = True
async def main():
    gaps, done = [], asyncio.Event()
    async def tick():
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.01); now = time.perf_counter(); gaps.append(now - last); last = now
    t = asyncio.create_task(tick()); await asyncio.sleep(0.03)
    out = await fast_tiers.resolve("tell me a story", "u", "s", phase="domain")
    done.set(); await t
    print(f"{max(gaps):.3f} {out!r} {fast_tiers._WARM}")
asyncio.run(main())
"""


def _first_call_gap(mode: str) -> tuple[float, str]:
    r = subprocess.run([sys.executable, "-c", _CHILD, str(SERVICE), mode], capture_output=True, text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin", "ZOE_STT_DISABLED": "1"})
    gap, rest = r.stdout.strip().splitlines()[-1].split(" ", 1)
    return float(gap), rest


def test_first_call_after_a_cold_start_does_not_block_the_loop():
    gap, rest = _first_call_gap("warm")
    assert gap < 0.2, f"the first resolve blocked the loop {gap:.3f}s"
    assert rest == "None True"  # chat -> the caller's brain lane, as before; the warm-up ran


def test_negative_control_without_the_warm_up_the_first_call_blocks():
    gap, rest = _first_call_gap("skip")
    assert gap >= 0.2 and rest == "None True", f"negative control saw only {gap:.3f}s"


def test_lazy_module_list_is_read_from_the_tiers():
    import fast_tiers

    mods = fast_tiers._lazy_modules()
    assert {"intent_router", "semantic_router", "expert_dispatch", "calc_speech"} <= set(mods)
    assert "asyncio" not in mods  # module-level imports are not lazy


UTTERANCES = [("what is 12 times 7", None), ("what's 2 plus 2", None), ("tell me a story", "chat"),
              ("what time is it", "time"), ("what's the weather", "weather"), ("add milk to my list", "lists"),
              ("thanks", "chat"), ("", None)]


async def test_warm_up_changes_no_answer(monkeypatch):
    import expert_dispatch
    import fast_tiers
    import semantic_router

    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "route", lambda t: {"domain": "chat", "scores": {"chat": 0.9}})

    def _fields(r):
        return None if r is None else (r.domain, r.reply, r.intent, r.tier, repr(getattr(r, "ui", None)))

    async def run_all(cold: bool):
        out = []
        for text, domain in UTTERANCES:
            dec = {"domain": domain, "scores": {domain or "chat": 0.9}} if domain else None
            monkeypatch.setattr(fast_tiers, "_WARM", not cold)
            out.append(_fields(await fast_tiers.resolve(text, "u-eq", f"s-eq-{cold}", phase="domain", router_decision=dec)))
        return out

    assert await run_all(cold=True) == await run_all(cold=False)
