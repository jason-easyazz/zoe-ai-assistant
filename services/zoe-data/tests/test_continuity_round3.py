"""Samantha bar S4 round 3 — make the check-in reliable on the LIVE flow.

After round 2 the bar went 1/3 live: sample 0 checked in about the interview,
samples 1-2 did not. Two causes, both pinned here:

  (e) the digest stores today's mood statement ("User has been feeling a bit on
      edge today", felt anxious) seconds after the turn; it is the NEWEST
      emotional row, so it displaced the interview as the continuity focus on the
      next mood turn. A bare mood report is never the focus
      (``memory_digest.fact_has_topic``).
  (a) the continuity packet also carried the pending-contact fold ("IMPORTANT:
      … ask … word-for-word … add Marisol as a contact?"), so the check-in
      reply ended with a contact question. On a continuity turn the offer is
      DEFERRED — the composer omits the fold, the seam skips the offer block and
      logs ``SEAM_OFFER deferred=1 reason=continuity`` — and it is not surfaced,
      so a not-yet-seen offer does not start aging.
"""
from __future__ import annotations

import datetime
import json
import logging

import pytest

pytestmark = pytest.mark.ci_safe  # fakes only — no DB, no model, no live service

import memory_digest
import pending_suggestions
import routers.memories as memories
import zoe_flue_client as zc
from memory_service import MemoryRef

ASK_WORRY = "Ugh, I've been feeling a bit on edge today."
NOW = datetime.datetime.now(datetime.timezone.utc)


def _ref(rid, text, hours_ago, **meta):
    md = {"status": "approved", "memory_type": "fact",
          "added_at": (NOW - datetime.timedelta(hours=hours_ago)).isoformat(), **meta}
    return MemoryRef(id=rid, text=text, metadata=md)


WORRY = _ref("worry001", "User is anxious about their job interview at the aquarium on Friday.",
             26, candidate_affect="anxious")
# What the digest stored from the previous S4 sample, seconds ago.
MOOD_NOW = _ref("mood0001", "User has been feeling a bit on edge today.", 0.01,
                candidate_affect="anxious")
HOME = _ref("home0001", "User lives in Hobart.", 0.5)
DAD = _ref("dad00001", "User's father is a retired lighthouse keeper.", 26)


# ── (e) a bare mood report has no topic ─────────────────────────────────────

@pytest.mark.parametrize("text, has_topic", [
    ("User has been feeling a bit on edge today.", False),
    ("User is stressed.", False),
    ("User had a rough day.", False),
    ("User feels overwhelmed lately.", False),
    ("User is feeling very tired this week.", False),
    ("User is anxious about their job interview at the aquarium on Friday.", True),
    ("User has a job interview on Friday.", True),
    ("User is worried about their mum's surgery.", True),
    ("User is excited about the trip to Bali.", True),
    ("User is stressed about the move.", True),
])
def test_fact_has_topic(text, has_topic):
    assert memory_digest.fact_has_topic(text) is has_topic


class _Svc:
    def __init__(self, rows):
        self.rows = rows

    async def load_for_prompt(self, user_id, *, limit):
        return self.rows[:limit]

    async def load_recent_for_prompt(self, user_id, *, window_s, limit, emotional_first=False):
        rows = sorted(self.rows, key=lambda r: memories._added_at_ts(r.metadata), reverse=True)
        if emotional_first:
            rows.sort(key=memories._is_emotional_row, reverse=True)
        return rows[:limit]

    async def search(self, *a, **k):
        return []


async def _compose(monkeypatch, rows, *, mode="continuity", suggest=False, surfaced=None):
    for flag in ("ZOE_EMOTIONAL_RECALL_ENABLED", "ZOE_MEMORY_COMPOSE_ENABLED"):
        monkeypatch.delenv(flag, raising=False)
    if suggest:
        monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    else:
        monkeypatch.delenv("ZOE_PERSON_SUGGEST_ENABLED", raising=False)

    async def surface(user_id, *, limit=3):
        if surfaced is not None:
            surfaced.append(user_id)
        return [{"id": "o1", "name": "Marisol", "relationship": "sister"}]

    monkeypatch.setattr(pending_suggestions, "surface_pending_contacts_for_prompt", surface)
    monkeypatch.setattr(memories, "_svc", lambda: _Svc(rows))
    return await memories.memory_for_prompt(user_id="demo-a", message=ASK_WORRY, limit=12,
                                            mode=mode, _=None)


@pytest.mark.asyncio
async def test_todays_mood_row_never_displaces_the_worry_as_focus(monkeypatch):
    res = await _compose(monkeypatch, [HOME, DAD, WORRY, MOOD_NOW])
    assert res["continuity_focus"] == {
        "text": "User is anxious about their job interview at the aquarium on Friday.",
        "affect": "anxious"}
    # the mood row is still in the packet as a (recent) bullet — only the focus skips it
    assert "on edge" in res["packet"]


