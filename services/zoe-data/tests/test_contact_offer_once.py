"""Contact offers are not robotic: at most ONE per turn, none on forget /
correction / decline turns, and the same offer is not repeated on the next turns.

Not `ci_safe`: importing routers.memories pulls memory_service (full-dir lane).
"""
import pytest

import pending_suggestions
import contact_offer_policy as cop
import routers.memories as mem


@pytest.fixture(autouse=True)
def _clear_offer_state(monkeypatch):
    # cadence / one-per-turn / cooldown are tested here with arbitrary messages; relatedness has its own file
    # (test_contact_offer_standing_decline.py)
    monkeypatch.setattr(cop, "related", lambda message, offer: True)
    cop._STATE.clear()
    yield
    cop._STATE.clear()


def _base():
    return {"packet": "## What I know about you\n- likes tea [mem]", "refs": [], "count": 0}


def _two(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    monkeypatch.delenv("ZOE_CONTACT_OFFER_BATCH", raising=False)

    async def fake_surface(user_id, *, limit=3):
        return [{"id": "a", "name": "Alder", "relationship": "husband"},
                {"id": "b", "name": "Birch", "relationship": None}][:limit]
    monkeypatch.setattr(pending_suggestions, "surface_pending_contacts_for_prompt", fake_surface)


async def _fold(user, msg):
    return (await mem._fold_pending_contact_offers(_base(), user, msg))["packet"]


@pytest.mark.asyncio
async def test_at_most_one_offer_per_turn(monkeypatch):
    _two(monkeypatch)
    pkt = await _fold("u1", "what's the weather")
    assert pkt.count("[pending-contact]") == 1 and "Alder" in pkt and "Birch" not in pkt


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", [
    "no not right now, just checking", "nope", "forget that", "never mind",
    "that's not right, I meant Cedar", "No, it was my brother", "not now", "no thanks",
    "please no", "thanks, but no", "I'd rather not", "maybe later", "not today", "skip it",
    "actually, her name is Cedar", "you misheard me",
])
async def test_no_offer_on_forget_correction_decline(monkeypatch, msg):
    _two(monkeypatch)
    assert await _fold("u1", msg) == _base()["packet"]


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", ["what about Alder?", "do I have any alarms", "no problem, thanks",
                                 "nothing to forget here", "Stop by the pharmacy",
                                 "no kidding", "no way, that's great", "Forget-me-nots are lovely"])
async def test_ordinary_turns_still_offer(monkeypatch, msg):
    """Negative control: the suppressor is not a blanket off-switch."""
    _two(monkeypatch)
    assert "[pending-contact]" in await _fold("u1", msg)


@pytest.mark.asyncio
async def test_same_offer_not_repeated_next_turn(monkeypatch):
    _two(monkeypatch)
    assert "Alder" in await _fold("u1", "turn one")
    assert "Alder" in await _fold("u1", "turn one")  # same turn rebuilt: still there
    p2 = await _fold("u1", "turn two")
    assert "Alder" not in p2 and "Birch" in p2        # next offer in line, not a repeat
    assert "[pending-contact]" not in await _fold("u1", "turn three")  # both cooling down
    assert "Alder" in await _fold("u2", "turn two")   # per-user


@pytest.mark.asyncio
async def test_offer_returns_after_cooldown(monkeypatch):
    _two(monkeypatch)
    assert "Alder" in await _fold("u1", "t0")
    for i in range(1, cop.COOLDOWN_TURNS + 1):
        assert "Alder" not in await _fold("u1", f"t{i}")  # every intervening turn
    assert "Alder" in await _fold("u1", "later")


@pytest.mark.asyncio
async def test_suppressed_turns_count_toward_cooldown(monkeypatch):
    _two(monkeypatch)
    assert "Alder" in await _fold("u1", "t0")
    for m in ("nope", "forget that", "not now"):
        await _fold("u1", m)
    assert "Alder" in await _fold("u1", "later")  # 3 suppressed turns elapsed


@pytest.mark.asyncio
async def test_identical_text_is_a_new_turn_after_the_window(monkeypatch):
    _two(monkeypatch)
    assert "Alder" in await _fold("u1", "okay")
    assert "Alder" in await _fold("u1", "okay")  # rebuild inside the window
    monkeypatch.setattr(cop, "SAME_TURN_S", -1.0)  # window elapsed
    p = await _fold("u1", "okay")
    assert "Alder" not in p and "Birch" in p


@pytest.mark.asyncio
async def test_state_is_lru_and_bounded(monkeypatch):
    _two(monkeypatch)
    monkeypatch.setattr(cop, "MAX_USERS", 2)
    await _fold("a", "x")
    await _fold("b", "x")
    await _fold("a", "y")   # a is now most recent
    await _fold("c", "x")   # evicts b, not a
    assert set(cop._STATE) == {"a", "c"}


@pytest.mark.asyncio
async def test_flue_fallback_follows_the_same_policy(monkeypatch):
    import zoe_flue_client as zf

    _two(monkeypatch)
    monkeypatch.setenv("ZOE_SEAM_OFFER_INJECT", "1")
    b1 = await zf._pending_offer_block("u1", "turn one")
    assert "Alder" in b1 and "Birch" not in b1               # one offer
    b2 = await zf._pending_offer_block("u1", "turn two")
    assert "Alder" not in b2 and "Birch" in b2               # no repeat
    assert await zf._pending_offer_block("u1", "turn three") == ""  # both cooling down
    assert await zf._pending_offer_block("u1", "forget that") == ""  # decline turn


@pytest.fixture(autouse=True)
def _previous_reply_made_the_offer(monkeypatch):
    # a bare yes/ok binds only when Zoe's previous reply was the offer (QA round 5); these tests are about WHICH offer
    import intent_router

    async def prev(_uid):
        return "Would you like me to add Alder Birch Caitlin Marcus Rodrigo as a contact?"

    monkeypatch.setattr(intent_router, "_previous_assistant_message", prev)


@pytest.mark.asyncio
async def test_bare_yes_binds_to_the_offer_voiced_not_the_oldest(monkeypatch):
    import intent_router

    _two(monkeypatch)
    await _fold("u1", "turn one")   # voices Alder
    await _fold("u1", "turn two")   # voices Birch (Alder cooling down)

    async def surfaced(user_id, *, limit=5):
        return [{"id": "a", "name": "Alder", "relationship": "husband"},
                {"id": "b", "name": "Birch", "relationship": None}]
    monkeypatch.setattr(pending_suggestions, "surfaced_person_offers", surfaced)
    intent = await intent_router._match_pending_offer_reply("yes", "u1")
    assert intent is not None and intent.slots["suggestion_id"] == "b"


@pytest.mark.asyncio
async def test_unoffered_rows_are_unmarked(monkeypatch):
    _two(monkeypatch)
    seen = []

    async def fake_unmark(user_id, pend, offered):
        seen.append(([p["id"] for p in pend], [p["id"] for p in offered]))
    monkeypatch.setattr(cop, "unmark_unoffered", fake_unmark)
    await _fold("u1", "turn one")
    assert seen == [(["a", "b"], ["a"])]  # only Alder entered the prompt


def test_tool_call_param_exists_and_defaults_off():
    # a brain tool call (recall_memory) passes tool_call=1: its model-made query must not tick the cooldown or carry an offer
    import inspect
    d = inspect.signature(mem.memory_for_prompt).parameters["tool_call"].default
    assert getattr(d, "default", d) is False
