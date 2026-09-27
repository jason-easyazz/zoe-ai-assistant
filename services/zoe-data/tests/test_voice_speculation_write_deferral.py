"""B1.1 phase 2 — side effects of a speculative turn wait for the daemon's verdict.

A speculative turn runs STT + the intent layer + the brain on a transcript PREFIX
while the daemon is still listening. The gate (test_voice_speculation_gate.py)
holds everything AUDIBLE; this file pins the other half: nothing the turn WRITES
may happen before the verdict.

Contract (docs/architecture/b1-speculative-turn-start.md, "Phase 2"):
  (a) a write intent fired speculatively is held, then executed EXACTLY ONCE on
      commit / equivalent-resolve, and DROPPED on cancel or hold-timeout;
  (b) a read intent runs immediately, verdict or not;
  (c) background side effects (history rows, memory passes) are queued in spawn
      order and run once on commit, never on cancel;
  (d) no speculative turn bound (flag off, every other turn/channel) = unchanged;
  (e) through the router: the binding reaches BOTH the voice_command task and the
      lazily-pulled stream body (where the brain lane runs);
  (f) negative control: with the execute_intent guard removed, the (a) checker
      goes RED.

Fakes only: no DB, no models, no network.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

pytestmark = pytest.mark.ci_safe

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

import fast_tiers
import intent_router
import routers.voice_tts as vt
import voice_speculation as vs
from intent_router import Intent


# ── helpers ──────────────────────────────────────────────────────────────

class _Ledger:
    """Records every executed side effect with the gate state at that moment."""

    def __init__(self):
        self.calls: list[dict] = []

    def record(self, what: str, gate) -> None:
        self.calls.append({"what": what, "resolved": bool(gate and gate.resolved),
                           "verdict": gate.verdict() if gate and gate.resolved else None})


def _patch_executors(monkeypatch, ledger: _Ledger, gate_ref: dict):
    async def _list_add(intent, user_id):
        ledger.record("list_add", gate_ref.get("gate"))
        return "Added milk to your shopping list."

    async def _list_show(intent, user_id):
        ledger.record("list_show", gate_ref.get("gate"))
        return "Your shopping list has eggs."

    monkeypatch.setattr(intent_router, "_execute_list_add_direct", _list_add)
    monkeypatch.setattr(intent_router, "_execute_list_show_direct", _list_show)


async def _speculative_write(verdict: str | None, *, hold_ms_env=None, monkeypatch=None,
                             ledger: _Ledger, gate_ref: dict, intent_name="list_add"):
    """Fire execute_intent inside a speculative turn, deliver ``verdict`` later."""
    gate = vs.open_gate(f"w-{intent_name}-{verdict}-{id(ledger)}")
    gate_ref["gate"] = gate
    token = vs.bind(gate)
    try:
        task = asyncio.ensure_future(
            intent_router.execute_intent(Intent(intent_name, {"item": "milk"}), "jason"))
    finally:
        vs.unbind(token)
    await asyncio.sleep(0.05)
    before = list(ledger.calls)
    if verdict is not None:
        gate.resolve(verdict)
    try:
        result = await task
        outcome = ("ok", result)
    except asyncio.CancelledError as exc:
        outcome = ("cancelled", exc)
    vs.close_gate(gate)
    return before, outcome, gate


def _assert_write_held_until_verdict(before, outcome, ledger: _Ledger, verdict: str) -> None:
    """The (a) invariant — shared with the negative control (f)."""
    assert before == [], f"write ran BEFORE the verdict: {before}"
    writes = [c for c in ledger.calls if c["what"] == "list_add"]
    if verdict in vs.RELEASED_VERDICTS:
        assert len(writes) == 1, f"commit must execute the write exactly once: {writes}"
        assert writes[0]["resolved"] and writes[0]["verdict"] in vs.RELEASED_VERDICTS
        assert outcome[0] == "ok"
    else:
        assert writes == [], f"a cancelled speculative turn executed a write: {writes}"
        # SpeculativeTurnCancelled is a CancelledError, so the turn's task ends
        # CANCELLED (awaiting it re-raises a plain CancelledError).
        assert outcome[0] == "cancelled"


# ── (a) write intents: held, once on commit, dropped on cancel ──────────

@pytest.mark.parametrize("verdict", ["commit", "cancel"])
def test_write_intent_is_held_then_committed_once_or_dropped(monkeypatch, verdict):
    ledger, gate_ref = _Ledger(), {}
    _patch_executors(monkeypatch, ledger, gate_ref)
    before, outcome, _ = asyncio.run(_speculative_write(verdict, ledger=ledger, gate_ref=gate_ref))
    _assert_write_held_until_verdict(before, outcome, ledger, verdict)


def test_equivalent_resolve_releases_the_write_once(monkeypatch):
    ledger, gate_ref = _Ledger(), {}
    _patch_executors(monkeypatch, ledger, gate_ref)

    async def _run():
        gate = vs.open_gate("w-equiv")
        gate.speculative_transcript = "add milk to the shopping list"
        gate_ref["gate"] = gate
        token = vs.bind(gate)
        try:
            task = asyncio.ensure_future(intent_router.execute_intent(Intent("list_add", {}), "jason"))
        finally:
            vs.unbind(token)
        await asyncio.sleep(0.05)
        before = list(ledger.calls)
        gate.resolve("resolve", final_transcript="Add milk to the shopping list.")
        await task
        vs.close_gate(gate)
        return before

    before = asyncio.run(_run())
    assert before == []
    assert [c["verdict"] for c in ledger.calls] == ["equivalent"]


def test_non_equivalent_resolve_drops_the_write(monkeypatch):
    ledger, gate_ref = _Ledger(), {}
    _patch_executors(monkeypatch, ledger, gate_ref)

    async def _run():
        gate = vs.open_gate("w-nonequiv")
        gate.speculative_transcript = "add milk"
        gate_ref["gate"] = gate
        token = vs.bind(gate)
        try:
            task = asyncio.ensure_future(intent_router.execute_intent(Intent("list_add", {}), "jason"))
        finally:
            vs.unbind(token)
        await asyncio.sleep(0.05)
        gate.resolve("resolve", final_transcript="add milk and eggs")
        with pytest.raises(asyncio.CancelledError):
            await task
        vs.close_gate(gate)

    asyncio.run(_run())
    assert ledger.calls == [], "the prefix 'add milk' must never write when the user said more"


def test_no_verdict_hold_timeout_drops_the_write(monkeypatch):
    monkeypatch.setenv(vs.MAX_HOLD_FLAG, "120")
    ledger, gate_ref = _Ledger(), {}
    _patch_executors(monkeypatch, ledger, gate_ref)
    before, outcome, gate = asyncio.run(_speculative_write(None, ledger=ledger, gate_ref=gate_ref))
    _assert_write_held_until_verdict(before, outcome, ledger, "cancel")
    assert gate.verdict() == "hold_timeout"


# ── (b) reads run immediately ─────────────────────────────────────────────

def test_read_intent_runs_immediately_without_a_verdict(monkeypatch):
    ledger, gate_ref = _Ledger(), {}
    _patch_executors(monkeypatch, ledger, gate_ref)

    async def _run():
        gate = vs.open_gate("r-1")
        gate_ref["gate"] = gate
        token = vs.bind(gate)
        try:
            result = await asyncio.wait_for(
                intent_router.execute_intent(Intent("list_show", {}), "jason"), timeout=1.0)
        finally:
            vs.unbind(token)
        unresolved = not gate.resolved
        vs.close_gate(gate)
        return result, unresolved

    result, unresolved = asyncio.run(_run())
    assert unresolved, "the read must not have needed a verdict"
    assert result == "Your shopping list has eggs."
    assert [c["what"] for c in ledger.calls] == ["list_show"]


# ── (d) nothing bound = unchanged ─────────────────────────────────────────

def test_write_without_a_speculative_turn_runs_at_once(monkeypatch):
    ledger, gate_ref = _Ledger(), {}
    _patch_executors(monkeypatch, ledger, gate_ref)
    result = asyncio.run(asyncio.wait_for(
        intent_router.execute_intent(Intent("list_add", {}), "jason"), timeout=1.0))
    assert result.startswith("Added")
    assert len(ledger.calls) == 1
    assert vs.bound_gate() is None


def test_defer_until_commit_is_identity_without_a_gate():
    async def _c():
        return 7
    coro = _c()
    assert vs.defer_until_commit(coro) is coro
    assert asyncio.run(coro) == 7


# ── (c) background side effects: queued in order, once, or dropped ───────

@pytest.mark.parametrize("verdict", ["commit", "cancel"])
def test_spawned_background_side_effects_wait_for_the_verdict(monkeypatch, verdict):
    order: list[str] = []

    async def _effect(name, delay):
        await asyncio.sleep(delay)
        order.append(name)

    async def _run():
        gate = vs.open_gate(f"bg-{verdict}")
        token = vs.bind(gate)
        try:
            # user-turn row first (slow write), reply row second (fast write):
            # the queue must keep spawn order even though the second is quicker.
            vt._spawn_bg(_effect("user_row", 0.03))
            vt._spawn_bg(_effect("reply_row", 0.0))
        finally:
            vs.unbind(token)
        await asyncio.sleep(0.05)
        before = list(order)
        gate.resolve(verdict)
        await asyncio.sleep(0.15)
        vs.close_gate(gate)
        return before

    before = asyncio.run(_run())
    assert before == [], "background side effect ran before the verdict"
    if verdict == "commit":
        assert order == ["user_row", "reply_row"], order
    else:
        assert order == []


# ── classification pins ───────────────────────────────────────────────────

def test_safe_intents_cover_every_tier0_read():
    assert fast_tiers._TIER0_READ_INTENTS <= vs.SPECULATION_SAFE_INTENTS


@pytest.mark.parametrize("name", [
    "list_add", "list_remove", "calendar_create", "reminder_create", "timer_create",
    "note_create", "journal_create", "people_create", "people_introduce",
    "memory_store", "memory_remember", "memory_forget_last", "memory_forget_entity",
    "transaction_create", "music_play", "music_control", "music_volume", "music_favorite",
    "smart_home", "set_volume", "pending_offer_accept", "engineering_task_create",
    "lets_talk", "daily_briefing", "some_future_intent",
])
def test_side_effect_and_unknown_intents_are_not_speculation_safe(name):
    assert not vs.intent_is_speculation_safe(name)


@pytest.mark.parametrize("intent_name,routed,sky,safe", [
    ("time_query", "time", None, True),
    (None, "chat", None, True),
    ("weather", "weather", ("weather", "forecast"), True),
    (None, "chat", ("clock", "time"), True),
    ("list_add", "lists", None, False),
    (None, "lists", None, False),            # a write-capable domain → brain may write
    (None, "chat", ("lists", "add_item"), False),
    (None, "chat", ("smart_home", "turn_on"), False),
    ("list_show", "lists", ("lists", "show"), False),  # read intent, write-capable domain
    # Router OFF (its default) → no routed domain: NEUTRAL, the verdict rests on
    # the regex read-allowlist and the Skybridge pair.
    (None, None, None, True),
    ("time_query", None, None, True),
    ("list_add", None, None, False),
    (None, None, ("lists", "add_item"), False),
])
def test_turn_classification(intent_name, routed, sky, safe):
    ok, why = vs.turn_is_speculation_safe(intent_name, routed, sky)
    assert ok is safe, why


def test_real_detect_intent_marks_a_shopping_add_as_a_write():
    det = intent_router.detect_intent("add milk to the shopping list", log_miss=False)
    assert det is not None and det.name == "list_add"
    assert vs.turn_is_speculation_safe(det.name, "chat")[0] is False


# ── (e) through the router: task AND lazily-pulled body are bound ─────────

def _app(monkeypatch, brain):
    monkeypatch.setenv("ZOE_VOICE_FILLER_ENABLED", "0")
    monkeypatch.setenv("ZOE_VOICE_GREETING_ENABLED", "0")

    async def _fake_stt(_path, capture=True):
        return "add milk to the shopping list"
    monkeypatch.setattr(vt, "_transcribe_audio", _fake_stt)

    async def _fake_tts(_text):
        return b"RIFFfakewav"
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", _fake_tts)
    monkeypatch.setattr(vt, "voice_command", brain)

    app = FastAPI()
    app.include_router(vt.router)
    app.dependency_overrides[vt._require_voice_auth] = lambda: {
        "source": "device", "panel_id": "test-panel", "user_id": "voice-daemon",
    }
    from database import get_db as _real_get_db
    app.dependency_overrides[_real_get_db] = lambda: None
    return app


def _post(client, turn_id: str) -> list[dict]:
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(),
               "panel_id": "test-panel", "speculative": True, "turn_id": turn_id}
    r = client.post("/api/voice/turn_stream", json=payload)
    assert r.status_code == 200
    out = []
    for ln in (x for x in r.content.split(b"\n") if x):
        try:
            out.append(json.loads(ln))
        except Exception:
            out.append({"_raw_audio": True})
    return out


def _deliver_verdict_later(turn_id: str, action: str, delay: float = 0.1) -> None:
    """The daemon's verdict, arriving while the write is waiting."""
    loop = asyncio.get_running_loop()
    loop.call_later(delay, lambda: vs.get_gate(turn_id) and vs.get_gate(turn_id).resolve(action))


