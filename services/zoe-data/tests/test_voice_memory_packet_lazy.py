"""The voice memory packet is LAZY on the Flue lane.

Measured live right after #1725: ``pre_brain_ms=53 memory_packet_ms=502
(history=0 memory=502 domain=0)``. The live lane (``ZOE_BRAIN_BACKEND=flue``)
never reads ``history`` / ``db_memory_context`` / ``portrait`` — the sidecar owns
its own memory and history, and its recall/offer blocks are built INSIDE
``zoe_flue_client`` from the message + user id — so awaiting the packet before
the brain call was ~500 ms of dead work on every voice turn.

Pinned here, through the real ``_voice_brain_kwargs`` -> ``brain_dispatch`` seam:

* a Flue-served turn NEVER calls the packet builder, and the sidecar gets the
  same message / session / user it always did (so its own recall + offer blocks
  are unchanged);
* a Flue transport failure that fails over to core OR legacy reaches that lane
  WITH the packet, built lazily at the hop, exactly once;
* the circuit-open skip and a configured non-Flue lane resolve it too;
* a failing loader degrades to no context, never a failed turn; replay
  isolation still fails closed at call time;
* ``ZOE_VOICE_MEMORY_PACKET_LAZY=false`` (kill switch) and non-Flue lanes keep the
  eager build — and that kill-switch case is the NEGATIVE CONTROL for the
  never-called assertion: the same assertion goes red when the packet is eager.
"""
from __future__ import annotations

import pytest

import brain_dispatch as bd
import routers.voice_tts as vt

pytestmark = pytest.mark.ci_safe

HISTORY = [{"role": "user", "content": "earlier turn"}]
DB_MEMORY = "[What you remember]\n- likes tea"
PORTRAIT = "You are speaking with Jason (the signed-in user)."
DOMAIN = "[Your lists]\n- milk"
CONTEXT_KEYS = ("history", "db_memory_context", "portrait")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    bd.reset_failover_state()
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    monkeypatch.delenv("ZOE_VOICE_MEMORY_PACKET_LAZY", raising=False)
    yield
    bd.reset_failover_state()


@pytest.fixture
def builder(monkeypatch):
    """Counting stand-in for the ~500 ms concurrent packet gather."""
    calls: list[tuple] = []

    async def fake_context(session_id, user_id, text, router_decision):
        calls.append((session_id, user_id, text, router_decision))
        return list(HISTORY), DB_MEMORY, PORTRAIT, DOMAIN, {
            "history": 0.0, "memory": 0.5, "domain": 0.0, "memory_packet": 0.5,
        }

    monkeypatch.setattr(vt, "_voice_brain_context", fake_context)
    return calls


def _flue(monkeypatch, *, fail: bool = False):
    import zoe_flue_client

    seen: list[dict] = []

    async def fake_flue(message, session_id, user_id="", **kw):
        seen.append({"message": message, "session_id": session_id, "user_id": user_id, **kw})
        if fail:
            raise zoe_flue_client.FlueTransportError("[Errno 111] Connection refused")
        yield "flue answered"

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", fake_flue)
    return seen


def _lane(monkeypatch, lane: str):
    """Record what the core / legacy lane was dispatched with."""
    seen: list[dict] = []

    async def fake_stream(message, session_id, user_id="", **kw):
        seen.append(kw)
        yield f"{lane} answered"

    async def fake_oneshot(message, session_id, user_id="", **kw):
        seen.append(kw)
        return f"{lane} answered"

    if lane == "core":
        import zoe_core_client

        monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", fake_stream)
        monkeypatch.setattr(zoe_core_client, "run_zoe_core", fake_oneshot)
    else:
        import zoe_agent

        monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "false")
        monkeypatch.setattr(zoe_agent, "run_zoe_agent_streaming", fake_stream)
        monkeypatch.setattr(zoe_agent, "run_zoe_agent", fake_oneshot)
    return seen


async def _voice_turn(streaming: bool = True):
    """What voice_command does: prepare the kwargs, then dispatch."""
    kwargs, timings, mode = await vt._voice_brain_kwargs(
        "voice-panel-1", "jason", "what do I like to drink", {"domain": "lists"}
    )
    if streaming:
        out = [d async for d in bd.brain_streaming(
            "what do I like to drink", "voice-panel-1", user_id="jason", voice_mode=True, **kwargs
        )]
    else:
        out = [await bd.brain_oneshot(
            "what do I like to drink", "voice-panel-1", user_id="jason", voice_mode=True, **kwargs
        )]
    return out, timings, mode


def _assert_packet(kw: dict) -> None:
    assert kw["history"] == HISTORY
    assert kw["db_memory_context"] == f"{DB_MEMORY}\n\n{DOMAIN}", "memory + domain merged"
    assert kw["portrait"] == PORTRAIT
    assert kw["voice_mode"] is True
    assert "context_loader" not in kw


# ── the live lane: Flue serves the turn, the packet is never built ───────────


