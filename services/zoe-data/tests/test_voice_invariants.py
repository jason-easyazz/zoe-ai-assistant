"""Voice invariants — LOCK IN the winning optimizations so a cleanup/refactor can't
silently revert them.

Every assertion here corresponds to a fix that regressed at least once before
(the git history literally has a commit "restore lost optimization stack from
pre-cleanup state"). The pattern was: an optimization ships without a guard, the
next refactor drops it, and the same symptom comes back. These guards fail CI the
moment a winning piece disappears — both the behaviour AND the wiring (a cleanup
that deletes a behaviour test often deletes the feature in the same sweep, so we
also pin the source wiring in a separately-named file).

If one of these legitimately needs to change, that's a deliberate decision — update
the guard in the same commit so the intent stays explicit. See
project_voice_recording_test_loop and feedback_fixed_models_are_rocks in memory.
"""
import asyncio
import os

import pytest

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # services/zoe-data


def _src(rel: str) -> str:
    return open(os.path.join(DATA, rel), encoding="utf-8").read()


# ── 1. SPEED: first audio snaps out on the first clause ──────────────────────
# Regressed: first-audio drifted from ~0.7s feel back to ~3s because the synth
# waited for a whole first sentence. #757 restored the clause flush.
def test_fast_first_audio_present_and_wired():
    s = _src("routers/voice_tts.py")
    assert "def _extract_first_unit" in s, "first-audio clause splitter removed"
    assert "_fast_first_audio_enabled" in s, "fast-first-audio flag removed"
    # Actually wired into the streaming loop (not just defined):
    assert "_emit_sentence(first_unit)" in s, "first-unit flush not wired into stream loop"


def test_first_unit_splits_early_and_is_decimal_safe():
    import routers.voice_tts as v
    unit, _ = v._extract_first_unit("It's twelve degrees and clear, with a light breeze.")
    assert unit == "It's twelve degrees and clear,"           # early clause break
    assert v._extract_first_unit("It's 12.4 degrees")[0] is None  # never split a decimal


# ── 2. TTFT: zoe-core streams text_delta (the 2.1s→0.7s win, #729) ───────────
def test_text_delta_streaming_present():
    s = _src("zoe_core_client.py")
    assert "text_delta" in s, "Pi text_delta streaming removed → TTFT regresses to whole-message"


# ── 3. RECALL ROUTING: voice defers people/memory to the brain (#755) ────────
# Regressed-prone: the expert path was slow (2-4s) and wrong (stored questions,
# "not on file"). Voice must hand recall/chat to the brain.
def test_voice_defers_people_and_memory():
    import fast_tiers as ft
    defer = ft.CHANNEL_PROFILES["voice"].get("defer_domains") or frozenset()
    assert {"people", "memory"} <= set(defer), "voice no longer defers people/memory to the brain"


# ── 4. RECALL WIRING: the voice brain turn injects the user's memory (#755) ──
# Without this the brain has no facts to recall → "I don't know your dad's name".
def test_voice_brain_memory_injected():
    s = _src("routers/voice_tts.py")
    assert "_voice_brain_memory" in s, "voice memory loader removed"
    assert "db_memory_context=_v" in s, "memory context not passed into the voice brain turn"


# ── 5. NO STORE-QUESTION: recall questions are answered, never stored (#756) ─
# Regressed on-device: "Do you remember my mum's name?" got saved as a fact.
def test_recall_question_is_not_stored(monkeypatch):
    import expert_dispatch as xd
    ingested: list[str] = []

    class _Svc:
        async def ingest(self, text, **kw):
            ingested.append(text)
        async def search(self, *a, **k):
            return []

    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Svc())
    recalled: list[str] = []

    async def _fake_recall(domain, text, user_id, session_id):
        recalled.append(text)
        return "RECALLED"

    monkeypatch.setattr(xd, "_run_expert", _fake_recall)
    out = asyncio.run(xd.store_fact("people", "Do you remember what my mum's name is?", "jason"))
    assert ingested == [], "a recall QUESTION was stored as a fact again"
    assert out == "RECALLED"
