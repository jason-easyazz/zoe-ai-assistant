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
    monkeypatch.setitem(
        sys.modules, "chat",
        types.SimpleNamespace(_save_chat_message=fake_save_chat_message),
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

    async def fake_save_chat_message(*args, **kwargs):
        return True

    monkeypatch.setitem(
        sys.modules, "chat",
        types.SimpleNamespace(_save_chat_message=fake_save_chat_message),
    )
    monkeypatch.setattr(voice_tts, "_spawn_bg", scheduled.append)

    for sentinel in ("voice-guest", "guest", "voice-daemon", ""):
        await voice_tts._schedule_voice_chat_save(SESSION_ID, "hi", "hello", sentinel)
    assert scheduled == []

    await voice_tts._schedule_voice_chat_save(SESSION_ID, "hi", "hello", BOUND_USER)
    assert len(scheduled) == 2  # user + assistant turns for a real user
    for coro in scheduled:
        coro.close()  # created-but-unscheduled coroutines must not warn


@pytest.mark.asyncio
async def test_schedule_save_failure_logs_warning_with_user(monkeypatch, caplog) -> None:
    """A failure while scheduling the save must log a warning naming the user
    id — never vanish into a silent except."""

    async def fake_save_chat_message(*args, **kwargs):
        return True

    def exploding_spawn(coro):
        coro.close()
        raise RuntimeError("scheduler down")

    monkeypatch.setitem(
        sys.modules, "chat",
        types.SimpleNamespace(_save_chat_message=fake_save_chat_message),
    )
    monkeypatch.setattr(voice_tts, "_spawn_bg", exploding_spawn)

    with caplog.at_level(logging.WARNING):
        await voice_tts._schedule_voice_chat_save(SESSION_ID, "hi", "hello", BOUND_USER)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "save failure produced no warning"
    joined = " ".join(r.getMessage() for r in warnings)
    assert BOUND_USER in joined
    assert SESSION_ID in joined
