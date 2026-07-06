"""ZOE_PERSON_LLM_PREFILTER — the person-mention gate for the per-turn LLM pass.

The LLM extraction fires on every non-guest turn and costs ~0.6–1.3 s of Gemma
time on the live brain's llama-server. The prefilter (flag-gated, default OFF)
skips turns with no plausible person mention. These tests lock in:
  * flag OFF (default) → byte-for-byte no-op: the LLM path is still reached
  * flag ON → non-person turns short-circuit BEFORE any HTTP call
  * the regex verdicts, incl. the measured false-negative traps
    (sentence-initial names) and capitals-that-aren't-people
"""
import pytest

pytestmark = pytest.mark.ci_safe  # pure regex + monkeypatched HTTP, slim-dep

import person_extractor_llm as pel


# ── mentions_person verdicts ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "my sister Sarah is coming over for dinner on Friday",
    "Tom got promoted to site manager last week",          # sentence-initial name
    "Emma passed her driving test yesterday",               # sentence-initial name
    "remember that Jess is allergic to peanuts",
    "my dad's birthday is on the twelfth of August",        # relationship word
    "the neighbour's name is Priya, she offered to feed the cat",
    "my mate from footy hurt his knee",                     # relationship word, no capitals
    "i told my wife i'd be home by six",
])
def test_person_turns_pass(text):
    assert pel.mentions_person(text) is True


@pytest.mark.parametrize("text", [
    "what's the weather like today",
    "turn off the kitchen lights please",
    "add bread and milk to the shopping list",
    "set a timer for ten minutes",
    "remind me to water the plants tomorrow morning",
    "what's on my calendar this afternoon",
    "actually cancel that reminder",
    "no i meant the other one",
])
def test_non_person_turns_skip(text):
    assert pel.mentions_person(text) is False


# ── flag wiring ──────────────────────────────────────────────────────────────

def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("ZOE_PERSON_LLM_PREFILTER", raising=False)
    assert pel.prefilter_enabled() is False


@pytest.mark.asyncio
async def test_flag_off_is_noop_llm_still_reached(monkeypatch):
    """Default OFF: a non-person turn must still reach the LLM call (no
    behavior change until the flag is deliberately enabled)."""
    monkeypatch.delenv("ZOE_PERSON_LLM_PREFILTER", raising=False)
    reached = []

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return None
        async def post(self, *a, **k):
            reached.append(True)
            raise RuntimeError("stop here — reaching the call is the assertion")

    monkeypatch.setattr(pel.httpx, "AsyncClient", _Client)
    out = await pel.process_text_llm("set a timer for ten minutes please", user_id="u1")
    assert out == 0 and reached, "flag OFF must still attempt the LLM call"


@pytest.mark.asyncio
async def test_flag_on_skips_llm_for_non_person_turn(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_LLM_PREFILTER", "1")

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("prefilter must skip BEFORE any HTTP client")

    monkeypatch.setattr(pel.httpx, "AsyncClient", _Boom)
    out = await pel.process_text_llm("set a timer for ten minutes please", user_id="u1")
    assert out == 0


@pytest.mark.asyncio
async def test_flag_on_person_turn_still_reaches_llm(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_LLM_PREFILTER", "1")
    reached = []

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return None
        async def post(self, *a, **k):
            reached.append(True)
            raise RuntimeError("stop here")

    monkeypatch.setattr(pel.httpx, "AsyncClient", _Client)
    await pel.process_text_llm("my sister Sarah is visiting on Friday", user_id="u1")
    assert reached, "a person turn must still fire the LLM with the flag ON"
