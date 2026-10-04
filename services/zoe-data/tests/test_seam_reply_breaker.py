"""A turn the SEAM answered without the sidecar (verify_on_challenge's honest
"can't check right now", outcome label ``seam_reply``) is no evidence the sidecar
is healthy: it must never close the failover breaker. A real sidecar success
still must. Fake dispatch only — no sidecar, no network (ci_safe)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _breaker(monkeypatch):
    import brain_dispatch as bd

    bd.reset_failover_state()
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    now = {"t": 100.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])
    bd._test_now = now  # type: ignore[attr-defined]
    yield now
    bd.reset_failover_state()


def _arm_then_expire(bd, now):
    """Open the breaker as a transport failure would, then let the cooldown lapse
    so the NEXT turn is the half-open probe."""
    gen = bd._observe_circuit()[1]
    bd._open_circuit(gen)
    assert bd._circuit_open() is True
    now["t"] += 31.0


def _fake_flue(monkeypatch, outcome):
    import zoe_flue_client as zf

    async def one(msg, sid, uid="", *, outcome_sink=None, **kw):
        if outcome_sink is not None and outcome:
            outcome_sink["outcome"] = outcome
        return "reply"

    async def stream(msg, sid, uid="", *, outcome_sink=None, **kw):
        if outcome_sink is not None and outcome:
            outcome_sink["outcome"] = outcome
        yield "reply"

    monkeypatch.setattr(zf, "run_flue_brain", one)
    monkeypatch.setattr(zf, "run_flue_brain_streaming", stream)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["oneshot", "streaming"])
async def test_seam_reply_does_not_close_the_breaker(monkeypatch, _breaker, kind):
    import brain_dispatch as bd

    _arm_then_expire(bd, _breaker)
    _fake_flue(monkeypatch, "seam_reply")
    if kind == "oneshot":
        assert await bd.brain_oneshot("are you sure", "s1", "jason") == "reply"
    else:
        assert [d async for d in bd.brain_streaming("are you sure", "s1", "jason")] == ["reply"]
    assert bd._flue_circuit_open_until != 0.0, "a seam-only answer proved nothing about the sidecar"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["oneshot", "streaming"])
@pytest.mark.parametrize("outcome", ["ok", ""])  # NEGATIVE CONTROL: a real success (or no verdict) closes it
async def test_real_sidecar_success_closes_the_breaker(monkeypatch, _breaker, kind, outcome):
    import brain_dispatch as bd

    _arm_then_expire(bd, _breaker)
    _fake_flue(monkeypatch, outcome)
    if kind == "oneshot":
        await bd.brain_oneshot("hello", "s1", "jason")
    else:
        [d async for d in bd.brain_streaming("hello", "s1", "jason")]
    assert bd._flue_circuit_open_until == 0.0
