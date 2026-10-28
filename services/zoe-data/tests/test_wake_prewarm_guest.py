"""Wake-word prewarm runs on EVERY wake, identified or not (regression: #2034 made it skip a guest).

Since strict voice identity (#2034) the wake handler resolves the panel's member through
``_resolve_fresh_panel_member``, which is None without a fresh PIN session - and the prewarm used to
``return`` on None, so an idle panel's first turn prefilled the whole ~2.7k-token system+tools prompt cold
(``FLUE_PROMPT_CACHE first_cache_n=0``, ~4.8 s to first token instead of ~1.1-1.7 s).

What these pin:
  * a guest wake still warms (identity is not a precondition) and reads / injects nothing about any member;
  * a fresh-PIN member wake is unchanged (their worker + facts cache);
  * a wake never refreshes a PIN or voice session (only a turn's own identity does);
  * a warm that fails never fails the wake;
  * the Flue KV warm carries NO identity on the wire, is skipped when the slot is hot, and never raises.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import brain_slot_guard
import routers.voice_tts as v
import zoe_flue_client as zfc

pytestmark = pytest.mark.ci_safe

PANEL = "panel-test"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wake(monkeypatch):
    """Wake-handler harness: records every warm call, raises if anything touches a session or a member."""
    calls = {"worker": [], "kv": 0, "facts": [], "touch": [], "observe": [], "member": None}
    monkeypatch.setattr(v, "_brain_prewarm_on_wake_enabled", lambda: True)
    monkeypatch.setattr(v, "_get_or_create_voice_session", lambda p: "voice-panel-test-abc123")
    monkeypatch.setattr(v, "_VOICE_SESSIONS", {})

    async def _fresh(panel_id, fallback_db=None):
        return calls["member"]

    async def _touch(panel_id, user_id):
        calls["touch"].append((panel_id, user_id))

    async def _facts(user_id):
        calls["facts"].append(user_id)
        return ("facts", None)

    async def _worker(user_id, session_id, *, voice_mode=False):
        calls["worker"].append((user_id, session_id, voice_mode))
        return True

    async def _kv(member=""):
        calls["kv"] += 1
        return "warmed"

    monkeypatch.setattr(v, "_resolve_fresh_panel_member", _fresh)
    monkeypatch.setattr(v, "_touch_panel_session", _touch)
    monkeypatch.setattr(v, "_voice_brain_memory", _facts)
    import zoe_core_client
    monkeypatch.setattr(zoe_core_client, "prewarm", _worker)
    monkeypatch.setattr(zfc, "warm_kv_prefix", _kv)
    import voice_session as _vs
    monkeypatch.setattr(_vs, "observe_turn", lambda **kw: calls["observe"].append(kw))
    monkeypatch.delenv("ZOE_BRAIN_BACKEND", raising=False)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    return calls


def _flue(monkeypatch):
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")


def test_guest_wake_on_the_live_flue_lane_warms_the_kv_prefix(wake, monkeypatch):
    """THE regression: no fresh member -> the KV warm still runs, with no member scope at all."""
    _flue(monkeypatch)
    _run(v._prewarm_brain_for_panel(PANEL))
    assert wake["kv"] == 1, "a guest wake must still warm the brain's shared prefix"
    assert wake["facts"] == [], "a guest has no memory to warm and none may be read on their behalf"
    assert wake["worker"] == [], "the Flue lane never reads the core worker - do not spawn a Pi process for it"
    assert wake["touch"] == [] and wake["observe"] == []
    assert v._VOICE_SESSIONS == {}, "an unidentified wake must not bind an identity to the panel"


def test_guest_wake_on_the_core_lane_warms_the_guest_worker(wake):
    """Core lane: the worker key is the one a guest turn uses (effective_user == 'guest')."""
    _run(v._prewarm_brain_for_panel(PANEL))
    assert wake["worker"] == [("guest", "voice-panel-test-abc123", True)]
    assert wake["facts"] == []
    assert wake["kv"] == 0, "the KV warm is the Flue lane's"


def test_fresh_pin_member_wake_is_unchanged_on_the_core_lane(wake):
    wake["member"] = "jason"
    _run(v._prewarm_brain_for_panel(PANEL))
    assert wake["worker"] == [("jason", "voice-panel-test-abc123", True)]
    assert wake["facts"] == ["jason"]


def test_fresh_pin_member_wake_on_the_flue_lane_warms_kv_and_their_facts(wake, monkeypatch):
    _flue(monkeypatch)
    wake["member"] = "jason"
    _run(v._prewarm_brain_for_panel(PANEL))
    assert wake["kv"] == 1
    assert wake["facts"] == ["jason"]
    assert wake["worker"] == []


def test_core_worker_is_still_warmed_when_failover_can_land_on_it(wake, monkeypatch):
    _flue(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _run(v._prewarm_brain_for_panel(PANEL))
    assert wake["kv"] == 1
    assert wake["worker"] == [("guest", "voice-panel-test-abc123", True)]


def test_a_stale_cached_binding_is_not_an_identity(wake):
    """A sentinel parked in the voice-session binding is the ABSENCE of a member (guest, not 'guest')."""
    v._VOICE_SESSIONS[PANEL] = {"bound_user_id": "guest"}
    _run(v._prewarm_brain_for_panel(PANEL))
    assert wake["facts"] == []
    assert wake["worker"][0][0] == "guest"


def test_wake_never_refreshes_a_session(wake, monkeypatch):
    """#2034: only a turn's OWN identity extends a PIN / voice session - a wake is nobody's turn."""
    _flue(monkeypatch)
    for member in (None, "jason"):
        wake["member"] = member
        _run(v._prewarm_brain_for_panel(PANEL))
    assert wake["touch"] == [], "the wake must not heartbeat ui_panel_sessions"
    assert wake["observe"] == [], "the wake must not open / renew a voice session"


