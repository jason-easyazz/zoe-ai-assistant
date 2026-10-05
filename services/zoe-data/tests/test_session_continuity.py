"""ZOE_STICKY_SESSION (default ON) — a chat request with no session id continues the last chat.

Live 2026-10-05: the estate ask-box posts /api/chat/ with no ``session_id`` and every request
got a fresh ``web_<8hex>`` session, so "i live here, its good" could not attach to the answer
before it and the follow-up "where do i live" found nothing. The server now reuses the user's
most recent web session when it is inside the window. Pinned here: reuse, the window boundary,
never across users / for shared identities / across channels, explicit ids untouched, the
flag, the three entry points, and the harnesses that must keep passing explicit ids.
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
    db = FakeDB([("web_aaaa1111", "member-a", ts(3)), ("web_bbbb2222", "member-a", ts(9))])
    assert resolve({"message": "hi"}, db=db) == "web_aaaa1111"


def test_boundary_exactly_at_the_window_reuses_one_second_over_mints():
    assert resolve({}, db=FakeDB([("web_edge0000", "member-a", ts(20))])) == "web_edge0000"
    sid = resolve({}, db=FakeDB([("web_edge0000", "member-a", ts(20 + 1 / 60))]))
    assert sid != "web_edge0000" and re.fullmatch(r"web_[0-9a-f]{8}", sid)


def test_window_is_configurable(monkeypatch):
    db = FakeDB([("web_old00000", "member-a", ts(30))])
    assert resolve({}, db=db) != "web_old00000"
    monkeypatch.setenv("ZOE_STICKY_SESSION_MINUTES", "45")
    assert resolve({}, db=db) == "web_old00000"
    monkeypatch.setenv("ZOE_STICKY_SESSION_MINUTES", "garbage")  # falls back to 20
    assert resolve({}, db=db) != "web_old00000"


def test_no_prior_session_mints_a_web_id():
    sid = resolve({}, db=FakeDB([]))
    assert re.fullmatch(r"web_[0-9a-f]{8}", sid)


def test_older_timestamp_formats_are_understood():
    assert resolve({}, db=FakeDB([("web_iso00000", "member-a", ts(5, "iso"))])) == "web_iso00000"


def test_the_freshest_of_several_wins_even_if_text_order_disagrees():
    rows = [("web_stale000", "member-a", ts(15, "iso")), ("web_fresh000", "member-a", ts(2))]
    assert resolve({}, db=FakeDB(rows)) == "web_fresh000"


# ── scoping ───────────────────────────────────────────────────────────────────

def test_never_across_users():
    db = FakeDB([("web_theirs00", "member-b", ts(1))])
    sid = resolve({}, user="member-a", db=db)
    assert sid != "web_theirs00"
    assert resolve({}, user="member-b", db=db) == "web_theirs00"


@pytest.mark.parametrize("shared", ["guest", "voice-guest", "", "default", "Guest"])
def test_shared_identities_never_inherit_a_session(shared):
    db = FakeDB([("web_somebody0", shared, ts(1))])
    sid = resolve({}, user=shared, db=db)
    assert sid != "web_somebody0" and db.calls == 0  # not even looked up


def test_other_channels_sessions_are_not_eligible():
    db = FakeDB([("telegram-123-e1", "member-a", ts(1)), ("voice-panel-x-1", "member-a", ts(1)),
                 ("session_1700000000000", "member-a", ts(1))])
    assert re.fullmatch(r"web_[0-9a-f]{8}", resolve({}, db=db))


def test_a_non_chat_channel_tag_never_joins_a_web_session():
    db = FakeDB([("web_aaaa1111", "member-a", ts(1))])
    assert resolve({}, db=db, channel="telegram") != "web_aaaa1111" and db.calls == 0


# ── explicit ids are untouched ────────────────────────────────────────────────

@pytest.mark.parametrize("explicit", ["bar-s1-abc", "telegram-6308-e1", "session_17", "web_deadbeef", "x"])
def test_explicit_session_id_is_returned_verbatim_without_a_lookup(explicit):
    db = FakeDB([("web_aaaa1111", "member-a", ts(1))])
    assert resolve({"session_id": explicit}, db=db) == explicit and db.calls == 0


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_ids_count_as_absent(blank):
    db = FakeDB([("web_aaaa1111", "member-a", ts(1))])
    assert resolve({"session_id": blank}, db=db) == "web_aaaa1111"


def test_flag_off_mints_like_before(monkeypatch):
    monkeypatch.setenv("ZOE_STICKY_SESSION", "0")
    db = FakeDB([("web_aaaa1111", "member-a", ts(1))])
    assert resolve({}, db=db) != "web_aaaa1111" and db.calls == 0


def test_a_lookup_failure_mints_instead_of_failing_the_turn():
    class Boom:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert re.fullmatch(r"web_[0-9a-f]{8}", resolve({}, db=Boom()))


# ── wiring: the entry points and the harnesses ────────────────────────────────

CHAT_PY = (Path(__file__).resolve().parents[1] / "routers" / "chat.py").read_text()


def test_chat_and_whatsapp_entry_points_use_the_resolver_and_none_mints_inline():
    assert 'body.get("session_id", f"web_' not in CHAT_PY
    assert CHAT_PY.count("await resolve_session_id(body, user_id") == 2
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
