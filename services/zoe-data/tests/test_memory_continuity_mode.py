"""memory_for_prompt(mode="continuity") — recent-first packet for mood statements.

Samantha bar S4: a day-2 mood statement ("I've been feeling a bit on edge
today") shares almost no words with the day-1 worry, so relevance ranking
buries it. Continuity mode pins what was captured in the last few days ahead
of relevance — emotional rows first — capped like every packet. Relevance mode
(every other caller, including an in-process caller that omits ``mode``) is
unchanged.
"""
from __future__ import annotations

import datetime

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import routers.memories as memories
from memory_service import MemoryRef

ASK = "Ugh, I've been feeling a bit on edge today."
NOW = datetime.datetime.now(datetime.timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - datetime.timedelta(hours=hours_ago)).isoformat()


def _ref(rid, text, hours_ago, memory_type="fact"):
    return MemoryRef(
        id=rid,
        text=text,
        metadata={"status": "approved", "memory_type": memory_type, "added_at": _iso(hours_ago)},
    )


# An OLD fact that is lexically much closer to the ask (on edge / today) — it is
# what relevance ranking leads with (top of load_for_prompt, top search hit).
OLD_CLOSE = _ref("old00001", "User gets on edge when the neighbour's dog barks all day", 24 * 20)
# Yesterday's worry: little lexical overlap with the ask.
WORRY = _ref("worry001", "User is anxious about a job interview at the aquarium on Friday", 24)
OTHER_RECENT = _ref("recent01", "User's sister Marisol is flying in from Lisbon", 20)


class _Svc:
    def __init__(self, rows, hits):
        self.rows, self.hits, self.load_limits = rows, hits, []

    async def load_for_prompt(self, user_id, *, limit):
        self.load_limits.append(limit)
        return self.rows[:limit]

    async def search(self, query, *, user_id, limit=6, **_):
        return list(self.hits)


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.delenv("ZOE_EMOTIONAL_RECALL_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_MEMORY_COMPOSE_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_PERSON_SUGGEST_ENABLED", raising=False)
    s = _Svc(rows=[OLD_CLOSE, OTHER_RECENT, WORRY], hits=[OLD_CLOSE])
    monkeypatch.setattr(memories, "_svc", lambda: s)
    return s


async def _packet(mode, limit=memories._PROMPT_PACKET_MAX_FACTS, **kw):
    args = dict(user_id="demo-a", message=ASK, limit=limit, _=None)
    if mode is not None:
        args["mode"] = mode
    args.update(kw)
    return await memories.memory_for_prompt(**args)


@pytest.mark.asyncio
async def test_continuity_ranks_recent_emotional_fact_above_older_closer_fact(svc):
    res = await _packet("continuity")
    p = res["packet"]
    assert WORRY.text in p and OLD_CLOSE.text in p
    assert p.index(WORRY.text) < p.index(OLD_CLOSE.text)
    # the emotional recent row leads the other recent row too
    assert p.index(WORRY.text) < p.index(OTHER_RECENT.text)
    worry_line = next(ln for ln in p.splitlines() if WORRY.text in ln)
    assert worry_line.startswith("- (recent) ")
    assert res["refs"][0]["id"] == WORRY.id and res["refs"][0].get("recent") is True
    # continuity reads the wide window so a crowded-out recent row is visible
    assert svc.load_limits == [memories._EMO_PIN_SCAN]


@pytest.mark.parametrize("mode", ["relevance", None])
@pytest.mark.asyncio
async def test_relevance_mode_and_omitted_mode_are_unchanged(svc, mode):
    """Relevance leads with the lexically close hit, no (recent) tags — and an
    in-process caller that omits ``mode`` (zoe_core_client) gets exactly that."""
    res = await _packet(mode)
    p = res["packet"]
    assert p.index(OLD_CLOSE.text) < p.index(WORRY.text)
    assert "(recent)" not in p
    assert all("recent" not in r for r in res["refs"])


@pytest.mark.asyncio
async def test_rows_outside_the_window_are_not_pinned(svc):
    svc.rows = [OLD_CLOSE, _ref("stale001", "User was anxious about a dentist visit", 24 * 5)]
    svc.hits = []
    p = (await _packet("continuity"))["packet"]
    assert "(recent)" not in p


@pytest.mark.asyncio
async def test_continuity_cap_respected(svc):
    svc.rows = [_ref(f"r{i:07d}", f"recent distinct thing number {i} about topic{i}", 1 + i) for i in range(30)]
    svc.hits = []
    res = await _packet("continuity", limit=12)
    assert res["count"] <= 12
    recent = [r for r in res["refs"] if r.get("recent")]
    assert len(recent) == memories._CONTINUITY_RECENT_MAX
    # newest first among equally non-emotional rows
    assert [r["id"] for r in recent] == [f"r{i:07d}" for i in range(memories._CONTINUITY_RECENT_MAX)]


def test_pick_recent_emotional_first_then_newest():
    now = NOW.timestamp()
    rows = [
        _ref("plain_new", "User bought a whale mug", 1),
        _ref("emo_old", "User felt lonely after the move", 60, memory_type="emotional_moment"),
        _ref("plain_old", "User booked a haircut", 50),
        MemoryRef(id="undated", text="User is worried about rent", metadata={"status": "approved"}),
    ]
    got = [r.id for r in memories._pick_recent_for_continuity(rows, now_ts=now)]
    assert got == ["emo_old", "plain_new", "plain_old"]  # undated never qualifies