@pytest.mark.parametrize("flue", [True, False])
def test_a_failing_warm_never_fails_the_wake(wake, monkeypatch, flue):
    if flue:
        _flue(monkeypatch)

    async def _boom(*a, **k):
        raise RuntimeError("sidecar exploded")

    monkeypatch.setattr(zfc, "warm_kv_prefix", _boom)
    import zoe_core_client
    monkeypatch.setattr(zoe_core_client, "prewarm", _boom)
    monkeypatch.setattr(v, "_voice_brain_memory", _boom)
    wake["member"] = "jason"
    _run(v._prewarm_brain_for_panel(PANEL))   # must simply return


def test_a_failing_identity_lookup_never_fails_the_wake_or_loses_its_warm(wake, monkeypatch):
    """The KV warm needs no identity, so nothing about the identity read may cost the wake its warm."""
    _flue(monkeypatch)

    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(v, "_resolve_fresh_panel_member", _boom)
    _run(v._prewarm_brain_for_panel(PANEL))
    assert wake["kv"] == 1


def test_a_failing_session_lookup_does_not_lose_the_kv_warm(wake, monkeypatch):
    _flue(monkeypatch)

    def _boom(panel_id):
        raise RuntimeError("session store broke")

    monkeypatch.setattr(v, "_get_or_create_voice_session", _boom)

    async def _go():
        await v._prewarm_brain_for_panel(PANEL)
        await asyncio.sleep(0)   # let the detached warm task run

    _run(_go())
    assert wake["kv"] == 1


def test_kill_switch_skips_everything(wake, monkeypatch):
    _flue(monkeypatch)
    monkeypatch.setattr(v, "_brain_prewarm_on_wake_enabled", lambda: False)
    _run(v._prewarm_brain_for_panel(PANEL))
    assert wake["kv"] == 0 and wake["worker"] == [] and wake["facts"] == []


