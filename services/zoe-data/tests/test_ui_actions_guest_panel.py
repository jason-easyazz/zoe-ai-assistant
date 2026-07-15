"""Guest kiosk access to panel-scoped UI actions (bind / sync / poll / enqueue).

Regression coverage for the kiosk-guest 403. The touch panel runs UI-action
bind/sync/poll as a **bare guest** (no device token — see
touch-ui-executor.getDataApiSession(), which sends no session for guest). Before
the fix, ``_authorize_panel`` rejected every guest with 403, so voice-driven
panel navigation never reached the kiosk. The gate must now let a guest act on
its OWN panel while preserving the panel-hijack protections:

  * a guest MAY bind/sync/poll a panel that is unclaimed or already guest-owned;
  * a guest may NOT bind/sync/poll/enqueue a panel owned by a real user;
  * a guest may NOT enqueue a privileged (sensitive_ui) action type.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.ui_actions as ui_actions  # noqa: E402
from guest_policy import can_use_ui_action  # noqa: E402

pytestmark = pytest.mark.ci_safe


GUEST = {"user_id": "guest", "role": "guest", "username": "guest"}


async def _allow(*_args, **_kwargs):
    return None


class _Cursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _PanelSessionDb:
    """Fake DB whose ``ui_panel_sessions`` SELECTs return a configurable owner.

    ``session_owner`` is either ``None`` (panel unclaimed / no session row) or a
    dict like ``{"user_id": "guest"}`` / ``{"user_id": "jason"}``. The
    ``_authorize_panel`` SELECT computes an ``owner_stale`` flag in SQL; the real
    DB evaluates it, so here we let tests set it via the row (defaulting to
    ``False`` = fresh owner when omitted). Writes are recorded so tests can assert
    an upsert did (or did not) happen, and inspect the emitted SQL/params.
    """

    def __init__(self, session_owner):
        self._session_owner = session_owner
        self.writes = []
        self.commits = 0

    async def execute(self, sql, params=()):
        head = sql.strip().upper()
        if head.startswith("SELECT") and "FROM UI_PANEL_SESSIONS" in head:
            row = self._session_owner
            if row is not None and "owner_stale" not in row:
                row = {**row, "owner_stale": False}
            return _Cursor(row=row)
        if head.startswith("SELECT") and "FROM UI_ACTIONS" in head:
            return _Cursor(rows=[])
        if head.startswith(("INSERT", "UPDATE", "DELETE")):
            self.writes.append((sql, params))
            return _Cursor()
        return _Cursor()

    async def commit(self):
        self.commits += 1


# ── bind ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guest_can_bind_unclaimed_panel(monkeypatch):
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner=None)

    result = await ui_actions.bind_panel(
        {"panel_id": "zoe-touch-pi", "page": "/touch/home.html"},
        user=GUEST,
        db=db,
    )

    assert result["status"] == "ok"
    assert result["panel_id"] == "zoe-touch-pi"
    assert db.commits == 1
    assert any("INSERT INTO ui_panel_sessions" in sql for sql, _ in db.writes)


@pytest.mark.asyncio
async def test_guest_can_bind_own_guest_owned_panel(monkeypatch):
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "guest"})

    result = await ui_actions.bind_panel(
        {"panel_id": "zoe-touch-pi"},
        user=GUEST,
        db=db,
    )

    assert result["status"] == "ok"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_guest_cannot_bind_panel_owned_by_real_user(monkeypatch):
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "jason"})

    with pytest.raises(ui_actions.HTTPException) as exc:
        await ui_actions.bind_panel(
            {"panel_id": "zoe-touch-pi"},
            user=GUEST,
            db=db,
        )

    assert exc.value.status_code == 403
    # No upsert / commit — the hijack is blocked before any write.
    assert db.writes == []
    assert db.commits == 0


# ── reclaim: stale non-guest owner ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guest_can_reclaim_panel_with_stale_owner(monkeypatch):
    """A guest kiosk may reclaim a panel whose non-guest owner has gone silent
    (stale last_seen_at) — the owner signed out and stopped refreshing the row."""
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "jason", "owner_stale": True})

    result = await ui_actions.bind_panel(
        {"panel_id": "zoe-touch-pi", "page": "/touch/home.html"},
        user=GUEST,
        db=db,
    )

    assert result["status"] == "ok"
    assert db.commits == 1
    # The reclaim upsert ran, rebinding the panel to guest.
    upserts = [sql for sql, _ in db.writes if "INSERT INTO ui_panel_sessions" in sql]
    assert len(upserts) == 1


@pytest.mark.asyncio
async def test_guest_cannot_reclaim_panel_with_fresh_owner(monkeypatch):
    """A guest cannot reclaim a panel whose non-guest owner is ACTIVELY present
    (fresh last_seen_at) — the owner-present protection must hold."""
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "jason", "owner_stale": False})

    with pytest.raises(ui_actions.HTTPException) as exc:
        await ui_actions.bind_panel(
            {"panel_id": "zoe-touch-pi"},
            user=GUEST,
            db=db,
        )

    assert exc.value.status_code == 403
    assert db.writes == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_reclaim_upsert_is_toctou_safe_against_racing_fresh_owner(monkeypatch):
    """The reclaim upsert must not clobber a fresh owner that races in between
    the authorize check and the write. We can't run real Postgres in this lane,
    so assert the STRUCTURAL guard: the guest bind upsert appends a WHERE clause
    that (a) permits guest/stale rows and (b) is evaluated against the row's
    CURRENT state at upsert time — so a just-written fresh owner (user_id != guest
    AND last_seen_at not stale) fails the WHERE and is left untouched."""
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "jason", "owner_stale": True})

    await ui_actions.bind_panel({"panel_id": "zoe-touch-pi"}, user=GUEST, db=db)

    upsert_sql = next(
        sql for sql, _ in db.writes if "INSERT INTO ui_panel_sessions" in sql
    )
    assert "ON CONFLICT(panel_id) DO UPDATE SET" in upsert_sql
    # Guard present: guest-owned OR stale owner, and the staleness half casts the
    # TEXT column to timestamptz (a bare NOW() - last_seen_at would raise).
    assert "WHERE ui_panel_sessions.user_id = 'guest'" in upsert_sql
    assert "ui_panel_sessions.last_seen_at::timestamptz" in upsert_sql
    assert "CURRENT_TIMESTAMP - INTERVAL" in upsert_sql


def test_guest_conflict_guard_only_applies_to_guests():
    """The upsert guard is emitted for guests only; non-guest upserts stay
    unrestricted (device-token / bound-user paths)."""
    guard = ui_actions._guest_conflict_guard(GUEST)
    assert guard.startswith(" WHERE ui_panel_sessions.user_id = 'guest' OR ")
    assert "last_seen_at::timestamptz" in guard

    assert ui_actions._guest_conflict_guard({"user_id": "jason", "role": "user"}) == ""


# ── state/sync ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guest_can_sync_own_panel(monkeypatch):
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "guest"})

    result = await ui_actions.sync_ui_state(
        {"panel_id": "zoe-touch-pi", "page": "/touch/lists.html"},
        user=GUEST,
        db=db,
    )

    assert result == {"status": "ok", "panel_id": "zoe-touch-pi"}
    assert db.commits == 1


@pytest.mark.asyncio
async def test_guest_cannot_sync_panel_owned_by_real_user(monkeypatch):
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "jason"})

    with pytest.raises(ui_actions.HTTPException) as exc:
        await ui_actions.sync_ui_state(
            {"panel_id": "zoe-touch-pi"},
            user=GUEST,
            db=db,
        )

    assert exc.value.status_code == 403
    assert db.writes == []
    assert db.commits == 0


# ── actions/pending ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guest_can_pull_pending_for_own_panel(monkeypatch):
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "guest"})

    result = await ui_actions.get_pending_ui_actions(
        panel_id="zoe-touch-pi",
        limit=10,
        user=GUEST,
        db=db,
    )

    assert result == {"actions": [], "count": 0}


@pytest.mark.asyncio
async def test_guest_cannot_pull_pending_for_other_users_panel(monkeypatch):
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "jason"})

    with pytest.raises(ui_actions.HTTPException) as exc:
        await ui_actions.get_pending_ui_actions(
            panel_id="zoe-touch-pi",
            limit=10,
            user=GUEST,
            db=db,
        )

    assert exc.value.status_code == 403
    # Never reached the stale-expire / supersede cleanup, so nothing committed.
    assert db.commits == 0


@pytest.mark.asyncio
async def test_guest_pending_never_serves_stale_owner_queue(monkeypatch):
    """When a guest polls a panel whose non-guest owner is stale (allowed through
    for reclaim, but the row is not yet rebound), the pending read must serve the
    GUEST queue — never the real owner's queued actions."""
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    db = _PanelSessionDb(session_owner={"user_id": "jason", "owner_stale": True})

    result = await ui_actions.get_pending_ui_actions(
        panel_id="zoe-touch-pi",
        limit=10,
        user=GUEST,
        db=db,
    )

    assert result == {"actions": [], "count": 0}
    # Every ui_actions cleanup query is scoped to user_id='guest', not 'jason'.
    ui_action_writes = [
        (sql, params) for sql, params in db.writes if "ui_actions" in sql
    ]
    assert ui_action_writes, "expected the pending-path cleanup UPDATEs to run"
    for _sql, params in ui_action_writes:
        assert params[0] == "guest"
        assert "jason" not in params


