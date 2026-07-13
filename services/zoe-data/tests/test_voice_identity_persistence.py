"""P-F6 — voice turn persistence attribution (guest-sentinel identity fallback).

Live-diagnosed bug (2026-07-07): panel voice turns reached the server but
resolved ``effective_user="voice-guest"`` even though ``ui_panel_sessions``
bound the panel to a real user. ``_schedule_voice_chat_save`` skipped only
``("guest", "voice-daemon", "")``, so the doomed write proceeded and died on
the ``users`` FK ("voice-guest is not present") — swallowed by a silent
``except: pass`` — and zero voice conversations were ever persisted.

Guards three contracts in ``routers/voice_tts.py``:

1. A guest-sentinel identity (e.g. a client-supplied
   ``identified_user_id="voice-guest"``) falls back to the panel's bound user
   from the existing ``ui_panel_sessions`` lookups, so the turn persists under
   the real user. Persistence attribution only — the PIN/sensitive-scope gate
   reads ``_scope_identity_user`` and is untouched.
2. With no panel binding, guest sentinels are skipped cleanly: no write is
   ever attempted (``_GUEST_SENTINEL_USERS`` now includes "voice-guest").
3. A failure while scheduling the save logs a warning naming the user id
   instead of vanishing.

No models, no network, no DB: the lazy ``chat`` import and every collaborator
module are faked, so this runs in the slim GitHub ``ci_safe`` lane.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import routers.voice_tts as voice_tts
from routers.voice_tts import voice_command


PANEL_ID = "panel-pf6"
SESSION_ID = "session-pf6"
UTTERANCE = "what's on my calendar"
BOUND_USER = "jason"


class _Audio:
    body = b"RIFF-test"
    media_type = "audio/wav"


def _wire_voice_command_fakes(monkeypatch, *, panel_user: str | None):
    """Stub every collaborator of the skybridge exit path of voice_command.

    Returns (chat_calls, spawned_tasks): chat_calls records every
    ``_save_chat_message(session_id, role, content, user_id=...)`` scheduled by
    the voice path; spawned_tasks collects the fire-and-forget tasks so the
    test can drain them before asserting.
    """
    chat_calls: list[tuple[str, str, str, str]] = []
    spawned: list[asyncio.Task] = []

    async def fake_save_chat_message(session_id, role, content, user_id=None, **_kw):
        chat_calls.append((session_id, role, content, user_id))
        return True

    def fake_spawn_bg(coro):
        spawned.append(asyncio.ensure_future(coro))

    async def resolve_skybridge_request(text, user_id, *, context=None, db=None):
        return {
            "handled": True,
            "spoken_summary": "Here is the calendar.",
            "intent": {"domain": "calendar", "action": "current"},
            "skybridge_context": {"domain": "calendar"},
            "cards": [],
        }

    async def fake_broadcast_skybridge_ui(*_args, **_kwargs):
        return None

    async def fake_synthesize(payload, caller=None):
        return _Audio()

    async def fake_panel_user(_panel_id, _db):
        return panel_user

    async def fake_no_panel_user(_panel_id, _db):
        return None

    async def fake_memory_passes(*_args, **_kwargs):
        return None

    class _Broadcaster:
        async def broadcast(self, channel, event, payload):
            return None

    # Lazy in-function imports resolve through sys.modules — fake them all so
    # the test never touches the real chat stack, push bus, or brain cue path.
    async def fake_ensure_user_and_chat_session(session_id, user_id):
        return None

    monkeypatch.setitem(
        sys.modules, "routers.chat",
        types.SimpleNamespace(
            _save_chat_message=fake_save_chat_message,
            _ensure_user_and_chat_session=fake_ensure_user_and_chat_session,
        ),
    )
    monkeypatch.setitem(
        sys.modules, "skybridge_service",
        types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request),
    )
    monkeypatch.setitem(
        sys.modules, "push", types.SimpleNamespace(broadcaster=_Broadcaster())
    )
    monkeypatch.setitem(
        sys.modules, "guest_policy",
        types.SimpleNamespace(record_policy_decision=lambda *_a, **_k: None),
    )
    monkeypatch.setitem(
        sys.modules, "semantic_router",
        types.SimpleNamespace(is_enabled=lambda: False),
    )

    async def _cue_unavailable(**_kw):
        return {"available": False, "text": ""}

    monkeypatch.setitem(
        sys.modules, "pi_hybrid_production",
        types.SimpleNamespace(
            PiHybridProductionConfig=types.SimpleNamespace(
                from_env=lambda: types.SimpleNamespace(enabled=False)
            ),
            pi_hybrid_production_eligible=lambda _t, config=None: (False, "disabled"),
            processing_cue_packet=_cue_unavailable,
            try_pi_hybrid_production=None,
        ),
    )

    # Identity + auth checks now resolve on a dedicated get_db_ctx() connection
    # (detached-task safety), so the harness must provide one or those paths fail.
    class _FakeIdConnCtx:
        async def __aenter__(self):
            return "IDCONN"
        async def __aexit__(self, *_a):
            return False

    monkeypatch.setitem(
        sys.modules, "db_pool",
        types.SimpleNamespace(get_db_ctx=lambda: _FakeIdConnCtx()),
    )

    monkeypatch.setattr(voice_tts, "_spawn_bg", fake_spawn_bg)
    monkeypatch.setattr(voice_tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(voice_tts, "_broadcast_skybridge_ui", fake_broadcast_skybridge_ui)
    monkeypatch.setattr(voice_tts, "_run_voice_memory_passes", fake_memory_passes)
    monkeypatch.setattr(
        voice_tts, "_resolve_recent_panel_session_user", fake_panel_user
    )
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", fake_no_panel_user)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(voice_tts, "_PENDING_CONFIRMATIONS", {})

    return chat_calls, spawned


@pytest.mark.asyncio
async def test_bound_panel_guest_sentinel_saves_as_bound_user(monkeypatch) -> None:
    """identified_user_id="voice-guest" on a panel bound to a real user must
    persist the turn under the bound user, not the sentinel."""
    chat_calls, spawned = _wire_voice_command_fakes(monkeypatch, panel_user=BOUND_USER)

    response = await voice_command(
        {
            "text": UTTERANCE,
            "panel_id": PANEL_ID,
            "session_id": SESSION_ID,
            "identified_user_id": "voice-guest",
        },
        caller={"source": "device", "user_id": "voice-daemon", "panel_id": PANEL_ID},
        stream=False,
        db=object(),
    )
    if spawned:
        await asyncio.gather(*spawned)

    assert response["ok"] is True
    saved_users = {call[3] for call in chat_calls}
    assert "voice-guest" not in saved_users, (
        "guest-sentinel identity leaked into chat persistence: %r" % (chat_calls,)
    )
    assert (SESSION_ID, "user", UTTERANCE, BOUND_USER) in chat_calls, (
        "user turn was not persisted under the panel-bound user: %r" % (chat_calls,)
    )


@pytest.mark.asyncio
async def test_unbound_panel_guest_sentinel_skips_save_cleanly(monkeypatch) -> None:
    """With no panel binding the sentinel stays — and NO write may be attempted
    (the old code attempted it and died on the users FK, silently)."""
    chat_calls, spawned = _wire_voice_command_fakes(monkeypatch, panel_user=None)

    response = await voice_command(
        {
            "text": UTTERANCE,
            "panel_id": PANEL_ID,
            "session_id": SESSION_ID,
            "identified_user_id": "voice-guest",
        },
        caller={"source": "device", "user_id": "voice-daemon", "panel_id": PANEL_ID},
        stream=False,
        db=object(),
    )
    if spawned:
        await asyncio.gather(*spawned)

    assert response["ok"] is True
    assert chat_calls == [], (
        "guest-sentinel turn attempted a chat write (doomed FK insert): %r"
        % (chat_calls,)
    )


@pytest.mark.asyncio
async def test_schedule_save_skips_voice_guest_sentinel(monkeypatch) -> None:
    """_schedule_voice_chat_save's skip set includes "voice-guest"."""
    scheduled: list[object] = []
    saved: list[tuple] = []
    ensured: list[tuple] = []

    async def fake_save_chat_message(session_id, role, content, user_id=None, **_kw):
        saved.append((session_id, role, content, user_id))
        return True

    async def fake_ensure(session_id, user_id):
        ensured.append((session_id, user_id))

    monkeypatch.setitem(
        sys.modules, "routers.chat",
        types.SimpleNamespace(
            _save_chat_message=fake_save_chat_message,
            _ensure_user_and_chat_session=fake_ensure,
        ),
    )
    monkeypatch.setattr(voice_tts, "_spawn_bg", scheduled.append)

    for sentinel in ("voice-guest", "guest", "voice-daemon", ""):
        await voice_tts._schedule_voice_chat_save(SESSION_ID, "hi", "hello", sentinel)
    assert scheduled == []

    await voice_tts._schedule_voice_chat_save(SESSION_ID, "hi", "hello", BOUND_USER)
    assert len(scheduled) == 1  # one persist task covering both turns
    await scheduled[0]
    # parent rows are minted BEFORE the inserts (session FK guard)
    assert ensured == [(SESSION_ID, BOUND_USER)]
    assert (SESSION_ID, "user", "hi", BOUND_USER) in saved
    assert (SESSION_ID, "assistant", "hello", BOUND_USER) in saved