# ── zoe_flue_client.warm_kv_prefix ────────────────────────────────────────────


@pytest.fixture
def sidecar(monkeypatch):
    """A fake Flue sidecar behind httpx.MockTransport; records every request it receives."""
    import httpx

    seen: list = []
    state = {"status": 200, "ctype": "application/x-ndjson", "stall": False, "aborts": [],
             "body": '"ready"\n{"done": true, "prompt_cache": [{"prompt_n": 2667, "cache_n": 0}]}\n'}

    async def _stalled():
        yield b'"..."\n'          # admitted, then silence: the model never finishes
        await asyncio.sleep(60)

    async def handler(request):
        seen.append(request)
        if request.url.path.endswith("/abort"):
            state["aborts"].append((request.url.path, request.headers.get("x-zoe-abort-submission"),
                                    request.headers.get("x-zoe-abort-reason")))
            return httpx.Response(200, json={"outcome": "requested"})
        headers = {"content-type": state["ctype"], "x-flue-submission-id": "sub-warm-1"}
        if state["stall"]:
            return httpx.Response(200, headers=headers, content=_stalled())
        return httpx.Response(state["status"], headers=headers, content=state["body"].encode())

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://sidecar.test")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "t")
    monkeypatch.delenv("ZOE_FLUE_WIRE", raising=False)
    monkeypatch.delenv("ZOE_BRAIN_KV_WARM_ON_WAKE", raising=False)
    brain_slot_guard.reset()
    monkeypatch.setattr(zfc, "_kv_warm_inflight", False)
    monkeypatch.setattr(zfc, "_kv_warm_session", {"id": "", "uses": 0, "seq": 0})
    yield seen, state
    brain_slot_guard.reset()


def _warm_message(req) -> str:
    return json.loads(req.content)["body"]


def _assert_guest_only(message: str) -> None:
    """The warm turn is an EXPLICIT guest: a leading guest envelope, then the fixed instruction, nothing else.

    No envelope at all is NOT anonymous - the live sidecar falls back to its own ZOE_BRAIN_USER_ID, fetches that
    member's user-model card into the system prompt and lets tools act as them (measured offline on the real sidecar).
    """
    assert message == " zoe-uid:guest\n" + zfc._KV_WARM_MESSAGE, repr(message)
    for needle in ("zoe-persona", "zoe-replay", "zoe-spec", "What you remember", "[MEMORY"):
        assert needle not in message, f"{needle!r} must never ride a warm turn"


def test_warm_is_an_explicit_guest_on_its_own_session(sidecar, caplog):
    seen, _ = sidecar
    caplog.set_level("INFO", logger="zoe_flue_client")
    assert _run(zfc.warm_kv_prefix()) == "warmed"
    assert len(seen) == 1
    req = seen[0]
    assert req.url.path.startswith("/agents/zoe/kvwarm-"), "never a real conversation's session"
    assert set(json.loads(req.content)) == {"kind", "body"}
    _assert_guest_only(_warm_message(req))
    assert req.headers.get("x-zoe-abort-on-cancel") == "1", "the sidecar must be able to abort a stalled warm"
    line = [r.getMessage() for r in caplog.records if "FLUE_KV_WARM" in r.getMessage()]
    assert line and "outcome=warmed first_prompt_n=2667 first_cache_n=0" in line[0]
    assert not any("FLUE_PROMPT_CACHE" in r.getMessage() for r in caplog.records), \
        "the warm must not pollute the live miss-rate log"


def test_negative_control_an_envelope_less_warm_is_caught(sidecar, monkeypatch):
    """Drop the envelope (what the first version did): the guard above must fail, or it proves nothing."""
    seen, _ = sidecar
    monkeypatch.setattr(zfc, "_kv_warm_message", lambda: zfc._KV_WARM_MESSAGE)
    _run(zfc.warm_kv_prefix())
    with pytest.raises(AssertionError):
        _assert_guest_only(_warm_message(seen[0]))