# ── enqueue protections (POST /actions) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_guest_cannot_enqueue_to_other_users_panel(monkeypatch):
    """Even with an allowed (safe_ui) action type, a guest cannot target another
    user's panel — the panel-hijack gate still rejects it."""
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)

    async def _allow_action(*_args, **_kwargs):
        return True

    monkeypatch.setattr(ui_actions, "can_use_ui_action", _allow_action)
    db = _PanelSessionDb(session_owner={"user_id": "jason"})

    with pytest.raises(ui_actions.HTTPException) as exc:
        await ui_actions.create_ui_action(
            {
                "action_type": "navigate",
                "panel_id": "zoe-touch-pi",
                "payload": {"page": "/touch/lists.html"},
            },
            user=GUEST,
            db=db,
        )

    assert exc.value.status_code == 403


# ── ack (POST /actions/{id}/ack) ──────────────────────────────────────────────

class _GuestAckDb:
    """Fake DB for the ack path. ``select_row`` is what the ack SELECT returns
    (the matched ui_actions row, or None when the caller isn't authorized)."""

    def __init__(self, select_row):
        self._select_row = select_row
        self.updated = []
        self.ledger = []
        self.commits = 0

    async def execute(self, sql, params=()):
        if "FROM ui_actions" in sql and "EXISTS" in sql:
            return _Cursor(row=self._select_row)
        if "UPDATE ui_actions" in sql:
            self.updated.append((sql, params))
            return _Cursor()
        if "INSERT INTO ui_action_ledger" in sql:
            self.ledger.append((sql, params))
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self):
        self.commits += 1