def _task_brain(ledger: _Ledger, turn_id: str, action: str):
    """voice_command doing its write INSIDE the sub_task (fast-tier / quick-intent path)."""
    def brain(payload, caller=None, stream=True, db=None):
        async def _inner():
            _deliver_verdict_later(turn_id, action)
            reply = await intent_router.execute_intent(Intent("list_add", {"item": "milk"}), "jason")
            vt._spawn_bg(_bg_row(ledger))
            return {"reply": reply, "audio_base64": base64.b64encode(b"RIFFok").decode()}
        return _inner()
    return brain


def _body_brain(ledger: _Ledger, turn_id: str, action: str):
    """voice_command returning at once; the write happens in the LAZY body (brain lane)."""
    def brain(payload, caller=None, stream=True, db=None):
        async def _make():
            async def _gen():
                _deliver_verdict_later(turn_id, action)
                reply = await intent_router.execute_intent(Intent("list_add", {"item": "milk"}), "jason")
                vt._spawn_bg(_bg_row(ledger))
                yield (json.dumps({"chunk": 0, "text": reply}) + "\n").encode()
                yield base64.b64encode(b"RIFFok") + b"\n"
                yield (json.dumps({"done": True, "reply": reply}) + "\n").encode()
            return StreamingResponse(_gen(), media_type="application/x-zoe-audio-stream")
        return _make()
    return brain


