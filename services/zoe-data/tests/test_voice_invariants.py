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


# ── 1. SPEED vs NATURALNESS: first audio starts fast without wrecking prosody ─
# History: first-audio once drifted to ~3s (waited for a whole sentence), #757 added
# an aggressive clause flush to fix it — but that split SHORT replies mid-sentence,
# giving a pause + pitch reset ("22 degrees," <pause> "mostly clear…") and even
# splitting inside "8:05". Kokoro on GPU is fast now, so short replies play whole and
# only long openings clause-break. Keep the flush wired, but not mid-short-sentence.
def test_fast_first_audio_present_and_wired():
    s = _src("routers/voice_tts.py")
    assert "def _extract_first_unit" in s, "first-audio clause splitter removed"
    assert "_fast_first_audio_enabled" in s, "fast-first-audio flag removed"
    # Actually wired into the streaming loop (not just defined):
    assert "_emit_sentence(first_unit)" in s, "first-unit flush not wired into stream loop"


def test_first_unit_keeps_short_replies_whole_and_is_number_safe():
    import routers.voice_tts as v
    # short reply: held whole (no mid-sentence pause), NOT clause-split
    assert v._extract_first_unit("It's twelve degrees and clear, with a light breeze.")[0] is None
    assert v._extract_first_unit("It's 12.4 degrees")[0] is None          # never split a decimal
    assert v._extract_first_unit("The current time is 8:")[0] is None      # never split inside a time
    # long opening still clause-breaks for fast first-audio
    long_open = ("Honey is one of the very few foods that never spoils, and archaeologists "
                 "have even found edible pots of it. ")
    assert v._extract_first_unit(long_open)[0] == "Honey is one of the very few foods that never spoils,"


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


# ── 6. PREFILL: the lean soul + ZOE_SELF summary stay lean (PR #812) ─────────
# Regressed risk: a refactor re-inflates the per-turn system prompt. The audit
# (docs/architecture/brain-prompt-tools-audit.md) cut _ZOE_SOUL_STATIC from
# 3,754 → 1,532 tok. These guards keep the wins wired without a tokenizer dep.
def test_lean_soul_base_present_and_deduped():
    s = _src("zoe_agent.py")
    # Lean phrasing landed…
    assert "You are Zoe — warm, curious, and genuinely present" in s, "lean _ZOE_SOUL_BASE reverted"
    # …and the TIER-2 research playbook is no longer spelled out in the prompt
    # (it lived in both the prompt and the deep_web_research schema). The long
    # per-bullet "ACCOMMODATION:"/"TRANSPORT:" dump must not come back.
    assert "ACCOMMODATION: \"hotels" not in s, "TIER-2 research dump re-duplicated into the prompt"


def test_zoe_self_summary_is_curated_not_a_flat_dump():
    s = _src("zoe_agent.py")
    # The curated summary keeps the behaviour-driving sections…
    assert "_ZOE_SELF_SUMMARY" in s, "lean ZOE_SELF summary constant removed"
    for needle in ("Tier0 intent_router", "escalate_to_hermes", "builder skills"):
        assert needle in s, f"ZOE_SELF summary lost behaviour-driving content: {needle}"


# ── 7. LAN IP is config, not baked into the static prompt (PR #812) ──────────
# Regressed risk: someone re-hardcodes the panel LAN IP into the prompt body.
def test_lan_ip_is_env_config_not_hardcoded_in_prompt():
    import importlib
    import zoe_agent
    importlib.reload(zoe_agent)
    # The prompt body uses a placeholder, substituted at import from the env var.
    assert "{ZOE_HOST_LAN_IP}" in zoe_agent._ZOE_SOUL_BASE, "LAN IP placeholder removed from base"
    assert "{ZOE_HOST_LAN_IP}" not in zoe_agent._ZOE_SOUL_STATIC, "placeholder not substituted at import"


def test_lan_ip_reads_env_with_default(monkeypatch):
    import importlib
    monkeypatch.setenv("ZOE_HOST_LAN_IP", "10.9.9.9")
    import zoe_agent
    importlib.reload(zoe_agent)
    try:
        assert zoe_agent._ZOE_HOST_LAN_IP == "10.9.9.9"
        assert "10.9.9.9" in zoe_agent._ZOE_SOUL_STATIC, "env LAN IP not injected into prompt"
    finally:
        monkeypatch.delenv("ZOE_HOST_LAN_IP", raising=False)
        importlib.reload(zoe_agent)  # restore default for later tests
    assert zoe_agent._ZOE_HOST_LAN_IP == "192.168.1.218"


# ── 8. VOICE TOOLS: lean set keeps a memory-WRITE path (PR #812 / Greptile) ──
# Regressed risk: trimming _VOICE_TOOLS drops the only write tool while
# _VOICE_ACTION_WORDS still routes remember/store/save to the LLM fallback,
# leaving a missed-intent memory write with no tool to execute it.
def test_voice_tools_keep_a_memory_write_path():
    import importlib
    import zoe_agent
    importlib.reload(zoe_agent)
    vt = set(zoe_agent._VOICE_TOOLS)
    # Every voice tool must be a real, dispatchable tool (no orphan names).
    tool_names = {t["function"]["name"] for t in zoe_agent._TOOLS}
    assert vt <= tool_names, f"voice tool(s) not in _TOOLS: {vt - tool_names}"
    # Action words that reach the LLM voice path must have an executor.
    write_words = {"remember", "store", "save", "forget"}
    assert write_words <= zoe_agent._VOICE_ACTION_WORDS, "voice write action-words changed"
    assert {"mempalace_add", "memory_update"} & vt, (
        "voice has no memory-write tool but still routes remember/store/save to the LLM"
    )
    # Stay lean: the trimmed-out tools must not creep back into the voice set.
    assert "escalate_to_openclaw" not in vt, "escalate_to_openclaw back in voice (use Hermes)"
    assert "show_chart" not in vt, "show_chart back in voice (not voice-rendered)"
