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
import time

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
    """Ranked read = `rows` in the given (score) order, like load_for_prompt;
    recency read = the same rows filtered to the window, newest first."""

    def __init__(self, rows, hits):
        self.rows, self.hits, self.load_limits, self.recent_calls = rows, hits, [], []

    async def load_for_prompt(self, user_id, *, limit):
        self.load_limits.append(limit)
        return self.rows[:limit]

    async def load_recent_for_prompt(self, user_id, *, window_s, limit, emotional_first=False):
        self.recent_calls.append((window_s, limit, emotional_first))
        cutoff = NOW.timestamp() - window_s
        dated = [(memories._added_at_ts(r.metadata), r) for r in self.rows]
        dated = [(ts, r) for ts, r in dated if ts >= cutoff]
        if emotional_first:
            dated.sort(key=lambda p: (memories._is_emotional_row(p[1]), p[0]), reverse=True)
        else:
            dated.sort(key=lambda p: p[0], reverse=True)
        return [r for _, r in dated[:limit]]

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
    # continuity reads recency DIRECTLY (bounded), not a wider ranked prefix
    assert svc.recent_calls == [
        (memories._CONTINUITY_RECENT_WINDOW_S, memories._CONTINUITY_RECENT_SCAN, True)
    ]
    assert svc.load_limits == [memories._PROMPT_PACKET_MAX_FACTS]


@pytest.mark.asyncio
async def test_recent_worry_survives_a_crowded_ranked_read(svc):
    """250 older, higher-scoring memories fill any ranked prefix (the old 200-row
    scan); yesterday's worry must still reach the packet via the recency read."""
    crowd = [_ref(f"old{i:05d}", f"long-standing distinct fact {i} about hobby{i}", 24 * (10 + i))
             for i in range(250)]
    svc.rows = crowd + [WORRY]  # ranked order: the worry is LAST, past 200
    svc.hits = []
    p = (await _packet("continuity"))["packet"]
    assert WORRY.text in p
    assert next(ln for ln in p.splitlines() if WORRY.text in ln).startswith("- (recent) ")


@pytest.mark.asyncio
async def test_recency_read_failure_falls_back_to_ranked_rows(svc):
    async def boom(*a, **k):
        raise RuntimeError("store down")

    svc.load_recent_for_prompt = boom
    p = (await _packet("continuity"))["packet"]
    # the ranked prefix (12 rows) still holds the worry here, so it is still pinned
    assert next(ln for ln in p.splitlines() if WORRY.text in ln).startswith("- (recent) ")


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


# ── MemoryService.load_recent_for_prompt against the REAL service logic ─────

def _matches(meta, where):
    """The subset of chroma `where` semantics these reads use: $and/$or, field
    equality, and $gte on a numeric field (a missing field never matches)."""
    if where is None:
        return True
    if "$and" in where:
        return all(_matches(meta, w) for w in where["$and"])
    if "$or" in where:
        return any(_matches(meta, w) for w in where["$or"])
    (key, cond), = where.items()
    if isinstance(cond, dict):
        (op, val), = cond.items()
        assert op == "$gte" and isinstance(val, (int, float)), cond  # chroma: numbers only
        v = meta.get(key)
        return isinstance(v, (int, float)) and v >= val
    return meta.get(key) == cond


class _Col:
    """A fake collection honouring `where` + `limit` and recording every get."""

    def __init__(self, rows):
        self.rows = rows  # [(id, text, meta)]
        self.gets = []

    def get(self, *, where=None, include=None, limit=None, **_kw):
        self.gets.append({"where": where, "limit": limit})
        hit = [r for r in self.rows if _matches(r[2], where)]
        if limit is not None:
            hit = hit[:limit]
        return {"ids": [r[0] for r in hit],
                "documents": [r[1] for r in hit],
                "metadatas": [dict(r[2]) for r in hit]}


def _svc_over(rows):
    from memory_service import MemoryService

    svc = MemoryService(data_dir="/nonexistent/zoe-test-continuity")
    col = _Col(rows)
    svc._collection = lambda: col
    return svc, col