async def _bg_row(ledger: _Ledger):
    ledger.record("history_row", None)


@pytest.mark.parametrize("brain_factory", [_task_brain, _body_brain], ids=["task", "lazy_body"])
@pytest.mark.parametrize("action", ["commit", "cancel"])
def test_router_speculative_write_waits_for_the_verdict(monkeypatch, brain_factory, action):
    monkeypatch.setenv(vs.FLAG, "1")
    monkeypatch.setenv(vs.MAX_HOLD_FLAG, "3000")
    ledger, gate_ref = _Ledger(), {}

    async def _list_add(intent, user_id):
        gate = vs.bound_gate()
        ledger.record("list_add", gate)
        return "Added milk."
    monkeypatch.setattr(intent_router, "_execute_list_add_direct", _list_add)

    turn_id = f"router-{brain_factory.__name__}-{action}"
    app = _app(monkeypatch, brain_factory(ledger, turn_id, action))
    with TestClient(app) as client:
        parsed = _post(client, turn_id)
        # let a committed turn's queued history row drain inside the app loop
        client.portal.call(asyncio.sleep, 0.1)
    writes = [c for c in ledger.calls if c["what"] == "list_add"]
    rows = [c for c in ledger.calls if c["what"] == "history_row"]
    if action == "commit":
        assert len(writes) == 1 and writes[0]["resolved"] and writes[0]["verdict"] == "commit", writes
        assert len(rows) == 1, rows
        assert any("chunk" in p or "full_audio" in p for p in parsed), parsed
        assert parsed[-1].get("done") and not parsed[-1].get("cancelled"), parsed
    else:
        assert writes == [] and rows == [], ledger.calls
        assert not any("chunk" in p or "full_audio" in p or p.get("_raw_audio") for p in parsed), parsed
        assert parsed[-1].get("done") and parsed[-1].get("cancelled"), parsed
    assert vs.get_gate(turn_id) is None