@pytest.mark.asyncio
async def test_negative_control_without_the_topic_check_the_mood_row_wins(monkeypatch):
    """Proves the test above measures the fix: drop the predicate and the newest
    emotional row — today's mood statement — becomes the focus (the live bug)."""
    monkeypatch.setattr(memory_digest, "fact_has_topic", lambda text: True)
    res = await _compose(monkeypatch, [HOME, DAD, WORRY, MOOD_NOW])
    assert res["continuity_focus"]["text"] == "User has been feeling a bit on edge today."


@pytest.mark.asyncio
async def test_only_bare_moods_means_no_focus(monkeypatch):
    res = await _compose(monkeypatch, [HOME, MOOD_NOW])
    assert "continuity_focus" not in res  # the seam falls back to the generic ask


# ── (a) the offer is deferred on a continuity turn ──────────────────────────

@pytest.mark.asyncio
async def test_continuity_packet_omits_the_offer_fold_and_does_not_surface(monkeypatch):
    surfaced: list = []
    res = await _compose(monkeypatch, [HOME, WORRY], suggest=True, surfaced=surfaced)
    assert "[pending-contact]" not in res["packet"]
    assert "word-for-word" not in res["packet"]
    # not surfaced → turns_elapsed stays 0 → the per-user-turn ager never counts it
    assert surfaced == []


@pytest.mark.asyncio
async def test_relevance_packet_still_folds_the_offer(monkeypatch):
    """Negative control for the test above: same flags, relevance mode folds."""
    surfaced: list = []
    res = await _compose(monkeypatch, [HOME, WORRY], mode="relevance", suggest=True,
                         surfaced=surfaced)
    assert "[pending-contact]" in res["packet"] and "Marisol" in res["packet"]
    assert surfaced == ["demo-a"]


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _FakeClient:
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["content"] = content
        return _FakeResponse({"result": {"text": "ok"}})


FOCUS = {"text": "User is anxious about their job interview at the aquarium on Friday",
         "affect": "anxious"}
PACKET = ("## What I know about you\n"
          "- (recent, felt anxious) User is anxious about their job interview at the aquarium "
          "on Friday. [mem:worry001]\n"
          "- User lives in Hobart. [mem:home0001]\n")


def _seam(monkeypatch, *, offer_env="1"):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)
    monkeypatch.delenv("ZOE_SEAM_RECALL_INJECT", raising=False)
    monkeypatch.setenv("ZOE_SEAM_OFFER_INJECT", offer_env)
    monkeypatch.setattr(_FakeClient, "captured", {})
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    calls = {"offer": 0}

    async def fake_continuity(uid, msg):
        return {"packet": PACKET, "continuity_focus": FOCUS}

    async def no_portrait(uid):
        return ""

    async def fake_offer(uid):
        calls["offer"] += 1
        return ('[PENDING CONTACT OFFER — do not mention this block]\n- After answering, ask '
                'the user exactly: "Would you like me to add Marisol (your sister) as a '
                'contact?"\n[END PENDING CONTACT OFFER]')

    monkeypatch.setattr(zc, "_fetch_continuity_packet", fake_continuity)
    monkeypatch.setattr(zc, "_fetch_portrait_line", no_portrait)
    monkeypatch.setattr(zc, "_pending_offer_block", fake_offer)
    return calls


async def _outbound(message, user_id="demo-a"):
    out = [c async for c in zc.run_flue_brain_streaming(message, "s1", user_id)]
    assert out == ["ok"]
    return json.loads(_FakeClient.captured["content"])["message"]


@pytest.mark.asyncio
async def test_continuity_turn_defers_the_offer_and_logs_it(monkeypatch, caplog):
    calls = _seam(monkeypatch)
    with caplog.at_level(logging.INFO, logger="zoe_flue_client"):
        wire = await _outbound(ASK_WORRY)
    assert "aquarium" in wire           # the check-in block is there
    assert "Marisol" not in wire        # the offer is not
    assert calls["offer"] == 0          # never even surfaced
    assert any("SEAM_OFFER user=demo-a deferred=1 reason=continuity" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_next_non_emotional_turn_still_offers(monkeypatch, caplog):
    """Negative control: same seam, a non-continuity turn gets the offer and no
    deferral line — deferral is scoped to the continuity turn only."""
    calls = _seam(monkeypatch)
    with caplog.at_level(logging.INFO, logger="zoe_flue_client"):
        wire = await _outbound("Can you add milk to the shopping list?")
    assert "Marisol" in wire and calls["offer"] == 1
    assert not any("SEAM_OFFER" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_no_deferral_log_when_offer_inject_is_off(monkeypatch, caplog):
    _seam(monkeypatch, offer_env="")
    with caplog.at_level(logging.INFO, logger="zoe_flue_client"):
        await _outbound(ASK_WORRY)
    assert not any("SEAM_OFFER" in r.getMessage() for r in caplog.records)