def test_one_warm_session_is_reused_then_rotated(sidecar):
    """Flue persists every turn and never deletes: reuse bounds the store's growth, rotation bounds the history."""
    seen, _ = sidecar
    for _i in range(zfc._KV_WARM_SESSION_TURNS + 1):
        assert _run(zfc.warm_kv_prefix()) == "warmed"
        brain_slot_guard.reset()
    paths = [r.url.path for r in seen]
    assert len(set(paths[:zfc._KV_WARM_SESSION_TURNS])) == 1, "the first N warms share ONE session"
    assert paths[zfc._KV_WARM_SESSION_TURNS] != paths[0], "then a fresh one, so the replayed history stays short"
    assert all(p.startswith("/agents/zoe/kvwarm-") for p in paths)


def test_a_failed_warm_rotates_the_session(sidecar):
    seen, state = sidecar
    _run(zfc.warm_kv_prefix())
    brain_slot_guard.reset()
    state.update(body='"ready"\n{"done": true}\n')
    _run(zfc.warm_kv_prefix())
    brain_slot_guard.reset()
    state.update(body='{"error": "model down"}\n')
    _run(zfc.warm_kv_prefix())
    brain_slot_guard.reset()
    state.update(body='"ready"\n{"done": true}\n')
    _run(zfc.warm_kv_prefix())
    assert seen[0].url.path == seen[1].url.path == seen[2].url.path   # reused until it failed...
    assert seen[3].url.path != seen[2].url.path                        # ...never reused after a failure


def test_a_stalled_warm_is_actually_aborted_not_just_abandoned(sidecar, monkeypatch, caplog):
    """On the deadline the reader is cancelled AND the admitted submission is aborted via the guarded route."""
    seen, state = sidecar
    caplog.set_level("INFO", logger="zoe_flue_client")
    state["stall"] = True
    monkeypatch.setattr(zfc, "_KV_WARM_MAX_S", 0.2)
    assert _run(zfc.warm_kv_prefix()) == "failed:timeout"
    assert len(state["aborts"]) == 1, "the stalled turn must be aborted or it keeps the one llama slot"
    path, submission, reason = state["aborts"][0]
    assert path == seen[0].url.path + "/abort" and submission == "sub-warm-1" and reason == "warm_timeout"
    assert any("abort=requested" in r.getMessage() for r in caplog.records)
    assert zfc._kv_warm_inflight is False and brain_slot_guard._active == 0


def test_a_finished_warm_is_not_aborted(sidecar):
    _, state = sidecar
    assert _run(zfc.warm_kv_prefix()) == "warmed"
    assert state["aborts"] == []


def test_a_failing_abort_never_breaks_the_warm(sidecar, monkeypatch):
    _, state = sidecar
    state["stall"] = True
    monkeypatch.setattr(zfc, "_KV_WARM_MAX_S", 0.2)

    async def _boom(*a, **k):
        raise RuntimeError("sidecar gone")

    monkeypatch.setattr(zfc, "_post_guarded_abort", _boom)
    assert _run(zfc.warm_kv_prefix()) == "failed:timeout"
    assert zfc._kv_warm_inflight is False and brain_slot_guard._active == 0


def test_warm_is_skipped_while_the_conversation_is_hot(sidecar):
    seen, _ = sidecar
    brain_slot_guard.turn_started()           # a brain turn is in flight
    assert _run(zfc.warm_kv_prefix()) == "skipped:hot"
    brain_slot_guard.turn_finished()          # just ended: still inside the quiet window
    assert _run(zfc.warm_kv_prefix()) == "skipped:hot"
    assert seen == [], "a hot slot already holds the prefix - the warm must not queue behind / evict it"


def test_warm_holds_the_slot_guard_so_helpers_cannot_evict_what_it_loaded(sidecar):
    _run(zfc.warm_kv_prefix())
    assert brain_slot_guard.is_hot(), "post-turn helpers must defer after a warm, exactly as after a turn"
    assert brain_slot_guard._active == 0, "and the in-flight count is released"