# ── (f) negative control: remove the guard → red ──────────────────────────

def test_negative_control_unguarded_execute_intent_is_caught(monkeypatch):
    async def _no_barrier(_name):
        return None
    monkeypatch.setattr(intent_router, "_speculative_side_effect_barrier", _no_barrier)
    for verdict in ("commit", "cancel"):
        ledger, gate_ref = _Ledger(), {}
        _patch_executors(monkeypatch, ledger, gate_ref)
        before, outcome, _ = asyncio.run(_speculative_write(verdict, ledger=ledger, gate_ref=gate_ref))
        with pytest.raises(AssertionError):
            _assert_write_held_until_verdict(before, outcome, ledger, verdict)


def test_negative_control_undeferred_spawn_is_caught(monkeypatch):
    monkeypatch.setattr(vs, "defer_until_commit", lambda coro, what="background": coro)
    order: list[str] = []

    async def _effect():
        order.append("row")

    async def _run():
        gate = vs.open_gate("bg-neg")
        token = vs.bind(gate)
        try:
            vt._spawn_bg(_effect())
        finally:
            vs.unbind(token)
        await asyncio.sleep(0.05)
        before = list(order)
        gate.resolve("cancel")
        vs.close_gate(gate)
        return before

    assert asyncio.run(_run()) == ["row"], "without the deferral the row lands before any verdict"


