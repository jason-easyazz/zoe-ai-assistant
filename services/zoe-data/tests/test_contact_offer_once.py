"""Contact offers are not robotic: at most ONE per turn, none on forget /
correction / decline turns, and the same offer is not repeated on the next turns.

Not `ci_safe`: importing routers.memories pulls memory_service (full-dir lane).
"""
import pytest

import pending_suggestions
import routers.memories as mem


@pytest.fixture(autouse=True)
def _clear_offer_state():
    mem._OFFER_STATE.clear()
    yield
    mem._OFFER_STATE.clear()


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
    for i in range(1, mem._OFFER_COOLDOWN_TURNS + 1):
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
    monkeypatch.setattr(mem, "_OFFER_SAME_TURN_S", -1.0)  # window elapsed
    p = await _fold("u1", "okay")
    assert "Alder" not in p and "Birch" in p


@pytest.mark.asyncio
async def test_state_is_lru_and_bounded(monkeypatch):
    _two(monkeypatch)
    monkeypatch.setattr(mem, "_OFFER_STATE_MAX_USERS", 2)
    await _fold("a", "x")
    await _fold("b", "x")
    await _fold("a", "y")   # a is now most recent
    await _fold("c", "x")   # evicts b, not a
    assert set(mem._OFFER_STATE) == {"a", "c"}