@pytest.mark.parametrize("failover", ["0", "1"])
@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.asyncio
async def test_flue_success_never_calls_the_packet_builder(monkeypatch, builder, failover, streaming):
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", failover)
    flue_seen = _flue(monkeypatch)
    _lane(monkeypatch, "core")

    out, timings, mode = await _voice_turn(streaming)

    assert out == ["flue answered"]
    assert builder == [], "the Flue lane never reads the packet — it must not be built"
    assert mode == "lazy" and timings == {}
    assert vt._voice_packet_state(mode, timings) == "skipped"
    # The sidecar gets exactly what it always got: the words, the session and the
    # identity its own recall/offer blocks are built from — and no packet kwargs.
    (call,) = flue_seen
    assert (call["message"], call["session_id"], call["user_id"]) == (
        "what do I like to drink", "voice-panel-1", "jason",
    )
    for key in (*CONTEXT_KEYS, "context_loader"):
        assert key not in call, key


@pytest.mark.asyncio
async def test_negative_control_kill_switch_builds_eagerly(monkeypatch, builder):
    """NEGATIVE CONTROL: with the packet eager (the pre-change behaviour, and the
    ``ZOE_VOICE_MEMORY_PACKET_LAZY=false`` kill switch), the never-called
    assertion above goes red — the builder runs before dispatch even though
    Flue then ignores what it built."""
    monkeypatch.setenv("ZOE_VOICE_MEMORY_PACKET_LAZY", "false")
    flue_seen = _flue(monkeypatch)

    out, timings, mode = await _voice_turn()

    assert out == ["flue answered"]
    assert mode == "eager" and len(builder) == 1
    with pytest.raises(AssertionError):
        assert builder == []
    assert flue_seen[0]["db_memory_context"] == f"{DB_MEMORY}\n\n{DOMAIN}"  # built, then ignored


# ── failover: the packet is built lazily at the hop ──────────────────────────


@pytest.mark.parametrize("lane", ["core", "legacy"])
@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.asyncio
async def test_flue_failure_falls_back_with_the_packet_built_lazily(monkeypatch, builder, lane, streaming):
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _flue(monkeypatch, fail=True)
    lane_seen = _lane(monkeypatch, lane)

    out, timings, mode = await _voice_turn(streaming)

    assert out == [f"{lane} answered"]
    assert len(builder) == 1, "built exactly once, at the hop"
    assert builder[0] == ("voice-panel-1", "jason", "what do I like to drink", {"domain": "lists"})
    (kw,) = lane_seen
    _assert_packet(kw)
    assert mode == "lazy" and timings["memory_packet"] == 0.5
    assert vt._voice_packet_state(mode, timings) == "lazy"


@pytest.mark.asyncio
async def test_circuit_open_skip_resolves_the_packet(monkeypatch, builder):
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    flue_seen = _flue(monkeypatch, fail=True)
    lane_seen = _lane(monkeypatch, "core")

    await _voice_turn()  # opens the breaker
    await _voice_turn()  # skips flue outright

    assert len(flue_seen) == 1, "second turn skipped the dead sidecar"
    assert len(builder) == 2 and len(lane_seen) == 2
    _assert_packet(lane_seen[1])


# ── non-Flue lanes and edge cases ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_flue_lane_keeps_the_eager_build(monkeypatch, builder):
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "core")
    lane_seen = _lane(monkeypatch, "core")

    out, timings, mode = await _voice_turn()

    assert out == ["core answered"] and mode == "eager"
    assert len(builder) == 1
    _assert_packet(lane_seen[0])


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.asyncio
async def test_a_loader_on_a_configured_non_flue_lane_is_resolved(monkeypatch, streaming):
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "core")
    lane_seen = _lane(monkeypatch, "core")

    async def loader():
        return {"history": HISTORY, "db_memory_context": DB_MEMORY, "portrait": PORTRAIT,
                "replay_isolation": True}  # not a context key — must not be merged

    if streaming:
        [d async for d in bd.brain_streaming("hi", "s", "jason", context_loader=loader)]
    else:
        await bd.brain_oneshot("hi", "s", "jason", context_loader=loader)

    (kw,) = lane_seen
    assert (kw["history"], kw["db_memory_context"], kw["portrait"]) == (HISTORY, DB_MEMORY, PORTRAIT)
    assert "replay_isolation" not in kw and "context_loader" not in kw


@pytest.mark.asyncio
async def test_failing_loader_degrades_to_no_context(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _flue(monkeypatch, fail=True)
    lane_seen = _lane(monkeypatch, "core")

    async def boom():
        raise RuntimeError("db down")

    out = [d async for d in bd.brain_streaming("hi", "s", "jason", context_loader=boom)]

    assert out == ["core answered"], "the turn is still answered"
    assert not any(k in lane_seen[0] for k in (*CONTEXT_KEYS, "context_loader"))
    assert "lazy lane context failed to load" in caplog.text


@pytest.mark.asyncio
async def test_replay_isolation_still_fails_closed_at_call_time(monkeypatch):
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "core")

    async def loader():  # pragma: no cover - must never be reached
        raise AssertionError("loader called on a refused turn")

    with pytest.raises(RuntimeError, match="replay_isolation"):
        bd.brain_streaming("hi", "s", "jason", replay_isolation=True, context_loader=loader)