def test_gate_answers_cancelled_when_a_dropped_side_effect_ends_the_upstream():
    """The pull that raises SpeculativeTurnCancelled can complete BEFORE the gate's
    own verdict waiter wakes; the stream must still end with the cancelled frame,
    not a propagated CancelledError (which would abort the response mid-stream)."""
    async def _run():
        gate = vs.open_gate("drop-race")

        async def _upstream():
            yield (json.dumps({"transcript": "add milk"}) + "\n").encode()
            gate.resolve("cancel")  # verdict lands; the held write raises in the same step
            raise vs.SpeculativeTurnCancelled("intent:list_add")
            yield b""  # pragma: no cover

        out = [f async for f in vs.gate_frames(_upstream(), gate)]
        return [json.loads(f) for f in out]

    parsed = asyncio.run(_run())
    assert parsed[-1].get("done") and parsed[-1].get("cancelled"), parsed




def test_router_off_read_turn_runs_and_write_turn_is_held():
    """Greptile #1742: with the semantic router off (default) every turn used to be
    held. Now: router off + read intent → safe; router off + write intent → held."""
    read = intent_router.detect_intent("what time is it", log_miss=False)
    write = intent_router.detect_intent("add milk to the shopping list", log_miss=False)
    assert vs.turn_is_speculation_safe(read.name if read else None, None)[0] is True
    assert vs.turn_is_speculation_safe(write.name, None)[0] is False


# ── background ORDER is by completion, not by a timer ─────────────────────

def test_background_effects_run_in_spawn_order_even_when_the_first_is_slow(monkeypatch):
    """Greptile #1742: a timed wait let a slow first effect be overtaken. A 0.3 s
    first effect and a fast second, with max-hold far SHORTER than 0.3 s: the second
    must still start only after the first completed."""
    monkeypatch.setenv(vs.MAX_HOLD_FLAG, "100")
    events: list[str] = []

    async def _slow():
        events.append("slow:start")
        await asyncio.sleep(0.3)
        events.append("slow:end")

    async def _fast():
        events.append("fast:start")
        events.append("fast:end")

    async def _run():
        gate = vs.open_gate("order-slow")
        token = vs.bind(gate)
        try:
            vt._spawn_bg(_slow())
            vt._spawn_bg(_fast())
        finally:
            vs.unbind(token)
        await asyncio.sleep(0.02)
        gate.resolve("commit")
        await asyncio.sleep(0.5)
        vs.close_gate(gate)

    asyncio.run(_run())
    assert events == ["slow:start", "slow:end", "fast:start", "fast:end"], events


# ── brain lane: tool writes arrive on ANOTHER request (intent-dispatch) ───