class _Broadcaster:
    async def broadcast(self, *_args, **_kwargs):
        return 1


@pytest.mark.asyncio
async def test_guest_can_ack_own_guest_owned_action(monkeypatch):
    """Poll → ack: a guest acks an action stored under user_id='guest' for its
    own panel. The SQL's ``user_id = ?`` branch matches, so the ack lands."""
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    monkeypatch.setattr(ui_actions, "broadcaster", _Broadcaster())

    db = _GuestAckDb(select_row={
        "id": "act-1",
        "user_id": "guest",
        "panel_id": "zoe-touch-pi",
        "status": "queued",
    })

    result = await ui_actions.ack_ui_action(
        "act-1",
        {"status": "success", "panel_id": "zoe-touch-pi"},
        user=GUEST,
        db=db,
    )

    assert result == {"status": "ok", "action_id": "act-1", "state": "success"}
    assert len(db.updated) == 1
    # The UPDATE is scoped to the action's own user_id ('guest').
    assert db.updated[0][1][-1] == "guest"
    assert len(db.ledger) == 1
    assert db.commits == 1


@pytest.mark.asyncio
async def test_guest_cannot_ack_real_users_action(monkeypatch):
    """A guest that is not registered on the panel cannot ack an action stored
    under a real user's user_id — the SELECT matches nothing, so it's a no-op
    (idempotent already_acked), never a cross-user mutation."""
    monkeypatch.setattr(ui_actions, "require_feature_access", _allow)
    monkeypatch.setattr(ui_actions, "broadcaster", _Broadcaster())

    db = _GuestAckDb(select_row=None)

    result = await ui_actions.ack_ui_action(
        "act-1",
        {"status": "failed", "panel_id": "zoe-touch-pi"},
        user=GUEST,
        db=db,
    )

    assert result == {"status": "already_acked", "action_id": "act-1"}
    assert db.updated == []
    assert db.ledger == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_guest_cannot_use_sensitive_ui_action_type():
    """Privileged (sensitive_ui) action types remain denied for guest;
    safe navigation-class actions remain allowed. Uses the default matrix
    (db=None), so no DB row is required."""
    assert await can_use_ui_action(None, GUEST, "delete_record") is False
    assert await can_use_ui_action(None, GUEST, "create_record") is False
    assert await can_use_ui_action(None, GUEST, "panel_show_fullscreen") is False

    assert await can_use_ui_action(None, GUEST, "navigate") is True
    assert await can_use_ui_action(None, GUEST, "panel_navigate") is True