@pytest.mark.asyncio
async def test_schedule_save_failure_logs_warning_with_user(monkeypatch, caplog) -> None:
    """A failure while scheduling the save must log a warning naming the user
    id — never vanish into a silent except."""

    async def fake_save_chat_message(*args, **kwargs):
        return True

    def exploding_spawn(coro):
        coro.close()
        raise RuntimeError("scheduler down")

    async def fake_ensure(session_id, user_id):
        return None

    monkeypatch.setitem(
        sys.modules, "routers.chat",
        types.SimpleNamespace(
            _save_chat_message=fake_save_chat_message,
            _ensure_user_and_chat_session=fake_ensure,
        ),
    )
    monkeypatch.setattr(voice_tts, "_spawn_bg", exploding_spawn)

    with caplog.at_level(logging.WARNING):
        await voice_tts._schedule_voice_chat_save(SESSION_ID, "hi", "hello", BOUND_USER)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "save failure produced no warning"
    joined = " ".join(r.getMessage() for r in warnings)
    assert BOUND_USER in joined
    assert SESSION_ID in joined


@pytest.mark.asyncio
async def test_identity_resolves_via_dedicated_conn_under_shared_db_race(monkeypatch) -> None:
    """P-F8: on the streaming panel path voice_command runs via ensure_future sharing
    turn_stream's pooled asyncpg connection, which is used concurrently. asyncpg forbids
    concurrent ops on one connection, so a resolver query on the shared `db` raised and
    the resolvers' `except: pass` swallowed it → None → effective_user='guest' → every
    panel voice turn was silently dropped. The fix resolves identity on a DEDICATED
    connection (get_db_ctx), so it must succeed even when the shared `db` is unusable.

    Reproduce: a shared `db` whose .execute raises (concurrent-use failure) + a working
    dedicated connection from get_db_ctx that yields the bound user. The turn must persist
    under the bound user, not guest.
    """
    chat_calls, spawned = _wire_voice_command_fakes(monkeypatch, panel_user=None)

    class _RacyDB:
        """Mimics the shared pooled connection mid-concurrent-use: every query raises."""
        async def execute(self, *_a, **_k):
            raise RuntimeError("another operation is in progress on this connection")

    class _GoodConnCtx:
        async def __aenter__(self):
            return "DEDICATED_CONN"
        async def __aexit__(self, *_a):
            return False

    def _fake_get_db_ctx():
        return _GoodConnCtx()

    # Resolver succeeds ONLY on the dedicated connection; on the racy shared db it
    # yields None — the NET effect of asyncpg raising under concurrent use and the
    # resolver's own `except: pass` swallowing it (the exact live failure → guest).
    async def _conn_aware_default(_panel_id, conn):
        if conn != "DEDICATED_CONN":
            return None
        return BOUND_USER

    async def _conn_aware_recent(_panel_id, conn):
        return None  # matches live (recent lookup returns None); default carries identity

    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=_fake_get_db_ctx))
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", _conn_aware_default)
    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", _conn_aware_recent)

    response = await voice_command(
        {"text": UTTERANCE, "panel_id": PANEL_ID, "session_id": SESSION_ID},
        caller={"source": "device", "user_id": "voice-daemon", "panel_id": PANEL_ID},
        stream=False,
        db=_RacyDB(),
    )
    if spawned:
        await asyncio.gather(*spawned)

    assert response["ok"] is True
    saved_users = {call[3] for call in chat_calls}
    assert "guest" not in saved_users and "voice-daemon" not in saved_users, (
        "identity fell back to guest — the shared-db race was not avoided: %r" % (chat_calls,)
    )
    assert BOUND_USER in saved_users, (
        "turn was not persisted under the panel-bound user via the dedicated connection "
        "(the P-W0 root cause): %r" % (chat_calls,)
    )


def test_voice_save_import_path_is_real() -> None:
    """Regression for the silent save outage (2026-07-13): the lazy import in
    _schedule_voice_chat_save must name a module that actually exists at
    runtime. The old ``from chat import ...`` only worked in tests because they
    injected a fake top-level ``chat`` into sys.modules; in production it raised
    ModuleNotFoundError on every panel turn and no transcript was ever saved.
    """
    import importlib.util

    assert importlib.util.find_spec("routers.chat") is not None
    # NOTE: we deliberately do NOT assert that a top-level ``chat`` module is
    # unresolvable — CI's sys.path includes routers/, so ``chat`` resolves
    # there (which is exactly how the buggy spelling passed CI while failing
    # in production, where only the service root is on sys.path).
