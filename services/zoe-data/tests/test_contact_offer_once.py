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
])
async def test_no_offer_on_forget_correction_decline(monkeypatch, msg):
    _two(monkeypatch)
    assert await _fold("u1", msg) == _base()["packet"]


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", ["what about Alder?", "do I have any alarms", "no problem, thanks",
                                 "nothing to forget here"])
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
    await _fold("u1", "t0")
    for i in range(1, mem._OFFER_COOLDOWN_TURNS + 1):
        await _fold("u1", f"t{i}")
    assert "Alder" in await _fold("u1", "later")
