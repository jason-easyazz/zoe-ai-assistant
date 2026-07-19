"""P-W2.1 — proactive/presence.py: the panel-presence primitive.

``panel_presence(user_id)`` must return the panel_id of a FRESH, FOREGROUND
``ui_panel_sessions`` row belonging to that user, else None.

The fake DB here is SEMANTIC, not a canned-row stub: it applies each WHERE
predicate only when that predicate is actually present in the SQL the helper
sent. That makes every miss-test falsifiable against the real query text —
delete ``is_foreground = 1`` from presence.py's SQL and
``test_background_session_misses`` goes red; delete the ``::timestamptz``
freshness predicate and ``test_stale_session_misses`` goes red; delete the
``user_id`` filter and ``test_other_user_misses`` goes red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import contextlib
from datetime import datetime, timedelta, timezone

import proactive.presence as presence


# --------------------------------------------------------------------------- #
# Semantic fake DB
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Exec:
    def __init__(self, factory):
        self._factory = factory

    def __await__(self):
        return self._factory().__await__()

    async def __aenter__(self):
        return await self._factory()

    async def __aexit__(self, *_):
        return False


class SemanticPanelDB:
    """Fake compat-db seeded with ui_panel_sessions rows.

    Each row: {"panel_id", "user_id", "is_foreground", "last_seen_at"(aware dt)}.
    Predicates are applied ONLY when present in the received SQL, so a test
    fails when the corresponding predicate is dropped from the real query.
    """

    def __init__(self, rows):
        self.rows = rows
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        return _Exec(lambda: self._do(sql, params))

    async def _do(self, sql, params):
        norm = " ".join(sql.split()).lower()
        self.queries.append((norm, tuple(params)))
        assert "ui_panel_sessions" in norm, "presence must read ui_panel_sessions"
        out = list(self.rows)
        if "user_id = ?" in norm:
            out = [r for r in out if r["user_id"] == params[0]]
        if "is_foreground = 1" in norm:
            out = [r for r in out if r["is_foreground"] == 1]
        if "last_seen_at::timestamptz" in norm and "interval '1 second'" in norm:
            assert len(params) >= 2, "freshness predicate must be parameterised"
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=int(params[1]))
            out = [r for r in out if r["last_seen_at"] >= cutoff]
        out.sort(key=lambda r: r["last_seen_at"], reverse=True)
        if "limit 1" in norm:
            out = out[:1]
        return _Cursor([{"panel_id": r["panel_id"]} for r in out])

    async def commit(self):  # pragma: no cover — presence is read-only
        raise AssertionError("panel_presence must never write/commit")


def _patch_db(monkeypatch, db):
    @contextlib.asynccontextmanager
    async def fake_compat_db():
        yield db

    monkeypatch.setattr(presence, "_get_compat_db", fake_compat_db)


def _row(panel_id, user_id, *, foreground=1, age_s=0):
    return {
        "panel_id": panel_id,
        "user_id": user_id,
        "is_foreground": foreground,
        "last_seen_at": datetime.now(timezone.utc) - timedelta(seconds=age_s),
    }


# --------------------------------------------------------------------------- #
# The four packet cases
# --------------------------------------------------------------------------- #
async def test_fresh_foreground_hit(monkeypatch):
    db = SemanticPanelDB([_row("panel-kitchen", "jason", age_s=60)])
    _patch_db(monkeypatch, db)
    assert await presence.panel_presence("jason") == "panel-kitchen"


async def test_stale_session_misses(monkeypatch):
    db = SemanticPanelDB([_row("panel-kitchen", "jason", age_s=3600)])
    _patch_db(monkeypatch, db)
    assert await presence.panel_presence("jason") is None


async def test_other_user_misses(monkeypatch):
    db = SemanticPanelDB([_row("panel-kitchen", "someone-else", age_s=60)])
    _patch_db(monkeypatch, db)
    assert await presence.panel_presence("jason") is None


async def test_background_session_misses(monkeypatch):
    db = SemanticPanelDB([_row("panel-kitchen", "jason", foreground=0, age_s=60)])
    _patch_db(monkeypatch, db)
    assert await presence.panel_presence("jason") is None


# --------------------------------------------------------------------------- #
# Window resolution + robustness
# --------------------------------------------------------------------------- #
async def test_explicit_within_s_overrides_default(monkeypatch):
    db = SemanticPanelDB([_row("panel-kitchen", "jason", age_s=120)])
    _patch_db(monkeypatch, db)
    assert await presence.panel_presence("jason", within_s=60) is None
    assert await presence.panel_presence("jason", within_s=300) == "panel-kitchen"


async def test_non_positive_within_s_falls_back_to_default(monkeypatch):
    """within_s=0 is a zero-width window and a negative value inverts the SQL
    arithmetic — both silently always-None. They must fall back to the default
    window, exactly as the env path already validates (Greptile, PR #1412)."""
    db = SemanticPanelDB([_row("panel-kitchen", "jason", age_s=120)])
    _patch_db(monkeypatch, db)
    assert await presence.panel_presence("jason", within_s=0) == "panel-kitchen"
    assert await presence.panel_presence("jason", within_s=-5) == "panel-kitchen"


async def test_env_window_shrinks_default(monkeypatch):
    monkeypatch.setenv("ZOE_PRESENCE_WINDOW_S", "30")
    db = SemanticPanelDB([_row("panel-kitchen", "jason", age_s=120)])
    _patch_db(monkeypatch, db)
    assert await presence.panel_presence("jason") is None


def test_bad_env_window_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ZOE_PRESENCE_WINDOW_S", "soon")
    assert presence._presence_window_s() == 900
    monkeypatch.setenv("ZOE_PRESENCE_WINDOW_S", "-5")
    assert presence._presence_window_s() == 900


async def test_freshest_foreground_panel_wins(monkeypatch):
    db = SemanticPanelDB(
        [
            _row("panel-hall", "jason", age_s=600),
            _row("panel-kitchen", "jason", age_s=30),
        ]
    )
    _patch_db(monkeypatch, db)
    assert await presence.panel_presence("jason") == "panel-kitchen"


async def test_db_error_reads_as_absent(monkeypatch):
    @contextlib.asynccontextmanager
    async def broken_compat_db():
        raise RuntimeError("pool down")
        yield  # pragma: no cover

    monkeypatch.setattr(presence, "_get_compat_db", broken_compat_db)
    assert await presence.panel_presence("jason") is None