def _dispatch_app(monkeypatch, calls: list, state: dict):
    import auth
    from routers.system import router as system_router

    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")

    async def _fake_exec(intent, user_id="guest"):
        gate = state.get("gate")
        calls.append({"intent": intent.name, "resolved": bool(gate and gate.resolved),
                      "verdict": gate.verdict() if gate and gate.resolved else None})
        return "Added milk."
    monkeypatch.setattr(intent_router, "execute_intent", _fake_exec)
    app = FastAPI()
    app.include_router(system_router)
    return app


def _dispatch(client, **extra):
    return client.post("/api/system/intent-dispatch",
                       json={"user_id": "jason", "intent": "list_add", "slots": {"item": "milk"}, **extra},
                       headers={"X-Internal-Token": "tok"})


@pytest.mark.parametrize("action,expect_ok", [("commit", True), ("cancel", False)])
def test_dispatch_from_the_speculative_turn_waits_for_its_verdict(monkeypatch, action, expect_ok):
    calls: list[dict] = []
    state: dict = {}
    app = _dispatch_app(monkeypatch, calls, state)
    turn_id = f"voiceturn{action}"
    with TestClient(app) as client:
        async def _open():
            gate = vs.open_gate(turn_id)
            asyncio.get_running_loop().call_later(0.1, gate.resolve, action)
            return gate
        state["gate"] = client.portal.call(_open)
        resp = _dispatch(client, speculative_turn_id=turn_id)
        client.portal.call(vs.close_gate, state["gate"])
    assert resp.status_code == 200 and resp.json()["ok"] is expect_ok
    if expect_ok:
        assert calls == [{"intent": "list_add", "resolved": True, "verdict": "commit"}]
    else:
        assert calls == [], "a brain tool write ran for a cancelled speculative turn"


def test_same_user_dispatch_from_another_session_runs_at_once(monkeypatch):
    """Greptile #1742: a pending voice turn must not hold (or, on cancel, refuse) the
    same user's write from a chat session — only the originating turn is held."""
    calls: list[dict] = []
    state: dict = {}
    app = _dispatch_app(monkeypatch, calls, state)
    with TestClient(app) as client:
        async def _open():
            return vs.open_gate("pending-voice-turn")
        state["gate"] = client.portal.call(_open)
        resp = _dispatch(client)                       # no speculative_turn_id: chat session
        client.portal.call(state["gate"].resolve, "cancel")
        client.portal.call(vs.close_gate, state["gate"])
    assert resp.json()["ok"] is True
    assert calls == [{"intent": "list_add", "resolved": False, "verdict": None}], \
        "the chat-session write must run immediately, before the voice verdict"


def test_late_dispatch_after_the_gate_closed_follows_the_recorded_verdict():
    async def _run():
        out = {}
        for tid, action in (("late-commit", "commit"), ("late-cancel", "cancel")):
            gate = vs.open_gate(tid)
            gate.resolve(action)
            vs.close_gate(gate)
            out[tid] = await vs.hold_speculative_dispatch(tid, "list_add")
        out["never-seen"] = await vs.hold_speculative_dispatch("never-seen", "list_add")
        out["read"] = await vs.hold_speculative_dispatch("late-cancel", "list_show")
        return out

    assert asyncio.run(_run()) == {"late-commit": True, "late-cancel": False,
                                   "never-seen": True, "read": True}


def test_negative_control_unkeyed_dispatch_hold_is_caught(monkeypatch):
    """Break the fix: ignore the turn id → the originating turn's write runs before
    its verdict and on a cancel."""
    async def _no_hold(turn_id, intent_name):
        return True
    monkeypatch.setattr(vs, "hold_speculative_dispatch", _no_hold)
    calls: list[dict] = []
    state: dict = {}
    app = _dispatch_app(monkeypatch, calls, state)
    with TestClient(app) as client:
        async def _open():
            gate = vs.open_gate("neg-dispatch")
            asyncio.get_running_loop().call_later(0.1, gate.resolve, "cancel")
            return gate
        state["gate"] = client.portal.call(_open)
        _dispatch(client, speculative_turn_id="neg-dispatch")
        client.portal.call(vs.close_gate, state["gate"])
    assert calls and calls[0]["resolved"] is False  # ran before (and despite) the cancel