def test_one_warm_at_a_time(sidecar, monkeypatch):
    seen, _ = sidecar
    monkeypatch.setattr(zfc, "_kv_warm_inflight", True)
    assert _run(zfc.warm_kv_prefix()) == "skipped:inflight"
    assert seen == []


def test_warm_kill_switch(sidecar, monkeypatch):
    seen, _ = sidecar
    monkeypatch.setenv("ZOE_BRAIN_KV_WARM_ON_WAKE", "off")
    assert _run(zfc.warm_kv_prefix()) == "skipped:off"
    assert seen == []


@pytest.mark.parametrize("status,ctype,body,expect", [
    (500, "application/json", "{}", "http_500"),
    (200, "application/json", "{}", "not_ndjson"),
    (200, "application/x-ndjson", '{"error": "model down"}\n', "error_terminal"),
    (200, "application/x-ndjson", '"ready"\n', "no_terminal"),
])
def test_warm_reports_sidecar_trouble_without_raising(sidecar, status, ctype, body, expect):
    _, state = sidecar
    state.update(status=status, ctype=ctype, body=body)
    assert _run(zfc.warm_kv_prefix()) == expect
    assert brain_slot_guard._active == 0 and zfc._kv_warm_inflight is False


def test_warm_never_raises_when_the_sidecar_is_unreachable(monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:1")   # nothing listens on port 1
    monkeypatch.delenv("ZOE_BRAIN_KV_WARM_ON_WAKE", raising=False)
    brain_slot_guard.reset()
    monkeypatch.setattr(zfc, "_kv_warm_inflight", False)
    assert _run(zfc.warm_kv_prefix()).startswith("failed:")
    assert brain_slot_guard._active == 0 and zfc._kv_warm_inflight is False
    brain_slot_guard.reset()


def test_warm_is_wall_clock_bounded(sidecar, monkeypatch):
    async def _hang(session_id, state, message=""):
        await asyncio.sleep(60)

    monkeypatch.setattr(zfc, "_kv_warm_turn", _hang)
    monkeypatch.setattr(zfc, "_KV_WARM_MAX_S", 0.05)
    assert _run(zfc.warm_kv_prefix()) == "failed:timeout"
    assert brain_slot_guard._active == 0 and zfc._kv_warm_inflight is False


def test_a_cancelled_warm_restores_the_slot_guard_and_the_inflight_flag(sidecar, monkeypatch):
    async def _go():
        gate = asyncio.Event()

        async def _hang(session_id, state, message=""):
            gate.set()
            await asyncio.sleep(60)

        monkeypatch.setattr(zfc, "_kv_warm_turn", _hang)
        task = asyncio.ensure_future(zfc.warm_kv_prefix())
        await gate.wait()
        assert zfc._kv_warm_inflight is True and brain_slot_guard._active == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(_go())
    assert zfc._kv_warm_inflight is False and brain_slot_guard._active == 0


def test_a_real_turn_that_starts_during_warm_preparation_stops_the_warm(sidecar, monkeypatch):
    """The entry is_hot() check runs BEFORE the (awaited) preparation; a real turn that starts while the warm prepares must
    stop it before submission, or the warm queues behind the turn and replaces its resident conversation."""
    seen, _ = sidecar
    real = zfc._kv_warm_outbound

    async def _prepare_while_a_turn_starts(member):
        out = await real(member)
        brain_slot_guard.turn_started()       # the member's real turn arrives mid-preparation
        return out

    monkeypatch.setattr(zfc, "_kv_warm_outbound", _prepare_while_a_turn_starts)
    try:
        assert _run(zfc.warm_kv_prefix()) == "skipped:hot"
        assert seen == [], "nothing may be submitted once a real turn holds the slot"
    finally:
        brain_slot_guard.turn_finished()
