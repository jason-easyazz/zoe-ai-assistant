"""Owner-facing delivery of the pendant day review (``omi_review_digest``) and the chunk budget (``omi_day_review.pack`` /
``max_calls``). SYNTHETIC ids and text only, a migrated SQLite (0045 + 0054); Telegram and the apply path are recorded doubles.

Negative controls (hand-mutate, watch the named test go red): drop the ``enabled()`` check in ``run_once`` (flag test); drop the
``user_id`` filter in a candidate query (other-member tests); make ``act`` call ``approve`` for ``skip`` (skip test); put
``DEFAULT_MAX_CALLS`` back to 8 (budget test)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import contextlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa

import db_compat
import omi_day_review as dr
import omi_review_apply
import omi_review_digest as dig
from test_omi_day_review import _Sqlite, _migrate

OWNER, OTHER = "owner-a", "member-b"
NOW = datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc)          # 09:00 Perth


# --- the chunk budget -----------------------------------------------------------------------------------------------------
def _lines(n: int):
    return [dr.Line(id=f"r1.{i}", recording_id="r1", at="09:00", text="I need to buy milk and call Sam tomorrow at 3pm " * 9)
            for i in range(n)]


def test_default_budget_fits_a_full_day_and_nothing_is_dropped_silently(monkeypatch):
    monkeypatch.delenv("ZOE_OMI_DAY_REVIEW_MAX_CALLS", raising=False)
    assert dr.max_calls() >= 13                      # 11-13 chunks was the measured full day; it used to be cut to 8
    chunks, counts = dr.pack(_lines(40), 400, 13)
    assert counts["chunks_total"] > 13
    assert len(chunks) + counts["chunks_skipped"] == counts["chunks_total"]
    chunks_all, counts_all = dr.pack(_lines(40), 400, 1000)
    assert counts_all["chunks_skipped"] == 0 and len(chunks_all) == counts_all["chunks_total"]


def test_over_ceiling_is_recorded_and_logged(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_OMI_DAY_REVIEW_MAX_CALLS", "2")
    chunks, counts = dr.pack(_lines(40), 400, dr.max_calls())
    assert len(chunks) == 2 and counts["chunks_skipped"] == counts["chunks_total"] - 2 > 0
    with caplog.at_level("WARNING"):
        dr._log({"user_id": OWNER, "day": "2026-10-25", "status": "ready"}, counts)
    assert any("chunks_skipped" in r.getMessage() for r in caplog.records)


# --- the digest -----------------------------------------------------------------------------------------------------------
@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine("sqlite:///" + path)
    _migrate(engine, "0045_omi_recordings.py")
    _migrate(engine, "0054_omi_day_review.py")
    db = _Sqlite(path)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setenv("ZOE_OMI_DAY_REVIEW", "on")
    monkeypatch.setenv("ZOE_OMI_REVIEW_TELEGRAM", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.delenv("ZOE_OMI_REVIEW_TELEGRAM_HOUR", raising=False)
    monkeypatch.setenv("ZOE_PUBLIC_URL", "https://zoe.example")
    prefs: dict = {}
    sent: list = []
    calls: dict = {"approve": [], "write": []}

    async def load(uid):
        return dict(prefs.get(uid, {}))

    async def save(uid, st):
        prefs[uid] = dict(st)

    async def fake_send(tid, text, url=None, label=None, *, client=None):
        sent.append((tid, text, url))
        return "sent"

    async def tid_of(member):
        return {OWNER: "111", OTHER: "222"}.get(member)

    async def fake_approve(uid, cid, *, fields=None, via="desktop"):
        calls["approve"].append((uid, cid, via))
        return {"ok": True}

    def boom(name):
        async def _w(*a, **k):
            calls["write"].append(name)
        return _w

    import telegram_notify
    monkeypatch.setattr(dig, "_load_state", load)
    monkeypatch.setattr(dig, "_save", save)
    monkeypatch.setattr(dig, "_telegram_id", tid_of)
    monkeypatch.setattr(dig, "_account", lambda uid: True)
    monkeypatch.setattr(telegram_notify, "send", fake_send)
    monkeypatch.setattr(telegram_notify, "available", lambda: True)
    monkeypatch.setattr(omi_review_apply, "approve", fake_approve)
    for name in dir(omi_review_apply):
        if name.startswith("write_"):
            monkeypatch.setattr(omi_review_apply, name, boom(name))
    return type("Env", (), dict(db=db, prefs=prefs, sent=sent, calls=calls))


def add(env, cid, kind, payload, *, user=OWNER, status="pending", created="2026-10-25T20:00:00Z"):
    env.db.conn.execute("INSERT INTO omi_day_candidates (id, review_id, user_id, day_local, kind, payload, dedup_key, status, created_at) "
                        "VALUES (?, 'rev', ?, '2026-10-25', ?, ?, ?, ?, ?)", (cid, user, kind, json.dumps(payload), cid, status, created))
    env.db.conn.commit()


def status_of(env, cid):
    return env.db.conn.execute("SELECT status FROM omi_day_candidates WHERE id = ?", (cid,)).fetchone()[0]


@pytest.mark.asyncio
async def test_digest_renders_candidates_for_the_owner_only(env):
    add(env, "c1", "reminder", {"title": "Send the invoice", "when": "Friday"})
    add(env, "c2", "list_item", {"item": "oat milk", "list": "shopping"})
    add(env, "x1", "reminder", {"title": "SECRET-OF-OTHER-MEMBER"}, user=OTHER)
    assert await dig.send_for(OWNER, NOW) == "sent" and len(env.sent) == 1
    tid, text, url = env.sent[0]
    assert tid == "111" and url == "https://zoe.example/pendant.html"
    assert "Send the invoice" in text and "oat milk" in text and "2 things" in text
    assert "SECRET-OF-OTHER-MEMBER" not in text
    assert env.calls["approve"] == [] and env.calls["write"] == []        # listing never writes


@pytest.mark.asyncio
async def test_run_once_each_member_gets_only_their_own_and_only_once_a_day(env):
    add(env, "c1", "reminder", {"title": "Owner thing"})
    add(env, "x1", "reminder", {"title": "Other thing"}, user=OTHER)
    await dig.run_once(NOW)
    await dig.run_once(NOW)                                               # same day: nothing more
    by_tid = {t: txt for t, txt, _ in env.sent}
    assert len(env.sent) == 2 and "Owner thing" in by_tid["111"] and "Other thing" not in by_tid["111"]
    assert "Other thing" in by_tid["222"] and "Owner thing" not in by_tid["222"]


@pytest.mark.asyncio
async def test_flag_off_means_no_message(env, monkeypatch):
    add(env, "c1", "reminder", {"title": "Owner thing"})
    monkeypatch.setenv("ZOE_OMI_REVIEW_TELEGRAM", "0")
    assert await dig.run_once(NOW) == {}
    monkeypatch.delenv("ZOE_OMI_REVIEW_TELEGRAM")
    assert await dig.run_once(NOW) == {}
    monkeypatch.setenv("ZOE_OMI_REVIEW_TELEGRAM", "1")
    monkeypatch.setenv("ZOE_OMI_DAY_REVIEW", "shadow")                    # shadow = no mention
    assert await dig.run_once(NOW) == {}
    assert env.sent == []


@pytest.mark.asyncio
async def test_not_before_the_hour_and_nothing_new_next_day(env):
    add(env, "c1", "reminder", {"title": "Owner thing"})
    assert await dig.run_once(datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)) == {}      # 05:00 Perth
    assert await dig.send_for(OWNER, NOW) == "sent"
    assert await dig.send_for(OWNER, datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc)) == "skipped:nothing_new"


@pytest.mark.asyncio
async def test_approve_calls_the_apply_path_exactly_once_and_skip_writes_nothing(env):
    add(env, "c1", "reminder", {"title": "A"})
    add(env, "c2", "reminder", {"title": "B"})
    assert await dig.act(OWNER, "approve", "c1") == "approved"
    assert env.calls["approve"] == [(OWNER, "c1", "phone")]
    assert await dig.act(OWNER, "skip", "c2") == "skipped"
    assert env.calls["approve"] == [(OWNER, "c1", "phone")] and env.calls["write"] == []
    assert status_of(env, "c2") == "dismissed"
    assert await dig.act(OWNER, "skip", "c2") == "already_decided"


@pytest.mark.asyncio
async def test_another_members_candidate_cannot_be_acted_on(env):
    add(env, "x1", "reminder", {"title": "Other"}, user=OTHER)
    assert await dig.act(OWNER, "approve", "x1") == "not_found"
    assert await dig.act(OWNER, "skip", "x1") == "not_found"
    assert env.calls["approve"] == [] and status_of(env, "x1") == "pending"


@pytest.mark.asyncio
async def test_waits_for_a_decision_after_repeated_unanswered_digests(env):
    add(env, "c1", "reminder", {"title": "A"})
    env.prefs[OWNER] = {"day": "2026-10-20", "sent_at": "2026-10-20T01:00:00Z", "unanswered": dig.MAX_UNANSWERED}
    add(env, "c2", "reminder", {"title": "B"}, created="2026-10-25T21:00:00Z")
    assert await dig.send_for(OWNER, NOW) == "skipped:waiting_for_a_decision"
    env.db.conn.execute("UPDATE omi_day_candidates SET status='dismissed', decided_at='2026-10-22T00:00:00Z' WHERE id='c1'")
    env.db.conn.commit()
    assert await dig.send_for(OWNER, NOW) == "sent"