# ── the seam forwards the turn id; non-echoing lanes hold instead ─────────

def test_flue_seam_wraps_only_a_bound_speculative_turn_on_wire_2(monkeypatch):
    import zoe_flue_client as zfc
    monkeypatch.delenv("ZOE_FLUE_WIRE", raising=False)

    async def _run():
        unbound = await zfc._speculative_turn_for_wire()
        gate = vs.open_gate("abc123")
        token = vs.bind(gate)
        try:
            bound = await zfc._speculative_turn_for_wire()
        finally:
            vs.unbind(token)
        vs.close_gate(gate)
        return unbound, bound

    unbound, bound = asyncio.run(_run())
    assert unbound is None and bound == "abc123"
    assert zfc._wrap_message_with_speculative_turn(" zoe-uid:jason\nhi", None) == " zoe-uid:jason\nhi"
    assert zfc._wrap_message_with_speculative_turn(" zoe-uid:jason\nhi", "abc123") == \
        " zoe-spec:abc123\n zoe-uid:jason\nhi"
    # a user-typed marker can never reach the wire as trusted
    assert zfc._strip_replay_envelope(" zoe-spec:forged\n zoe-replay:1\nadd milk") == "add milk"


@pytest.mark.parametrize("wire,turn_id", [("1", "abc123"), ("2", "bad id!")])
def test_flue_seam_holds_when_it_cannot_echo(monkeypatch, wire, turn_id):
    """Wire 1 (no parser) or a non-wire-safe id: the brain waits for the verdict."""
    import zoe_flue_client as zfc
    monkeypatch.setenv("ZOE_FLUE_WIRE", wire)

    async def _run():
        gate = vs.open_gate(turn_id)
        token = vs.bind(gate)
        try:
            task = asyncio.ensure_future(zfc._speculative_turn_for_wire())
        finally:
            vs.unbind(token)
        await asyncio.sleep(0.05)
        waited = not task.done()
        gate.resolve("cancel")
        with pytest.raises(asyncio.CancelledError):
            await task
        vs.close_gate(gate)
        return waited

    assert asyncio.run(_run()) is True


def test_python_and_typescript_agree_on_the_speculative_envelope():
    from pathlib import Path
    import zoe_flue_client as zfc
    ts = (Path(__file__).resolve().parents[3] / "labs" / "flue-zoe-brain-2x" / "src"
          / "speculative-turn.ts").read_text()
    assert f"export const SPECULATIVE_ENVELOPE_PREFIX = '{zfc._SPECULATIVE_ENVELOPE_PREFIX}'" in ts
    tools = (Path(__file__).resolve().parents[3] / "labs" / "flue-zoe-brain-2x" / "src"
             / "tools" / "zoe-tools.ts").read_text()
    assert "speculative_turn_id: currentSpeculativeTurnId(signal)" in tools


@pytest.mark.parametrize("action", ["commit", "cancel"])
def test_non_flue_brain_lane_waits_for_the_verdict(monkeypatch, action):
    import brain_dispatch
    monkeypatch.setattr(brain_dispatch, "use_flue_brain", lambda: False)
    monkeypatch.setattr(brain_dispatch, "use_core_brain", lambda: True)
    started: list[bool] = []

    async def _fake_core(message, session_id, user_id, **kw):
        started.append(True)
        yield "hello"

    import zoe_core_client
    monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", _fake_core)

    async def _run():
        gate = vs.open_gate(f"core-{action}")
        token = vs.bind(gate)
        try:
            stream = brain_dispatch.brain_streaming("hi", "s1", "jason")
        finally:
            vs.unbind(token)

        async def _consume():
            return [d async for d in stream]
        task = asyncio.ensure_future(_consume())
        await asyncio.sleep(0.05)
        before = list(started)
        gate.resolve(action)
        try:
            out = await task
        except asyncio.CancelledError:
            out = None
        vs.close_gate(gate)
        return before, out

    before, out = asyncio.run(_run())
    assert before == [], "the non-echoing lane started before the verdict"
    if action == "commit":
        assert out == ["hello"] and started == [True]
    else:
        assert out is None and started == []
