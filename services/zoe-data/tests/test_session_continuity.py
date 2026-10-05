"""ZOE_STICKY_SESSION (default ON) — a chat request with no session id continues the last ask.

Live 2026-10-05: the estate ask-box posts /api/chat/ with no ``session_id`` and every request
got a fresh ``web_<8hex>`` session, so "i live here, its good" could not attach to the answer
before it and the follow-up "where do i live" found nothing. Id-less requests now live in their own
``ask_`` namespace and reuse the user's most recent ``ask_`` session inside the window - NEVER a
``web_`` desktop session ("New Chat" mints those), never one whose turn is in flight. Pinned here:
reuse, the window boundary, never across users / shared identities / channels / the desktop
namespace, the busy fallback, explicit ids untouched, the flag, the entry points, and the
harnesses that must keep passing explicit ids.
Fake DB (the SQL's user + prefix filters are honoured); synthetic ids (ci_safe)."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import session_continuity as sc

pytestmark = pytest.mark.ci_safe

NOW = datetime(2026, 10, 5, 0, 30, 0, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[3]


def ts(minutes_ago: float, fmt="pg") -> str:
    t = NOW - timedelta(minutes=minutes_ago)
    if fmt == "pg":  # what Postgres NOW()::text yields
        return t.strftime("%Y-%m-%d %H:%M:%S.%f") + "+00"
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class FakeDB:
    """chat_sessions rows; honours the ``user_id = ?`` and ``substr(id,1,4) = ?`` filters."""

    def __init__(self, rows):
        self.rows = rows  # (id, user_id, updated_at)
        self.calls = 0

    async def execute(self, sql, params=()):
        self.calls += 1
        assert "FROM chat_sessions" in sql and "user_id = ?" in sql
        uid, prefix = params
        got = [{"id": i, "updated_at": u} for i, owner, u in self.rows
               if owner == uid and i.startswith(prefix)]
        return _Cur(sorted(got, key=lambda r: r["updated_at"], reverse=True)[:5])


def resolve(body, user="member-a", db=None, **kw):
    return asyncio.run(sc.resolve_session_id(body, user, now=NOW, db=db, **kw))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ZOE_STICKY_SESSION", raising=False)
    monkeypatch.delenv("ZOE_STICKY_SESSION_MINUTES", raising=False)


# ── the rule ──────────────────────────────────────────────────────────────────

def test_no_id_reuses_the_last_session_inside_the_window():
    db = FakeDB([("ask_aaaa1111", "member-a", ts(3)), ("ask_bbbb2222", "member-a", ts(9))])
    assert resolve({"message": "hi"}, db=db) == "ask_aaaa1111"


def test_boundary_exactly_at_the_window_reuses_one_second_over_mints():
    assert resolve({}, db=FakeDB([("ask_edge0000", "member-a", ts(20))])) == "ask_edge0000"
    sid = resolve({}, db=FakeDB([("ask_edge0000", "member-a", ts(20 + 1 / 60))]))
    assert sid != "ask_edge0000" and re.fullmatch(r"ask_[0-9a-f]{8}", sid)


def test_window_is_configurable(monkeypatch):
    db = FakeDB([("ask_old00000", "member-a", ts(30))])
    assert resolve({}, db=db) != "ask_old00000"
    monkeypatch.setenv("ZOE_STICKY_SESSION_MINUTES", "45")
    assert resolve({}, db=db) == "ask_old00000"
    monkeypatch.setenv("ZOE_STICKY_SESSION_MINUTES", "garbage")  # falls back to 20
    assert resolve({}, db=db) != "ask_old00000"


def test_no_prior_session_mints_an_ask_id():
    sid = resolve({}, db=FakeDB([]))
    assert re.fullmatch(r"ask_[0-9a-f]{8}", sid)


def test_older_timestamp_formats_are_understood():
    assert resolve({}, db=FakeDB([("ask_iso00000", "member-a", ts(5, "iso"))])) == "ask_iso00000"


def test_the_freshest_of_several_wins_even_if_text_order_disagrees():
    rows = [("ask_stale000", "member-a", ts(15, "iso")), ("ask_fresh000", "member-a", ts(2))]
    assert resolve({}, db=FakeDB(rows)) == "ask_fresh000"


# ── scoping ───────────────────────────────────────────────────────────────────

def test_never_across_users():
    db = FakeDB([("ask_theirs00", "member-b", ts(1))])
    sid = resolve({}, user="member-a", db=db)
    assert sid != "ask_theirs00"
    assert resolve({}, user="member-b", db=db) == "ask_theirs00"


@pytest.mark.parametrize("shared", ["guest", "voice-guest", "", "default", "Guest"])
def test_shared_identities_never_inherit_a_session(shared):
    db = FakeDB([("ask_somebody0", shared, ts(1))])
    sid = resolve({}, user=shared, db=db)
    assert sid != "ask_somebody0" and db.calls == 0  # not even looked up


def test_other_channels_sessions_are_not_eligible():
    db = FakeDB([("telegram-123-e1", "member-a", ts(1)), ("voice-panel-x-1", "member-a", ts(1)),
                 ("session_1700000000000", "member-a", ts(1))])
    assert re.fullmatch(r"ask_[0-9a-f]{8}", resolve({}, db=db))


def test_a_non_chat_channel_tag_never_joins_a_web_session():
    db = FakeDB([("ask_aaaa1111", "member-a", ts(1))])
    assert resolve({}, db=db, channel="telegram") != "ask_aaaa1111" and db.calls == 0


def test_a_desktop_new_chat_session_is_never_attached_to():
    """POST /api/chat/sessions/ ("New Chat") mints web_ ids. An id-less ask-box / music-page /
    planner request must not become a turn in that open desktop transcript."""
    db = FakeDB([("web_desktop1", "member-a", ts(1)), ("web_desktop2", "member-a", ts(0.1))])
    sid = resolve({}, db=db)
    assert sid.startswith("ask_") and sid not in {"web_desktop1", "web_desktop2"}
    # ... while the ask namespace still continues next to it
    db.rows.append(("ask_panel001", "member-a", ts(5)))
    assert resolve({}, db=db) == "ask_panel001"


def test_a_session_with_a_turn_in_flight_is_skipped_for_a_fresh_one():
    """locked_chat_stream rejects a 2nd concurrent turn after 5 s with session_busy; the 2nd
    id-less request must get its own session instead."""
    db = FakeDB([("ask_inflight0", "member-a", ts(0.2)), ("ask_idle00000", "member-a", ts(4))])
    busy = {"ask_inflight0"}
    assert resolve({}, db=db, busy=lambda sid: sid in busy) == "ask_idle00000"   # next-best idle one
    busy.add("ask_idle00000")
    sid = resolve({}, db=db, busy=lambda sid: sid in busy)
    assert sid.startswith("ask_") and sid not in busy                            # none free -> mint
    assert resolve({}, db=db, busy=lambda sid: False) == "ask_inflight0"          # no contention -> freshest


def test_the_chat_route_passes_its_lock_probe_as_the_busy_callback():
    assert "busy=lambda sid: _get_session_lock(sid).locked()" in CHAT_PY


# ── explicit ids are untouched ────────────────────────────────────────────────

@pytest.mark.parametrize("explicit", ["bar-s1-abc", "telegram-6308-e1", "session_17", "web_deadbeef", "x"])
def test_explicit_session_id_is_returned_verbatim_without_a_lookup(explicit):
    db = FakeDB([("ask_aaaa1111", "member-a", ts(1))])
    assert resolve({"session_id": explicit}, db=db) == explicit and db.calls == 0


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_ids_count_as_absent(blank):
    db = FakeDB([("ask_aaaa1111", "member-a", ts(1))])
    assert resolve({"session_id": blank}, db=db) == "ask_aaaa1111"


def test_flag_off_mints_exactly_like_before(monkeypatch):
    monkeypatch.setenv("ZOE_STICKY_SESSION", "0")
    db = FakeDB([("ask_aaaa1111", "member-a", ts(1))])
    sid = resolve({}, db=db)
    assert re.fullmatch(r"web_[0-9a-f]{8}", sid) and db.calls == 0


def test_a_lookup_failure_mints_instead_of_failing_the_turn():
    class Boom:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert re.fullmatch(r"ask_[0-9a-f]{8}", resolve({}, db=Boom()))


# ── wiring: the entry points and the harnesses ────────────────────────────────

CHAT_PY = (Path(__file__).resolve().parents[1] / "routers" / "chat.py").read_text()


def test_chat_and_whatsapp_entry_points_use_the_resolver_and_none_mints_inline():
    assert 'body.get("session_id", f"web_' not in CHAT_PY
    assert CHAT_PY.count("await resolve_session_id(") == 2
    assert "channel=req_channel" in CHAT_PY


def test_new_chat_still_mints_unconditionally():
    m = re.search(r'async def create_session\(.*?\n(?=@router)', CHAT_PY, re.S)
    assert m and 'session_id = f"web_{uuid.uuid4().hex[:8]}"' in m.group(0)
    assert "resolve_session_id" not in m.group(0)


def test_every_posting_harness_passes_an_explicit_session_id():
    """The bar / day-sim / parity gates build their own ids, so sticky cannot merge their
    scripted conversations (verified by grep when this rule was written)."""
    for rel in ("scripts/perf/samantha_bar.py", "labs/flue-zoe-brain-2x/parity/security_gate.py",
                "labs/flue-zoe-brain-2x/parity/tool_breadth_gate.py",
                "labs/flue-zoe-brain-2x/parity/reliability_gate.py"):
        src = (REPO / rel).read_text()
        assert re.search(r'"session_id":\s*\w+', src), rel
    assert "session_id: sessionId" in (REPO / "labs/flue-zoe-telegram-2x/src/brain.ts").read_text()


def test_desktop_chat_page_persists_the_session_it_sends():
    html = (REPO / "services/zoe-ui/dist/chat.html").read_text()
    assert "session_id: currentSessionId || (currentSessionId = `session_${Date.now()}`)" in html
    assert "if (!currentSessionId) await createOrGetCurrentSession();" in html
    assert "session_id: currentSessionId || `session_${Date.now()}`" not in html  # the throwaway form


# ── the NON-streaming path takes the per-session lock too ─────────────────────

class _Stop(Exception):
    """Raised by the fake first write so chat() unwinds right after the lock section starts."""


class _Req:
    def __init__(self, body):
        self._body, self.headers = body, {}

    async def json(self):
        return self._body


def _chat_mod():
    return pytest.importorskip("routers.chat")


def _run_overlapping(monkeypatch, bodies):
    """Start len(bodies) non-stream chat() calls that each block inside their first write,
    holding the session lock. Returns (session ids seen at the write, results)."""
    chat_mod = _chat_mod()
    seen: list[str] = []

    async def go():
        release = asyncio.Event()

        async def fake_recent(user_id, **kw):
            # The resolver's busy probe RACES the other request's acquire: both pick the same row.
            return "ask_shared00"

        async def fake_ensure(sid, uid):
            return None

        async def fake_save(sid, role, content, user_id=None, **kw):
            seen.append(sid)
            await release.wait()
            raise _Stop()

        monkeypatch.setattr(sc, "recent_session_id", fake_recent)
        monkeypatch.setattr(chat_mod, "_ensure_user_and_chat_session", fake_ensure)
        monkeypatch.setattr(chat_mod, "_save_chat_message", fake_save)
        chat_mod._SESSION_LOCKS.clear()
        tasks = []
        for b in bodies:
            tasks.append(asyncio.ensure_future(chat_mod.chat(_Req(b), {"user_id": "member-a"}, stream=False)))
            for _ in range(5):  # let it resolve, take the lock and block in the write
                await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        release.set()
        return await asyncio.gather(*tasks, return_exceptions=True)

    return seen, asyncio.run(go())


def test_two_overlapping_nonstream_idless_calls_never_share_a_session(monkeypatch):
    seen, results = _run_overlapping(monkeypatch, [{"message": "one"}, {"message": "two"}])
    assert len(seen) == 2 and seen[0] == "ask_shared00"
    assert seen[1] != seen[0] and re.fullmatch(r"ask_[0-9a-f]{8}", seen[1])  # fresh ask_, no race
    assert all(isinstance(r, _Stop) for r in results)


def test_break_the_fix_control_without_the_lock_both_attach_to_one_session(monkeypatch):
    """Same overlap with the lock section neutralised: both end up in ask_shared00. This is
    the bug the lock fixes, so the test above is measuring the lock."""
    chat_mod = _chat_mod()

    class NoLock:
        def locked(self):
            return False

        async def acquire(self):
            return True

        def release(self):
            return None

    monkeypatch.setattr(chat_mod, "_get_session_lock", lambda sid: NoLock())
    seen, _ = _run_overlapping(monkeypatch, [{"message": "one"}, {"message": "two"}])
    assert seen == ["ask_shared00", "ask_shared00"]


def test_overlapping_nonstream_calls_on_an_explicit_id_serialise_then_answer_busy(monkeypatch):
    chat_mod = _chat_mod()
    monkeypatch.setattr(chat_mod, "_SESSION_LOCK_TIMEOUT_S", 0.05)
    seen, results = _run_overlapping(monkeypatch, [{"message": "one", "session_id": "bar-s1"},
                                                   {"message": "two", "session_id": "bar-s1"}])
    assert seen == ["bar-s1"]  # the 2nd never reached its write
    assert isinstance(results[0], _Stop)
    assert results[1]["code"] == "session_busy" and results[1]["session_id"] == "bar-s1"


def test_the_lock_is_released_so_sequential_idless_calls_keep_the_same_session(monkeypatch):
    chat_mod = _chat_mod()
    seen, results = _run_overlapping(monkeypatch, [{"message": "one"}])
    assert isinstance(results[0], _Stop) and not chat_mod._get_session_lock("ask_shared00").locked()
    seen2, _ = _run_overlapping(monkeypatch, [{"message": "two"}])
    assert seen2 == ["ask_shared00"]  # nothing in flight: continuity unchanged