def _meta(hours_ago, *, legacy=False, **kw):
    m = {"user_id": "demo-a", "status": "approved", "added_at": _iso(hours_ago),
         "confidence": 0.7, **kw}
    if not legacy:
        m["added_ts"] = (NOW - datetime.timedelta(hours=hours_ago)).timestamp()
    return m


WINDOW = 72 * 3600


@pytest.mark.asyncio
async def test_service_recent_read_pushes_time_bound_and_cap_into_the_store_query():
    rows = [(f"old{i:04d}", f"old high-score fact {i}", _meta(24 * 30, confidence=1.0, access_count=500))
            for i in range(250)]
    rows += [
        ("worry001", WORRY.text, _meta(24)),
        ("newest01", "User bought a whale mug", _meta(2)),
        ("stale001", "User was worried about rent", _meta(24 * 4)),
        ("other001", "someone else's recent fact", {**_meta(1), "user_id": "demo-b", "wing": "demo-b"}),
    ]
    svc, col = _svc_over(rows)
    # the service reads the REAL clock; bracket its call with this test's own
    # clock reads (the module-level NOW is taken at import/collection, which a
    # full-lane run reaches minutes before this test executes)
    t_before = time.time()
    got = await svc.load_recent_for_prompt("demo-a", window_s=WINDOW, limit=50)
    t_after = time.time()
    assert [r.id for r in got] == ["newest01", "worry001"]
    # ONE bounded query: scope AND added_ts >= cutoff, with a hard limit — never a full fetch
    import memory_service as ms

    assert len(col.gets) == 1
    q = col.gets[0]
    assert q["limit"] == ms._RECENT_SCAN_CAP
    clauses = q["where"]["$and"]
    ts_clause = next(c for c in clauses if "added_ts" in c)
    cutoff = ts_clause["added_ts"]["$gte"]
    assert t_before - WINDOW - 1 <= cutoff <= t_after - WINDOW + 1
    # …and the ranked read (unchanged) buries the worry past a 200-row prefix
    ranked = await svc.load_for_prompt("demo-a", limit=200)
    assert "worry001" not in {r.id for r in ranked}
    assert await svc.load_recent_for_prompt("guest", window_s=WINDOW, limit=50) == []


@pytest.mark.asyncio
async def test_service_recent_read_keeps_an_emotional_row_under_a_burst_of_newer_rows():
    rows = [("worry001", WORRY.text, _meta(30))]
    rows += [(f"new{i:04d}", f"ordinary capture number {i} about chore{i}", _meta(29 - i * 0.2))
             for i in range(120)]
    svc, _ = _svc_over(rows)
    got = await svc.load_recent_for_prompt("demo-a", window_s=WINDOW, limit=50, emotional_first=True)
    assert len(got) == 50 and got[0].id == "worry001"
    # without emotional-first it is newest-first, and the worry is past the cut
    plain = await svc.load_recent_for_prompt("demo-a", window_s=WINDOW, limit=50)
    assert "worry001" not in {r.id for r in plain}


@pytest.mark.asyncio
async def test_service_recent_read_falls_back_for_rows_without_added_ts():
    rows = [
        ("legacy01", WORRY.text, _meta(24, legacy=True)),
        ("legacy02", "User booked a haircut", _meta(24 * 5, legacy=True)),
    ]
    svc, col = _svc_over(rows)
    got = await svc.load_recent_for_prompt("demo-a", window_s=WINDOW, limit=50, emotional_first=True)
    assert [r.id for r in got] == ["legacy01"]
    assert len(col.gets) == 2 and col.gets[1]["limit"] is None  # bounded miss → legacy scan


def test_build_metadata_stamps_numeric_added_ts_matching_added_at():
    from memory_service import MemoryService

    md = MemoryService._build_metadata(
        user_id="demo-a", source="chat", session_id=None, user_turn_id=None,
        memory_type="fact", confidence=0.7, status="approved", tags=[],
        entity_type=None, entity_id=None, expires_at=None,
    )
    added = datetime.datetime.fromisoformat(md["added_at"].replace("Z", "+00:00"))
    assert isinstance(md["added_ts"], float)
    assert abs(md["added_ts"] - added.timestamp()) < 1e-3
